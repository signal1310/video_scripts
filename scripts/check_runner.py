"""검사 실행기: 정적 검사, 위생 검사와 병렬 단위 테스트를 실행하고 실패 원인과 전체 로그를 남김

- `scripts/check.ps1`에서 호출. 인자는 저장소 루트와 상세 출력 여부(`--verbose`)
- 로그는 Git 공용 디렉터리(`git rev-parse --git-common-dir`) 아래에 둬서 작업 트리가 달라도 같은 위치를 봄
- 실패한 샤드는 자동으로 다시 실행하지 않음. 간헐 실패를 가리지 않기 위함
"""

from __future__ import annotations

import argparse
import glob
import os
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

TEST_SHARDS: int = 4
SHARD_TIMEOUT_SEC: int = 300
# - 바깥 예비 제한 시간은 샤드 안쪽 faulthandler보다 길어야 스택이 먼저 남음
SHARD_KILL_MARGIN_SEC: int = 30
FAILURE_TAIL_LINES: int = 60
LOG_DIR_NAME: str = "video-scripts-check"
LOG_FILE_NAME: str = "last.log"
UNSIGNED_MASK: int = 0xFFFFFFFF
TEST_SUMMARY_RE: re.Pattern[str] = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
FAULT_TIMEOUT_RE: re.Pattern[str] = re.compile(r"^Timeout \(\d+:\d{2}:\d{2}\)!", re.MULTILINE)
FAIL_LINE_RE: re.Pattern[str] = re.compile(r"^(?:FAIL|ERROR): .+$", re.MULTILINE)
EXIT_LABELS: dict[int, str] = {
    0x1: "테스트 실패 또는 처리되지 않은 예외",
    0xFFFFFFFF: "바깥에서 강제 종료되었을 수 있음(Stop-Process 등)",
    0xC0000005: "메모리 접근 위반",
    0xC0000409: "중단 호출(STATUS_STACK_BUFFER_OVERRUN)",
}
# - 제한 시간 스택은 샤드 프로세스 안에서 남아야 하므로 래퍼가 faulthandler 타이머를 건 뒤 unittest를 실행
# - 옵션은 모듈 이름보다 앞에 둬야 unittest가 읽음
SHARD_WRAPPER: str = (
    "import faulthandler, sys, unittest\n"
    "faulthandler.dump_traceback_later(float(sys.argv[1]), exit=True)\n"
    "unittest.main(module=None, argv=['unittest', sys.argv[2], *sys.argv[3:]], exit=True)\n"
)


@dataclass(frozen=True)
class CommandSpec:
    """실행할 명령, 표시 이름, 제한 시간(없으면 무제한), 샤드가 담은 모듈 목록"""

    label: str
    command: list[str]
    timeout_sec: float | None = None
    modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    """명령 실행 결과. 바깥 제한 시간으로 끝났는지와 faulthandler 제한 시간 출력 여부를 구분"""

    spec: CommandSpec
    returncode: int
    output: str
    elapsed_sec: float
    timed_out: bool

    @property
    def fault_timeout(self) -> bool:
        """faulthandler 제한 시간 출력이 있는지 여부"""
        return FAULT_TIMEOUT_RE.search(self.output) is not None

    @property
    def failed(self) -> bool:
        """바깥 제한 시간 또는 0이 아닌 종료 코드인지 여부"""
        return self.timed_out or self.returncode != 0


def format_exit_code(code: int) -> str:
    """종료 코드를 부호 없는 10진수와 16진수로 표시"""
    value = code & UNSIGNED_MASK
    return f"{value} (0x{value:08X})"


def describe_failure(result: CommandResult) -> str:
    """실패 원인 한 줄. faulthandler 제한 시간 출력을 먼저 보고, 바깥 제한 시간을 따로 표시"""
    if result.fault_timeout:
        return f"제한 시간 초과(샤드 안에서 스택을 남기고 종료, 제한 {SHARD_TIMEOUT_SEC}초)"
    if result.timed_out:
        return "제한 시간 초과(바깥 예비 장치로 종료, 샤드 안쪽 스택 없음)"
    value = result.returncode & UNSIGNED_MASK
    label = EXIT_LABELS.get(value, "알 수 없는 종료 코드")
    return f"종료 코드 {format_exit_code(result.returncode)}, {label}"


def plan_shards(test_paths: Sequence[str], shard_count: int) -> list[list[str]]:
    """파일 크기 내림차순 탐욕 배분으로 테스트 모듈을 샤드에 나눔. 같은 크기는 이름순으로 정해 재현 순서를 고정"""
    ordered = sorted(test_paths, key=lambda path: (-os.path.getsize(path), path))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    sizes: list[int] = [0] * shard_count
    for path in ordered:
        target = sizes.index(min(sizes))
        shards[target].append("tests." + pathlib.Path(path).stem)
        sizes[target] += os.path.getsize(path)
    return [shard for shard in shards if shard]


def shard_command(
    python_exe: str,
    modules: Sequence[str],
    unit_flag: str,
    timeout_sec: int = SHARD_TIMEOUT_SEC,
) -> list[str]:
    """faulthandler 래퍼로 단위 테스트 샤드 하나를 실행하는 명령"""
    return [python_exe, "-X", "faulthandler", "-c", SHARD_WRAPPER, str(timeout_sec), unit_flag, *modules]


def run_command(spec: CommandSpec, cwd: str, env: dict[str, str]) -> CommandResult:
    """명령을 실행해 표준 출력과 오류를 합쳐 받고, 바깥 제한 시간을 넘기면 강제 종료"""
    output: str = ""
    timed_out = False
    started = time.monotonic()
    with subprocess.Popen(
        spec.command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        try:
            output, _ = proc.communicate(timeout=spec.timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            output, _ = proc.communicate()
        returncode = proc.wait()
    return CommandResult(
        spec=spec,
        returncode=returncode,
        output=output,
        elapsed_sec=time.monotonic() - started,
        timed_out=timed_out,
    )


def format_failure(result: CommandResult, log_text: str) -> str:
    """실패한 단계 또는 샤드의 원인, 시간, 출력 끝부분, 실패한 테스트 이름, 로그 위치"""
    lines: list[str] = [
        f"FAILED: {result.spec.label}",
        f"  원인: {describe_failure(result)}",
        f"  소요 시간: {result.elapsed_sec:.1f}s, 출력: {len(result.output.encode('utf-8'))} bytes",
    ]
    if result.spec.modules:
        lines.append("  모듈: " + ", ".join(result.spec.modules))
    if not result.output.strip():
        lines.append("  출력 없음")
    else:
        for match in FAIL_LINE_RE.finditer(result.output):
            lines.append("  " + match.group(0))
        tail = result.output.rstrip("\n").splitlines()[-FAILURE_TAIL_LINES:]
        lines.append(f"  출력 마지막 {len(tail)}줄:")
        lines.extend("    " + line for line in tail)
    lines.append(f"  전체 로그: {log_text}")
    return "\n".join(lines)


def render_log(results: Sequence[CommandResult], total_sec: float) -> str:
    """모든 단계와 샤드의 명령, 종료 코드, 플래그, 시간, 전체 출력을 한 파일 텍스트로 만듦"""
    lines: list[str] = [f"total time: {total_sec:.2f}s", ""]
    for res in results:
        command = ["<shard wrapper>" if arg == SHARD_WRAPPER else arg for arg in res.spec.command]
        lines.append(f"== {res.spec.label}")
        lines.append("command: " + " ".join(command))
        lines.append(f"exit: {format_exit_code(res.returncode)}")
        lines.append(f"flags: timed_out={res.timed_out}, fault_timeout={res.fault_timeout}")
        lines.append(f"elapsed: {res.elapsed_sec:.2f}s, output bytes: {len(res.output.encode('utf-8'))}")
        if res.spec.modules:
            lines.append("modules: " + ", ".join(res.spec.modules))
        lines.append("--- output ---")
        lines.append(res.output.rstrip("\n"))
        lines.append("")
    return "\n".join(lines)


def write_log(path: pathlib.Path, text: str) -> None:
    """실행마다 이전 로그를 덮어씀"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def resolve_log_path(root: str) -> pathlib.Path | None:
    """Git 공용 디렉터리 아래 로그 경로. Git 저장소가 아니면 None"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return pathlib.Path(root, completed.stdout.strip()) / LOG_DIR_NAME / LOG_FILE_NAME


def build_specs(root: str, unit_flag: str) -> tuple[list[CommandSpec], list[CommandSpec]]:
    """정적 검사 세 단계와 단위 테스트 샤드 명령"""
    venv_scripts = os.path.join(root, ".venv", "Scripts")
    python_exe = os.path.join(venv_scripts, "python.exe")
    steps = [
        CommandSpec("[1/4] Running Ruff static linter...", [os.path.join(venv_scripts, "ruff.exe"), "check"]),
        CommandSpec(
            "[2/4] Running BasedPyright strict type checker...",
            [os.path.join(venv_scripts, "basedpyright.exe")],
        ),
        CommandSpec(
            "[3/4] Running repository hygiene and local-leak check...",
            [python_exe, os.path.join(root, "scripts", "verify_hygiene.py")],
        ),
    ]
    shards = plan_shards(glob.glob(os.path.join(root, "tests", "test_*.py")), TEST_SHARDS)
    shard_specs = [
        CommandSpec(
            label=f"shard {index}/{len(shards)}",
            command=shard_command(python_exe, modules, unit_flag),
            timeout_sec=SHARD_TIMEOUT_SEC + SHARD_KILL_MARGIN_SEC,
            modules=tuple(modules),
        )
        for index, modules in enumerate(shards, start=1)
    ]
    return steps, shard_specs


def _print_text(text: str) -> None:
    if text:
        print(text, end="" if text.endswith("\n") else "\n", flush=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="정적 검사와 병렬 단위 테스트 실행기")
    parser.add_argument("root", help="저장소 루트 경로")
    parser.add_argument("--verbose", action="store_true", help="통과한 샤드 출력도 표시")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """정적 검사, 위생 검사, 단위 테스트 샤드를 실행하고 종료 코드를 반환"""
    args = _parse_args(argv)
    root: str = args.root
    unit_flag = "-v" if args.verbose else "-b"
    step_specs, shard_specs = build_specs(root, unit_flag)
    env: dict[str, str] = {**os.environ, "PYTHONUTF8": "1"}
    specs = [*step_specs, *shard_specs]
    started = time.monotonic()
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda spec: run_command(spec, root, env), specs))
    total_sec = time.monotonic() - started

    log_path = resolve_log_path(root)
    if log_path is not None:
        write_log(log_path, render_log(results, total_sec))
    log_text = str(log_path) if log_path is not None else "저장하지 않음(Git 저장소가 아님)"

    step_results = results[: len(step_specs)]
    shard_results = results[len(step_specs):]
    for res in step_results:
        if res.failed:
            print(format_failure(res, log_text), flush=True)
            return 1
        print(res.spec.label, flush=True)
        _print_text(res.output)

    print("[4/4] Running unit test suite...", flush=True)
    if args.verbose:
        for res in shard_results:
            _print_text(res.output)
    failed = [res for res in shard_results if res.failed]
    if failed:
        for res in failed:
            print(format_failure(res, log_text), flush=True)
        return 1
    total = sum(
        int(match.group(1))
        for res in shard_results
        for match in TEST_SUMMARY_RE.finditer(res.output)
    )
    print(
        f"Ran {total} tests in {len(shard_results)} parallel shards\n"
        f"Total time: {total_sec:.1f}s\nOK",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
