Most of the setup is already done on this PC: the Python environment and the npm packages were installed while I built Milestone 1, so browser mode runs today. To get the real desktop window you need two more things: the Microsoft C++ build tools and Rust. Tauri, the desktop shell, compiles with these. Everything installs with `winget`, which is already on your PC.

## 1. Install the C++ build tools (about 5 GB, 10–20 minutes)

This adds the C++ compiler and the Windows SDK. Accept the Windows admin prompt (UAC) when it appears.
```bash
winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

## 2. Install Rust
```bash
winget install --id Rustlang.Rustup
```

Then **close and reopen your terminal** so it picks up the new PATH, and set the default toolchain:
```bash
rustup default stable-msvc
```

Check that it worked; both should print a version number:
```bash
cargo --version
```

## 3. Run the desktop app
From `E:\AI training\resumeflow-ai\desktop`:
```bash
npm run tauri dev
```
The first build compiles a few hundred Rust packages and takes several minutes. Later builds are fast. WebView2, which displays the app's UI, is already installed.

## Optional
- **Git**, to put the project under version control (it isn't a git repository yet):
  ```bash
  winget install --id Git.Git
  ```
- **LibreOffice** is only needed if we choose it for PDF export in Milestone 5. Skip it for now.

## Setting up a different, clean machine
You'd also need Node.js and Python. Install them, then run from the `resumeflow-ai` folder:
```bash
winget install --id OpenJS.NodeJS.LTS
```
```bash
cd backend; py -3.15 -m venv .venv; .venv\Scripts\python -m pip install -e ".[dev]"
```
```bash
cd desktop; npm install
```
If you don't have Python 3.15, any version from 3.12 up works. In that case, change `py -3.15` in the second command to match your version.

I can run steps 1–3 here for you. You'd still need to approve the admin prompt for the build tools. Once Rust is in, I'll compile the desktop shell and fix whatever comes up before we start Milestone 2.