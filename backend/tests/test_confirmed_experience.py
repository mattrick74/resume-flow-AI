"""v1.8 acceptance tests: job-skill gaps, the operator's confirmations, role-level additions and their provenance."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from resumeflow.db.migrate import alembic_config
from resumeflow.db.session import create_db_engine
from tests.conftest import upload

PLATFORM_JD = """Senior Platform Data Engineer
Acme Data Co - Austin, TX (Remote)

About the role
You will build data pipelines and APIs for our analytics platform.

Requirements
- 5+ years building data pipelines with Python and SQL
- Hands-on experience with Kubernetes and GraphQL
- Experience with Apache Spark

Nice to have
- Redis
- Terraform
"""
KUBERNETES_WORK = "deployed the Spark pipelines on Kubernetes clusters"


@pytest.fixture
def avery(client, tmp_path_factory) -> dict:
    """Avery Quinlan, imported and authorized; no verification, and the full confirmation flow (ask about missing skills,
    approve drafted bullets). test_instant_by_default covers the defaults (no waiting, bullets added at once)."""
    client.patch(
        "/settings",
        json={
            "require_verification": False,
            "gap_confirmation": "ask",
            "auto_approve_bullets": False,
            "output_root": str(tmp_path_factory.mktemp("o")),
            "pdf_converter": "none",
        },
    )
    candidate = upload(client, 0).json()["candidate"]
    client.patch(f"/candidates/{candidate['id']}", json={"consent_confirmed": True})
    detail = client.get(f"/candidates/{candidate['id']}").json()
    roles = {r["employer"]: r for r in detail["profile"]["employment"]}
    return {"id": candidate["id"], "detail": detail, "northwind": roles["Northwind Analytics Inc"], "bluebonnet": roles["Bluebonnet Logistics LLC"]}


def _job(client, suffix: str = "1") -> dict:
    return client.post("/jobs", json={"job_url": f"https://jobs.example.com/platform-{suffix}", "raw_jd": PLATFORM_JD}).json()


def _gaps(client, job_id: str) -> dict[str, dict]:
    return {i["normalized"]: i for i in client.get(f"/jobs/{job_id}/skill-gaps").json()["items"]}


def _generate(client, job_id: str, candidate_id: str) -> dict:
    app = client.post("/applications", json={"job_id": job_id, "candidate_id": candidate_id}).json()
    client.app.state.queue.drain()
    return client.get(f"/applications/{app['id']}").json()


def _bullets(app: dict) -> list[dict]:
    return [b for role in app["resume"]["content"]["experience"] for b in role["bullets"]]


def test_missing_job_skills_are_found_and_offered(client, avery):
    """AT-01: skills the imported resume lacks are detected; nothing is confirmed because the job asks for it."""
    job = _job(client)
    gaps = _gaps(client, job["id"])
    assert {gaps[k]["status"] for k in ("kubernetes", "graphql", "redis")} <= {"unknown", "plausible"}
    assert all(gaps[k]["needs_answer"] for k in ("kubernetes", "graphql", "redis"))
    assert gaps["python"]["status"] == "matched" and not gaps["python"]["needs_answer"]
    assert gaps["kubernetes"]["kind"] == "required" and gaps["redis"]["kind"] == "preferred"
    # Two scores: confirmed evidence only, and the potential if the gaps were confirmed (MAT-06).
    top = next(m for m in job["matches"] if m["candidate_id"] == avery["id"])
    assert top["potential_score"] > top["score"]


def test_confirmations_flow_into_documents_only_once_approved(client, avery):
    """AT-02/03/04/06/09: unconfirmed and rejected skills stay out; a general skill reaches Skills only; a company
    bullet appears only after the role and the work were confirmed and the bullet approved; facts never change."""
    job = _job(client)
    answered = client.post(
        f"/candidates/{avery['id']}/skills/confirm-bulk",
        json={
            "source_job_ids": [job["id"]],
            "answers": [
                {"skill": "Kubernetes", "decision": "used", "role_ids": [avery["northwind"]["id"]], "context": KUBERNETES_WORK},
                {"skill": "GraphQL", "decision": "not_used"},
                {"skill": "Redis", "decision": "used"},  # used, but not tied to any employer
            ],
        },
    )
    assert answered.status_code == 200, answered.text
    suggestion = answered.json()["suggestions"][0]
    assert suggestion["status"] == "suggested" and suggestion["drafted_by"] == "offline"  # no API key in tests
    assert "Kubernetes" in suggestion["narrative"] and suggestion["issue_counts"]["error"] == 0

    app = _generate(client, job["id"], avery["id"])
    assert {"Kubernetes", "Redis"} <= set(app["resume"]["content"]["skills"])
    assert "GraphQL" not in app["resume"]["content"]["skills"]
    assert not any("Kubernetes" in b["text"] or "Redis" in b["text"] for b in _bullets(app))  # not approved yet

    approved = client.post(f"/applications/{app['id']}/approve-additions", json={"record_ids": [suggestion["id"]], "regenerate": True})
    assert approved.status_code == 202, approved.text
    client.app.state.queue.drain()
    app = client.get(f"/applications/{app['id']}").json()
    added = [b for b in _bullets(app) if suggestion["id"] in b["source_ids"]]
    assert added and "Kubernetes" in added[0]["text"]
    northwind = next(r for r in app["resume"]["content"]["experience"] if r["employer"] == "Northwind Analytics Inc")
    assert added[0] in northwind["bullets"]
    assert not any("Redis" in b["text"] for b in _bullets(app))
    assert app["resume"]["issue_counts"]["error"] == 0, app["resume"]["issues"]
    assert app["addition_ids"] == [suggestion["id"]] and app["profile_revision"] == app["current_profile_revision"]

    profile = client.get(f"/candidates/{avery['id']}").json()["profile"]
    for role in app["resume"]["content"]["experience"]:
        source = next(r for r in profile["employment"] if r["id"] == role["role_id"])
        assert (role["employer"], role["title"], role["start"], role["end"]) == (source["employer"], source["title"], source["start"], source["end"])
    assert [e["id"] for e in app["resume"]["content"]["education"]] == [e["id"] for e in profile["education"]]


def test_rejected_skills_are_not_suggested_again(client, avery):
    """AT-05"""
    job = _job(client)
    client.post(f"/candidates/{avery['id']}/skills/confirm-bulk", json={"answers": [{"skill": "GraphQL", "decision": "not_used"}]})
    gap = _gaps(client, job["id"])["graphql"]
    assert gap["status"] == "rejected" and not gap["needs_answer"]
    batch = client.post("/jobs/skill-gaps/batch", json={"job_ids": [job["id"]]}).json()
    assert "graphql" not in {s["normalized"] for c in batch["candidates"] for s in c["skills"]}
    skills = {s["normalized_name"]: s for s in client.get(f"/candidates/{avery['id']}").json()["skills"]}
    assert skills.get("graphql") is None or skills["graphql"]["verification_status"] == "rejected"


def test_batch_confirmation_continues_every_waiting_job(client, avery):
    """AT-07/08 and UX-04: jobs wait for answers (without blocking others), one session answers them all, and the
    jobs continue automatically with refreshed scores. Another candidate's profile is untouched."""
    other = upload(client, 1).json()["candidate"]["id"]
    saved = client.post(
        "/jobs/batch",
        # Same description posted twice (e.g. two locations): saved on purpose.
        json={"items": [{"job_url": f"https://jobs.example.com/p-{n}", "raw_jd": PLATFORM_JD} for n in (1, 2)], "process": True, "confirm_duplicates": True},
    ).json()
    client.app.state.queue.drain()
    jobs = [client.get(f"/jobs/{r['job']['id']}").json() for r in saved["results"]]
    assert all(j["needs_confirmation"] and not j["applications"] for j in jobs)
    assert any(w["code"] == "needs_confirmation" for w in jobs[0]["warnings"])
    assert client.get("/dashboard/summary").json()["jobs_needing_confirmation"] == 2

    batch = client.post("/jobs/skill-gaps/batch", json={}).json()
    (bucket,) = batch["candidates"]
    assert bucket["candidate_id"] == avery["id"]
    kubernetes = next(s for s in bucket["skills"] if s["normalized"] == "kubernetes")
    assert kubernetes["required_count"] == 2 and bucket["skills"][0]["required_count"] >= bucket["skills"][-1]["required_count"]

    answers = [
        {"skill": "Kubernetes", "decision": "used", "role_ids": [avery["northwind"]["id"]], "context": KUBERNETES_WORK},
        *({"skill": s["skill"], "decision": "unsure"} for s in bucket["skills"] if s["normalized"] != "kubernetes"),
    ]
    first = client.post(f"/candidates/{avery['id']}/skills/confirm-bulk", json={"answers": answers, "source_job_ids": bucket["job_ids"]}).json()
    assert first["resumed"] == []  # the drafted bullet still waits for approval
    approved = client.post(f"/candidates/{avery['id']}/experience-evidence/approve", json={"ids": [first["suggestions"][0]["id"]]}).json()
    assert sorted(approved["resumed"]) == sorted(j["id"] for j in jobs)
    client.app.state.queue.drain()

    for job in jobs:
        detail = client.get(f"/jobs/{job['id']}").json()
        assert not detail["needs_confirmation"] and len(detail["applications"]) == 1
        match = next(m for m in detail["matches"] if m["candidate_id"] == avery["id"])
        assert match["previous_score"] is not None and match["score"] > match["previous_score"]
        assert "kubernetes" in match["details"]["skill_changes"]["added"]
    other_skills = {s["normalized_name"] for s in client.get(f"/candidates/{other}").json()["skills"]}
    assert "kubernetes" not in other_skills

    app_id = client.get(f"/jobs/{jobs[0]['id']}").json()["applications"][0]["id"]
    report = client.get(f"/applications/{app_id}/coverage").json()
    coverage = report["coverage"]
    assert coverage["original_coverage_pct"] < coverage["keyword_coverage_pct"]
    assert "Kubernetes" not in coverage["required"]["missing"] and "GraphQL" in coverage["required"]["missing"]
    assert "not an applicant tracking system score" in coverage["note"].lower()
    assert any(b["kind"] == "added" for b in report["changes"]["bullets"])
    assert "Kubernetes" in report["changes"]["skills"]["added"]
    experience = client.get("/analytics").json()["experience"]
    assert experience["skills_confirmed"] >= 1 and experience["bullets_approved"] == 1 and experience["jobs_resumed"] == 2


def test_unsupported_claims_are_flagged_before_approval(client, avery):
    """AT-11 and VAL-07: numbers, leadership and tools the operator didn't state are flagged; approval needs an acknowledgement."""
    overview = client.post(
        f"/candidates/{avery['id']}/experience-evidence",
        json={"role_id": avery["northwind"]["id"], "skills": ["Kubernetes"], "context": KUBERNETES_WORK},
    ).json()
    record = overview["records"][0]
    edited = client.patch(
        f"/candidates/{avery['id']}/experience-evidence/{record['id']}",
        json={"narrative": "Led a team of 6 engineers running Kubernetes and Redis for 40 services."},
    ).json()["records"][0]
    codes = {i["code"] for i in edited["issues"]}
    assert {"unsupported_number", "unsupported_leadership", "unconfirmed_tool"} <= codes
    refused = client.post(f"/candidates/{avery['id']}/experience-evidence/approve", json={"ids": [record["id"]]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "unresolved_issues"
    assert client.get(f"/candidates/{avery['id']}/experience-evidence").json()["records"][0]["status"] == "suggested"


def test_contradictions_and_history(client, avery):
    """VAL-07 (technology before release, conflicting years) and the confirmation history (UX-02)."""
    profile = avery["detail"]["profile"]
    old = next(r for r in profile["employment"] if r["id"] == avery["bluebonnet"]["id"])
    old["start"], old["end"] = "2008-01", "2012-06"
    client.patch(f"/candidates/{avery['id']}", json={"profile": profile})
    result = client.post(
        f"/candidates/{avery['id']}/skills/confirm-bulk",
        json={"answers": [{"skill": "Kubernetes", "decision": "used", "role_ids": [old["id"]], "years": 30}]},
    ).json()
    codes = {i["code"] for i in result["answers"][0]["issues"]}
    assert {"before_release", "years_conflict"} <= codes
    history = client.get(f"/candidates/{avery['id']}/experience-evidence").json()["history"]
    assert history[0]["skill"] == "Kubernetes" and history[0]["decision"] == "used" and history[0]["roles"]


def test_additions_survive_reading_the_resume_again(client, avery):
    """CAN-16 / NFR-08: approved additions are stored apart from the imported resume and find their role again."""
    created = client.post(
        f"/candidates/{avery['id']}/experience-evidence",
        json={"role_id": avery["northwind"]["id"], "skills": ["Kubernetes"], "context": KUBERNETES_WORK},
    ).json()["records"][0]
    client.post(f"/candidates/{avery['id']}/experience-evidence/approve", json={"ids": [created["id"]]})
    version = avery["detail"]["resume_versions"][0]["id"]
    assert client.post(f"/candidates/{avery['id']}/resumes/{version}/reextract").status_code == 200
    record = client.get(f"/candidates/{avery['id']}/experience-evidence").json()["records"][0]
    assert record["status"] == "approved" and record["role_found"] and record["role"].startswith(avery["northwind"]["title"])


def test_ai_drafts_from_the_description(client, avery, secret_store, fake_ai):
    """With AI, the bullet is written from the operator's description and the approved bullet reaches the resume."""
    job = _job(client)  # analyzed offline, before the key is set
    secret_store.set("openai_api_key", "sk-test-key-123456")

    def drafts(text: str) -> dict:
        ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[")]
        return {"bullets": [{"record_id": i, "text": "Deployed Spark data pipelines to Kubernetes clusters for the analytics platform"} for i in ids]}

    fake_ai.on("AIDraftedBullets", drafts)
    answered = client.post(
        f"/candidates/{avery['id']}/skills/confirm-bulk",
        json={"answers": [{"skill": "Kubernetes", "decision": "used", "role_ids": [avery["northwind"]["id"]], "context": KUBERNETES_WORK}]},
    ).json()
    suggestion = answered["suggestions"][0]
    assert suggestion["drafted_by"] == "ai" and suggestion["issue_counts"]["error"] == 0
    assert "Work (as described): " + KUBERNETES_WORK in fake_ai.calls[-1]["input"]  # only the record, not the whole profile
    client.post(f"/candidates/{avery['id']}/experience-evidence/approve", json={"ids": [suggestion["id"]]})

    def tailoring(text: str) -> dict:
        role_ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[R-")]
        bullets = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[B-")]
        addition = f"B-{suggestion['id']}"
        original = next(b for b in bullets if b != addition)
        return {
            "headline": None,
            "summary": "Data engineer building cloud data platforms.",
            "skill_order": ["Kubernetes", "Python"],
            "roles": [
                {"role_id": role_ids[0], "bullets": [
                    {"text": "Ran Spark data pipelines on Kubernetes for the analytics platform", "source_bullet_ids": [addition]},
                    {"text": "Built Spark and Airflow event pipelines on AWS", "source_bullet_ids": [original]},
                ]},
            ],
        }

    fake_ai.on("AIResumeTailoring", tailoring)
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"})
    app = _generate(client, job["id"], avery["id"])
    texts = [b["text"] for b in _bullets(app)]
    assert "Ran Spark data pipelines on Kubernetes for the analytics platform" in texts
    assert "Built Spark and Airflow event pipelines on AWS" in texts


def test_instant_by_default(client, avery, secret_store, fake_ai):
    """Defaults: generation never waits for answers, and a bullet written from the operator's description is added at
    once. An AI draft that says more than the description is replaced by the description's own wording."""
    from resumeflow.services.settings import AppSettings

    assert AppSettings().gap_confirmation == "off" and AppSettings().auto_approve_bullets is True
    client.patch("/settings", json={"gap_confirmation": "off", "auto_approve_bullets": True})

    saved = client.post("/jobs/batch", json={"items": [{"job_url": "https://jobs.example.com/now", "raw_jd": PLATFORM_JD}], "process": True}).json()
    client.app.state.queue.drain()
    job = client.get(f"/jobs/{saved['results'][0]['job']['id']}").json()
    assert not job["needs_confirmation"] and len(job["applications"]) == 1  # generated straight away
    app_id = job["applications"][0]["id"]
    assert not any("Kubernetes" in b["text"] for b in _bullets(client.get(f"/applications/{app_id}").json()))

    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on(
        "AIDraftedBullets",
        lambda text: {"bullets": [{"record_id": line.split("]")[0].lstrip("["), "text": "Led a team of 6 engineers running Kubernetes for 40 services"} for line in text.splitlines() if line.startswith("[")]},
    )
    answered = client.post(
        f"/candidates/{avery['id']}/skills/confirm-bulk",
        json={"source_job_ids": [job["id"]], "answers": [{"skill": "Kubernetes", "decision": "used", "role_ids": [avery["northwind"]["id"]], "context": KUBERNETES_WORK}]},
    ).json()
    assert answered["suggestions"] == []
    (added,) = answered["approved"]
    assert added["status"] == "approved" and added["drafted_by"] == "offline"
    assert added["narrative"] == "Deployed the Spark pipelines on Kubernetes clusters."  # the operator's words, nothing added

    secret_store.delete("openai_api_key")
    assert client.post(f"/applications/{app_id}/approve-additions", json={"record_ids": [], "regenerate": True}).status_code == 202
    client.app.state.queue.drain()
    bullets = _bullets(client.get(f"/applications/{app_id}").json())
    assert any(added["id"] in b["source_ids"] and "Kubernetes" in b["text"] for b in bullets)


def test_ticking_an_employer_lists_the_skill_under_that_employer(client, avery):
    """Have used + an employer, without a description: the skill goes on that employer's Technologies line (and in
    Skills); no other employer gets it, and the exported Word file shows the line."""
    client.patch("/settings", json={"auto_approve_bullets": True})
    job = _job(client)
    client.post(
        f"/candidates/{avery['id']}/skills/confirm-bulk",
        json={"source_job_ids": [job["id"]], "answers": [{"skill": "Kubernetes", "decision": "used", "role_ids": [avery["northwind"]["id"]]}]},
    )
    app = _generate(client, job["id"], avery["id"])
    roles = {r["employer"]: r for r in app["resume"]["content"]["experience"]}
    assert "Kubernetes" in roles["Northwind Analytics Inc"]["technologies"]
    assert "Kubernetes" not in roles["Bluebonnet Logistics LLC"]["technologies"]
    assert "Kubernetes" in app["resume"]["content"]["skills"]
    assert app["resume"]["issue_counts"]["error"] == 0

    from docx import Document as Docx
    import io

    from resumeflow.domain.content import ResumeContent
    from resumeflow.services.render import render_resume

    text = "\n".join(p.text for p in Docx(io.BytesIO(render_resume(ResumeContent.model_validate(app["resume"]["content"])))).paragraphs)
    assert "Technologies: " in text and "Kubernetes" in text


def test_an_edited_technology_line_is_saved_as_written(client, avery):
    """Edits are saved as written: there is no checking step."""
    job = _job(client)
    app = _generate(client, job["id"], avery["id"])
    content = app["resume"]["content"]
    content["experience"][0]["technologies"] = ["GraphQL"]
    edited = client.put(f"/applications/{app['id']}/documents/resume", json={"content": content}).json()
    assert edited["resume"]["content"]["experience"][0]["technologies"] == ["GraphQL"] and edited["resume"]["issues"] == []


def test_dotted_skill_names_match():
    """".NET" used to be stored as "net", so a job asking for ".NET Core" never matched it."""
    from resumeflow.domain.skills import display_name, normalize_skill

    assert normalize_skill(".NET") == normalize_skill(".NET Core") == normalize_skill("ASP.NET Core") == ".net"
    assert display_name(".net", "• .NET Core.") == ".NET Core"


def test_profile_skills_named_more_fully_still_count():
    """A profile entry that names a job skill covers it ("CI/CD Pipelines" → CI/CD, "Stash/Bitbucket" → Git); a different
    skill that merely shares letters does not ("JavaScript" isn't Java)."""
    from resumeflow.domain.skills import normalize_skill, skill_covers

    assert skill_covers("CI/CD Pipelines", normalize_skill("CI/CD"))
    assert skill_covers("Stash/Bitbucket", normalize_skill("Git"))
    assert skill_covers("Microsoft SQL Server", normalize_skill("SQL"))
    assert not skill_covers("JavaScript", normalize_skill("Java"))
    assert not skill_covers("Object-Oriented Design", normalize_skill("Object-oriented programming"))


def test_covering_skills_close_the_gap(client, avery):
    """A profile listing "CI/CD Pipelines" isn't asked about a job's "CI/CD"."""
    job = client.post(
        "/jobs",
        json={"job_url": "https://jobs.example.com/airflow", "raw_jd": PLATFORM_JD.replace("- Redis", "- CI/CD")},
    ).json()
    client.post(f"/candidates/{avery['id']}/skills/bulk", json={"text": "CI/CD Pipelines"})
    gaps = _gaps(client, job["id"])
    assert gaps["ci/cd"]["status"] in {"confirmed", "matched"} and not gaps["ci/cd"]["needs_answer"]


def test_upgrade_repairs_stored_skill_names(tmp_path):
    """Migration 0008: skills stored with the old normalization (".NET" as "net") are matched again."""
    from alembic import command

    engine = create_db_engine(tmp_path / "old.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO candidates (id, full_name, preferences_json, status, profile_revision, created_at, updated_at) "
                "VALUES ('c1', 'Avery Quinlan', '{}', 'active', 0, '2026-10-01 10:00:00', '2026-10-01 10:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candidate_skills (id, candidate_id, skill_name, normalized_name, verification_status, created_at, updated_at) "
                "VALUES ('s1', 'c1', '.NET', 'net', 'unverified', '2026-10-01 10:00:00', '2026-10-01 10:00:00')"
            )
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT normalized_name FROM candidate_skills WHERE id = 's1'")).scalar() == ".net"
    engine.dispose()


def test_unauthorized_candidates_are_never_updated(client, avery):
    """NFR-07"""
    client.patch(f"/candidates/{avery['id']}", json={"consent_confirmed": False})
    refused = client.post(f"/candidates/{avery['id']}/skills/confirm-bulk", json={"answers": [{"skill": "Kubernetes", "decision": "used"}]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "candidate_not_authorized"


def test_asking_can_be_turned_off(client, avery):
    """With Settings → Ask about missing job skills off, background processing generates from approved facts only."""
    client.patch("/settings", json={"gap_confirmation": "off"})
    saved = client.post("/jobs/batch", json={"items": [{"job_url": "https://jobs.example.com/p-9", "raw_jd": PLATFORM_JD}], "process": True}).json()
    client.app.state.queue.drain()
    detail = client.get(f"/jobs/{saved['results'][0]['job']['id']}").json()
    assert not detail["needs_confirmation"] and len(detail["applications"]) == 1


def test_upgrade_keeps_existing_data(tmp_path):
    """AT-12 / NFR-08: the 1.8 migration only adds; 1.7 rows are unchanged."""
    from alembic import command

    engine = create_db_engine(tmp_path / "old.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO candidates (id, full_name, preferences_json, status, created_at, updated_at) "
                "VALUES ('c1', 'Avery Quinlan', '{}', 'active', '2026-10-01 10:00:00', '2026-10-01 10:00:00')"
            )
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT full_name, profile_revision, created_at FROM candidates WHERE id = 'c1'")).one()
        assert tuple(row) == ("Avery Quinlan", 0, "2026-10-01 10:00:00.000000") or (row[0], row[1]) == ("Avery Quinlan", 0)
        assert connection.execute(text("SELECT count(*) FROM candidate_role_experience")).scalar() == 0
    engine.dispose()
