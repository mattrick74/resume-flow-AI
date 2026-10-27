"""v2.0 backend for the Chrome extension: pairing, the extension origin, local-only analysis, captured jobs, generated
files, answers to application questions and the captured confirmation screen."""

from __future__ import annotations


import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pymupdf


from resumeflow.config import EXTENSION_ID
from resumeflow.devtools.sample_data import SAMPLE_JOBS
from resumeflow.pairing import PAIRING_FILE, load_or_create_token, new_pairing_code
from tests.test_workflow import _tailoring, create, drain, setup  # noqa: F401  (fixture)


def _png() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((20, 50), "Thank you for applying!", fontsize=12)
    return page.get_pixmap().tobytes("png")


# ---------------------------------------------------------------- pairing and origin (BE-02, BE-03)


def test_pairing_code_gives_the_token_once(anon_client, config):
    code = new_pairing_code(config.data_dir)
    response = anon_client.post("/pair", json={"code": code})
    assert response.status_code == 200
    assert response.json()["token"] == config.session_token
    assert anon_client.post("/pair", json={"code": code}).status_code == 401  # used up


def test_wrong_codes_are_limited_and_codes_expire(anon_client, config):
    code = new_pairing_code(config.data_dir)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert anon_client.post("/pair", json={"code": wrong}).status_code == 401
    assert anon_client.post("/pair", json={"code": code}).status_code == 401  # 5 wrong attempts: the code is gone

    code = new_pairing_code(config.data_dir)
    state = json.loads((config.data_dir / PAIRING_FILE).read_text(encoding="utf-8"))
    state["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    (config.data_dir / PAIRING_FILE).write_text(json.dumps(state), encoding="utf-8")
    assert anon_client.post("/pair", json={"code": code}).status_code == 401


def test_the_saved_token_survives_restarts(tmp_path):
    first = load_or_create_token(tmp_path)
    assert len(first) == 64 and load_or_create_token(tmp_path) == first


def test_the_extension_origin_is_allowed(anon_client):
    origin = f"chrome-extension://{EXTENSION_ID}"
    response = anon_client.options("/settings", headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert response.headers.get("access-control-allow-origin") == origin
    other = anon_client.options("/settings", headers={"Origin": "chrome-extension://someoneelse", "Access-Control-Request-Method": "GET"})
    assert other.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------- local analysis (BE-04), capture (BE-06)


AMBIGUOUS_JD = "We are a fast-growing team looking for someone great to join us and help build things that matter for our customers every day."


def test_local_analysis_only_never_calls_the_ai(client, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    client.patch("/settings", json={"local_analysis_only": True})
    job = client.post("/jobs", json={"raw_jd": AMBIGUOUS_JD, "job_url": "https://jobs.example.com/vague", "source": "extension", "site": "jobs.example.com"}).json()
    assert fake_ai.calls == []
    assert job["requirements_source"] == "heuristic" and job["source"] == "extension" and job["site"] == "jobs.example.com"
    assert any(w["code"] == "low_confidence_local_analysis" and "local analysis only" in w["message"] for w in job["warnings"])


def test_page_title_is_a_title_fallback(client):
    job = client.post(
        "/jobs", json={"raw_jd": SAMPLE_JOBS[0]["text"], "page_title": "Platform Engineer - Acme | LinkedIn", "analyze": False, "source": "extension"}
    ).json()
    assert job["title"] == "Platform Engineer"


# ---------------------------------------------------------------- generated files (BE-07)


def test_generated_files_can_be_downloaded(client, setup):
    app = create(client, setup).json()
    drain(client)
    response = client.get(f"/applications/{app['id']}/files/resume", params={"format": "docx"})  # PDFs are off in this setup
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert 'filename="Resume.docx"' in response.headers["content-disposition"]
    missing = client.get(f"/applications/{app['id']}/files/resume", params={"format": "pdf"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "file_not_found"


# ---------------------------------------------------------------- answers (BE-08, BE-09)


def _fields() -> list[dict]:
    return [
        {"field_id": "first", "label": "First Name", "type": "text"},
        {"field_id": "email", "label": "Email", "type": "email"},
        {"field_id": "linkedin", "label": "LinkedIn Profile", "type": "url"},
        {"field_id": "auth", "label": "Are you legally authorized to work in the United States?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "sponsor", "label": "Will you now or in the future require visa sponsorship?", "type": "select", "options": ["Select…", "Yes", "No"]},
        {"field_id": "salary", "label": "Desired salary", "type": "text"},
        {"field_id": "gender", "label": "Gender", "type": "select", "options": ["Male", "Female", "I decline to self-identify"]},
        {"field_id": "veteran", "label": "Veteran status", "type": "radio", "options": ["I am a protected veteran", "I am not a protected veteran"]},
        {"field_id": "consent", "label": "I certify that the information provided is true and complete", "type": "checkbox"},
        {"field_id": "python", "label": "Do you have experience with Python?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "k8s", "label": "Do you have 3+ years of experience with Kubernetes?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "years", "label": "How many years of professional experience do you have?", "type": "number"},
        {"field_id": "why", "label": "Why do you want to work at Acme Data Co?", "type": "textarea", "max_length": 400},
        {"field_id": "resume", "label": "Resume/CV", "type": "file"},
    ]


def test_questions_are_answered_from_the_profile_and_answer_bank_with_one_ai_request(client, setup, secret_store, fake_ai):
    app = create(client, setup).json()
    drain(client)
    secret_store.set("openai_api_key", "sk-test-key-123456")
    candidate_id = setup["candidates"]["Avery Quinlan"]
    client.put("/answer-bank", json={"key": "work_authorization", "answer": "Yes"})  # for everyone
    client.put("/answer-bank", json={"candidate_id": candidate_id, "key": "sponsorship", "answer": "No"})
    client.put("/answer-bank", json={"candidate_id": candidate_id, "key": "salary_expectation", "answer": "150,000"})
    fake_ai.on("AIFormAnswers", lambda text: {"answers": [{"field_id": "why", "answer": "I build reliable data pipelines on AWS, the work your team describes."}]})

    result = client.post(f"/applications/{app['id']}/answers", json={"page_url": "https://boards.example.com/acme/apply", "fields": _fields()}).json()
    answers = {a["field_id"]: a for a in result["answers"]}
    assert answers["first"] == {**answers["first"], "value": "Avery", "source": "profile"}
    assert answers["email"]["value"] == "avery.quinlan@example.com"
    assert answers["auth"]["value"] == "Yes" and answers["auth"]["source"] == "answer_bank"
    assert answers["sponsor"]["value"] == "No"
    assert answers["salary"]["value"] == "150,000"
    assert answers["gender"]["value"] == "I decline to self-identify"  # QA-02: no saved answer, decline offered
    assert answers["veteran"]["source"] == "needs_input"  # no decline option offered: left for the user
    assert answers["consent"]["source"] == "needs_input" and answers["consent"]["value"] is None  # QA-03
    assert answers["python"]["value"] == "Yes" and answers["python"]["source"] == "resume"  # the tailored resume shows it
    assert answers["k8s"]["source"] == "needs_input"  # QA-01: neither the resume nor the profile shows it (asked, no answer)
    assert answers["years"]["source"] == "profile" and int(answers["years"]["value"]) >= 9  # total years from the profile
    assert answers["why"]["source"] == "ai" and "data pipelines" in answers["why"]["value"]
    assert answers["resume"] == {**answers["resume"], "value": "resume", "source": "file"}
    assert answers["linkedin"]["source"] == "needs_input"  # the sample profile has no LinkedIn URL
    assert result["ai_request"] is True
    assert [c["schema"] for c in fake_ai.calls].count("AIFormAnswers") == 1  # QA-05: one request for the page
    sent = fake_ai.calls[-1]["input"]
    assert "<tailored_resume>" in sent and "<candidate_notes>" not in sent  # no notes saved for this candidate
    questions = sent.split("<questions>")[1]
    assert "Kubernetes" in questions  # a skill question the deterministic checks can't settle goes to the AI, with the resume
    assert "LinkedIn" not in questions and "salary" not in questions.lower()  # contact and personal questions need notes

    # The same page again: the free-text answer is reused, no new AI request (QA-05); Answers.json is written (QA-06).
    client.post(f"/applications/{app['id']}/answers", json={"page_url": "https://boards.example.com/acme/apply", "fields": _fields()})
    assert [c["schema"] for c in fake_ai.calls].count("AIFormAnswers") == 1
    detail = client.get(f"/applications/{app['id']}").json()
    answers_file = Path(detail["output_directory"]) / "Answers.json"
    saved = json.loads(answers_file.read_text(encoding="utf-8"))
    assert {a["field_id"] for a in saved["answers"]} == {f["field_id"] for f in _fields()}


def test_no_ai_request_when_nothing_needs_it(client, setup, secret_store, fake_ai):
    app = create(client, setup).json()
    drain(client)
    secret_store.set("openai_api_key", "sk-test-key-123456")
    result = client.post(f"/applications/{app['id']}/answers", json={"fields": _fields()[:3]}).json()
    assert result["ai_request"] is False and not [c for c in fake_ai.calls if c["schema"] == "AIFormAnswers"]


def test_a_custom_question_saved_to_the_answer_bank_is_reused(client, setup):
    app = create(client, setup).json()
    drain(client)
    field = {"field_id": "q1", "label": "Are you comfortable with on-call rotations?", "type": "radio", "options": ["Yes", "No"]}
    first = client.post(f"/applications/{app['id']}/answers", json={"fields": [field]}).json()["answers"][0]
    assert first["source"] == "needs_input"
    saved = client.put("/answer-bank", json={"question": field["label"], "answer": "Yes"}).json()
    assert saved["key"].startswith("q:")
    again = client.post(f"/applications/{app['id']}/answers", json={"fields": [field]}).json()["answers"][0]
    assert again["value"] == "Yes" and again["source"] == "answer_bank"
    bank = client.get("/answer-bank").json()
    assert any(e["key"] == "work_authorization" for e in bank["standard"]) and any(e["id"] == saved["id"] for e in bank["entries"])
    assert client.delete(f"/answer-bank/{saved['id']}").status_code == 204


def test_candidate_notes_let_the_ai_answer_what_the_resume_cannot(client, setup, secret_store, fake_ai):
    app = create(client, setup).json()
    drain(client)
    secret_store.set("openai_api_key", "sk-test-key-123456")
    candidate_id = setup["candidates"]["Avery Quinlan"]
    notes = "Salary: 145-160k base. Open to relocating to Austin. Ran a 3-node Kubernetes home lab since 2021."
    detail = client.patch(f"/candidates/{candidate_id}", json={"answer_notes": f"  {notes}\n"}).json()
    assert detail["answer_notes"] == notes  # kept with the candidate, not in the profile used for resumes
    assert "home lab" not in json.dumps(detail["profile"])

    fields = [
        {"field_id": "relocate", "label": "Are you willing to relocate?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "salary", "label": "Desired salary", "type": "text"},
        {"field_id": "k8s", "label": "Do you have 3+ years of experience with Kubernetes?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "snack", "label": "Favorite office snack", "type": "text"},
        {"field_id": "gender", "label": "Gender", "type": "select", "options": ["Male", "Female", "I decline to self-identify"]},
        {"field_id": "consent", "label": "I certify that the information provided is true and complete", "type": "checkbox"},
        {"field_id": "why", "label": "Why do you want to work at Acme Data Co?", "type": "textarea"},
    ]
    fake_ai.on(
        "AIFormAnswers",
        lambda text: {
            "answers": [
                {"field_id": "relocate", "answer": "Yes"},
                {"field_id": "salary", "answer": "145,000-160,000"},
                {"field_id": "k8s", "answer": "Yes"},
                {"field_id": "snack", "answer": ""},
                {"field_id": "why", "answer": "Your data platform work is what I do every day."},
            ]
        },
    )
    result = client.post(f"/applications/{app['id']}/answers", json={"fields": fields}).json()
    answers = {a["field_id"]: a for a in result["answers"]}
    calls = [c for c in fake_ai.calls if c["schema"] == "AIFormAnswers"]
    assert len(calls) == 1  # still one request for the page (QA-05)
    sent = calls[0]["input"]
    assert "<candidate_notes>" in sent and "home lab" in sent
    questions = sent.split("<questions>")[1]
    assert '"short": true' in questions and '"options": ["Yes", "No"]' in questions
    assert "Gender" not in questions and "certify" not in questions  # QA-02 and QA-03 never reach the AI
    assert answers["relocate"] == {**answers["relocate"], "value": "Yes", "source": "ai"}
    assert answers["salary"]["value"] == "145,000-160,000"
    assert answers["k8s"]["value"] == "Yes"  # shown by the notes, not the resume
    assert answers["snack"]["source"] == "needs_input" and "your notes" in answers["snack"]["note"]
    assert answers["gender"]["value"] == "I decline to self-identify" and answers["consent"]["source"] == "needs_input"
    assert answers["why"]["source"] == "ai"

    # The same page again, unchanged: the question the AI couldn't answer isn't asked again (QA-05).
    client.post(f"/applications/{app['id']}/answers", json={"fields": fields})
    assert len([c for c in fake_ai.calls if c["schema"] == "AIFormAnswers"]) == 1

    # Cleared notes: personal questions (relocation, salary) are left for the user and not sent to the AI; the skill
    # question keeps its earlier answer; the notes changed, so the unanswered question is asked once more, without notes.
    client.patch(f"/candidates/{candidate_id}", json={"answer_notes": ""})
    again = {a["field_id"]: a for a in client.post(f"/applications/{app['id']}/answers", json={"fields": fields}).json()["answers"]}
    assert again["relocate"]["source"] == again["salary"]["source"] == "needs_input"
    assert again["k8s"]["value"] == "Yes" and "earlier" in again["k8s"]["note"]
    calls = [c for c in fake_ai.calls if c["schema"] == "AIFormAnswers"]
    asked = calls[-1]["input"].split("<questions>")[1]
    assert len(calls) == 2 and "<candidate_notes>" not in calls[-1]["input"]
    assert "snack" in asked and "relocate" not in asked and "Desired salary" not in asked


def test_answers_follow_the_tailored_resume(client, setup, secret_store, fake_ai):
    app = create(client, setup).json()
    drain(client)
    secret_store.set("openai_api_key", "sk-test-key-123456")
    # The resume as sent: Terraform named on the first role (and in Skills), a wording only this resume has.
    content = client.get(f"/applications/{app['id']}").json()["resume"]["content"]
    first = content["experience"][0]
    first["technologies"] = [*first.get("technologies", []), "Terraform"]
    first["bullets"][0]["text"] += " (tailored wording for Acme)"
    content["skills"] = [*content["skills"], "Terraform"]
    client.put(f"/applications/{app['id']}/documents/resume", json={"content": content})
    drain(client)

    fields = [
        {"field_id": "tf", "label": "Do you have experience with Terraform?", "type": "radio", "options": ["Yes", "No"]},
        {"field_id": "tf_years", "label": "Years of experience with Terraform", "type": "number"},
        {"field_id": "why", "label": "Describe a project you are proud of.", "type": "textarea"},
    ]
    fake_ai.on("AIFormAnswers", lambda text: {"answers": [{"field_id": "why", "answer": "I moved our pipelines to Airflow."}]})
    answers = {a["field_id"]: a for a in client.post(f"/applications/{app['id']}/answers", json={"fields": fields}).json()["answers"]}
    assert answers["tf"] == {**answers["tf"], "value": "Yes", "source": "resume", "note": "The tailored resume shows Terraform"}
    assert answers["tf_years"]["source"] == "resume" and int(answers["tf_years"]["value"]) >= 1  # the first role's dates
    sent = [c for c in fake_ai.calls if c["schema"] == "AIFormAnswers"][-1]["input"]
    resume_part = sent.split("<tailored_resume>")[1].split("</tailored_resume>")[0]
    assert "(tailored wording for Acme)" in resume_part and "Terraform" in resume_part  # the latest version, as sent
    assert " to " in resume_part  # role dates, for "years of" questions


# ---------------------------------------------------------------- the confirmation screen (BE-10)


def test_captured_confirmation_marks_the_application_submitted(client, setup):
    app = create(client, setup).json()
    drain(client)
    response = client.post(
        f"/applications/{app['id']}/submission",
        files={"image": ("confirmation.png", _png(), "image/png")},
        data={"page_url": "https://boards.example.com/acme/thanks", "page_title": "Thank you", "detected_by": "auto"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "SUBMITTED" and body["submitted_at"]
    shot = body["submission"]["screenshots"][0]
    assert shot["exists"] and Path(shot["path"]).name.startswith("Submission_") and shot["page_url"].endswith("/thanks")
    assert Path(shot["path"]).parent == Path(body["output_directory"])
    metadata_path = next(f["path"] for f in body["files"] if f["type"] == "metadata")
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    assert metadata["submission"]["screenshots"][0]["detected_by"] == "auto"

    undone = client.delete(f"/applications/{app['id']}/submission/{shot['id']}").json()
    assert undone["status"] == "EXPORTED" and undone["submitted_at"] is None and not Path(shot["path"]).exists()


def test_only_png_screenshots_are_accepted(client, setup):
    app = create(client, setup).json()
    drain(client)
    response = client.post(f"/applications/{app['id']}/submission", files={"image": ("x.png", b"not an image", "image/png")})
    assert response.status_code == 422 and response.json()["error"]["code"] == "not_png"


def test_a_capture_during_export_marks_it_submitted_when_the_export_finishes(client, setup):
    app = create(client, setup).json()
    client.app.state.queue.drain(limit=1)  # generation only; the export is still queued
    assert client.get(f"/applications/{app['id']}").json()["status"] == "APPROVED"
    captured = client.post(f"/applications/{app['id']}/submission", files={"image": ("c.png", _png(), "image/png")}).json()
    assert captured["status"] == "APPROVED" and captured["submitted_at"]
    drain(client)
    done = client.get(f"/applications/{app['id']}").json()
    assert done["status"] == "SUBMITTED"
    assert any(Path(f["path"]).name.startswith("Submission_") for f in done["files"] if f["type"] == "submission_screenshot")
