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
    & $Python -m pytest `
        tests/test_core_prompt.py `
        tests/test_governance_policy_engine.py `
        tests/test_tts.py `
        src/tests/test_finance_manager.py `
        src/tests/test_personality.py `
        -q `
        --junitxml "$resultsDir/smoke-tests.xml"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
