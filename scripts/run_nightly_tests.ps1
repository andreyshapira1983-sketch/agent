param(
    [string]$Python = "python",
    [string]$OutputDir = "test-results/nightly"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resultsDir = Join-Path $projectRoot $OutputDir
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $resultsDir "nightly-$timestamp.xml"
$logPath = Join-Path $resultsDir "nightly-$timestamp.log"

New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

Push-Location $projectRoot
try {
    & $Python -m pytest tests/ src/tests/ -q --junitxml $reportPath 2>&1 | Tee-Object -FilePath $logPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
