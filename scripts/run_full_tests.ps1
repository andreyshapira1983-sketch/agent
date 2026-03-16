param(
    [string]$Python = "python",
    [string]$OutputDir = "test-results"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resultsDir = Join-Path $projectRoot $OutputDir

New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

Push-Location $projectRoot
try {
    $junitPath = Join-Path $resultsDir "full-test-suite.xml"
    $fullFallbackLog = Join-Path $resultsDir "full-test-suite.fallback.log"

    # Primary run: with JUnit report.
    & $Python -m pytest tests/ src/tests/ -q --junitxml $junitPath
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        exit 0
    }

    # Fallback for intermittent junitxml writer failures on some Windows setups:
    # re-run without junitxml to validate actual test regressions.
    Write-Warning "Full tests failed (exit=$exitCode). Retrying without --junitxml to verify real regressions..."
    & $Python -m pytest tests/ src/tests/ -q 2>&1 | Tee-Object -FilePath $fullFallbackLog
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
