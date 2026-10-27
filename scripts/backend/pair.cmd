@echo off
rem Shows a 6-digit code to pair the ResumeFlow Chrome extension with this PC's backend (valid for 5 minutes).
setlocal
set "ROOT=%~dp0..\.."
set "RESUMEFLOW_SESSION_TOKEN="
if exist "%ROOT%\backend\dist\resumeflow-backend\resumeflow-backend.exe" (
  "%ROOT%\backend\dist\resumeflow-backend\resumeflow-backend.exe" pair
) else (
  pushd "%ROOT%\backend"
  ".venv\Scripts\python.exe" -m resumeflow pair
  popd
)
echo Enter the code in the extension: Options ^> Pair.
pause
