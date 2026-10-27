# ResumeFlow AI

A local-first Windows desktop app that matches job descriptions to verified candidate profiles and
produces one truthful tailored resume and cover letter per application, with human review before
anything is exported.

**Status: 2.0.0 — one backend, two front ends.**

| Part | What it is |
|---|---|
| **Backend** (`backend/`) | The local service on 127.0.0.1:8765 that holds all data and does the work. Starts at sign-in (`scripts\backend\install-startup.ps1`) or with whichever front end opens first, and keeps running. |
| **Desktop app** (`desktop/`) | The full workspace below: jobs (pasted or batch), candidates, applications, settings, analytics. |
| **Chrome extension** (`extension/`) | Next to the job posting: capture it, rank candidates locally, generate with **one** AI request, fill application forms (one AI request per page for free-text answers), and save the confirmation screen after you submit. Set up with [extension/README.md](extension/README.md). |

Use either front end on its own or both together; they show the same data. Requirements:
[docs/ResumeFlow_AI_Requirements_v2.1.docx](docs/ResumeFlow_AI_Requirements_v2.1.docx). See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the architecture, API, test strategy and remaining risks.

## How to use it

1. **Settings.** Choose an output folder. Optionally store your OpenAI API key, set budgets and check the PDF
   converter. Without a key everything still works offline: extraction and tailoring then use deterministic
   rules over verified facts only.
2. **Candidates → Import resumes.** Pick or drag in each candidate's original resume (DOCX or text-based PDF).
   Originals are stored unchanged and versioned. With an OpenAI key, resumes are read with AI (any layout); without one, an
   offline parser handles common layouts. Imported before adding a key, or a profile looks incomplete? Use **Re-read** on a resume
   version (or **Re-read all resumes** on the Candidates page): verified facts that come back unchanged keep their tick, skills
   from the earlier reading are replaced, and skills you added yourself are kept.
3. **Set up each candidate.** Check the extracted roles, bullets, education and summary, fix anything the reading got wrong,
   and add what the resume left out: projects as bullets under the role where they happened, and skills on the **Skills** tab
   (paste a whole list at once). Mark a skill *Don't use* to keep it off resumes. Turn on *Candidate authorization*; a candidate
   also needs at least one role. Everything in the profile is used as entered: there are no ticks or review step unless you turn
   on Settings → *Require verification of candidate details* (then only ticked facts are used, after *Mark review complete*).
   Resumes still only state what's in the profile: the writer can't add skills or experience that aren't there.
   The **Evidence** tab holds experience you confirmed beyond the imported resume: drafted bullets awaiting approval, approved
   additions (edit, undo or delete), an *Add experience* interview (employer, what was built, technologies) and the history of
   every answer. The original resume file is never changed.
4. **New Job.** Paste the job description and its URL, then click *Analyze & match* (Ctrl+Enter), or *Save for
   later*. To add many at once, switch to **Several jobs**: paste each URL and description (or *Import .txt files*,
   one job per file with the URL on the first line), then *Save N for later* or *Save & generate all*. **A posting
   whose URL is already in your history is skipped**: it isn't saved or generated again (if the earlier copy was only
   saved for later, *Save & generate* generates that one). Near-identical descriptions under a different URL are flagged
   and can be saved anyway. Turn this off in Settings → *Skip job URLs already in history*.
5. **Jobs.** Saved jobs wait here. **Generate all saved** (also on the Dashboard) or tick some and **Generate for N
   selected**: each job is analyzed, candidates are ranked, and the top eligible candidate gets a tailored resume
   and cover letter, all in the background. Open any job to review the extracted requirements and ranked candidates,
   generate for the top match, or choose someone else with a written reason. Work authorization, clearance, licenses,
   location and work arrangement don't block generation: every ready candidate is ranked by score (check those conditions
   yourself before applying). To use them as pass/fail gates again, turn on Settings → *Check hard requirements*.
   **Generate saves the files straight away** (Settings → After generating). What the checks flag is fixed automatically instead
   of holding the application: a flagged cover-letter sentence (e.g. a claim the profile doesn't support, or a number that isn't
   in it) is removed, a flagged resume bullet goes back to the profile's own wording, and if too much is wrong the profile-based
   document is used. Each fix is listed under *Checks* as a note. Choose *Wait for my approval* to review every application.
   The installed desktop app shows one Windows notification after the entire processing queue is empty—after every selected
   job, document generation, export and PDF conversion has finished—not after each individual resume.
   Add **Instructions for the AI** next to Generate/Regenerate (and a default in Settings) for wording, emphasis and order; facts the
   evidence doesn't support are still removed.
   **Job skills found in the candidate's experience (automatic).** Before writing, every skill the job asks for that the resume
   doesn't name is searched for in all of the candidate's roles, bullets (projects included) and skills. When an employer's own
   work shows it — a more specific technology (Amazon EKS → Kubernetes, Next.js → React, PostGIS → PostgreSQL), an unambiguous
   description ("Lambda functions triggered by S3" → AWS Lambda) or the AI semantic check — the employer with the most and
   strongest evidence is chosen and one of its bullets names the skill ("Ran 30 microservices on Amazon EKS (Kubernetes)…"); with
   AI on, the resume-writing response also writes that bullet and Python checks it (no new numbers, tools or claims) before using it—there is no separate skill-expansion request. The bullet
   is inserted under that employer even if the writer leaves it out, survives the checks and automatic fixes, and reaches the
   PDF; the skill also goes on that employer's Technologies line and in Skills. Related-but-different work (Docker on ECS for
   Kubernetes) is put first but never claimed, and skills nothing shows are not added. Each resume lists what was added where
   and what wasn't (and why) under *Checks*, and the export's metadata keeps the full gap report. Nothing asks for confirmation.
   **Missing job skills (optional).** Generation never waits: it uses what the profile already shows. Each job also lists, collapsed,
   the skills it asks for that the top candidate's profile doesn't show. Answer *Have used* / *Have not used* / *Unsure* whenever you
   like; for *Have used*, tick the employer(s) where it happened: the skill is listed on that employer's **Technologies:** line in
   every resume (no description needed). Add a few words about what was done and a bullet for that employer is also written
   from your words and **added at once** (if an AI draft says more than you wrote, your own wording is used). Skills the resume
   already names count as present, also under a fuller name ("CI/CD Pipelines" covers CI/CD, "Stash/Bitbucket" covers Git).
   A skill used but not tied to an employer goes in Skills only; *Have not used* is never suggested again; unanswered skills stay off
   the resume. Answers apply to every later job. Prefer to be asked first, or to approve each bullet? Settings → *Ask about missing
   job skills before generating* (jobs then wait; Jobs → *Answer missing skills* answers them all in one session) and *Add bullets
   from my descriptions without approval*. Match cards show the **verified** score (confirmed evidence only, used to choose) and the
   **potential** score if the open skills were confirmed. A job description is never treated as evidence of experience.
6. **Review Queue → application.** Compare the resume and cover letter side by side. Validation flags any
   claim without verified evidence, any invented number, and any leakage between candidates. Edit, regenerate a
   single section, then *Approve*. **Coverage** shows the job's required and preferred skills covered before (original resume)
   and after, what's missing, near-duplicate bullets and suggestions, plus every added, reworded and unused bullet. **Improve**
   lists skills to answer and drafted bullets; *Regenerate with approved additions* rebuilds the documents from the updated profile.
7. **Export.** Files are written to `Applications\YYYY-MM-DD\NNN Profile Name\`, for example
   `2026-10-14\001 Avery Quinlan\`, as `NNN_Company_Resume.pdf`, `_Cover_Letter.pdf`, `_JD.txt`, `_Job_URL.txt`
   and `_Metadata.json` (Settings → Files to save: PDF only by default; the Word file is saved only if no PDF can
   be made, or when you choose PDF and Word). The number is the application's sequence for that date. A job without
   a URL uses the same folder layout and simply omits `_Job_URL.txt`. Long profile names are shortened to stay within
   the Windows path limit. Existing files are never overwritten.
   **Templates** (Settings → Templates) set the look: Professional, Compact, or any style copied from a resume
   you like. *Copy style from a resume…* reads a DOCX or text-based PDF (or use *Use as template* on a candidate's
   resume version) and keeps only its look: fonts, sizes, colors, heading style, name alignment and margins, never
   its text. Adjust anything the copy missed, open a sample in Word, then *Use this template*; it applies to every
   export and to the review preview.
8. **After applying.** ResumeFlow never submits applications. Record *Submitted*, *Interview*, *Offer* or
   *Rejected* yourself.

Ten fictional sample resumes and two job descriptions are in [`samples/`](samples) for a trial run.
Regenerate them with `backend\.venv\Scripts\python -m resumeflow.devtools.make_samples ..\samples`.

Keyboard: Ctrl+K command palette · Alt+1…8 switch screens · Ctrl+B sidebar · Ctrl+S save · Ctrl+Enter analyze.

## Install (production)

Run `ResumeFlow AI_1.8.5_x64-setup.exe` (per-user install, no admin rights needed). App data, backups and
logs live in `%LOCALAPPDATA%\ResumeFlowAI`. The installer isn't code-signed yet, so on first run Windows
SmartScreen shows *More info → Run anyway*. PDFs are made with LibreOffice if installed, otherwise
Microsoft Word.

## Develop

One-time setup:

```powershell
cd backend
py -3.15 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,package]"
cd ..\desktop
npm install
```

| Task | Command |
|---|---|
| Browser mode (no Rust): dev backend on port 8767 + UI at http://127.0.0.1:1420 | `node scripts/dev-browser.mjs` |
| Desktop app (needs Rust + VS Build Tools); debug builds use port 8766 and `.dev-data` | `cd desktop; npm run tauri dev` |
| Shared backend on its own (port 8765, real data) | `scripts\backend\start-backend.ps1` |
| Extension workspace page | `cd desktop; npm run build:extension` |
| Backend tests (182) | `cd backend; .venv\Scripts\python -m pytest` |
| Frontend tests (71) + type-check | `cd desktop; npm test; npm run typecheck` |
| Windows installer | `cd desktop; npm run release` |

`npm run release` builds the UI, freezes the backend with PyInstaller, then smoke-tests the frozen executable
end to end: import, verify, match, generate, approve, export. Finally it compiles the shell and writes
`desktop\src-tauri\target\release\bundle\nsis\ResumeFlow AI_<version>_x64-setup.exe`.

In PowerShell, use `npm.cmd` if script execution is disabled on your machine.

## Layout

| Path | What |
|---|---|
| `backend/resumeflow/api/` | FastAPI routes: candidates, jobs, applications, processing, analytics, backups, settings, system |
| `backend/resumeflow/services/` | Resume parser, AI layer, matching, generation, validation, rendering, PDF, export, queue, backups |
| `backend/resumeflow/domain/` | Profile, requirements, content schemas, skills vocabulary, status transitions |
| `backend/resumeflow/migrations/` | Alembic migrations (run automatically on launch) |
| `desktop/src/` | React UI: pages, components, design system |
| `desktop/src-tauri/` | Tauri shell: connects to the shared backend (starts it if nothing answers), hands the UI its URL and saved token |
| `extension/` | Chrome extension (Manifest V3): side panel, options, page scripts; `workspace/` is built from `desktop/` |
| `scripts/` | `dev-browser.mjs` (dev), `build-sidecar.mjs` (freeze + smoke test), `backend/` (start, start at sign-in, pair) |
| `samples/` | Fictional sample resumes and job descriptions |
