param(
    [switch]$VerboseTests
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $unitFlag = if ($VerboseTests) { "-v" } else { "-b" }
    $runner = @'
import glob
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

root, unit_flag = sys.argv[1], sys.argv[2]
venv_scripts = os.path.join(root, ".venv", "Scripts")
python_exe = os.path.join(venv_scripts, "python.exe")
TEST_SHARDS = 4

# 테스트 모듈을 파일 크기 내림차순 탐욕 배분으로 샤드에 나눠 프로세스별 병렬 실행(샌드박스는 프로세스별 임시 디렉터리)
test_files = sorted(glob.glob(os.path.join(root, "tests", "test_*.py")), key=os.path.getsize, reverse=True)
shards = [[] for _ in range(TEST_SHARDS)]
shard_sizes = [0] * TEST_SHARDS
for path in test_files:
    target = shard_sizes.index(min(shard_sizes))
    shards[target].append("tests." + os.path.splitext(os.path.basename(path))[0])
    shard_sizes[target] += os.path.getsize(path)
test_cmds = [[python_exe, "-m", "unittest", *modules, unit_flag] for modules in shards if modules]

steps = [
    ("[1/4] Running Ruff static linter...", [os.path.join(venv_scripts, "ruff.exe"), "check"]),
    ("[2/4] Running BasedPyright strict type checker...", [os.path.join(venv_scripts, "basedpyright.exe")]),
    ("[3/4] Running repository hygiene and local-leak check...", [python_exe, os.path.join(root, "scripts", "verify_hygiene.py")]),
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

all_cmds = [cmd for _, cmd in steps] + test_cmds
with ThreadPoolExecutor() as pool:
    results = list(pool.map(run_step, all_cmds))

for (label, _), res in zip(steps, results[: len(steps)]):
    print(label, flush=True)
    if res.stdout:
        print(res.stdout, end="" if res.stdout.endswith("\n") else "\n", flush=True)
    if res.returncode != 0:
        sys.exit(res.returncode)

print("[4/4] Running unit test suite...", flush=True)
test_results = results[len(steps):]
failed = [res for res in test_results if res.returncode != 0]
if failed or unit_flag == "-v":
    for res in test_results:
        print(res.stdout, end="" if res.stdout.endswith("\n") else "\n", flush=True)
    if failed:
        sys.exit(failed[0].returncode)
total = sum(int(m.group(1)) for res in test_results if (m := re.search(r"^Ran (\d+) tests?", res.stdout, re.M)))
print(f"Ran {total} tests in {len(test_results)} parallel shards\nOK", flush=True)
'@

    & "$Root\.venv\Scripts\python.exe" -c $runner $Root $unitFlag
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "ALL CHECKS PASSED." -ForegroundColor Green
} finally {
    Pop-Location
}
