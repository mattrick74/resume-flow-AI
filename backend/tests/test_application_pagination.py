from __future__ import annotations

from datetime import date

from resumeflow.db.models import Application, Candidate, Job


def test_application_list_comes_in_filtered_pages(client, db):
    candidates = [Candidate(full_name="Avery Quinlan"), Candidate(full_name="Jordan Lee")]
    db.add_all(candidates)
    db.flush()

    applications = []
    for index in range(1, 13):
        job = Job(
            job_url=f"https://jobs.example.com/{index}",
            normalized_url=f"jobs.example.com/{index}",
            company="Acme" if index % 2 else "Globex",
            title=f"Data Engineer {index}",
            raw_jd=f"Data Engineer {index}\nRequirements\n- Python",
            jd_hash=f"{index:064x}",
        )
        db.add(job)
        db.flush()
        applications.append(
            Application(
                job_id=job.id,
                candidate_id=candidates[index % 2].id,
                application_date=date(2026, 10, 29),
                sequence_number=index,
            )
        )
    db.add_all(applications)
    db.commit()

    pages = [client.get("/applications", params={"limit": 5, "offset": offset}).json() for offset in (0, 5, 10)]
    assert [page["total"] for page in pages] == [12, 12, 12]
    assert [len(page["items"]) for page in pages] == [5, 5, 2]
    assert [item["sequence_number"] for page in pages for item in page["items"]] == list(range(12, 0, -1))
    assert client.get("/applications", params={"limit": 5, "offset": 20}).json() == {
        "items": [],
        "total": 12,
        "offset": 20,
        "limit": 5,
    }

    filtered = client.get(
        "/applications",
        params={"candidate_id": candidates[0].id, "limit": 2, "offset": 0},
    ).json()
    assert filtered["total"] == 6 and len(filtered["items"]) == 2

    # The no-offset response remains an array for an already-loaded extension during a rolling upgrade.
    legacy = client.get("/applications", params={"limit": 5}).json()
    assert isinstance(legacy, list) and len(legacy) == 5
