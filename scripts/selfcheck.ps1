# scripts\selfcheck.ps1
# Прогоняет 6 проверок автономного агента подряд. Каждый шаг видно в консоли
# и одновременно дублируется в logs\selfcheck_<timestamp>\<step>.log.

$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot\..

. .\.venv\Scripts\Activate.ps1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING     = 'utf-8'

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logRoot = "logs\selfcheck_$stamp"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

function Run-Step {
    param([string]$Name, [string[]]$PyArgs)
    $log = Join-Path $logRoot "$Name.log"
    Write-Host ""
    Write-Host "==== [$Name] ====  -> $log" -ForegroundColor Cyan
    $started = Get-Date
    & python @PyArgs 2>&1 | Tee-Object -FilePath $log | Out-Host
    $elapsed = (Get-Date) - $started
    $code = $LASTEXITCODE
    $ok = ($code -eq 0)
    $color = if ($ok) { 'Green' } else { 'Yellow' }
    Write-Host ("  [{0}] exit={1}  elapsed={2:N1}s" -f $Name, $code, $elapsed.TotalSeconds) -ForegroundColor $color
    return $ok
}

$results = [ordered]@{}

$results['pytest']        = Run-Step 'pytest'          @('-m','pytest','-q')
$results['live_audit']    = Run-Step 'live_audit'      @('-m','scripts._audit')
$results['auto_run_dry']  = Run-Step 'auto_run_dry'    @('main.py','--ask',':auto-run --limit 5','--auto-approve','deny','--workspace','.')
$results['introspect']    = Run-Step 'introspect'      @('main.py','--ask','Проанализируй сам себя: какие у тебя есть инструменты, какие из них работают, какие ограничения ты знаешь о себе. Используй list_dir, file_read, current_time. Не выдумывай — только проверяемые факты.','--auto-approve','deny','--workspace','.')
$results['sweep_todo']    = Run-Step 'sweep_todo'      @('main.py','--ask','Сколько TODO/FIXME в директории core/? Используй ОДИН вызов shell_exec с findstr или grep по всей директории, не file_read по одному файлу.','--auto-approve','deny','--workspace','.')
$results['confidence']    = Run-Step 'confidence_gate' @('main.py','--ask','Какая текущая дата и день недели? Используй current_time, не угадывай.','--auto-approve','deny','--workspace','.')

Write-Host ""
Write-Host "================ SELF-CHECK SUMMARY ================" -ForegroundColor Magenta
foreach ($k in $results.Keys) {
    $status = if ($results[$k]) { 'OK ' } else { 'FAIL' }
    $color  = if ($results[$k]) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}]  {1}" -f $status, $k) -ForegroundColor $color
}
Write-Host ""
Write-Host "Лог-файлы: $logRoot"
