"""v1.8.5 acceptance tests: missing job skills are found in the candidate's own experience, named in the bullets of the
best-supported employer, survive validation and the automatic fixes, and reach the exported PDF, with no confirmation
or approval on the way. Skills without evidence are reported, never written into the experience.
"""

from __future__ import annotations

import io
import json
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from resumeflow.devtools.sample_data import SampleCandidate, SampleRole, resume_docx_bytes
from resumeflow.domain.content import AIResumeTailoring, AISkillPlacement
from resumeflow.domain.requirements import JobRequirements, empty_requirements
from resumeflow.services.generation import ai_resume
from resumeflow.services.inference import (
    check_proposed_bullet,
    expand_snapshot,
    infer_role_skills,
    name_skill,
)
from tests.conftest import DOCX

# A fictional candidate whose evidence for each job skill sits with a different employer.
JORDAN = SampleCandidate(
    "Jordan Ellery",
    "jordan.ellery@example.com",
    "(555) 410-2290",
    "Denver, CO",
    "Senior Software Engineer",
    "Software engineer with 11 years building web platforms and cloud services.",
    ["Python", "JavaScript", "TypeScript", "Docker", "Jenkins", "Git", "RabbitMQ"],
    [
        SampleRole("Senior Software Engineer", "Harborview Software Inc", "Denver, CO", "Mar 2021 – Present", [
            "Built a customer self-service portal in Next.js used by 40,000 monthly users",
            "Built Lambda functions triggered by S3 uploads to generate PDF invoices",
            "Packaged three internal services as Helm charts for the platform team",
            "Mentored four engineers through code reviews and pairing",
        ]),
        SampleRole("Software Engineer", "Crestline Logistics LLC", "Boulder, CO", "Jun 2017 – Feb 2021", [
            "Ran 30 microservices on Amazon EKS, cutting deployment time by 50%",
            "Debugged production incidents across clusters with kubectl and Grafana",
            "Automated build and deployment pipelines for 12 services in Jenkins",
        ]),
        SampleRole("Software Developer", "Pinecrest Analytics Co", "Fort Collins, CO", "Jan 2014 – May 2017", [
            "Modeled delivery zones with PostGIS and tuned spatial queries, reducing report time by 60%",
            "Processed order events from RabbitMQ queues for real-time dashboards",
        ]),
    ],
    ["B.S. Computer Science, Colorado State University, 2013"],
)
HARBORVIEW, CRESTLINE, PINECREST = "Harborview Software Inc", "Crestline Logistics LLC", "Pinecrest Analytics Co"
EMPLOYERS = [HARBORVIEW, CRESTLINE, PINECREST]

PLATFORM_JD = """Senior Cloud Platform Engineer
Summit Peak Technologies - Denver, CO

About the role
You will build customer-facing web applications and event-driven cloud services.

Requirements
- 6+ years of software engineering
- React for customer-facing web applications
- AWS Lambda for event-driven services
- Kubernetes in production
- PostgreSQL
- Terraform

Nice to have
- Apache Kafka
- Golang
"""
SUPPORTED = {"React": HARBORVIEW, "AWS Lambda": HARBORVIEW, "Kubernetes": CRESTLINE, "PostgreSQL": PINECREST}
UNSUPPORTED = ["Terraform", "Kafka", "Golang"]


def docx_to_pdf(documents: list[bytes], settings) -> list[bytes]:
    """Stands in for Word/LibreOffice in the default test run: a real PDF with the rendered DOCX's text, paragraph by
    paragraph (test_exported_pdf_with_microsoft_word runs the real converter)."""
    from docx import Document

    pdfs = []
    for data in documents:
        lines = []
        for paragraph in Document(io.BytesIO(data)).paragraphs:
            text = paragraph.text.replace("\t", "   ").replace("–", "-").replace("·", "|").replace("“", '"').replace("”", '"')
            prefix = "- " if paragraph.style.name == "List Bullet" else ""
            lines += textwrap.wrap(prefix + text, 95) or [""]
        pdf = pymupdf.open()
        page, y = pdf.new_page(), 50
        for line in lines:
            if y > 800:
                page, y = pdf.new_page(), 50
            page.insert_text((40, y), line, fontsize=9)
            y += 12
        pdfs.append(pdf.tobytes())
        pdf.close()
    return pdfs


def pdf_text(path: str | Path) -> str:
    with pymupdf.open(str(path)) as pdf:
        return " ".join(" ".join(page.get_text().split()) for page in pdf)


def employer_sections(text: str) -> dict[str, str]:
    """The exported resume's text under each employer (from its heading to the next employer, or Education)."""
    starts = sorted((text.find(e), e) for e in EMPLOYERS)
    assert all(pos >= 0 for pos, _ in starts), f"an employer is missing from the PDF: {text[:400]}"
    end = text.upper().find("EDUCATION", starts[-1][0])
    sections = {}
    for i, (pos, employer) in enumerate(starts):
        sections[employer] = text[pos : starts[i + 1][0] if i + 1 < len(starts) else (end if end > 0 else len(text))]
    return sections


def plain(text: str) -> str:
    return " ".join(text.replace("–", "-").replace("·", "|").split())


@pytest.fixture
def jordan(client, tmp_path_factory, monkeypatch) -> dict:
    """Defaults that matter here: no verification step, no questions about missing skills, export automatically, PDF
    only. The PDF converter is the stand-in above."""
    import resumeflow.services.applications as applications

    monkeypatch.setattr(applications, "resolve_converter", lambda settings: "word")
    monkeypatch.setattr(applications, "convert_documents_to_pdf", docx_to_pdf)
    client.patch(
        "/settings",
        json={
            "require_verification": False,
            "gap_confirmation": "off",
            "auto_approve_bullets": True,
            "export_mode": "auto",
            "output_formats": "pdf",
            "pdf_converter": "auto",
            "output_root": str(tmp_path_factory.mktemp("o")),
        },
    )
    upload = client.post("/candidates/import", files={"file": ("Jordan_Ellery_Resume.docx", resume_docx_bytes(JORDAN), DOCX)})
    assert upload.status_code in (200, 201), upload.text
    candidate = upload.json()["candidate"]
    client.patch(f"/candidates/{candidate['id']}", json={"consent_confirmed": True})
    job = client.post("/jobs", json={"job_url": "https://jobs.example.com/summit-peak-platform", "raw_jd": PLATFORM_JD}).json()
    client.app.state.queue.drain()
    profile = client.get(f"/candidates/{candidate['id']}").json()["profile"]
    return {"id": candidate["id"], "job_id": job["id"], "profile": profile}


def _generate(client, jordan) -> dict:
    created = client.post("/applications", json={"job_id": jordan["job_id"], "candidate_id": jordan["id"]})
    assert created.status_code in (200, 201), created.text
    client.app.state.queue.drain()  # generation, validation, automatic fixes, then the export it queues
    return client.get(f"/applications/{created.json()['id']}").json()


def _roles(app: dict) -> dict[str, dict]:
    return {r["employer"]: r for r in app["resume"]["content"]["experience"]}


def _bullets_naming(role: dict, skill: str) -> list[str]:
    return [b["text"] for b in role["bullets"] if re.search(rf"(?<![\w.]){re.escape(skill)}(?![\w])", b["text"])]


def _exported(app: dict, kind: str, fmt: str) -> Path:
    path = next(f["path"] for f in app["files"] if f["type"] == kind and f["format"] == fmt and f["exists"])
    return Path(path)


def _assert_distributed(app: dict, profile: dict) -> dict[str, str]:
    """Each supported skill is in a bullet of its best-supported employer and of no other; returns skill → bullet."""
    roles = _roles(app)
    inserted = {}
    for skill, employer in SUPPORTED.items():
        named = _bullets_naming(roles[employer], skill)
        assert named, f"{skill} is not in a {employer} bullet: {roles[employer]['bullets']}"
        inserted[skill] = named[0]
        for other in set(EMPLOYERS) - {employer}:
            assert not _bullets_naming(roles[other], skill), f"{skill} was put under {other}"
    # Employer names, titles, dates and education are the profile's own.
    for role in app["resume"]["content"]["experience"]:
        source = next(r for r in profile["employment"] if r["id"] == role["role_id"])
        assert (role["employer"], role["title"], role["start"], role["end"]) == (source["employer"], source["title"], source["start"], source["end"])
    assert [e["id"] for e in app["resume"]["content"]["education"]] == [e["id"] for e in profile["education"]]
    return inserted


def _assert_pdf(app: dict, inserted: dict[str, str]) -> None:
    """The exported PDF (read back from disk) has each inserted bullet under the right employer, and nothing for the
    skills without evidence."""
    text = pdf_text(_exported(app, "resume", "pdf"))
    sections = employer_sections(text)
    for skill, bullet in inserted.items():
        employer = SUPPORTED[skill]
        assert plain(bullet) in plain(sections[employer]), f"“{bullet}” is not under {employer} in the PDF: {sections[employer]}"
        for other in set(EMPLOYERS) - {employer}:
            assert plain(bullet) not in plain(sections[other])
    for skill in UNSUPPORTED:
        assert skill.lower() not in text.lower(), f"{skill} has no evidence but is in the PDF"


def _gap_report(app: dict) -> dict[str, dict]:
    metadata = json.loads(_exported(app, "metadata", "json").read_text(encoding="utf-8"))
    return {r["skill"]: r for r in metadata["skill_gap_report"]}


# ---------------------------------------------------------------- end to end, offline (no API key)


def test_supported_skills_are_inserted_under_their_employers_and_exported(client, jordan):
    """A supported JD skill goes into the correct employer's bullets; several missing skills are spread over their
    best-supported employers (Kubernetes goes to Crestline with two supporting bullets, not to the more recent
    Harborview with one); the bullets survive validation; the exported PDF has them; nothing waits for a person."""
    app = _generate(client, jordan)
    assert app["status"] == "EXPORTED" and app["approved_by"] == "automatic" and not app["error"], app.get("error")
    assert app["resume"]["mode"] == "deterministic"  # the offline generator inserts bullets, not only Technologies lines
    assert app["resume"]["issue_counts"]["error"] == 0, app["resume"]["issues"]

    inserted = _assert_distributed(app, jordan["profile"])
    assert inserted["React"] == "Built a customer self-service portal in Next.js (React) used by 40,000 monthly users"
    assert inserted["AWS Lambda"] == "Built AWS Lambda functions triggered by S3 uploads to generate PDF invoices"
    assert inserted["Kubernetes"] == "Ran 30 microservices on Amazon EKS (Kubernetes), cutting deployment time by 50%"
    assert inserted["PostgreSQL"].startswith("Modeled delivery zones with PostGIS (PostgreSQL)")
    roles = _roles(app)
    assert not _bullets_naming(roles[HARBORVIEW], "Kubernetes")  # Helm alone is weaker evidence than Crestline's
    assert "Kubernetes" in roles[CRESTLINE]["technologies"] and "React" in roles[HARBORVIEW]["technologies"]
    assert {"React", "AWS Lambda", "Kubernetes", "PostgreSQL"} <= set(app["resume"]["content"]["skills"])

    _assert_pdf(app, inserted)

    report = _gap_report(app)
    for skill, employer in SUPPORTED.items():
        assert report[skill]["status"] == "added_to_experience" and report[skill]["employer"] == employer
    assert report["Terraform"]["status"] == "not_supported" and report["Terraform"]["required"] and not report["Terraform"]["resolved"]
    assert report["Golang"]["status"] == "not_supported"
    kafka = next(r for name, r in report.items() if "Kafka" in name)
    assert kafka["status"] == "transferable" and kafka["employer"] == PINECREST and kafka["related"] == ["RabbitMQ"]
    notes = " ".join(i["message"] for i in app["resume"]["issues"] if i["code"] == "skill_gap_report")
    assert "Terraform" in notes and "React (Harborview Software Inc)" in notes

    # The Changes view shows the candidate's own wording as the original; the coverage report carries the gap report.
    coverage = client.get(f"/applications/{app['id']}/coverage").json()
    rewritten = {b["text"]: b["original"] for b in coverage["changes"]["bullets"] if b["kind"] == "rewritten"}
    assert rewritten[inserted["React"]] == "Built a customer self-service portal in Next.js used by 40,000 monthly users"
    assert {r["skill"] for r in coverage["coverage"]["skill_gap_report"]} >= set(SUPPORTED) | {"Terraform"}
    # The job never waited for answers about missing skills.
    assert not client.get("/dashboard/summary").json().get("jobs_needing_confirmation")


def test_regenerating_keeps_the_same_placements(client, jordan):
    app = _generate(client, jordan)
    first = _assert_distributed(app, jordan["profile"])
    client.post(f"/applications/{app['id']}/generate", json={})
    client.app.state.queue.drain()
    again = client.get(f"/applications/{app['id']}").json()
    assert _assert_distributed(again, jordan["profile"]) == first
    assert again["resume"]["issue_counts"]["error"] == 0


def test_skills_the_operator_rejected_are_never_added(client, jordan):
    client.post(f"/candidates/{jordan['id']}/skills/confirm-bulk", json={"answers": [{"skill": "React", "decision": "not_used"}]})
    app = _generate(client, jordan)
    assert not any("React" in b["text"] for r in app["resume"]["content"]["experience"] for b in r["bullets"])
    assert _gap_report(app)["React"]["status"] == "not_used"
    assert _bullets_naming(_roles(app)[CRESTLINE], "Kubernetes")  # the others are still placed


# ---------------------------------------------------------------- end to end with the AI steps (scripted)


def _listing(text: str) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    """From an AI prompt's evidence listing: employer → R- id, and employer → [(B- id, text)]."""
    roles: dict[str, str] = {}
    bullets: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if m := re.match(r"\[(R-[^\]]+)\] (?:Role: )?.+? at (.+?) \(", line):
            current = m.group(2)
            roles[current] = m.group(1)
            bullets.setdefault(current, [])
        elif m := re.match(r"\[(B-[^\]]+)\] (?:\[(.+?)\] )?(.+)", line):
            employer = m.group(2) or current
            bullets.setdefault(employer, []).append((m.group(1), m.group(3)))
    return roles, bullets


def _find(bullets: list[tuple[str, str]], words: str) -> tuple[str, str]:
    return next((i, t) for i, t in bullets if words in t)


def _expansion(text: str) -> dict:
    roles, bullets = _listing(text)
    portal = _find(bullets[HARBORVIEW], "self-service portal")
    lam = _find(bullets[HARBORVIEW], "Lambda functions")
    helm = _find(bullets[HARBORVIEW], "Helm charts")
    zones = _find(bullets[PINECREST], "delivery zones")
    pipes = _find(bullets[CRESTLINE], "pipelines")
    queues = _find(bullets[PINECREST], "RabbitMQ")

    def item(skill, employer, ids, classification, bullet, reason="x"):
        return {"skill": skill, "employer_id": roles[employer] if employer else None, "evidence_ids": ids, "classification": classification, "bullet": bullet, "reason": reason}

    return {
        "placements": [
            item("React", HARBORVIEW, [portal[0]], "equivalent", "Built a React customer self-service portal with Next.js used by 40,000 monthly users"),
            item("AWS Lambda", HARBORVIEW, [lam[0]], "equivalent", "Built AWS Lambda functions triggered by S3 uploads to generate PDF invoices"),
            # The wrong employer: the evidence ranking keeps Kubernetes at Crestline.
            item("Kubernetes", HARBORVIEW, [helm[0]], "equivalent", "Packaged three internal services as Kubernetes Helm charts for the platform team"),
            # Adds a number the source doesn't have: rejected, the standard wording is used instead.
            item("PostgreSQL", PINECREST, [zones[0]], "equivalent", "Modeled delivery zones in PostgreSQL with PostGIS, cutting report time by 75%"),
            # The AI alone calls this equivalent; nothing confirms it, so it is never added.
            item("Terraform", CRESTLINE, [pipes[0]], "equivalent", "Automated build and deployment pipelines for 12 services in Jenkins and Terraform"),
            item("Apache Kafka", PINECREST, [queues[0]], "related", ""),
            item("Golang", None, [], "none", ""),
        ]
    }


def _tailoring(text: str) -> dict:
    """A writer that forgets the job skills: it rewrites the React bullet without React and leaves out the Kubernetes
    bullet."""
    roles, bullets = _listing(text)
    portal = _find(bullets[HARBORVIEW], "self-service portal")
    lam = _find(bullets[HARBORVIEW], "Lambda functions")
    pipes = _find(bullets[CRESTLINE], "pipelines")
    zones = _find(bullets[PINECREST], "delivery zones")
    result = {
        "headline": "Senior Software Engineer",
        "summary": "Software engineer with 11 years building web platforms and cloud services.",
        "skill_order": ["Python", "JavaScript", "Docker"],
        "roles": [
            {"role_id": roles[HARBORVIEW], "bullets": [
                {"text": "Delivered a customer self-service portal used by 40,000 monthly users", "source_bullet_ids": [portal[0]]},
                {"text": lam[1], "source_bullet_ids": [lam[0]]},
            ]},
            {"role_id": roles[CRESTLINE], "bullets": [
                {"text": pipes[1], "source_bullet_ids": [pipes[0]]},
            ]},
            {"role_id": roles[PINECREST], "bullets": [{"text": zones[1], "source_bullet_ids": [zones[0]]}]},
        ],
    }
    result["placements"] = _expansion(text)["placements"]
    result["new_bullets"] = []
    return result


def test_ai_bullets_are_checked_and_inserted_even_when_the_writer_drops_them(client, jordan, secret_store, fake_ai):
    """The AI proposes a bullet per missing skill in one call; Python keeps the employer the evidence ranking chose,
    takes the proposals that pass the checks, and inserts every placed skill even though the writer dropped them."""
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring)
    fake_ai.on(
        "AICoverLetter",
        lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I build web platforms and cloud services.", "evidence_ids": []}, {"text": "I would welcome a conversation.", "evidence_ids": []}], "closing": "Sincerely,"},
    )

    app = _generate(client, jordan)
    assert app["status"] == "EXPORTED" and app["approved_by"] == "automatic" and not app["error"], app.get("error")
    assert app["resume"]["mode"] == "ai"
    assert app["resume"]["issue_counts"]["error"] == 0, app["resume"]["issues"]
    schemas = [c["schema"] for c in fake_ai.calls]
    assert schemas == ["AIApplicationDocuments"]  # one request writes both documents, placements included
    assert "AISkillExpansion" not in schemas  # placement and writing share one response

    inserted = _assert_distributed(app, jordan["profile"])
    assert inserted["React"] == "Built a React customer self-service portal with Next.js used by 40,000 monthly users"  # the AI's checked bullet
    assert inserted["Kubernetes"] == "Ran 30 microservices on Amazon EKS (Kubernetes), cutting deployment time by 50%"  # ranking kept Crestline
    assert "75%" not in inserted["PostgreSQL"] and "(PostgreSQL)" in inserted["PostgreSQL"]  # the proposal added a number
    assert "Terraform" not in json.dumps(app["resume"]["content"])  # the AI's unconfirmed claim didn't get in

    _assert_pdf(app, inserted)

    issues = app["resume"]["issues"]
    report = _gap_report(app)
    assert report["Terraform"]["status"] == "transferable" and not report["Terraform"]["resolved"]  # emphasised, not claimed
    notes = " ".join(i["message"] for i in issues if i["code"] == "skill_gap_report")
    assert "75%" not in notes and "adds numbers" in notes and "Terraform was not added" in notes


@pytest.mark.skipif(not __import__("resumeflow.services.pdf", fromlist=["word_available"]).word_available(), reason="Microsoft Word is not installed")
def test_exported_pdf_with_microsoft_word(client, jordan, monkeypatch):
    """The production converter: Word turns the rendered resume into the PDF that is read back here."""
    import faulthandler

    import resumeflow.services.applications as applications
    from resumeflow.services.pdf import convert_documents_to_pdf

    monkeypatch.setattr(applications, "convert_documents_to_pdf", convert_documents_to_pdf)
    client.patch("/settings", json={"pdf_converter": "word"})
    monkeypatch.setattr(applications, "resolve_converter", lambda settings: "word")
    # Word's COM calls raise and handle RPC exceptions while it shuts down; pytest's fault handler would print each one.
    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        app = _generate(client, jordan)
    finally:
        if enabled:
            faulthandler.enable()
    assert app["status"] == "EXPORTED" and app["pdf_status"] == "succeeded", app.get("error")
    _assert_pdf(app, _assert_distributed(app, jordan["profile"]))


# ---------------------------------------------------------------- the engine


def _req(*required: str) -> JobRequirements:
    return empty_requirements().model_copy(update={"mandatory_skills": list(required)})


def _snap(*roles: tuple[str, list[str]], skills: list[dict] | None = None) -> dict:
    return {
        "full_name": "Test Person",
        "employment": [
            {"id": f"r{i}", "employer": employer, "title": "Engineer", "bullets": [{"id": f"r{i}b{j}", "text": t} for j, t in enumerate(texts)]}
            for i, (employer, texts) in enumerate(roles)
        ],
        "skills": skills or [],
    }


def test_best_supported_employer_wins_and_ties_go_to_the_more_recent():
    snap = _snap(("Newer", ["Packaged services as Helm charts"]), ("Older", ["Ran services on Amazon EKS", "Used kubectl daily"]))
    req = _req("Kubernetes")
    (place,) = infer_role_skills(snap, req)
    assert place["employer"] == "Older" and [r["employer"] for r in place["ranking"]] == ["Newer", "Older"]
    tie = _snap(("Newer", ["Packaged services as Helm charts"]), ("Older", ["Ran services on Amazon EKS"]))
    assert infer_role_skills(tie, req)[0]["employer"] == "Newer"


def test_related_tools_are_not_proof():
    snap = _snap(("Acme", ["Ran Docker containers on Amazon ECS", "Ported reports from MySQL", "Wrote Python lambda expressions"]))
    req = _req("Kubernetes", "PostgreSQL", "AWS Lambda")
    assert infer_role_skills(snap, req) == []
    result, _ = expand_snapshot(snap, req)
    report = {r["normalized"]: r for r in result["skill_gap_report"]}
    assert report["kubernetes"]["status"] == "transferable" and report["postgresql"]["status"] == "transferable"
    assert report["aws lambda"]["status"] == "not_supported"
    assert result["employment"][0]["bullets"][0]["text"] == "Ran Docker containers on Amazon ECS"  # never reworded


def test_naming_a_skill_keeps_the_candidates_sentence():
    assert name_skill("Ran services on Amazon EKS, cutting costs", "Kubernetes", "amazon eks") == "Ran services on Amazon EKS (Kubernetes), cutting costs"
    assert name_skill("Built Lambda functions for S3 events", "AWS Lambda", "Lambda") == "Built AWS Lambda functions for S3 events"
    assert name_skill("Shipped APIs (C#, Redis)", ".NET", "c#") == "Shipped APIs (C#, .NET, Redis)"
    assert name_skill("Deployed apps on AWS (EC2, S3)", "Cloud", "amazon web services") == "Deployed apps on AWS (Cloud, EC2, S3)"
    assert name_skill("Tuned queries.", "SQL", None) == "Tuned queries (SQL)."


def test_informal_spellings_use_the_jobs_wording():
    snap = _snap(("Acme", ["Tuned Postgres queries for billing", "Ran k8s clusters"]))
    result, _ = expand_snapshot(snap, _req("PostgreSQL", "Kubernetes"))
    texts = [b["text"] for b in result["employment"][0]["bullets"]]
    assert texts == ["Tuned PostgreSQL queries for billing", "Ran Kubernetes clusters"]
    assert result["employment"][0]["bullets"][0]["original_text"] == "Tuned Postgres queries for billing"


def test_proposed_bullets_must_keep_to_their_source():
    source = "Ran 30 microservices on Amazon EKS, cutting deployment time by 50%"
    ok = "Ran 30 microservices on Kubernetes (Amazon EKS), cutting deployment time by 50%"
    assert check_proposed_bullet(ok, source, "kubernetes", "Kubernetes", set()) is None
    assert check_proposed_bullet("Ran 40 microservices on Kubernetes (Amazon EKS)", source, "kubernetes", "Kubernetes", set()) == "adds numbers"
    assert "terraform" in check_proposed_bullet("Ran 30 microservices on Kubernetes with Terraform, cutting deployment time by 50%", source, "kubernetes", "Kubernetes", set())
    assert check_proposed_bullet("Led a platform team on Kubernetes", source, "kubernetes", "Kubernetes", set()) == "drops most of the original bullet"
    assert check_proposed_bullet("Ran 30 microservices on Amazon EKS", source, "kubernetes", "Kubernetes", set()) == "doesn't name the skill"


def test_ai_alone_never_adds_a_skill():
    snap = _snap(("Acme", ["Automated build and deployment pipelines for 12 services"]))
    proposal = AISkillPlacement(
        skill="Terraform", employer_id="R-r0", evidence_ids=["B-r0b0"], classification="equivalent",
        bullet="Automated build and deployment pipelines for 12 services with Terraform", reason="IaC",
    )
    result, notes = expand_snapshot(snap, _req("Terraform"), proposals=[proposal])
    assert not result.get("inferred_skills") and "Terraform" not in json.dumps(result["employment"])
    assert {r["normalized"]: r["status"] for r in result["skill_gap_report"]}["terraform"] == "transferable"
    assert any("Terraform was not added" in n for n in notes)


def test_new_bullets_for_missing_skills_are_inserted_under_the_chosen_employer():
    snap = _snap(("Acme", ["Built REST APIs in Python for order processing", "Maintained PostgreSQL schemas"]), ("Globex", ["Wrote internal tools in Python"]))

    def bullet(skill: str, employer: str, text: str, ids: tuple[str, ...] = ()) -> dict:
        return {"skill": skill, "employer_id": employer, "text": text, "based_on_ids": list(ids)}

    reply = AIResumeTailoring.model_validate({
        "headline": None,
        "summary": "Backend engineer.",
        "skill_order": ["Python"],
        "roles": [
            {"role_id": "R-r0", "bullets": [{"text": "Built REST APIs in Python for order processing", "source_bullet_ids": ["B-r0b0"]}]},
            {"role_id": "R-r1", "bullets": [{"text": "Wrote internal tools in Python", "source_bullet_ids": ["B-r1b0"]}]},
        ],
        "placements": [],
        "new_bullets": [
            bullet("Apache Kafka", "R-r0", "Built event-driven order pipelines on Apache Kafka", ("B-r0b0", "B-r1b0")),
            bullet("Terraform", "R-r1", "Provisioned internal tool environments with Terraform"),
            bullet("Apache Kafka", "R-r1", "Streamed tool events through Apache Kafka"),  # one bullet per skill
            bullet("Go", "R-r9", "Wrote services in Go"),  # not one of the candidate's employers
        ],
    })
    ai = SimpleNamespace(run=lambda **_: reply)
    req = _req("Python", "Apache Kafka", "Terraform", "Go")

    resume, *_ = ai_resume(ai, snap, req, "Initech", "job", "app", None, "all")
    acme, globex = ({b.text: b.source_ids for b in role.bullets} for role in resume.experience)
    assert list(acme) == ["Built REST APIs in Python for order processing", "Built event-driven order pipelines on Apache Kafka"]
    assert acme["Built event-driven order pipelines on Apache Kafka"] == ["r0b0"]  # only that employer's own bullets are cited
    assert list(globex) == ["Wrote internal tools in Python", "Provisioned internal tool environments with Terraform"]
    assert {"Apache Kafka", "Terraform"} <= set(resume.skills) and "Go" not in resume.skills

    # A skill the operator said the candidate hasn't used never gets a bullet.
    resume, *_ = ai_resume(ai, snap, req, "Initech", "job", "app", None, "all", exclude=frozenset({"terraform"}))
    assert "Terraform" not in json.dumps(resume.model_dump()["experience"])
