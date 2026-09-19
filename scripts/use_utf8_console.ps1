$ErrorActionPreference = "Stop"

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"

try {
    chcp 65001 | Out-Null
} catch {
    # Some hosts do not expose chcp. The .NET stream settings above are enough.
}

Write-Host "UTF-8 console enabled for this PowerShell session."
