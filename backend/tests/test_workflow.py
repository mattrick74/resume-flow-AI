from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from sqlalchemy import select, update

from resumeflow.db.models import Application, ProcessingJob
from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, SAMPLE_JOBS
from resumeflow.domain.enums import ProcessingStatus
from resumeflow.services.ai.provider import AIError, TransientAIError
from tests.conftest import make_ready, upload

# Export layout: <root>/YYYY-MM-DD/<daily sequence> <profile name>/


@pytest.fixture
def setup(client, tmp_path_factory):
    """Ten ready candidates, one analysed job, output to a temp folder, PDFs off."""
    ids = {}
    for index, sample in enumerate(SAMPLE_CANDIDATES):
        candidate = upload(client, index).json()["candidate"]
        make_ready(client, candidate["id"])
        ids[sample.name] = candidate["id"]
    # A short root (not the per-test tmp_path, which embeds the test name) so the exact layout fits the path limit.
    out = tmp_path_factory.mktemp("o")
    client.patch("/settings", json={"output_root": str(out), "pdf_converter": "none"})
    job = client.post(
        "/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"], "company": SAMPLE_JOBS[0]["company"], "title": SAMPLE_JOBS[0]["title"]}
    ).json()
    return {"candidates": ids, "job": job, "out": out}


def drain(client) -> int:
    return client.app.state.queue.drain()


def create(client, setup, name="Avery Quinlan", **extra):
    return client.post("/applications", json={"job_id": setup["job"]["id"], "candidate_id": setup["candidates"][name], **extra})


def generated_app(client, setup) -> dict:
    app = create(client, setup).json()
    assert app["status"] == "GENERATING"
    drain(client)
    return client.get(f"/applications/{app['id']}").json()


def test_deterministic_generation_is_truthful(client, setup):
    app = generated_app(client, setup)
    assert app["status"] == "AWAITING_REVIEW"
    assert app["selection_method"] == "automatic"
    resume = app["resume"]
    assert resume["mode"] == "deterministic"
    assert resume["issue_counts"]["error"] == 0, resume["issues"]
    content = resume["content"]
    assert [r["employer"] for r in content["experience"]] == ["Northwind Analytics Inc", "Bluebonnet Logistics LLC"]
    assert content["skills"][0] in {"Python", "SQL", "Apache Spark", "Apache Airflow", "AWS"}
    assert all(b["source_ids"] for r in content["experience"] for b in r["bullets"])
    cover = app["cover_letter"]
    assert cover["issue_counts"]["error"] == 0, cover["issues"]
    assert "Acme Data Co" in " ".join(p["text"] for p in cover["content"]["paragraphs"])


def test_override_requires_reason(client, setup):
    response = create(client, setup, "Theo Lindqvist")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "override_reason_required"
    ok = create(client, setup, "Theo Lindqvist", override_reason="Client asked for Theo specifically")
    assert ok.status_code == 201
    assert ok.json()["selection_method"] == "manual_override"


def test_another_application_for_the_same_candidate_and_consent(client, setup):
    first = create(client, setup)
    second = create(client, setup)  # the same candidate and job again: a new application with its own number
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["sequence_number"] == first.json()["sequence_number"] + 1
    marisol = setup["candidates"]["Marisol Venn"]
    client.patch(f"/candidates/{marisol}", json={"consent_confirmed": False})
    response = create(client, setup, "Marisol Venn", override_reason="test")
    assert response.json()["error"]["code"] == "candidate_not_authorized"


def test_review_edit_and_approval(client, setup):
    app = generated_app(client, setup)
    content = app["resume"]["content"]
    content["experience"][0]["bullets"][0]["text"] += " for 250 clients"
    edited = client.put(f"/applications/{app['id']}/documents/resume", json={"content": content}).json()
    assert edited["resume"]["version"] == 2
    assert edited["resume"]["issues"] == []  # saved as written: there is no checking step
    assert edited["resume"]["content"]["experience"][0]["bullets"][0]["text"].endswith(" for 250 clients")

    approved = client.post(f"/applications/{app['id']}/approve", json={}).json()
    assert approved["status"] == "APPROVED"
    assert approved["resume"]["status"] == "approved"

    # Editing approved content sends it back to review; the approved version is kept.
    content["experience"][0]["bullets"][0]["text"] = app["resume"]["content"]["experience"][0]["bullets"][0]["text"]
    again = client.put(f"/applications/{app['id']}/documents/resume", json={"content": content}).json()
    assert again["status"] == "AWAITING_REVIEW"
    statuses = {v["version"]: v["status"] for v in again["versions"]["resume"]}
    assert statuses[2] == "approved" and statuses[3] == "draft"


def test_export_writes_exact_layout(client, setup):
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    assert client.post(f"/applications/{app['id']}/export").status_code == 202
    drain(client)
    exported = client.get(f"/applications/{app['id']}").json()
    assert exported["status"] == "EXPORTED"  # never SUBMITTED automatically
    folder = setup["out"] / date.today().isoformat() / "001 Avery Quinlan"
    names = sorted(p.name for p in folder.iterdir())
    assert names == sorted(
        [
            "001_Acme_Data_Co_Resume.docx",
            "001_Acme_Data_Co_Cover_Letter.docx",
            "001_Acme_Data_Co_JD.txt",
            "001_Acme_Data_Co_Job_URL.txt",
            "001_Acme_Data_Co_Metadata.json",
        ]
    )
    assert exported["pdf_status"] == "skipped"
    text = "\n".join(p.text for p in DocxDocument(folder / "001_Acme_Data_Co_Resume.docx").paragraphs)
    assert "Avery Quinlan" in text and "Northwind Analytics Inc" in text
    assert (folder / "001_Acme_Data_Co_JD.txt").read_text(encoding="utf-8") == SAMPLE_JOBS[0]["text"].strip()
    metadata = json.loads((folder / "001_Acme_Data_Co_Metadata.json").read_text(encoding="utf-8"))
    assert metadata["application_id"] == app["id"]
    assert metadata["source_url"] == SAMPLE_JOBS[0]["url"]
    assert metadata["review_status"] == "APPROVED"
    assert {f["format"] for f in metadata["files"]} == {"docx", "txt"}

    # Export is idempotent: requesting it again doesn't create another job or files.
    client.post(f"/applications/{app['id']}/outcome", json={"status": "SUBMITTED"})
    assert client.get(f"/applications/{app['id']}").json()["status"] == "SUBMITTED"


def test_exported_status_comes_after_every_file(client, setup, monkeypatch):
    import resumeflow.services.applications as applications

    seen = []
    real = applications._write_metadata

    def spy(ctx, app_id, folder, prefix):
        with ctx.session() as db:
            seen.append(db.get(Application, app_id).status.value)
        real(ctx, app_id, folder, prefix)

    monkeypatch.setattr(applications, "_write_metadata", spy)
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)
    assert seen == ["APPROVED"]  # a reader that sees EXPORTED never finds the metadata missing
    exported = client.get(f"/applications/{app['id']}").json()
    assert exported["status"] == "EXPORTED"
    assert all(f["exists"] for f in exported["files"])


def test_long_output_folder_still_exports_within_the_path_limit(client, setup, tmp_path_factory):
    # A long root and profile name are shortened without exceeding the Windows path limit.
    base = tmp_path_factory.mktemp("o")
    deep = base / ("Long_Shared_Folder_" * 10)[: 120 - len(str(base)) - 1]
    assert len(str(deep)) == 120
    client.patch("/settings", json={"output_root": str(deep)})
    long_name = "Avery " + ("Very Long Profile Name " * 8)
    client.patch(f"/candidates/{setup['candidates']['Avery Quinlan']}", json={"full_name": long_name})
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)
    exported = client.get(f"/applications/{app['id']}").json()
    assert exported["status"] == "EXPORTED", exported.get("error")
    folder = Path(exported["output_directory"])
    assert folder.parent == deep / date.today().isoformat()
    assert folder.name.startswith("001 Avery Very Long Profile Name")
    assert len(folder.name) < len(f"001 {long_name}")
    assert all(len(f["path"]) <= 240 for f in exported["files"])


def test_export_never_overwrites(client, setup):
    folder = setup["out"] / date.today().isoformat() / "001 Avery Quinlan"
    folder.mkdir(parents=True)
    existing = folder / "001_Acme_Data_Co_Resume.docx"
    existing.write_bytes(b"operator's own file")
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)
    assert existing.read_bytes() == b"operator's own file"
    assert (folder / "001_Acme_Data_Co_Resume_v2.docx").exists()


def test_url_less_export_uses_the_same_numbered_profile_folder(client, setup):
    job = client.post(
        "/jobs",
        json={"raw_jd": SAMPLE_JOBS[0]["text"], "company": SAMPLE_JOBS[0]["company"], "title": SAMPLE_JOBS[0]["title"]},
    )
    assert job.status_code == 201
    url_less_setup = {**setup, "job": job.json()}

    app = generated_app(client, url_less_setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)

    exported = client.get(f"/applications/{app['id']}").json()
    folder = setup["out"] / date.today().isoformat() / "001 Avery Quinlan"
    assert Path(exported["output_directory"]) == folder
    assert folder.is_dir()
    assert not any(path.name.endswith("Job_URL.txt") for path in folder.iterdir())
    metadata = json.loads((folder / "001_Acme_Data_Co_Metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_url"] is None


def test_pdf_failure_keeps_docx_and_retry_succeeds(client, setup, monkeypatch):
    import resumeflow.services.applications as applications
    from resumeflow.services.pdf import PdfConversionError

    client.patch("/settings", json={"pdf_converter": "auto"})
    monkeypatch.setattr(applications, "resolve_converter", lambda settings: "word")

    def broken(documents, settings):
        raise PdfConversionError("Word is busy")

    monkeypatch.setattr(applications, "convert_documents_to_pdf", broken)
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    client.post(f"/applications/{app['id']}/export")
    drain(client)
    failed = client.get(f"/applications/{app['id']}").json()
    assert failed["status"] == "EXPORTED" and failed["pdf_status"] == "failed"
    assert "Word is busy" in failed["error"] and "Word (.docx) files were saved instead" in failed["error"]
    assert {f["format"] for f in failed["files"]} >= {"docx"}  # PDF-only, but no PDF: the Word files are the fallback

    monkeypatch.setattr(applications, "convert_documents_to_pdf", lambda documents, settings: [b"%PDF-1.7 fake" for _ in documents])
    client.post(f"/applications/{app['id']}/retry-pdf")
    drain(client)
    fixed = client.get(f"/applications/{app['id']}").json()
    assert fixed["pdf_status"] == "succeeded" and fixed["error"] is None
    folder = Path(fixed["output_directory"])
    assert (folder / "001_Acme_Data_Co_Resume.pdf").exists()
    metadata = json.loads((folder / "001_Acme_Data_Co_Metadata.json").read_text(encoding="utf-8"))
    assert metadata["pdf_status"] == "succeeded"


def test_outcomes_are_operator_only_and_ordered(client, setup):
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    response = client.post(f"/applications/{app['id']}/outcome", json={"status": "SUBMITTED"})
    assert response.status_code == 409  # must be exported first


def test_section_regeneration_keeps_other_sections(client, setup):
    app = generated_app(client, setup)
    client.post(f"/applications/{app['id']}/approve", json={})
    before = client.get(f"/applications/{app['id']}").json()["resume"]["content"]
    client.post(f"/applications/{app['id']}/generate", json={"document": "resume", "section": "summary"})
    drain(client)
    after = client.get(f"/applications/{app['id']}").json()
    assert after["status"] == "AWAITING_REVIEW"
    assert after["resume"]["version"] == 2
    assert after["resume"]["content"]["experience"] == before["experience"]
    assert after["cover_letter"]["version"] == 1  # untouched


def _tailoring(other_employer: str):
    def handler(text: str) -> dict:
        role_ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[R-")]
        bullet_ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[B-")]
        return {
            "headline": "Senior Data Engineer",
            "summary": f"Data engineer who previously worked at {other_employer}.",
            "skill_order": ["Apache Spark", "Python", "COBOL"],
            "roles": [
                {"role_id": role_ids[0], "bullets": [
                    {"text": "Built Spark and Airflow pipelines processing 4 TB of events daily on AWS", "source_bullet_ids": [bullet_ids[0]]},
                    {"text": "Invented a quantum scheduler", "source_bullet_ids": ["B-nonexistent"]},
                ]},
            ],
            "placements": [],
        }

    return handler


def test_ai_documents_are_saved_as_written_without_a_checking_step(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeTailoring", _tailoring("Front Range Payments Inc"))
    fake_ai.on(
        "AICoverLetter",
        lambda text: {"greeting": "Dear Acme Data Co team,", "paragraphs": [{"text": "I am applying to Acme Data Co. " * 30, "evidence_ids": ["YRS"]}], "closing": "Sincerely,"},
    )
    app = generated_app(client, setup)
    resume = app["resume"]
    assert resume["mode"] == "ai"
    content = resume["content"]
    # The writer's summary is used as written: no fact-check call, no automatic fixes, nothing held.
    assert content["summary"] == "Data engineer who previously worked at Front Range Payments Inc."
    assert not [c for c in fake_ai.calls if c["schema"] == "AIClaimReview"]
    assert resume["issue_counts"]["error"] == 0
    assert not [i for i in resume["issues"] if i["code"] in ("auto_fixed", "ai_unsupported_claim")]
    # A bullet can still only come from the candidate's own roles: one citing an id that doesn't exist is left out.
    assert all("quantum" not in b["text"] for r in content["experience"] for b in r["bullets"])

    # Candidate isolation in prompts: nothing about other candidates is sent.
    generation_inputs = " ".join(c["input"] for c in fake_ai.calls if c["schema"] in {"AIResumeTailoring", "AICoverLetter"})
    for other in SAMPLE_CANDIDATES[1:]:
        assert other.name not in generation_inputs
        assert other.email not in generation_inputs

    resume_call = next(c for c in fake_ai.calls if c["schema"] == "AIResumeTailoring")
    cover_call = next(c for c in fake_ai.calls if c["schema"] == "AICoverLetter")
    assert "<candidate_evidence>" in resume_call["cache_prefix"]
    assert "<job_description>" not in resume_call["cache_prefix"]
    assert "<candidate_evidence>" in cover_call["cache_prefix"]
    assert resume_call["max_output_tokens"] == 6_000
    assert cover_call["max_output_tokens"] == 3_000


def test_transient_document_failure_retries_only_failed_call(client, setup, secret_store, fake_ai, db):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    calls = {"n": 0}

    def flaky(text: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientAIError("rate limited")
        return _tailoring("nobody")(text)

    fake_ai.on("AIResumeTailoring", flaky)
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "Hello Acme Data Co.", "evidence_ids": []}], "closing": "Thanks,"})
    fake_ai.on("AIClaimReview", lambda text: {"issues": []})
    app = create(client, setup).json()
    drain(client)
    job = db.scalars(select(ProcessingJob).where(ProcessingJob.application_id == app["id"])).one()
    assert job.status == ProcessingStatus.SUCCEEDED and job.retry_count == 0
    assert calls["n"] == 2
    assert len([call for call in fake_ai.calls if call["schema"] == "AICoverLetter"]) == 1
    assert client.get(f"/applications/{app['id']}").json()["status"] == "AWAITING_REVIEW"


def test_non_transient_ai_error_falls_back_to_deterministic(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.fail_with = AIError("model refused")
    app = create(client, setup).json()
    drain(client)
    detail = client.get(f"/applications/{app['id']}").json()
    assert detail["status"] == "AWAITING_REVIEW"  # deterministic fallback, noted in issues
    assert any("AI writing failed" in i["message"] for i in detail["resume"]["issues"])


def test_failed_generation_can_be_retried(client, setup, monkeypatch):
    import resumeflow.services.applications as applications

    real = applications.generate_documents
    monkeypatch.setattr(applications, "generate_documents", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    app = create(client, setup).json()
    drain(client)
    failed = client.get(f"/applications/{app['id']}").json()
    assert failed["status"] == "FAILED" and "disk full" in failed["error"]
    assert failed["processing"][0]["status"] == "failed"

    monkeypatch.setattr(applications, "generate_documents", real)
    client.post(f"/processing-jobs/{failed['processing'][0]['id']}/retry")
    assert client.get(f"/applications/{app['id']}").json()["status"] == "GENERATING"
    drain(client)
    assert client.get(f"/applications/{app['id']}").json()["status"] == "AWAITING_REVIEW"


def test_restart_recovery_and_cancel(client, setup, db):
    app = create(client, setup).json()
    job_id = client.get(f"/applications/{app['id']}").json()["processing"][0]["id"]
    db.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(status=ProcessingStatus.RUNNING))
    db.commit()
    assert client.app.state.queue.recover() == 1
    drain(client)
    assert client.get(f"/applications/{app['id']}").json()["status"] == "AWAITING_REVIEW"

    second = create(client, setup, "Hana Kobayashi", override_reason="testing cancel").json()
    pending = client.get(f"/applications/{second['id']}").json()["processing"][0]["id"]
    cancelled = client.post(f"/processing-jobs/{pending}/cancel").json()
    assert cancelled["status"] == "cancelled"
    retried = client.post(f"/processing-jobs/{pending}/retry").json()
    assert retried["status"] == "queued"
    drain(client)
    assert client.get(f"/applications/{second['id']}").json()["status"] == "AWAITING_REVIEW"


def test_budget_exceeded_falls_back_without_ai(client, setup, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    client.patch("/settings", json={"daily_budget_usd": 0})
    calls_before = len(fake_ai.calls)
    app = generated_app(client, setup)
    assert len(fake_ai.calls) == calls_before
    assert app["resume"]["mode"] == "deterministic"
    assert any("budget" in i["message"].lower() for i in app["resume"]["issues"])
    summary = client.get("/dashboard/summary").json()
    assert summary["budget"]["daily_exceeded"] is True


def test_analytics_and_backups(client, setup, config):
    generated_app(client, setup)
    stats = client.get("/analytics", params={"days": 7}).json()
    assert len(stats["days"]) == 7
    assert stats["totals"]["applications"] == 1
    assert stats["totals"]["generated"] == 1

    backup = client.post("/backups").json()
    assert (config.backups_dir / backup["name"]).exists()
    extra = upload(client, 0, data=b"", name="x.docx")  # invalid, ignored
    assert extra.status_code == 422
    client.post("/candidates", json={"full_name": "Temporary Person"})
    assert any(c["full_name"] == "Temporary Person" for c in client.get("/candidates").json())
    restored = client.post("/backups/restore", json={"name": backup["name"]}).json()
    assert restored["restored"] == backup["name"]
    assert not any(c["full_name"] == "Temporary Person" for c in client.get("/candidates").json())
    assert client.post("/backups/restore", json={"name": "../../etc/passwd"}).status_code == 422
