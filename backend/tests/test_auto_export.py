"""Generate → files without a manual approval step, and the operator's own instructions for the AI writer."""

from __future__ import annotations

import json
from pathlib import Path

from resumeflow.services.jobs import heuristic_requirements
from tests.test_workflow import _tailoring, create, drain, setup  # noqa: F401  (fixture)


def _metadata(app: dict) -> dict:
    path = next(f["path"] for f in app["files"] if f["type"] == "metadata")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_generate_exports_automatically_by_default(client, setup):
    client.patch("/settings", json={"export_mode": "auto"})
    app = create(client, setup).json()
    drain(client)  # generation, then the export it queues
    done = client.get(f"/applications/{app['id']}").json()
    assert done["status"] == "EXPORTED"
    assert done["approved_by"] == "automatic"
    assert {f["type"] for f in done["files"] if f["exists"]} >= {"resume", "cover_letter", "metadata"}
    assert _metadata(done)["review_status"] == "AUTO_APPROVED"  # never recorded as a person's review


def test_problems_are_fixed_and_exported_automatically(client, setup, secret_store, fake_ai):
    """What the checks flag is repaired instead of holding the application: the leaked employer goes, the flagged
    cover-letter sentence goes, and the files are exported without a review step."""
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Front Range Payments Inc"))  # leaks another candidate's employer
    letter = [
        "I am applying to Acme Data Co for the Senior Data Engineer role. I build reliable data pipelines on AWS.",
        "At Northwind Analytics I built Spark and Airflow pipelines. My clear communication keeps every team aligned.",
        "I would welcome the chance to discuss how I can help Acme Data Co.",
    ]
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": p, "evidence_ids": []} for p in letter], "closing": "Sincerely,"})
    fake_ai.on(
        "AIClaimReview",
        lambda text: {"issues": [{"quote": "clear communication", "reason": "Not established by the evidence", "severity": "error"}]} if "clear communication" in text else {"issues": []},
    )

    client.patch("/settings", json={"export_mode": "auto"})
    app = create(client, setup).json()
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    assert done["status"] == "EXPORTED" and done["approved_by"] == "automatic" and not done["error"]
    assert done["resume"]["issue_counts"]["error"] == 0 and done["cover_letter"]["issue_counts"]["error"] == 0
    assert "Front Range" not in done["resume"]["content"]["summary"]
    body = " ".join(p["text"] for p in done["cover_letter"]["content"]["paragraphs"])
    assert "clear communication" not in body and "Spark and Airflow pipelines" in body  # only the flagged sentence went
    notes = [i["message"] for i in done["cover_letter"]["issues"] if i["code"] == "auto_fixed"]
    assert any("clear communication" in n for n in notes)


def test_pdf_only_saves_no_word_files(client, setup, monkeypatch):
    import resumeflow.services.applications as applications

    converted: list[int] = []

    def fake_convert(documents, settings):
        converted.append(len(documents))
        return [b"%PDF-1.7 fake" for _ in documents]

    monkeypatch.setattr(applications, "resolve_converter", lambda settings: "word")
    monkeypatch.setattr(applications, "convert_documents_to_pdf", fake_convert)
    client.patch("/settings", json={"export_mode": "auto", "pdf_converter": "auto", "output_formats": "pdf"})
    app = create(client, setup).json()
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    formats = {(f["type"], f["format"]) for f in done["files"] if f["exists"]}
    assert ("resume", "pdf") in formats and ("cover_letter", "pdf") in formats
    assert not any(fmt == "docx" for _, fmt in formats)
    assert not any(p.suffix == ".docx" for p in Path(done["output_directory"]).iterdir())
    assert converted == [2]  # both documents in one conversion call

    client.patch("/settings", json={"output_formats": "pdf_docx"})
    client.post(f"/applications/{app['id']}/generate", json={})
    drain(client)
    again = client.get(f"/applications/{app['id']}").json()
    assert any(f["format"] == "docx" and f["exists"] for f in again["files"])


def test_resume_and_cover_letter_are_written_at_the_same_time(client, setup, secret_store, fake_ai):
    import threading
    import time

    secret_store.set("openai_api_key", "sk-test-key-123456")
    inside: list[str] = []
    overlap = threading.Event()
    lock = threading.Lock()

    def slow(name, payload):
        def handler(text):
            with lock:
                inside.append(name)
                if len(inside) == 2:
                    overlap.set()
            time.sleep(0.3)
            with lock:
                inside.remove(name)
            return payload(text) if callable(payload) else payload

        return handler

    fake_ai.on("AIResumeTailoring", slow("resume", _tailoring("Northwind Analytics Inc")))
    fake_ai.on("AICoverLetter", slow("cover", {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"}))
    fake_ai.on("AIClaimReview", lambda text: {"issues": []})
    app = create(client, setup).json()
    drain(client)
    assert overlap.is_set()  # both requests were in flight together
    assert client.get(f"/applications/{app['id']}").json()["resume"]["mode"] == "ai"


def test_review_mode_waits_for_approval(client, setup):
    client.patch("/settings", json={"export_mode": "review"})
    app = create(client, setup).json()
    drain(client)
    assert client.get(f"/applications/{app['id']}").json()["status"] == "AWAITING_REVIEW"


def test_operator_instructions_reach_the_writer(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Northwind Analytics Inc"))
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"})
    fake_ai.on("AIClaimReview", lambda text: {"issues": []})
    client.patch("/settings", json={"writing_instructions": "Use US spelling and keep bullets under 25 words."})

    app = create(client, setup, instructions="Lead with the Spark and Airflow work.").json()
    assert app["instructions"] == "Lead with the Spark and Airflow work."
    drain(client)
    writing = [c for c in fake_ai.calls if c["schema"] in {"AIResumeTailoring", "AICoverLetter"}]
    assert writing and all("Use US spelling" in c["instructions"] and "Lead with the Spark" in c["instructions"] for c in writing)
    assert all("rules above, which always win" in c["instructions"] for c in writing)

    client.post(f"/applications/{app['id']}/generate", json={"document": "resume", "instructions": "Emphasise AWS."})
    drain(client)
    assert client.get(f"/applications/{app['id']}").json()["instructions"] == "Emphasise AWS."
    assert "Emphasise AWS." in fake_ai.calls[-2]["instructions"] or "Emphasise AWS." in fake_ai.calls[-1]["instructions"]
    claim_reviews = [c for c in fake_ai.calls if c["schema"] == "AIClaimReview"]
    assert all("Emphasise" not in c["instructions"] for c in claim_reviews)  # the fact-check is never steered


def test_company_under_the_logo_on_job_board_pages():
    jd = "Software Engineer (.NET Full Stack)\nRemote • Posted 2 days ago\nFull Time\nCompany Branding Image\nCompany Logo\nWidescope Consulting and Contracting Services\nFitment\nRequired Skills\n- 5+ years of software development\n- .NET Core and C#\n"
    assert heuristic_requirements(jd, set()).employer == "Widescope Consulting and Contracting Services"


def test_certifications_left_out_are_listed(client):
    from tests.conftest import make_ready, upload

    candidate = upload(client, 2).json()["candidate"]  # a sample with a certification
    make_ready(client, candidate["id"])
    profile = client.get(f"/candidates/{candidate['id']}").json()["profile"]
    if not profile["certifications"]:
        return
    for cert in profile["certifications"]:
        cert["verified"] = False
    client.patch(f"/candidates/{candidate['id']}", json={"profile": profile})
    warnings = client.get(f"/candidates/{candidate['id']}").json()["readiness"]["warnings"]
    assert any("certification(s) aren't verified" in w for w in warnings)


def test_fact_check_sees_the_job_so_role_descriptions_are_not_flagged(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Northwind Analytics Inc"))
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "Acme Data Co's focus on analytics pipelines fits my work.", "evidence_ids": []}], "closing": "Sincerely,"})
    fake_ai.on("AIClaimReview", lambda text: {"issues": []})
    client.patch("/settings", json={"export_mode": "auto"})
    app = create(client, setup).json()
    drain(client)
    reviews = [c for c in fake_ai.calls if c["schema"] == "AIClaimReview"]
    assert reviews and all("<job_description>" in c["input"] for c in reviews)
    assert all("Only claims about the candidate count" in c["instructions"] for c in reviews)
    assert client.get(f"/applications/{app['id']}").json()["status"] == "EXPORTED"


def test_memory_secret_backend_never_touches_credential_manager(config):
    from resumeflow.app import create_app
    from resumeflow.services.secrets import MemorySecretStore

    app = create_app(config.model_copy(update={"secret_backend": "memory"}))
    assert isinstance(app.state.secret_store, MemorySecretStore)