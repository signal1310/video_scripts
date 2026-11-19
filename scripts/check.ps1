param(
    [switch]$VerboseTests
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $runnerArgs = @($Root)
    if ($VerboseTests) { $runnerArgs += "--verbose" }

    & "$Root\.venv\Scripts\python.exe" "$Root\scripts\check_runner.py" @runnerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
} finally {
    Pop-Location
}
