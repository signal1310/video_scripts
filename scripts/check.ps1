$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "[1/3] Running Ruff static linter..." -ForegroundColor Cyan
    & "$Root\.venv\Scripts\ruff.exe" check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[2/3] Running BasedPyright strict type checker..." -ForegroundColor Cyan
    & "$Root\.venv\Scripts\basedpyright.exe"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[3/3] Running unit test suite..." -ForegroundColor Cyan
    & "$Root\.venv\Scripts\python.exe" -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
} finally {
    Pop-Location
}
