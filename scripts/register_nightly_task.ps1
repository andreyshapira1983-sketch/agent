param(
    [string]$TaskName = "AgentNightlyTests",
    [string]$Python = "python",
    [string]$StartTime = "02:00"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scripts\run_nightly_tests.ps1"
$command = "PowerShell -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Python `"$Python`""

schtasks /Create /F /SC DAILY /TN $TaskName /TR $command /ST $StartTime
Write-Output "Registered scheduled task '$TaskName' at $StartTime"
