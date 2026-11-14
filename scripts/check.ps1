param(
    [switch]$VerboseTests
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $unitFlag = if ($VerboseTests) { "-v" } else { "-b" }
    $runner = @'
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

root, unit_flag = sys.argv[1], sys.argv[2]
venv_scripts = os.path.join(root, ".venv", "Scripts")
python_exe = os.path.join(venv_scripts, "python.exe")

steps = [
    ("[1/4] Running Ruff static linter...", [os.path.join(venv_scripts, "ruff.exe"), "check"]),
    ("[2/4] Running BasedPyright strict type checker...", [os.path.join(venv_scripts, "basedpyright.exe")]),
    ("[3/4] Running repository hygiene and local-leak check...", [python_exe, os.path.join(root, "scripts", "verify_hygiene.py")]),
    ("[4/4] Running unit test suite...", [python_exe, "-m", "unittest", "discover", "-s", "tests", "-t", ".", unit_flag]),
]

env = os.environ.copy()
env["PYTHONUTF8"] = "1"

def run_step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

with ThreadPoolExecutor() as pool:
    results = list(pool.map(run_step, [cmd for _, cmd in steps]))

for (label, _), res in zip(steps, results):
    print(label, flush=True)
    if res.stdout:
        print(res.stdout, end="" if res.stdout.endswith("\n") else "\n", flush=True)
    if res.returncode != 0:
        sys.exit(res.returncode)
'@

    & "$Root\.venv\Scripts\python.exe" -c $runner $Root $unitFlag
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
} finally {
    Pop-Location
}
