#Requires -Version 5.1
<#
.SYNOPSIS
    Registers the autonomous agent daemon as a Windows Task Scheduler task.

.DESCRIPTION
    Creates a scheduled task called "AutonomousAgentTick" that runs
    agent_tick.py every 30 minutes (configurable). The task runs even
    when the user is NOT logged in, in the background, silently.

    After installation the agent will:
      - Run pytest automatically every 30 minutes
      - Write repair proposals to the approval inbox when tests fail
      - Greet you with "DAEMON NOTICE" the next time you open main.py

.PARAMETER Workspace
    Path to the agent workspace. Default: parent directory of this script.

.PARAMETER IntervalMinutes
    How often to run the tick. Default: 30.

.PARAMETER AllowEffects
    Switch: pass --allow-effects to the tick so it can write files.
    Default: dry-run only (safe).

.PARAMETER Uninstall
    Switch: remove the scheduled task instead of creating it.

.EXAMPLE
    # Install with defaults (dry-run, every 30 min):
    .\scripts\install_daemon.ps1

.EXAMPLE
    # Install with 60-minute interval and real effects:
    .\scripts\install_daemon.ps1 -IntervalMinutes 60 -AllowEffects

.EXAMPLE
    # Remove the task:
    .\scripts\install_daemon.ps1 -Uninstall
#>

param(
    [string]  $Workspace      = (Split-Path $PSScriptRoot -Parent),
    [int]     $IntervalMinutes = 30,
    [switch]  $AllowEffects,
    [switch]  $Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "AutonomousAgentTick"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Scheduled task '$TaskName' removed." -ForegroundColor Green
    } else {
        Write-Host "Scheduled task '$TaskName' does not exist." -ForegroundColor Yellow
    }
    exit 0
}

# ── Resolve Python interpreter ────────────────────────────────────────────────
$VenvPython = Join-Path $Workspace ".venv\Scripts\python.exe"
$SysPython  = (Get-Command python -ErrorAction SilentlyContinue)?.Source

if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
    Write-Host "Using venv Python: $PythonExe" -ForegroundColor Cyan
} elseif ($SysPython) {
    $PythonExe = $SysPython
    Write-Host "Using system Python: $PythonExe" -ForegroundColor Cyan
} else {
    Write-Error "Python not found. Install Python or create a venv at $Workspace\.venv"
    exit 1
}

# ── Resolve agent_tick.py ─────────────────────────────────────────────────────
$TickScript = Join-Path $Workspace "agent_tick.py"
if (-not (Test-Path $TickScript)) {
    Write-Error "agent_tick.py not found at: $TickScript"
    exit 1
}

# ── Build argument string ─────────────────────────────────────────────────────
$TickArgs = "`"$TickScript`" --workspace `"$Workspace`""
if ($AllowEffects) {
    $TickArgs += " --allow-effects"
}

# ── Log file for Task Scheduler output ────────────────────────────────────────
$LogDir  = Join-Path $Workspace "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "daemon_task_scheduler.log"

# Wrap in cmd so stdout/stderr are redirected to the log file.
# Task Scheduler itself cannot redirect output; this is the standard workaround.
$CmdArgs = "/c `"`"$PythonExe`" $TickArgs >> `"$LogFile`" 2>&1`""

# ── Create the scheduled task ─────────────────────────────────────────────────
$Action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $CmdArgs -WorkingDirectory $Workspace
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -Once -At (Get-Date)
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ([Math]::Max(10, $IntervalMinutes - 2))) `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew

# Run as current user (no elevated password prompt needed for dry-run)
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U `
    -RunLevel Limited

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Updating existing task '$TaskName'..." -ForegroundColor Yellow
    Set-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
} else {
    Write-Host "Creating scheduled task '$TaskName'..." -ForegroundColor Cyan
    Register-ScheduledTask `
        -TaskName  $TaskName `
        -Action    $Action `
        -Trigger   $Trigger `
        -Settings  $Settings `
        -Principal $Principal `
        -Description "Autonomous agent daemon: runs health checks every $IntervalMinutes min and writes repair proposals to the approval inbox."
}

Write-Host ""
Write-Host "Done! Task '$TaskName' is now active." -ForegroundColor Green
Write-Host "  Interval : every $IntervalMinutes minutes"
Write-Host "  Python   : $PythonExe"
Write-Host "  Script   : $TickScript"
Write-Host "  Log      : $LogFile"
Write-Host "  Dry-run  : $(-not $AllowEffects)"
Write-Host ""
Write-Host "To check status:   Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To run now:        Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:         .\scripts\install_daemon.ps1 -Uninstall"
