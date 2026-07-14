#Requires -Version 5.1
<#
.SYNOPSIS
    Idempotently start llmster, the local OpenAI-compatible API server, and load qwen-local.

.DESCRIPTION
    Sequence (safe to re-run):
      1. Resolve `lms` CLI
      2. lms daemon up
      3. lms server start  (port from LOCAL_LLM_BASE_URL or 1234)
      4. lms load <LOCAL_LLM_MODEL>
      5. Health-check GET /v1/models

    Also works when LM Studio desktop "Run on login" / headless service is enabled;
    this script just ensures daemon + server + model are up for the agent.

.PARAMETER Workspace
    Agent workspace (default: parent of scripts/). Used for logs/ and optional .env load.

.PARAMETER SkipLoad
    Skip `lms load` (rely on JIT model loading).

.EXAMPLE
    .\scripts\start_local_llm.ps1
#>
param(
    [string] $Workspace = (Split-Path $PSScriptRoot -Parent),
    [switch] $SkipLoad
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

function Write-Log {
    param([string] $Message, [string] $Level = "INFO")
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    if (-not $script:LogFile) { return }
    # Retry: Task Scheduler may still be holding the redirect handle briefly.
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Add-Content -Path $script:LogFile -Value $line -Encoding UTF8 -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Milliseconds 50
        }
    }
}

function Import-DotEnv {
    param([string] $Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [string]::IsNullOrWhiteSpace($name) -and -not (Test-Path "Env:$name")) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Find-Lms {
    $cmd = Get-Command lms -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"),
        (Join-Path $env:USERPROFILE ".lmstudio\bin\lms"),
        (Join-Path $env:LOCALAPPDATA "LM Studio\lms.exe")
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

function Get-LocalPort {
    $base = $env:LOCAL_LLM_BASE_URL
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = "http://127.0.0.1:1234/v1"
    }
    try {
        $uri = [Uri]$base
        if ($uri.Port -gt 0) { return $uri.Port }
    } catch {
        # fall through
    }
    return 1234
}

function Invoke-Lms {
    param(
        [string] $Lms,
        [string[]] $Args,
        [switch] $IgnoreFailure
    )
    Write-Log ("Running: {0} {1}" -f $Lms, ($Args -join " "))
    & $Lms @Args 2>&1 | ForEach-Object {
        Write-Log ("  {0}" -f $_)
    }
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    if ($code -ne 0 -and -not $IgnoreFailure) {
        Write-Log ("lms exit code {0}" -f $code) "WARN"
    }
    return $code
}

function Test-LocalHealth {
    param([int] $Port, [int] $Attempts = 12, [int] $DelaySec = 2)
    $url = "http://127.0.0.1:$Port/v1/models"
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
                Write-Log ("Health OK: {0} (attempt {1})" -f $url, $i)
                return $true
            }
        } catch {
            Write-Log ("Health attempt {0}/{1} failed: {2}" -f $i, $Attempts, $_.Exception.Message) "WARN"
        }
        Start-Sleep -Seconds $DelaySec
    }
    return $false
}

# ── bootstrap ────────────────────────────────────────────────────────────────
$LogDir = Join-Path $Workspace "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$script:LogFile = Join-Path $LogDir "local_llm_startup.log"

Import-DotEnv (Join-Path $Workspace ".env")

$model = $env:LOCAL_LLM_MODEL
if ([string]::IsNullOrWhiteSpace($model)) { $model = "qwen-local" }
$port = Get-LocalPort

Write-Log "=== Local LLM startup begin (model=$model port=$port) ==="

$lms = Find-Lms
if (-not $lms) {
    Write-Log "lms CLI not found. Install llmster (irm https://lmstudio.ai/install.ps1 | iex) or LM Studio, then re-run." "ERROR"
    exit 1
}
Write-Log "Using lms: $lms"

# daemon up — idempotent; ignore "already running"
Invoke-Lms -Lms $lms -Args @("daemon", "up") -IgnoreFailure | Out-Null

# server start
$serverArgs = @("server", "start", "--port", "$port")
Invoke-Lms -Lms $lms -Args $serverArgs -IgnoreFailure | Out-Null

if (-not $SkipLoad) {
    Invoke-Lms -Lms $lms -Args @("load", $model) -IgnoreFailure | Out-Null
}

if (-not (Test-LocalHealth -Port $port)) {
    Write-Log "Local LLM API did not become healthy at port $port" "ERROR"
    exit 1
}

Write-Log "=== Local LLM startup complete ==="
exit 0
