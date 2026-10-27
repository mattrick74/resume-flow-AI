<#
.SYNOPSIS
  Starts the ResumeFlow backend automatically when you sign in to Windows (a shortcut in your Startup folder).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\backend\install-startup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\backend\install-startup.ps1 -Remove
#>
param([switch]$Remove)

$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "ResumeFlow Backend.lnk"
if ($Remove) {
  Remove-Item $shortcut -ErrorAction SilentlyContinue
  Write-Host "Removed: the backend no longer starts at sign-in."
  return
}

$start = Join-Path $PSScriptRoot "start-backend.ps1"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = (Get-Command powershell.exe).Source
$link.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
$link.WorkingDirectory = $PSScriptRoot
$link.Description = "ResumeFlow backend for the Chrome extension"
$link.Save()
Write-Host "Installed: the ResumeFlow backend starts when you sign in ($shortcut)."
& $start
