#Requires -Version 5.1
<#
.SYNOPSIS
    Register (or remove) local-LLM autostart after Windows logon.

.DESCRIPTION
    Starts scripts/start_local_llm.ps1 at user logon (llmster → server → load model).

    Registration order (stops at first success):
      1. Task Scheduler "LocalLLMStartup" (AtLogOn, current user)
      2. schtasks.exe ONLOGON for current user
      3. Startup-folder shortcut (no admin required — reliable fallback)

    Complement: in LM Studio settings enable "Run LLM service on login".

.PARAMETER Workspace
    Path to the agent workspace. Default: parent directory of this script.

.PARAMETER Uninstall
    Remove the scheduled task and/or Startup shortcut.

.EXAMPLE
    .\scripts\install_local_llm.ps1

.EXAMPLE
    .\scripts\install_local_llm.ps1 -Uninstall
#>
param(
    [string] $Workspace = (Split-Path $PSScriptRoot -Parent),
    [switch] $Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "LocalLLMStartup"
$StartScript = Join-Path $PSScriptRoot "start_local_llm.ps1"
$StartupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$StartupCmd = Join-Path $StartupDir "LocalLLMStartup.cmd"
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $StartScript)) {
    Write-Error "start_local_llm.ps1 not found at: $StartScript"
    exit 1
}

$LogDir = Join-Path $Workspace "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "local_llm_startup.log"

# Run the starter; it owns logs/local_llm_startup.log (no double-redirect).
$PsArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -Workspace `"$Workspace`""
$CmdLine = "powershell.exe $PsArgs"

function Remove-LocalLlmAutostart {
    $removed = $false
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Scheduled task '$TaskName' removed." -ForegroundColor Green
        $removed = $true
    } else {
        # schtasks may have created it even if Get-ScheduledTask can't see it yet
        $null = & schtasks.exe /Delete /TN $TaskName /F 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Scheduled task '$TaskName' removed (schtasks)." -ForegroundColor Green
            $removed = $true
        }
    }
    if (Test-Path $StartupCmd) {
        Remove-Item -LiteralPath $StartupCmd -Force
        Write-Host "Startup shortcut removed: $StartupCmd" -ForegroundColor Green
        $removed = $true
    }
    if (-not $removed) {
        Write-Host "Nothing to remove (no task, no Startup shortcut)." -ForegroundColor Yellow
    }
}

if ($Uninstall) {
    Remove-LocalLlmAutostart
    exit 0
}

function Test-TaskExists {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        return $true
    }
    $out = & schtasks.exe /Query /TN $TaskName 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Register-ViaScheduledTaskModule {
    $Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $CmdLine" -WorkingDirectory $Workspace
    # Pin trigger to this user — AtLogOn without UserId often needs elevation.
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
    $Settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -MultipleInstances IgnoreNew
    $Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

    try {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Set-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
                -Settings $Settings -Principal $Principal -ErrorAction Stop | Out-Null
        } else {
            Register-ScheduledTask `
                -TaskName $TaskName `
                -Action $Action `
                -Trigger $Trigger `
                -Settings $Settings `
                -Principal $Principal `
                -Description "Start llmster + local OpenAI-compatible API + load qwen-local at logon." `
                -ErrorAction Stop | Out-Null
        }
    } catch {
        Write-Host "  Task Scheduler module: $($_.Exception.Message)" -ForegroundColor DarkYellow
        return $false
    }
    return (Test-TaskExists)
}

function Register-ViaSchtasks {
    # /IT = interactive only (current user session) — no admin password needed.
    $tr = "cmd.exe /c $CmdLine"
    $args = @(
        "/Create", "/TN", $TaskName, "/SC", "ONLOGON",
        "/RL", "LIMITED", "/IT", "/F",
        "/TR", $tr
    )
    Write-Host "  Trying schtasks.exe ONLOGON..." -ForegroundColor DarkYellow
    $out = & schtasks.exe @args 2>&1
    $out | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    return (Test-TaskExists)
}

function Register-ViaStartupFolder {
    New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null
    $content = @"
@echo off
rem Autostart local LLM for agent (created by scripts\install_local_llm.ps1)
cd /d "$Workspace"
$CmdLine
"@
    Set-Content -LiteralPath $StartupCmd -Value $content -Encoding ASCII
    if (-not (Test-Path $StartupCmd)) {
        return $false
    }
    return $true
}

Write-Host "Installing Local LLM autostart..." -ForegroundColor Cyan

$method = $null
if (Register-ViaScheduledTaskModule) {
    $method = "Task Scheduler (AtLogOn)"
} elseif (Register-ViaSchtasks) {
    $method = "schtasks ONLOGON"
} elseif (Register-ViaStartupFolder) {
    $method = "Startup folder ($StartupCmd)"
}

if (-not $method) {
    Write-Error @"
Could not register Local LLM autostart (access denied / policy blocked Task Scheduler).

Options:
  1. Re-run elevated:
       Start → PowerShell → Run as administrator
       cd '$Workspace'
       .\scripts\install_local_llm.ps1
  2. Or enable LM Studio: Settings → Run LLM service on login
  3. Or manually copy a shortcut to:
       $StartupDir
     that runs:
       powershell -File '$StartScript'
"@
    exit 1
}

Write-Host ""
Write-Host "Done! Autostart registered via: $method" -ForegroundColor Green
Write-Host "  Script : $StartScript"
Write-Host "  Log    : $LogFile"
Write-Host ""
if ($method -like "Task*" -or $method -like "schtasks*") {
    Write-Host "To check:   Get-ScheduledTask -TaskName '$TaskName'"
    Write-Host "To run now: Start-ScheduledTask -TaskName '$TaskName'"
} else {
    Write-Host "To run now: .\scripts\start_local_llm.ps1"
    Write-Host "Runs automatically at next Windows logon via Startup folder."
}
Write-Host "To remove:  .\scripts\install_local_llm.ps1 -Uninstall"
Write-Host ""
Write-Host "Tip: also enable LM Studio 'Run LLM service on login'."
