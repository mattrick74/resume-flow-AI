# ResumeFlow AI — Chrome extension (v2.0)

The extension is one of ResumeFlow's two front ends; the other is the desktop app. Both use the same ResumeFlow
backend on this PC (`http://127.0.0.1:8765`), so a job captured here shows in the desktop app and the other way
round. Use either on its own or both together. All candidates, jobs, documents and files stay on the PC.

## Set up (once)

1. **Build the workspace page** (the React screens for candidates, settings and applications):
   ```
   cd desktop
   npm run build:extension
   ```
   This writes `extension/workspace/`.
2. **Start the backend**: `powershell -ExecutionPolicy Bypass -File scripts\backend\install-startup.ps1`
   starts it now and at every Windows sign-in (`-Remove` undoes that). To start it once instead, run
   `scripts\backend\start-backend.ps1`, or just open the desktop app: it starts the backend if it isn't running, and the
   backend stays up after you close the app.
3. **Load the extension**: open `chrome://extensions`, turn on *Developer mode*, click *Load unpacked* and
   choose this `extension` folder. Its ID is always `ocfabkpfecphnpllkgbhopleplbogifp` (fixed by the `key` in
   `manifest.json`); the backend allows exactly that origin.
4. **Pair**: run `scripts\backend\pair.cmd` and enter the 6-digit code in the extension's Options page
   (right-click the toolbar icon → Options). The backend keeps one saved token, so this is needed only once.
5. **Allow on job sites** (Options, or the first time you click Capture): lets ResumeFlow read the page you're on,
   fill forms and capture the confirmation screen. It only acts when you click it.

## Everyday use

Click the ResumeFlow icon to open the side panel next to the job posting.

| Step | What happens |
|---|---|
| **Capture job** | Reads title, company, location, URL and description (LinkedIn, Indeed, Greenhouse, Lever, Workday, Ashby, SmartRecruiters, iCIMS, Dice, ZipRecruiter; any page that publishes schema.org job data; or your text selection). Check them, then **Save & rank candidates**. |
| **Save & rank** | The backend analyzes the job and ranks candidates **locally** (no AI). Postings saved before are listed. |
| **Save for later** | Saves the job only. Later, on that tab, **Generate now** (best candidate, in the background) or **Choose candidate**; or **Generate all saved** (the *Saved jobs* card, also Jobs in the workspace) does every saved job at once. |
| **Generate** | **One AI request** writes the resume and cover letter; Resume.pdf and CoverLetter.pdf are saved in `Downloads\Applications\<date>\<NNN> <Candidate>`. |
| **Answer questions** | On the application form: fills fields from the profile and your saved answers (Settings → Application answers), writes free-text answers with one AI request per page, attaches Resume.pdf where the site allows. Fields left for you are outlined in amber; save an answer there to reuse it. Consent boxes are never ticked and nothing is submitted. |
| **Notes for answers** | Each candidate has a *Notes for answers* tab (salary, relocation, availability, eligibility, the story behind a project). The AI reads them with the profile for that page's questions, short and multiple-choice ones too; they never go into resumes or cover letters. Self-identification and consent questions still never go to the AI. |
| **Submit** (you) | After your submit click, the confirmation page is recognized and captured as `Submission_<date>_<time>.png` in the application folder; the application is marked Submitted. **Capture now** does it by hand. |

**Open workspace** opens the full app (candidates, settings, applications, analytics) in a tab.

The panel remembers each tab: switch away and back and you find the same captured job (with your edits), candidate
choice and form answers, until the tab is closed.

## Files

- `manifest.json` — Manifest V3; `host_permissions` only for the local backend, `<all_urls>` optional (asked once).
- `src/background.js` — service worker: the only part that calls the backend; injects page scripts; captures tabs.
- `src/sidepanel/` — the side panel. `src/options/` — pairing and permissions.
- `src/content/capture.js`, `form.js`, `watch.js` — page scripts injected on your click (tested in
  `desktop/src/test/extension.test.ts`).
- `workspace/` — built from `desktop/` (not in git).
