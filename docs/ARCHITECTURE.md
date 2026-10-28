# ResumeFlow AI — Architecture

Implements `ResumeFlow_AI_Complete_Requirements.docx` (PRD v1.0). Version 2.0.0: one local backend with two separate
front ends (desktop app and Chrome extension), per [ResumeFlow_AI_Requirements_v2.1.docx](ResumeFlow_AI_Requirements_v2.1.docx)
(section 4i below). Earlier as-built requirements: [ResumeFlow_AI_Requirements_v1.8.4.docx](ResumeFlow_AI_Requirements_v1.8.4.docx).

## 1. Architecture

```
┌──────────── Tauri 2 shell (Rust) ────────────────────┐
│ spawns sidecar on a random 127.0.0.1 port            │
│ per-session 64-hex token via env (never argv)        │
│ Windows Job Object: sidecar dies with the app        │
│ IPC: backend_connection · backend_status · restart   │
│  ┌──────── WebView2: React 19 + TS 7 + Vite 8 ─────┐ │
│  │ HashRouter · TanStack Query · Tailwind 4 ·      │ │
│  │ Radix/shadcn-style components · Lucide          │ │
│  └───────────────┬─────────────────────────────────┘ │
└──────────────────┼───────────────────────────────────┘
                   │ HTTP + Bearer token (loopback only)
┌──────────────────▼──── FastAPI sidecar (Python 3.15) ─────────────────┐
│ api/ candidates · jobs · applications · processing-jobs · analytics  │
│      backups · settings · dashboard · system · health                │
│ services/                                                            │
│   resume_parser   DOCX/PDF text + offline structured parse           │
│   ai/             provider (OpenAI Responses) · budgets · cost runs  │
│   candidates      import · versions · verification · snapshot        │
│   jobs            intake · URL/JD duplicates · requirement analysis  │
│   matching        hard gates · weighted score · AI semantic step     │
│   generation      AI or deterministic tailoring from verified facts  │
│   validation      claim-to-evidence · numbers · leakage · AI review  │
│   render · pdf    DOCX templates · LibreOffice / Word conversion     │
│   applications    workflow · versions · approval · export · metadata │
│   queue           persistent SQLite queue · retries · recovery       │
│   backups · analytics                                                │
└──────┬──────────────────────────────┬─────────────────────────────────┘
  SQLite (WAL, FKs, Alembic)    Windows Credential Manager (API key)
  %LOCALAPPDATA%\ResumeFlowAI   Output: <root>\YYYY-MM-DD\NNN Profile Name\
```

## 2. How truthfulness is enforced (PRD §5, §8)

1. **Extraction is never trusted.** Heuristic or AI extraction produces *unverified* facts. Every role, bullet,
   skill, certification, education entry, the summary and the work authorization carry their own `verified`
   flag. A skill can only be verified once it has evidence. AI-quoted evidence that isn't in the source text
   is discarded.
2. **Only the verified snapshot is used.** Matching and generation read `verified_snapshot()`, which contains
   verified facts only. Each application stores the snapshot it was generated from.
3. **The AI never writes facts.** Employers, titles, dates, education and credentials are copied from the
   snapshot. The model may only rephrase or reorder verified bullets, each citing source ids from the same
   role. Uncited or mis-cited output is dropped before the document is assembled.
4. **Validation runs on every version.**
   - Errors: unknown or altered roles, bullets without real sources, numbers absent from their sources,
     unverified skills, leakage of other candidates' names, contacts or employers, placeholder text.
   - Warnings: cover letter length, company not named, skills mentioned without evidence.
   - An optional AI fact-check may add findings, but only for quotes that exist verbatim in the document.
5. **People decide.** Approval with unresolved errors needs an explicit acknowledgement. Edits create new
   versions, and approved versions are never rewritten. Submission and outcomes are operator-only states.
6. **Untrusted input is treated as data.** Job descriptions and resumes are wrapped in tagged blocks with a
   "data, not instructions" rule, and likely prompt-injection text in a job description is flagged.

## 3. Matching (PRD §7)

- **Local-first JD analysis:** identical requirements previously corrected by a person or extracted by AI are reused.
  Otherwise the deterministic parser extracts the title, required/preferred skills, duties, years and supported hard
  conditions first. Clear, well-structured results proceed directly to local scoring; ambiguous structure, unknown
  required technologies, or conditional requirements use one AI fallback request when an API key is available.
- **A. Hard gates** (pass / fail / unknown) cannot be offset by score. Since 1.8.2 they apply only with
  `settings.hard_requirement_gates` on (default off: no gates, every ready candidate is eligible and ranked by score, and the
  requirements editor hides them). Placeholder values ("No", "None", "N/A", "Not specified") are removed from authorization,
  clearance, credential and other-condition fields on save and on read:
  - work authorization and sponsorship (citizen / PR / authorized / requires sponsorship)
  - security clearance level
  - on-site or hybrid location, relocation, and accepted work arrangements
  - required licenses and certifications
- **B. Score** (0–100, weights configurable; default 35/25/15/10/10/5): required skills, relevant experience
  (duty coverage + title similarity), seniority and years (merged verified date ranges), preferred skills,
  industry, other fit. Each skill match records its evidence: a verified skill, an experience line, or a
  word-stem match.
- **C. Semantic step** (optional, AI): for the top 5 candidates only, unmatched skills are checked against
  cited verified evidence. A match is accepted only if the evidence id exists.
- **D. Results:** eligible candidates are ranked and the top 3 recommended. Needs-review, ineligible and
  excluded candidates (not reviewed, no consent, archived) are listed with reasons. If nobody qualifies, the
  job is marked `NO_ELIGIBLE_CANDIDATE`; nothing is fabricated.

## 4. Status model (PRD §14)

Jobs: `DRAFT → ANALYZING → MATCHED | NO_ELIGIBLE_CANDIDATE | FAILED`. A job in `DRAFT` is shown as *Saved*: it was stored for later and waits to be queued.

## 4a. Batch jobs (1.1.0)

Several jobs can be saved at once (`POST /jobs/batch`, each item checked on its own so one bad or duplicate
item never blocks the others) and processed later with one click (`POST /jobs/process` with `job_ids` or
`all_saved`). Each queued `process_job` task runs the whole pipeline in the background: analyze → match →
create the application for the rank-1 eligible candidate → queue generation. The documents then wait in the
Review Queue for approval as usual, so nothing changes about human review or export.

- **No duplicate work:** a job already queued, being analyzed, or with an open application is skipped with a reason.
- **No fabrication:** a job nobody qualifies for stops at `NO_ELIGIBLE_CANDIDATE` without an application.
- **Failures are visible and retryable:** the job turns `FAILED` with a `processing_failed` warning; queueing it again
  re-runs the pipeline. Cancelling a queued run returns the job to `DRAFT`.
- Saved jobs show an offline title/company/location guess until analysis sets the real values.
- **One queue-complete notification:** the last queued/running processing row becoming terminal records a durable
  `queue_idle` event. The desktop confirms the global queue remains empty on its next poll, then sends one native
  Windows notification. Delayed retries still count as queued, and intermediate resume/export stages do not notify.

Applications: `MATCHED → GENERATING → VALIDATING → AWAITING_REVIEW → APPROVED → EXPORTED → SUBMITTED →
INTERVIEW / OFFER / REJECTED`, plus `FAILED` (retryable). APPROVED and EXPORTED can return to
AWAITING_REVIEW (edits) or GENERATING (regeneration). `APPROVED`, `SUBMITTED`, `INTERVIEW`, `OFFER` and
`REJECTED` are operator-only. Export never implies submission.

## 4b. Document templates (1.2.0)

A template is only a visual style (`domain/templates.TemplateStyle`): body and heading fonts, sizes, name size/weight/
alignment, colors for name, headings, rule, text and contact/dates, heading caps/bold/rule, margins and paragraph
spacing. The renderer's layout and section order never change, so any template works for any candidate and AI
never controls formatting (PRD §8).

- **Copy from a resume** (`services/template_extract.py`): DOCX is read through its styles chain, theme fonts and
  theme colors, paragraph borders and section margins; text-based PDF through span fonts, sizes, colors and drawn
  rule lines. The name is the largest text near the top, headings are recognised by common section names, and body
  values are character-weighted modes. Pale colors (e.g. a name on a dark banner) fall back to readable ones, and
  fonts not installed on the PC are flagged. Only the style is stored, never the source text.
- **One global choice**: `settings.resume_template` is a built-in name or `custom:<id>`; export and the review
  preview both use it. Deleting the template in use reverts to Professional.

## 4c. Re-reading resumes (1.3.0)

`POST /candidates/{id}/resumes/{vid}/reextract` reads a stored resume again (AI when a key is set, otherwise the offline
parser) and replaces the extracted profile. Facts the operator verified keep their tick when they come back word for word
(roles by employer, title and start; bullets, summary, education and certifications by text); everything else is unverified and
the review must be completed again. Skills previously read from that same version are replaced, so a misreading can't leave
junk behind; hand-added skills and other versions' skills are kept. Regenerating an application refreshes its snapshot from
the current verified profile (when the candidate is ready), so facts verified after the application was created are used.

The offline parser handles roles without an Experience heading, "Title / dates / Company • Full-time" layouts, "Company |
Title | dates" lines, promotions under one company, icon-font separators, lone bullet glyphs, wrapped and hyphenated lines,
and role "Technologies:" lists (collected as skills, not bullets).

## 4d. Automatic export and operator instructions (1.4.0)

`settings.export_mode`: `auto` (default) approves and exports right after generation unless validation found an error, which
keeps the application in the Review Queue with the reason; `always` exports regardless; `review` waits for Approve. Since 1.8.3
generation repairs instead of holding (`validation.auto_fix_resume` / `auto_fix_cover_letter`): flagged bullets revert to their
source text (or are dropped), a flagged or leaking summary/headline, skills, contact, education or certifications are restored
from the profile-only resume, flagged cover-letter sentences (AI fact-check quotes, invented numbers, other candidates' details)
are removed, and if errors remain or fewer than two paragraphs are left the deterministic document is used. Fixes are recorded
as `auto_fixed` info issues and remaining AI findings are warnings, so `auto` effectively always exports. Automatic
approval uses `Actor.AUTOMATION`, may approve but never submit, and is recorded as `approved_by = automatic` and
`review_status: AUTO_APPROVED` in the metadata. `settings.writing_instructions` and per-application `instructions` are
appended to the writing prompts as operator preferences that the hard rules override; validation still removes unsupported
facts, and the fact-check prompt never sees them. Default models are `gpt-6-luna` / `gpt-6.1-sol` with October 2026 prices
built in; dated snapshots match their base model only.

## 4e. Faster generation and PDF-only output (1.5.0)

With AI, the resume and cover letter are written concurrently and their fact-checks run concurrently, so generation takes about
as long as the slower document. Export renders the DOCX in memory (~30 ms) and, with `settings.output_formats = pdf` (default),
saves only PDFs; the DOCX is written only if no PDF can be made. Word conversion uses one Word session per document (reusing a
session proved unreliable over COM) with a retry; LibreOffice converts all documents in one run. Retry PDF re-renders the approved
versions when no DOCX was kept.

Writing requests keep the complete verified candidate evidence as a stable user-role prefix and put the changing job description
after it. GPT-5.6-and-later models receive an explicit 30-minute prompt-cache breakpoint at that boundary; older configured models
receive the same two-message input without unsupported cache controls. No candidate file or vector store is created, and API
responses are not retained (`store=false`). Cache reads/writes are included in usage logging and estimated costs. The resume and
cover-letter output ceilings are 6,000 and 3,000 tokens respectively, comfortably above measured production maxima while bounding
runaway requests.

## 4f. Skipping job URLs already in history (1.6.0)

With `settings.skip_known_urls` (default on), a job whose normalized URL matches an existing job is skipped, not saved:
`POST /jobs` answers 409 `url_in_history` (no confirm override) and `POST /jobs/batch` reports the item as `skipped`. With
`process`, a skipped posting whose earlier copy is still only saved (DRAFT, never queued) queues that copy instead
(`queued_job_id`). Generation is skipped too when another job with the same URL already has a non-failed application (jobs
added while the setting was off): `queue_jobs` skips with "Already generated for this job URL", `create_application` answers
409 `url_already_generated`, and background processing records that as a job warning. Turning the setting off restores the
1.5 behavior (same URL = duplicate that can be confirmed).

## 4g. Profiles without verification (1.7.0)

`settings.require_verification` (default off). Off: `verified_snapshot` takes every role, bullet, education entry,
certification, the summary/headline, work authorization and every skill not `rejected`; `readiness` only requires an active,
authorized candidate with at least one role (no review step). On: the original behavior (ticked facts, verified skills, completed
review). The snapshot is still the only fact source for matching, writing and validation, so the writer cannot introduce skills or
experience missing from the profile; the operator widens it by editing the profile. `POST /candidates/{id}/skills/bulk`
adds a pasted list (comma, semicolon or line separated) and restores rejected entries. The UI hides ticks, the review button and
skill statuses (replaced by *Use / Don't use*) while verification is off.

## 4h. JD-driven skill and experience expansion (1.8.0)

Implements `ResumeFlow_AI_Requirements_v1.8.0.docx`. A job description is evidence of employer demand, never of the
candidate's work: every new fact comes from the operator (`services/experience.py`, `services/additions.py`).

- **Gaps (JOB-09/10/11):** `analyze_gaps` classifies each required/preferred skill for a candidate as `matched` (shown in a role,
  exact or alias; or an AI-cited semantic match), `confirmed` (in the profile, no role shows it), `plausible` (related wording
  only), `unknown` (no evidence, or answered Unsure) or `rejected`; credentials, clearance, authorization, degrees and years are
  listed as hard gates, never as skills (MAT-08). `batch_gaps` aggregates per top candidate with required/preferred counts and a
  score impact, most-required first.
- **Answers (CAN-12/14/17/18):** `candidate_skill_evidence` stores Have used / Have not used / Unsure per skill with roles
  (id + "employer | title | start" so it survives a re-read), optional years, proficiency, last used, note and source jobs.
  *Used* makes the skill verified (Skills section, both verification modes); *Not used* marks it Don't use and it is never
  suggested again; role ids must belong to the candidate; consent is required (NFR-07).
- **Role records and bullets (CAN-13/15/16, GEN-03/04/08, VAL-05/06/07):** a *used* answer with roles and a description of the
  work becomes a `candidate_role_experience` record; its bullet is drafted in one batched AI call (`EXPERIENCE_DRAFTING`, only
  the role and the record are sent — OPS-09) or offline from the operator's own words. `check_record` flags numbers, tools and
  leadership the description doesn't state (errors), other employers, role dates, technologies used before their release year
  and copied job-description wording (warnings); approval with errors needs an acknowledgement. Only `approved` records are
  merged into `verified_snapshot` (bullet id = record id, so generated bullets cite it), and every change bumps
  `candidates.profile_revision`. A general skill never becomes a company claim: rewritten bullets that name a technology neither
  their sources nor the role's confirmations show are put back to the source wording (`unsupported_tools`), and validation
  reports `unsupported_tool` for edits.
- **Technologies per employer (1.8.4):** `ResumeRole.technologies` is rendered as a "Technologies:" line under each role:
  skills confirmed for that role (Have used + employer, or an approved record) first, then job skills the role's own bullets
  name, job order first, at most 12 (`generation.role_technologies`). Validation reports any other technology as
  `unsupported_tool` and the auto-fix restores the computed line. Skill names keep a leading dot (".NET" was stored as "net";
  migration `0008` re-normalizes stored names), and `skills.skill_covers` lets a profile entry that names a job skill cover it
  ("CI/CD Pipelines" → CI/CD, "Stash/Bitbucket" → Git, "Microsoft SQL Server" → SQL) in matching, gaps, coverage and skill order.
- **Job skills from existing experience (1.8.5, `services/inference.py`):** the resume-generation request handles every
  job skill no employer's bullets name in the same response as tailoring. *Evidence:* `bullet_evidence` finds a more specific technology
  (`skills.IMPLIES`, transitive: Amazon EKS → Kubernetes, PostGIS → PostgreSQL, Next.js → React), an unambiguous description
  (`skills.DESCRIBES`: "Lambda" beside S3/DynamoDB/…, "serverless functions on AWS", kubectl, build/deployment pipelines, pull
  requests) or the strict AI semantic match from matching citing that exact bullet; informal spellings take the job's wording
  (`skills.SPELLINGS`: Postgres → PostgreSQL). Related tools (`skills.RELATED`: Docker/ECS for Kubernetes, MySQL for PostgreSQL)
  are never proof. *Ranking:* each employer scores the sum of its supporting bullets (implied/described 1.0, semantic 0.8); the
  highest wins, the more recent on a tie; a skill the operator confirmed for some employers is only placed at one of those, and
  "Have not used" skills are excluded. *AI (optional, merged into `RESUME_GENERATION` / `AIResumeTailoring`):* per skill the
  employer id, evidence ids, a rewritten bullet and a classification (equivalent / confirmed / related / none). The cover-letter
  request runs concurrently and does not wait for these resume-specific decisions. Python decides:
  the ranking keeps its employer and bullet; the AI bullet is used only if `check_proposed_bullet` passes (names the skill, no
  numbers or technologies beyond the source and the skill, ≥ 50% of the source's wording kept, not much longer, no job-description
  copy, no app wording), else `name_skill` words it ("Amazon EKS (Kubernetes)", "Lambda" → "AWS Lambda"); an AI-only
  "equivalent" is never placed (emphasis + gap report). *Record:* `apply_inferences` makes the skill a skill of that employer in
  the application snapshot and rewrites the supporting bullet there (`original_text` kept), so writing, validation, automatic
  fixes, the profile-only fallback and edits all treat it as that employer's evidence. *Insertion:* `ensure_placements` runs after
  the AI writer, in `deterministic_resume` and inside `auto_fix_resume`: the skill is put back in the employer's bullets (re-named
  beside its evidence, the record's bullet, or inserted first), and the Technologies line and Skills section are repaired.
  AI fact-check findings that quote only bullets taken word for word from the evidence record become notes instead of reverting
  them (`_supported_by_record`); everything else is fixed as before. *Gap report:* `snapshot.skill_gap_report` (statuses
  added_to_experience / in_experience / skills_section / transferable / not_supported / not_used, with `required` and `resolved`)
  is shown as notes under *Checks*, in the coverage response and in the export metadata (`skill_gap_report`,
  `skills_from_existing_experience`). Transferable bullets are put first and the writer is told never to name the missing skill.
- **Instant by default (1.8.1):** `settings.gap_confirmation = off` and `settings.auto_approve_bullets = true` are the defaults:
  generation never waits for answers, and a bullet written from the operator's own description is approved at creation
  (`auto_approve`); an AI draft with error flags is first replaced by `offline_bullet` (the description's own wording), so nothing
  the operator didn't state is added. Unanswered skills simply stay off the resume. Both steps can be switched back on.
- **Waiting jobs (UX-03/04, JOB-12, MAT-09):** with `settings.gap_confirmation = ask` background processing pauses a
  job (`jobs.confirmation_requested_at`) while the top candidate has unanswered skills or unapproved drafts; other jobs continue.
  `after_profile_change` resumes waiting jobs once nothing is left and queues `MATCH_JOB` re-ranking for the jobs the answers came
  from. Matches keep `previous_score` and `skill_changes`.
- **Scores (MAT-06/07):** `score` is the verified score (evidence weights: shown in a role or confirmed for a role 1.0, general
  skill 0.9, related wording or AI-semantic 0.8) and alone ranks and chooses; `potential_score` assumes every non-rejected missing
  skill were confirmed for a role and is display-only.
- **Quality (VAL-09/10, EXP-07, OPS-08):** `coverage` reports required/preferred coverage before (imported resume) and after,
  confirmed coverage, missing skills, near-duplicate bullets, unsupported claims and suggestions (labelled as not an ATS score);
  `resume_changes` lists added/reworded/kept/unused bullets and skills against the imported resume and the prior version.
  Applications store `profile_revision`, `addition_ids_json` and `coverage_json`; metadata adds approved additions (ids, skills,
  times, source jobs — not the operator's descriptions), skill confirmations, validation counts and coverage. Analytics adds
  gaps, answers, drafted/approved bullets, paused/resumed jobs, re-scored matches, time to answer and coverage before/after.
- **Writing (GEN-02/05/06/09):** `settings.bullet_density` (concise / standard / detailed) sets bullets per role; the prompt asks for
  varied verbs, natural job terminology, no stuffing, no repeats, no copied job-description sentences.

## 4i. Local backend + Chrome extension (2.0.0)

- **Process (BE-01/02/03):** run on its own (`resumeflow-backend` / `python -m resumeflow`, no `--parent-pid`) the backend
  listens on 127.0.0.1:8765, exits at once if one already answers there, and uses one saved token
  (`%LOCALAPPDATA%\ResumeFlowAI\api_token`, `pairing.load_or_create_token`). `resumeflow-backend pair` writes a 6-digit
  code (hash only, 5 minutes, 5 attempts) that `POST /pair` (unauthenticated) exchanges for the token; `show-token` prints it.
  CORS also allows `chrome-extension://<config.extension_ids>` (the extension's ID is fixed by the `key` in its manifest).
  Launchers: `scripts/backend/start-backend.ps1`, `install-startup.ps1` (Startup shortcut), `pair.cmd`.
- **License keys (`licensing.py`, `api/license.py`, `scripts/license/keygen.py`):** every protected route also depends on
  `require_license` (403 `license_required|license_expired|license_other_machine|license_clock|license_invalid|
  license_not_configured`, with `details.machine_code`); `GET/POST /license` need only the token. A key is
  `RF1-<base64url JSON>.<base64url Ed25519 signature>` with id, name, issued, expires and an optional machine code
  (SHA-256 of the Windows MachineGuid, `XXXX-XXXX-XXXX-XXXX`). The backend verifies with `license_key.PUBLIC_KEY`; the
  private key exists only with the keygen (`%USERPROFILE%\.resumeflow-keygen\signing_key.pem`, `issued.csv`). The active
  key is `<data dir>/license.key`; `license_state.json` keeps the latest time seen, and a clock more than 12 h earlier is
  refused. `config.license_required` can be turned off only in source runs (debug Tauri, dev-browser); frozen builds
  force it on. The desktop and the extension's workspace wrap the app in `LicenseGate` (machine code, key entry, a
  banner when ≤ 2 days are left; a 403 `license_*` anywhere re-checks); the side panel shows *License needed/expired*.
  `build-sidecar.mjs` refuses to build without the keygen and checks the frozen exe is locked until a 1-day key is
  activated.
- **Two front ends, one backend (BE-12):** the desktop shell (`desktop/src-tauri/src/sidecar.rs`) no longer starts a
  private backend. It checks `GET /health` on 127.0.0.1:8765, reads the saved token from `<data dir>/api_token`
  (`%LOCALAPPDATA%\ResumeFlowAI`, or `RESUMEFLOW_DATA_DIR`), and only when nothing answers starts the bundled backend
  without a session token, detached, so it keeps running after the window closes; `restart_backend` starts it again
  if it stopped answering. Debug builds use port 8766 with the repo's `.dev-data`; `scripts/dev-browser.mjs` uses 8767,
  so development never touches the real data or the shared service.
- **Local analysis (BE-04):** `settings.local_analysis_only` (default on): `analyze_job` never makes the `JD_ANALYSIS` fallback
  request (the heuristic result is kept with a `low_confidence_local_analysis` warning) and the semantic match never runs.
- **One request (BE-05):** with both documents requested, `generate_documents` makes one `APPLICATION_DOCUMENTS` request
  (`AIApplicationDocuments {resume: AIResumeTailoring, cover_letter: AICoverLetter}`, 15k output tokens): the resume
  instructions and input as they are, then the cover-letter instructions, candidate evidence sent once (cached prefix).
  `resume_request` / `resume_from_result` and `cover_from_result` are the unchanged halves of `ai_resume` / `ai_cover_letter`.
  Transient errors: the in-place retries and queue backoff apply to that one request; other errors: profile-based documents
  for both, no second request. Regenerating one document uses that document's own request.
- **Capture (BE-06):** `POST /jobs` takes `source`, `site` (stored on `jobs`, migration 0010) and `page_title` (a title
  fallback) and returns `same_url` (earlier jobs and applications for the posting); `GET /jobs/by-url?url=` answers the same
  before saving. Repeated postings are still saved.
- **Files (BE-07):** `GET /applications/{id}/files/{resume|cover_letter|submission_screenshot}?format=` returns the latest
  exported file (for attaching to forms).
- **Answers (BE-08/09, `services/answers.py`):** `answer_bank` (per candidate or global; standard keys plus `q:<hash>` for
  questions saved from a form) with `GET/PUT/DELETE /answer-bank`. `POST /applications/{id}/answers` answers a page's fields
  in order: consent/certification → left for the user; EEO keys → saved answer, else a "decline" option, else left;
  profile facts (name, contact, links, location, current employer/title, education, total years, years with a skill from
  role dates); answer bank; free-text questions → one `QUESTION_ANSWERING` request (analysis model, `AIFormAnswers`) for the
  page, reusing earlier answers to the same question; anything else → `needs_input`. Choices only from the page's options
  (`choose_option`, numeric ranges). Stored in `application_answers` and written to `Answers.json`.
  **Tailored resume first:** the application's latest structured resume (the one exported and attached) is read too.
  Skill and years-of questions count its roles and Skills (`resume_skill_years`, `resume_shows_skill`; source `resume`,
  the larger of resume and profile years). The AI request gets it as `<tailored_resume>` (with role dates) and is told to
  base every answer on it and never contradict it, using `<candidate_evidence>` and notes only for what it doesn't cover.
  Short and choice questions nothing else answered go to that request too; personal ones (answer-bank keys) and contact
  fields only with notes. A question the AI couldn't answer (`AI_DECLINED`) isn't asked again for the application until
  the candidate (notes, profile) or the resume changes.
  **Open questions** (why this company/role, why changing jobs, interests, strengths, goals, a project; `is_open_question`:
  textareas, and one-line boxes starting with why/describe/tell us/explain..., numbering ignored) must always be answered:
  the instructions separate them from questions of fact (left empty when no source states them) and ask for a positive,
  forward-looking reason for why-leaving questions, with no invented layoffs, pay or conflicts. The request uses the
  writing model, numbers the questions q1..qN (answers are mapped back by those ids, the field id, or by position), and
  `fit()` trims an answer at a sentence end within the field's max length.
  **Candidate notes** (`candidates.answer_notes`, migration 0011, `PATCH /candidates/{id} {answer_notes}`, ≤ 20,000
  characters, desktop *Notes for answers* tab): free text the user keeps for application questions. When present it goes
  into that same request as `<candidate_notes>`, and the page's short and choice questions the profile and answer bank
  don't cover (text, number, date, select, radio, email, tel, url; never checkboxes, EEO or consent) are asked too, with
  their options and `"short": true`. Resume and cover-letter generation never read the notes.
- **Multi-step forms and apply pages elsewhere:** `answerForm` in the service worker scans the visible fields (`form.js`;
  field ids `rf-<load>-<n>` stay unique for the page, so a hidden earlier step never receives a later step's answer),
  posts them, fills them and starts `watchSteps` (a MutationObserver per frame that sends `form-step` when visible fields
  appear that were never read). The service worker then answers again by itself (`scheduleAutoAnswer`, debounced, at
  most 30 runs per page load) on the same host only, including after a page load, posting only empty fields
  (`filled` from the scan) and filling with `keepFilled`; results reach the panel as `form-answered`. Tab state keeps
  `autoAnswer` (unset → on at the first answers, false when turned off or after the confirmation is captured) and
  `answerHost`. `watch.js` ignores Next/Continue/Save/Review buttons (also via `SubmitEvent.submitter`). A tab opened
  from a linked tab (`openerTabId`) inherits its job and application; an unlinked tab whose address or title contains a
  recent open application's company (`companyKeys`) gets a one-click suggestion.
- **Side panel per tab:** one panel serves every tab of a window. On a tab switch it saves what the old tab showed that the
  backend doesn't hold (unsaved capture and edits, chosen candidate and reason, form answers and typed drafts, an error) in
  `chrome.storage.session` under `panel:<tabId>`, and puts it back on return after reloading the tab's linked job or
  application; loads for an earlier tab are dropped (`seq`). *Save for later* posts the job with `analyze: false`; the tab
  then offers *Generate now* (`POST /jobs/process {job_ids}`) or *Choose candidate* (`POST /jobs/{id}/match`), and the
  *Saved jobs* card runs `POST /jobs/process {all_saved: true}` (count from `/dashboard/summary`).
- **Submission (BE-10, `services/submission.py`):** `POST /applications/{id}/submission` (multipart PNG ≤ 15 MB) saves
  `Submission_<date>_<time>.png` in the application folder (document type `submission_screenshot`, format `png`), updates
  metadata.json (`submission`) and records SUBMITTED (operator action; while the export is still running it is applied when
  the export finishes, `apply_pending_submission`). `DELETE …/submission/{doc_id}` removes it and, with none left, returns
  the application to EXPORTED.
- **Extension (`extension/`):** Manifest V3. The service worker (`src/background.js`) is the only backend caller; the side
  panel and options page message it. Page scripts are injected on the user's click into all frames: `capture.js` (site
  readers for 10 job sites, schema.org JobPosting, main text or the selection; canonical LinkedIn/Indeed URLs),
  `form.js` (scan with labels/legends/options incl. open shadow roots; fill via native value setters and input/change
  events; never ticks consent boxes; attaches PDFs through `DataTransfer`), `watch.js` (after a submit click on a linked tab,
  2 minutes of confirmation-text / confirmation-URL detection; the service worker re-injects it after navigation and
  captures with `tabs.captureVisibleTab`). The workspace page is the desktop React app built with
  `npm run build:extension` (relative base, extension transport in `lib/backend.ts` reading the paired token from
  `chrome.storage.local`). `optional_host_permissions: <all_urls>` is requested once (needed for injection after navigation
  and for tab capture); `host_permissions` cover only the loopback backend.

## 5. Data model

The 10 PRD tables, migrated by Alembic (`0001` initial, `0002` profiles/review/export metadata, `0003` skill
display names, `0004` resume templates, `0005` certification-name repair, `0006` approved_by and instructions, `0007` confirmed
experience: `candidate_skill_evidence`, `candidate_role_experience`, `candidates.profile_revision`, `candidate_matches.potential_score`
/ `previous_score`, `jobs.confirmation_requested_at` / `confirmation_resolved_at`, `applications.profile_revision` / `addition_ids_json`
/ `coverage_json`; purely additive). Notable additions, each tied to a requirement:

- `candidates.profile_json` / `profile_reviewed_at` / `consent_confirmed_at`
- `jobs.status` / `normalized_url` / `requirements_source` / `warnings_json`
- `applications.sequence_number` (the `001_` prefix) / `profile_snapshot_json` / `pdf_status` / `approved_at`
- `documents.content_json` / `warnings_json` / `origin` / `approved_at`
- `processing_jobs` idempotency key, backoff and lease columns
- Costs are stored in integer micro-USD.

## 6. API

All routes except `/health` require `Authorization: Bearer <session token>`. Errors use the shape
`{"error": {"code", "message", "details"}}`.

| Area | Routes |
|---|---|
| Health/settings | `GET /health` · `GET/PATCH /settings` · `PUT/DELETE /settings/secrets/{name}` · `GET /dashboard/summary` |
| Candidates | `GET/POST /candidates` · `POST /candidates/{id}/resumes/{vid}/reextract` · `POST /candidates/import` · `GET/PATCH/DELETE /candidates/{id}` · `POST /candidates/{id}/resumes` · `GET /candidates/{id}/resumes/{vid}` · `POST …/apply-profile` · `POST /candidates/{id}/review-complete` · skills CRUD + `bulk` + `verify-with-evidence` · `POST …/skills/confirm-bulk` · `GET/POST …/experience-evidence` · `PATCH/DELETE …/experience-evidence/{rid}` · `POST …/experience-evidence/approve` |
| Jobs | `GET/POST /jobs` (409 `duplicate_job` unless `confirm_duplicate`; `url_in_history`) · `POST /jobs/batch` (per-item `created`/`duplicate`/`skipped`/`invalid`; optional `process`) · `POST /jobs/process` (202; `job_ids` or `all_saved`) · `GET/PATCH/DELETE /jobs/{id}` · `POST /jobs/{id}/analyze` · `POST /jobs/{id}/match` · `POST /jobs/{id}/rematch` · `PUT /jobs/{id}/requirements` · `GET /jobs/{id}/skill-gaps` · `POST /jobs/skill-gaps/batch` |
| Applications | `GET/POST /applications` · `GET /applications/{id}` · `POST …/generate` · `PUT …/documents/{type}` · `GET …/documents/{type}/versions/{n}` · `POST …/approve` · `POST …/export` · `POST …/retry-pdf` · `POST …/outcome` · `POST …/suggest-additions` · `POST …/approve-additions` · `GET …/coverage` |
| Processing | `GET /processing-jobs` · `GET /processing-jobs/events` (cursor events + global `queue_depth`) · `GET /processing-jobs/{id}` · `POST …/retry` · `POST …/cancel` |
| Templates | `GET /templates` · `POST /templates/import` (DOCX/PDF upload) · `POST /templates/from-resume` · `PATCH/DELETE /templates/{id}` · `POST /templates/sample` |
| Operations | `GET /analytics?days=` · `GET/POST /backups` · `POST /backups/restore` · `POST /system/open` · `POST /system/open-url` · `GET /system/pdf-converter` |

## 7. Reliability (PRD §12, §15)

- **Queue:** SQLite-backed, 1–8 workers following the setting live, atomic claim, unique idempotency keys,
  exponential backoff for rate limits, timeouts and 5xx errors, cancellation, restart recovery, and failure
  hooks that mark the application `FAILED` with the reason.
- **No SQLite write lock is held while calling the AI;** reads and AI calls complete first.
- **Export is crash-safe:** each output file is recorded as it's written, so re-running reuses files and never
  duplicates them. Writes are atomic, names never overwrite (`_v2`…), and over-long paths are refused. If PDF
  conversion fails, the DOCX files are kept and the PDF can be retried.
- **Budgets:** checked before every AI call. When exceeded, generation falls back to deterministic tailoring.
- **Backups:** automatic daily backup at startup, manual backups, and online restore through SQLite's backup
  API (after a safety copy). Retention is configurable.
- **Sidecar supervision:** Job Object kill-on-close, a `--parent-pid` watchdog, and a "Restart engine"
  action in the UI.

## 8. Test strategy

| Layer | What's covered | Count |
|---|---|---|
| Backend (pytest) | auth/host/CORS; migrations vs models; pragmas/FKs; settings & secrets; parser accuracy on 20 sample resumes; import validation (bad/scanned/oversized/duplicate files); versioning; candidate isolation; verified snapshot; URL normalisation; duplicate/similar JD; injection flags; local-first heuristic requirements with low-confidence AI fallback; authorization classes; ranking; gates not offset by score; no-eligible reporting; AI extraction/analysis/semantic step with fake provider; deterministic generation truthfulness; override reasons; edit/approve/acknowledge; exact export layout; never overwrite; PDF failure + retry; operator-only outcomes; section regeneration; AI output constrained + leakage + hallucinated review quotes dropped; prompt isolation; queue retry/backoff, recovery, cancel; budget fallback; analytics; backup/restore; batch save (per-item results, duplicates on purpose), one-click processing of every saved job, no-eligible jobs, no duplicate queueing, failure + retry; dated daily-number/profile export folders shortened to fit the path limit; EXPORTED only after every file is written; template style round-trips (DOCX, PDF, built-ins), template API, export in the selected template, certification brackets; real-world resume layouts, re-reading with ticks kept and stale skills replaced, regeneration with newly verified facts, no-role readiness, internal-wording and job-title checks; automatic export (exported, held on errors, always), instructions in writing prompts only, job-board company; PDF-only output with Word fallback, concurrent AI writing; duplicate jobs processed independently; profiles used without verification, pasted skill lists; v1.8 acceptance tests AT-01–AT-12 (gaps, answers, role-only bullets, rejection memory, batch session with jobs that wait and continue, before/after scores and coverage, flagged claims, contradictions, additions surviving a re-read, consent, asking off, 1.7 data kept by the migration) and the AI drafting/tool rule; 1.8.5 skill expansion end to end (a multi-employer candidate and a job asking for React, AWS Lambda, Kubernetes, PostgreSQL, Terraform, Kafka, Golang: each supported skill in a bullet of its best-supported employer and no other, through validation, automatic fixes and automatic export, read back from the exported PDF — also through Word when installed; unsupported skills reported and absent; regeneration stable; "Have not used" respected; AI proposals checked, wrong employer/added numbers/AI-only claims rejected, skills re-inserted when the writer drops them, fact-check flags on evidence-record bullets kept) and the engine (ranking, related tools not proof, naming, spellings, proposal checks) | 200 |
| Frontend (Vitest) | API client (browser/Tauri); navigation, shortcuts, palette; startup gate; dashboard; settings; resume import; duplicate job dialog; job validation; override reason; approval acknowledgement; several-jobs form (validation, duplicates, .txt import); save for later; Jobs list generate all/selected and progress; dashboard saved-jobs banner; template gallery (select, copy from file, adjust, from a candidate's resume) and template-styled previews; re-read one/all resumes; readiness warnings; verify-and-complete review; instructions on generate/regenerate; export settings; skipped job URLs (several jobs, one job); profile without ticks and pasted skills; verification and URL-skip settings; missing-skill answers and drafted-bullet approval (incl. approve anyway), batch session, Evidence tab, coverage and regenerate with additions, 1.8 settings | 67 |
| Frozen build | `build-sidecar.mjs` runs import → match → generate → automatic export, then a batch job that waits for skill answers, is answered (bullet approved) and continues to export, against the PyInstaller exe | 1 flow |
| Manual E2E | Browser mode: 10 resumes imported by drag & drop, reviewed, matched, generated, approved, exported with real PDFs via Word | — |

## 9. Remaining risks and open items

| Risk | Status / mitigation |
|---|---|
| AI path not exercised against the real OpenAI API | Covered by a fake provider in tests; the SDK calls follow `responses.parse` in openai 3.31. Verify with your key on a few jobs first; default models `gpt-6-luna` / `gpt-6.1-sol` are editable in Settings |
| Model prices are estimates | Editable in Settings → Model prices; budgets use them |
| Offline resume parsing is rule-based | Exact on the sample formats; real-world layouts vary. All results are unverified until reviewed; AI extraction is more robust |
| Clean-machine install not tested here | The installer bundles the frozen backend and WebView2 bootstrapper. Test on a clean Windows VM before wide rollout |
| Unsigned installer | SmartScreen warning; buy a code-signing certificate for distribution |
| PDF converter | LibreOffice (portable or installed) or Microsoft Word. With neither, DOCX is still produced and PDFs fail with a retry option |
| Dev and prod share the Credential Manager entry `ResumeFlowAI` | Acceptable for a single operator |
