# How to Build the ResumeFlow AI Windows Installer (.exe)

Oct 14, 2026 · @Tim McCarthy

## Overview

One command, `npm.cmd run release`, turns the source into a single Windows installer: `ResumeFlow AI_1.1.0_x64-setup.exe` (about 45 MB). It takes about 5 minutes on a typical PC once the tools are installed.

The installer is self-contained. The person installing it needs no Python, Node.js or Rust. It contains three parts:

| Part | Built from | Ends up as |
| --- | --- | --- |
| Desktop window (UI) | `desktop/src` (React, Vite) | Bundled inside the app exe |
| Desktop shell | `desktop/src-tauri` (Rust, Tauri 2) | `resumeflow-desktop.exe` |
| Backend engine | `backend/resumeflow` (Python, FastAPI) | `sidecar\resumeflow-backend.exe` (frozen with PyInstaller) |

The output lands in `resumeflow-ai\desktop\src-tauri\target\release\bundle\nsis\`. All paths in this guide are relative to the `resumeflow-ai` project folder.

## Prerequisites (one-time)

You need four tools on the build PC, all installable with `winget`. Allow about 10 GB of free disk space.

| Tool | Version used for 1.0.0 | Install command |
| --- | --- | --- |
| Windows 10 or 11, 64-bit | Windows 11 Pro | — |
| Node.js | 24.x | `winget install --id OpenJS.NodeJS.LTS` |
| Python | 3.12 or newer (3.15 used) | `winget install --id Python.Python.3.13` or the Python install manager (`py install 3.15`) |
| Visual Studio 2022 Build Tools, C++ workload | 17.x | `winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"` |
| Rust (MSVC toolchain) | 1.99 | `winget install --id Rustlang.Rustup`, then `rustup default stable-msvc` |

WebView2 ships with Windows 11, so nothing extra is needed for it. LibreOffice or Microsoft Word is only needed at run time for PDF export, not for building.

After installing Rust, close and reopen the terminal so it picks up the new PATH. Check that each tool answers:

```powershell
node --version
py --version
cargo --version
```

## First-time project setup

Install the project's own dependencies once per PC: a Python virtual environment for the backend and the Node packages for the desktop app. PyInstaller comes with the `package` extra.

1. Open PowerShell in the `resumeflow-ai` folder.
2. Create the backend environment and install the backend, test and packaging dependencies:

   ```powershell
   cd backend
   py -3.15 -m venv .venv
   .venv\Scripts\python -m pip install -e ".[dev,package]"
   ```
3. Install the desktop app's packages:

   ```powershell
   cd ..\desktop
   npm.cmd install
   ```
4. Optional but recommended: run both test suites so you start from a known-good state. Expect 98 backend and 34 frontend tests to pass.

   ```powershell
   cd ..\backend; .venv\Scripts\python -m pytest
   cd ..\desktop; npm.cmd test
   ```

Use any Python 3.12 or newer in step 2. If you use a different version, change `-3.15` to match. The backend's `pyproject.toml` lists every runtime dependency (FastAPI, SQLAlchemy, python-docx, PyMuPDF, OpenAI SDK, pywin32).

## Build the installer

Run one command from the `desktop` folder. The 1.0.0 build took 4 minutes 15 seconds; the first build on a new PC takes a few minutes longer because Rust compiles every dependency once.

```powershell
cd desktop
npm.cmd run release
```

The command runs these stages in order and stops at the first failure:

1. **Build the UI.** Type-checks the code and bundles the React app into `desktop\dist` (about 15 seconds).
2. **Freeze the backend.** `scripts\build-sidecar.mjs` runs PyInstaller with `backend\packaging\resumeflow-backend.spec` and writes `backend\dist\resumeflow-backend\` (about 1 minute).
3. **Smoke-test the frozen backend.** It starts the frozen exe with a throwaway data folder and runs a full workflow: import a sample DOCX and PDF resume, verify the candidate, match a job, generate, approve and export. Success prints `[sidecar] smoke test passed`.
4. **Compile the desktop shell.** Builds the Rust code in release mode (1.5 to 3 minutes).
5. **Package.** Copies the frozen backend into the app's `sidecar` folder and builds the NSIS installer. The first time, Tauri downloads the NSIS tools automatically.

&#91;embedded content: release build · 5 steps, 1 gate\]

The steps run in number order. If the smoke test fails, the build stops before the Rust shell is compiled, so no installer is produced from a broken backend.

The finished installer is `desktop\src-tauri\target\release\bundle\nsis\ResumeFlow AI_1.1.0_x64-setup.exe`. The same folder tree also holds `target\release\resumeflow-desktop.exe`, which runs without installing.

Always build with `npm.cmd run release`. It adds `tauri.release.conf.json`, which bundles the backend. A plain `npm.cmd run tauri build` produces an installer with no backend engine, and that app cannot start.

To rebuild and test only the backend, run `node scripts\build-sidecar.mjs` from the `resumeflow-ai` folder.

## Verify the build

A build is good when the smoke test passed, the app opens to "Engine ready", and the installer installs and launches. Check all three before handing the file to anyone.

1. **Smoke test line.** The build output must include `[sidecar] smoke test passed: imported DOCX+PDF, matched, generated, approved and exported 5 files`. The build stops if it fails, so a finished installer means it passed.
2. **Run without installing.** Start `desktop\src-tauri\target\release\resumeflow-desktop.exe`. After a short "Starting ResumeFlow engine" screen, the top-right corner should show "Engine ready". Closing the window also stops the `resumeflow-backend.exe` process.
3. **Install it.** Run `ResumeFlow AI_1.1.0_x64-setup.exe`. It installs for the current user only and needs no admin rights. Because the installer isn't code-signed yet, Windows SmartScreen shows a warning: choose *More info*, then *Run anyway*.
4. **First run.** The installed app creates its database, backups and logs in `%LOCALAPPDATA%\ResumeFlowAI`. Open *Settings* and check the PDF converter status: LibreOffice or Microsoft Word must show as found for PDF export.

If the app stays on the startup screen, read `%LOCALAPPDATA%\ResumeFlowAI\logs\backend.log` for the cause.

## Releasing a new version

The version number lives in five files, and all five must match. `tauri.conf.json` sets the installer's file name; the backend's `__version__` shows in the app's engine status and in every exported `Metadata.json`.

| File | Line to change |
| --- | --- |
| `desktop\src-tauri\tauri.conf.json` | `"version": "1.1.0"` |
| `desktop\package.json` | `"version": "1.1.0"` |
| `desktop\src-tauri\Cargo.toml` | `version = "1.1.0"` |
| `backend\pyproject.toml` | `version = "1.1.0"` |
| `backend\resumeflow\__init__.py` | `__version__ = "1.1.0"` |

Installing a newer version over an older one keeps the user's data in `%LOCALAPPDATA%\ResumeFlowAI`. Database changes ship as Alembic migrations, which the app applies automatically on its next start. Create one from the `backend` folder with `.venv\Scripts\python -m alembic revision --autogenerate -m "describe the change"`, then review the generated file.

Release checklist:

- [ ] Version number updated in all five files
- [ ] New database migration created and reviewed, if the data model changed
- [ ] Backend tests pass (`.venv\Scripts\python -m pytest`)
- [ ] Frontend tests and type-check pass (`npm.cmd test`, `npm.cmd run typecheck`)
- [ ] `npm.cmd run release` finished with the smoke test passing
- [ ] Built app opens to "Engine ready" and closes cleanly
- [ ] Installer tested on a PC that has never had ResumeFlow installed

## Troubleshooting

Most build failures come from the terminal environment, not the code. These are the problems seen while building 1.0.0 and their fixes.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `npm.ps1 cannot be loaded because running scripts is disabled` | PowerShell's execution policy blocks npm's script wrapper | Type `npm.cmd` instead of `npm`, or allow local scripts once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `failed to run 'cargo metadata' ... program not found` | The terminal was opened before Rust was installed | Close and reopen the terminal (in VS Code, restart VS Code), then check `cargo --version` |
| `failed to rename archive file ... (os error 32)` during the Rust compile | Something on that drive briefly locks newly written files | Keep the project on `C:`, or set `[build] target-dir` in `desktop\src-tauri\.cargo\config.toml` to a folder on `C:` |
| `No module named PyInstaller` | Packaging dependencies not installed | `.venv\Scripts\python -m pip install -e ".[dev,package]"` in `backend` |
| `[sidecar] smoke test failed: ...` | The frozen backend is missing a library or a migration fails | Read the message. A missing module goes into `hiddenimports` (or `datas` for data files) in `backend\packaging\resumeflow-backend.spec`; then re-run |
| `alembic.exe` or other `.venv\Scripts\*.exe` do nothing | The project folder was moved after the virtual environment was created | Call tools as modules (`.venv\Scripts\python -m alembic ...`) or delete `.venv` and redo the first-time setup |
| Built app closes immediately on start | Built with `npm.cmd run tauri build`, so the backend engine wasn't bundled | Rebuild with `npm.cmd run release` |

## Distributing to other PCs

The installer runs on any 64-bit Windows 10 or 11 PC, but two gaps remain before wide distribution: it isn't code-signed, and it hasn't been tested on a clean machine.

- **Code signing.** Without a signature, SmartScreen warns every new user. Buy a code-signing certificate, then set `bundle.windows.certificateThumbprint` (and `digestAlgorithm`, `timestampUrl`) in `tauri.conf.json` so `npm.cmd run release` signs the installer.
- **Clean-machine test.** Install on a fresh Windows VM or a PC that has never had Python, Node or ResumeFlow, and run one candidate through to export. This proves nothing outside the installer is needed.
- **WebView2.** Present on Windows 11 and current Windows 10. If missing, the installer downloads it (needs internet once).
- **PDF export.** Each PC needs LibreOffice or Microsoft Word for PDFs. Without either, the app still writes DOCX files and offers a PDF retry later.
- **Data and privacy.** Everything stays on the PC: database, backups and logs in `%LOCALAPPDATA%\ResumeFlowAI`, exported files in the output folder chosen in Settings. Each user stores their own OpenAI API key, which goes in Windows Credential Manager.
- **Uninstall.** Use *Settings > Apps > Installed apps > ResumeFlow AI*. Uninstalling removes the program but keeps `%LOCALAPPDATA%\ResumeFlowAI` and exported files; delete that folder to remove all data.
