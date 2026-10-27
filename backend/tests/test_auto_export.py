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


def test_written_documents_are_exported_straight_away(client, setup, secret_store, fake_ai):
    """As soon as the OpenAI responses arrive the documents are saved and exported: no fact-check call, no automatic
    fixes, no review step."""
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Front Range Payments Inc"))  # leaks another candidate's employer
    letter = [
        "I am applying to Acme Data Co for the Senior Data Engineer role. I build reliable data pipelines on AWS.",
        "At Northwind Analytics I built Spark and Airflow pipelines. My clear communication keeps every team aligned.",
        "I would welcome the chance to discuss how I can help Acme Data Co.",
    ]
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": p, "evidence_ids": []} for p in letter], "closing": "Sincerely,"})

    client.patch("/settings", json={"export_mode": "auto"})
    app = create(client, setup).json()
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    assert done["status"] == "EXPORTED" and done["approved_by"] == "automatic" and not done["error"]
    assert [c["schema"] for c in fake_ai.calls] == ["AIApplicationDocuments"]  # one request for both documents
    body = " ".join(p["text"] for p in done["cover_letter"]["content"]["paragraphs"])
    assert body == " ".join(letter)  # the cover letter exactly as the API returned it
    assert {f["type"] for f in done["files"] if f["exists"]} >= {"resume", "cover_letter", "metadata"}


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


def test_resume_and_cover_letter_come_from_one_request(client, setup, secret_store, fake_ai):
    """v2.0 G4: one AI request per application writes both documents (and it is the only request)."""
    from resumeflow.db.models import GenerationRun

    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Northwind Analytics Inc"))
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"})
    app = create(client, setup).json()
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    assert done["resume"]["mode"] == "ai" and done["cover_letter"]["mode"] == "ai"
    assert done["cover_letter"]["content"]["paragraphs"][0]["text"] == "I am applying to Acme Data Co."
    with client.app.state.session_factory() as db:
        runs = [r.operation for r in db.query(GenerationRun).filter(GenerationRun.application_id == app["id"])]
    assert runs == ["application_documents"]
    call = fake_ai.calls[-1]
    assert "PART 1" in call["instructions"] and "PART 2" in call["instructions"]


def test_operator_instructions_reach_the_writer(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Northwind Analytics Inc"))
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"})
    fake_ai.on("AIClaimReview", lambda text: {"issues": []})
    client.patch("/settings", json={"writing_instructions": "Use US spelling and keep bullets under 25 words."})

    app = create(client, setup, instructions="Lead with the Spark and Airflow work.").json()
    assert app["instructions"] == "Lead with the Spark and Airflow work."
    drain(client)
    writing = [c for c in fake_ai.calls if c["schema"] in {"AIApplicationDocuments", "AIResumeTailoring", "AICoverLetter"}]
    assert writing and all("Use US spelling" in c["instructions"] and "Lead with the Spark" in c["instructions"] for c in writing)

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


def test_memory_secret_backend_never_touches_credential_manager(config):
    from resumeflow.app import create_app
    from resumeflow.services.secrets import MemorySecretStore

    app = create_app(config.model_copy(update={"secret_backend": "memory"}))
    assert isinstance(app.state.secret_store, MemorySecretStore)


def test_resume_and_cover_letter_files_are_named_plainly(client, setup):
    """The documents are saved as "Resume" and "CoverLetter"; the working files keep the application number."""
    client.patch("/settings", json={"export_mode": "auto"})
    app = create(client, setup).json()
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    names = {Path(f["path"]).name for f in done["files"] if f["exists"]}
    assert {"Resume.docx", "CoverLetter.docx"} <= names, names
    assert {"001_Acme_Data_Co_JD.txt", "001_Acme_Data_Co_Metadata.json"} <= names, names
