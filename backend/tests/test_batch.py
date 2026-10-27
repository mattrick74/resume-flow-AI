from __future__ import annotations

import pytest

from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, SAMPLE_JOBS
from tests.conftest import make_ready, upload


def _item(index: int = 0, **overrides) -> dict:
    job = SAMPLE_JOBS[index]
    return {"job_url": job["url"], "raw_jd": job["text"], **overrides}


NO_ONE_QUALIFIES = """Bridge Design Engineer
Civil Works Co - Remote

Requirements
- Must hold an active Professional Engineer (PE) license
- 8+ years designing highway bridges
- Python for structural calculations
"""


@pytest.fixture
def ready(client, tmp_path):
    for index in range(len(SAMPLE_CANDIDATES)):
        candidate = upload(client, index).json()["candidate"]
        make_ready(client, candidate["id"])
    client.patch("/settings", json={"output_root": str(tmp_path / "out"), "pdf_converter": "none"})


def test_batch_save_reports_each_item(client):
    response = client.post(
        "/jobs/batch",
        json={
            "items": [
                _item(0),
                _item(1),
                _item(0),  # same URL as the first: saved again (the earlier one is reported, see below)
                _item(1, job_url="https://other.example.com/x", raw_jd="too short"),
                _item(0, job_url="not a url"),
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [r["status"] for r in body["results"]] == ["created", "created", "created", "invalid", "invalid"]
    assert body["results"][3]["code"] == "jd_too_short"
    assert body["results"][4]["code"] == "invalid_url"
    assert body["queued"] == []

    saved = client.get("/jobs", params={"status": "DRAFT"}).json()
    assert len(saved) == 3
    assert all(job["processing"] is None and job["application"] is None for job in saved)
    # Not analyzed yet, but the list still shows a readable offline guess.
    acme = next(job for job in saved if job["job_url"] == SAMPLE_JOBS[0]["url"])
    assert (acme["title"], acme["company"], acme["location"]) == ("Senior Data Engineer", "Acme Data Co", "Austin, TX")
    assert client.get("/dashboard/summary").json()["saved_jobs"] == 3


def test_a_repeated_url_is_saved_and_the_earlier_job_is_reported(client):
    """1.8.6 saves every posting; v2.0 (BE-06) lists the earlier jobs with the same URL so the extension can say so."""
    first = client.post("/jobs", json={**_item(0), "analyze": False}).json()
    assert first["same_url"] == []
    again = client.post("/jobs", json={**_item(0), "analyze": False}).json()
    assert again["id"] != first["id"]
    assert [j["job_id"] for j in again["same_url"]] == [first["id"]]
    lookup = client.get("/jobs/by-url", params={"url": SAMPLE_JOBS[0]["url"] + "?utm_source=x"}).json()
    assert {j["job_id"] for j in lookup["same_url"]} == {first["id"], again["id"]}
    assert client.get("/jobs/by-url", params={"url": "not a url"}).json()["same_url"] == []


def test_one_click_processes_every_saved_job(client, ready):
    client.post("/jobs/batch", json={"items": [_item(0), _item(1)]})
    queued = client.post("/jobs/process", json={"all_saved": True})
    assert queued.status_code == 202
    assert len(queued.json()["queued"]) == 2

    client.app.state.queue.drain()  # analysis, matching, then the generation each one queued
    jobs = client.get("/jobs").json()
    assert {j["status"] for j in jobs} == {"MATCHED"}
    for job in jobs:
        assert job["processing"]["status"] == "succeeded"
        assert job["application"]["status"] == "EXPORTED"  # approved and exported automatically
        detail = client.get(f"/applications/{job['application']['id']}").json()
        assert detail["resume"] is not None and detail["cover_letter"] is not None
        assert detail["selection_method"] == "automatic"
    acme = next(j for j in jobs if j["company"] == "Acme Data Co")
    assert client.get(f"/applications/{acme['application']['id']}").json()["candidate_name"] == "Avery Quinlan"
    assert client.get("/dashboard/summary").json()["saved_jobs"] == 0


def test_job_nobody_qualifies_for_gets_no_application(client, ready):
    saved = client.post("/jobs/batch", json={"items": [{"job_url": "https://civil.example.com/bridge", "raw_jd": NO_ONE_QUALIFIES}], "process": True}).json()
    assert len(saved["queued"]) == 1
    client.app.state.queue.drain()
    job = client.get("/jobs").json()[0]
    assert job["status"] == "NO_ELIGIBLE_CANDIDATE"
    assert job["application"] is None
    assert job["processing"]["status"] == "succeeded"


def test_queueing_never_duplicates_work(client, ready):
    job_id = client.post("/jobs/batch", json={"items": [_item(0)]}).json()["results"][0]["job"]["id"]
    assert client.get("/dashboard/summary").json()["saved_jobs"] == 1
    assert client.post("/jobs/process", json={"job_ids": [job_id]}).json()["queued"] == [job_id]
    assert client.get("/dashboard/summary").json()["saved_jobs"] == 0  # queued jobs no longer wait
    second = client.post("/jobs/process", json={"job_ids": [job_id, job_id]}).json()
    assert second["queued"] == [] and second["skipped"][0]["reason"] == "Already queued"
    assert client.post("/jobs/process", json={"all_saved": True}).status_code == 422  # nothing left waiting
    client.app.state.queue.drain()
    third = client.post("/jobs/process", json={"job_ids": [job_id]}).json()
    assert third["skipped"][0]["reason"] == "Already has an application"
    assert len(client.get("/applications").json()) == 1


def test_processing_failure_is_reported_and_retryable(client, ready, monkeypatch):
    import resumeflow.services.batch as batch

    real = batch.run_matching
    monkeypatch.setattr(batch, "run_matching", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database busy")))
    job_id = client.post("/jobs/batch", json={"items": [_item(0)], "process": True}).json()["queued"][0]
    client.app.state.queue.drain()
    failed = client.get(f"/jobs/{job_id}").json()
    assert failed["status"] == "FAILED"
    assert failed["processing"]["status"] == "failed"
    assert any(w["code"] == "processing_failed" for w in failed["warnings"])
    summary = client.get("/dashboard/summary").json()
    assert summary["jobs_failed"] == 1 and summary["processing_failed"] == 0

    monkeypatch.setattr(batch, "run_matching", real)
    assert client.post("/jobs/process", json={"job_ids": [job_id]}).json()["queued"] == [job_id]
    client.app.state.queue.drain()
    assert client.get(f"/jobs/{job_id}").json()["status"] == "MATCHED"
    assert client.get("/dashboard/summary").json()["jobs_failed"] == 0


def test_nothing_to_process(client):
    response = client.post("/jobs/process", json={"all_saved": True})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "nothing_to_process"


def test_a_posting_generated_before_is_reported_with_its_application(client, ready):
    first = client.post("/jobs/batch", json={"items": [_item(0)], "process": True}).json()["results"][0]["job"]["id"]
    client.app.state.queue.drain()
    application = client.get(f"/jobs/{first}").json()["applications"][0]

    copy = client.post("/jobs", json={**_item(0), "source": "extension", "site": "jobs.example.com"}).json()
    earlier = next(j for j in copy["same_url"] if j["job_id"] == first)
    assert earlier["applications"][0]["application_id"] == application["id"]
    assert earlier["applications"][0]["candidate_name"] == "Avery Quinlan"


def test_save_and_generate_processes_repeated_postings_independently(client, ready):
    earlier = client.post("/jobs/batch", json={"items": [_item(0)]}).json()["results"][0]["job"]["id"]
    again = client.post("/jobs/batch", json={"items": [_item(0), _item(1)], "process": True}).json()
    assert [r["status"] for r in again["results"]] == ["created", "created"]
    assert earlier not in again["queued"] and len(again["queued"]) == 2
    client.app.state.queue.drain()
    assert len(client.get("/jobs").json()) == 3 and len(client.get("/applications").json()) == 2


def test_single_job_with_a_known_url_is_saved_and_reported(client):
    client.post("/jobs/batch", json={"items": [_item(0)]})
    response = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"], "analyze": False})
    assert response.status_code == 201 and len(response.json()["same_url"]) == 1


