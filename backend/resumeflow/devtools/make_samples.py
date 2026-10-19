"""Writes the fictional sample resumes and job descriptions to a folder.

    .venv\\Scripts\\python -m resumeflow.devtools.make_samples ..\\samples
"""

from __future__ import annotations

import sys
from pathlib import Path

from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, SAMPLE_JOBS, resume_docx_bytes, resume_pdf_bytes


def main(target: str) -> None:
    root = Path(target)
    resumes = root / "resumes"
    jobs = root / "jobs"
    resumes.mkdir(parents=True, exist_ok=True)
    jobs.mkdir(parents=True, exist_ok=True)
    for index, candidate in enumerate(SAMPLE_CANDIDATES):
        stem = candidate.name.replace(" ", "_")
        # Alternate formats so both import paths get exercised.
        if index % 2 == 0:
            (resumes / f"{stem}_Resume.docx").write_bytes(resume_docx_bytes(candidate))
        else:
            (resumes / f"{stem}_Resume.pdf").write_bytes(resume_pdf_bytes(candidate))
    for job in SAMPLE_JOBS:
        name = f"{job['company'].replace(' ', '_')}_{job['title'].replace(' ', '_')}.txt"
        (jobs / name).write_text(f"URL: {job['url']}\n\n{job['text']}", encoding="utf-8")
    (root / "README.txt").write_text(
        "Fictional sample data for trying ResumeFlow AI. Every person and company is invented.\n\n"
        "1. Candidates > Import resumes: select everything in the resumes folder (or drag the files in).\n"
        "2. Open each candidate, check the extracted details, confirm authorization and mark the review complete.\n"
        "3. New Job: paste a job description from the jobs folder and its URL (first line of the file).\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(SAMPLE_CANDIDATES)} resumes and {len(SAMPLE_JOBS)} job descriptions to {root.resolve()}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")
