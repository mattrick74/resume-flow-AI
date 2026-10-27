<#
.SYNOPSIS
  Starts the ResumeFlow backend for the Chrome extension, in the background on http://127.0.0.1:8765.

.DESCRIPTION
  Uses the built backend (backend\dist\resumeflow-backend\resumeflow-backend.exe) when it exists, otherwise the
  development environment (backend\.venv). Running it twice is harmless: a second copy exits when the backend is
  already running. The backend keeps its saved token in %LOCALAPPDATA%\ResumeFlowAI, so the paired extension stays
  connected across restarts.
#>
param(
  [int]$Port = 8765,
  [string]$Exe = ""
)

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$built = Join-Path $root "backend\dist\resumeflow-backend\resumeflow-backend.exe"
if (-not $Exe -and (Test-Path $built)) { $Exe = $built }

# Run on its own: no desktop-shell token, so the saved token is used.
Remove-Item Env:RESUMEFLOW_SESSION_TOKEN -ErrorAction SilentlyContinue

if ($Exe) {
  Start-Process -FilePath $Exe -ArgumentList @("--port", $Port) -WindowStyle Hidden
} else {
  $pythonw = Join-Path $root "backend\.venv\Scripts\pythonw.exe"
  if (-not (Test-Path $pythonw)) { throw "No built backend and no development environment found ($pythonw)." }
  Start-Process -FilePath $pythonw -ArgumentList @("-m", "resumeflow", "--port", $Port) -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
}
Write-Host "ResumeFlow backend starting on http://127.0.0.1:$Port"
