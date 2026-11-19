"""검사 실행기: Ruff 안전 자동 수정 뒤 정적 검사, 위생 검사와 병렬 단위 테스트를 실행하고 실패 원인과 전체 로그를 남김

- `scripts/check.ps1`에서 호출. 인자는 저장소 루트와 상세 출력 여부(`--verbose`)
- 로그는 Git 공용 디렉터리(`git rev-parse --git-common-dir`) 아래에 둬서 작업 트리가 달라도 같은 위치를 봄
- 실패한 샤드는 자동으로 다시 실행하지 않음. 간헐 실패를 가리지 않기 위함
- 전체 검사가 스킵 없이 통과하면 작업 트리 지문과 환경 지문을 검증 기록으로 남기고, 같은 지문이면 전체 검사를 생략
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

SHARD_TIMEOUT_SEC: int = 300
# - 바깥 예비 제한 시간은 샤드 안쪽 faulthandler보다 길어야 스택이 먼저 남음
SHARD_KILL_MARGIN_SEC: int = 30
FAILURE_TAIL_LINES: int = 60
LOG_DIR_NAME: str = "video-scripts-check"
LOG_FILE_NAME: str = "last.log"
# - 샤드 수는 논리 코어 수를 이 값으로 나눈 몫(최소 1). 물리 코어 기준은 측정에서 뚜렷이 빠르지 않아 쓰지 않음
SHARD_CORE_DIVISOR: int = 2
# - 모듈별 시간은 다음 실행의 배분에 쓰도록 로그 디렉터리에 남김. 기록이 없으면 파일 크기로 배분
TIMINGS_FILE_NAME: str = "timings.json"
TIMINGS_RE: re.Pattern[str] = re.compile(r"TIMINGS_BEGIN (.*?) TIMINGS_END", re.DOTALL)
# - 통과한 상태의 검증 기록은 로그와 같은 공용 디렉터리 아래 전용 폴더에 두고 최근 것만 보관
VERIFIED_DIR_NAME: str = "verified"
VERIFIED_FILE_PREFIX: str = "verified-"
VERIFIED_TIME_FORMAT: str = "%Y%m%d-%H%M%S-%f"
VERIFIED_FILE_RE: re.Pattern[str] = re.compile(re.escape(VERIFIED_FILE_PREFIX) + r"\d{8}-\d{6}-\d{6}\.json")
VERIFIED_KEEP_COUNT: int = 5
VERIFIED_TIME_LABEL_FORMAT: str = "%Y-%m-%d %H:%M:%S"
SHORT_HASH_LEN: int = 10
# - 실패 로그는 시각 순으로 정렬되는 이름을 써서 최근 것만 남길 때 이름 순서로 고를 수 있게 함
LOG_KEEP_COUNT: int = 10
FAILURE_LOG_PREFIX: str = "failure-"
FAILURE_LOG_TIME_FORMAT: str = "%Y%m%d-%H%M%S-%f"
FAILURE_LOG_RE: re.Pattern[str] = re.compile(re.escape(FAILURE_LOG_PREFIX) + r"\d{8}-\d{6}-\d{6}\.log")
UNSIGNED_MASK: int = 0xFFFFFFFF
# - 안전 수정은 기본 설정으로 실행하며 종료 코드 1은 고치지 못한 위반이 남은 상태(검사 단계가 보고)
RUFF_FIX_LABEL: str = "Ruff 자동 수정"
RUFF_VIOLATIONS_EXIT_CODE: int = 1
TEST_SUMMARY_RE: re.Pattern[str] = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
FAULT_TIMEOUT_RE: re.Pattern[str] = re.compile(r"^Timeout \(\d+:\d{2}:\d{2}\)!", re.MULTILINE)
FAIL_LINE_RE: re.Pattern[str] = re.compile(r"^(?:FAIL|ERROR): .+$", re.MULTILINE)
EXIT_LABELS: dict[int, str] = {
    0x1: "테스트 실패 또는 처리되지 않은 예외",
    0xFFFFFFFF: "바깥에서 강제 종료되었을 수 있음(Stop-Process 등)",
    0xC0000005: "메모리 접근 위반",
    0xC0000409: "중단 호출(STATUS_STACK_BUFFER_OVERRUN)",
}
# - 제한 시간 스택은 샤드 프로세스 안에서 남아야 하므로 래퍼가 faulthandler 타이머를 건 뒤 모듈을 하나씩 실행
# - 모듈별 시간은 표준 출력의 구분 마커 사이 JSON으로 전달. 테스트 샌드박스가 샤드 안의 파일 쓰기를 막음
# - 상세 출력(-v)은 버퍼를 끄고 통과한 테스트의 출력도 남겨, 강제 종료 때 traceback을 잃지 않게 함
SHARD_WRAPPER: str = (
    "import faulthandler, json, sys, time, unittest\n"
    "faulthandler.dump_traceback_later(float(sys.argv[1]), exit=True)\n"
    "unit_flag, *modules = sys.argv[2:]\n"
    "verbose = unit_flag == '-v'\n"
    "timings = {}\n"
    "ok = True\n"
    "for name in modules:\n"
    "    started = time.monotonic()\n"
    "    suite = unittest.defaultTestLoader.loadTestsFromName(name)\n"
    "    result = unittest.TextTestRunner(verbosity=2 if verbose else 1, buffer=not verbose).run(suite)\n"
    "    timings[name] = time.monotonic() - started\n"
    "    ok = ok and result.wasSuccessful()\n"
    "sys.stdout.write('\\nTIMINGS_BEGIN ' + json.dumps(timings) + ' TIMINGS_END\\n')\n"
    "sys.stdout.flush()\n"
    "sys.exit(0 if ok else 1)\n"
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


def default_shard_count(cpu_count: int | None) -> int:
    """논리 코어 수를 나눈 샤드 수(최소 1). 코어 수를 알 수 없으면 1"""
    return max(1, (cpu_count or 1) // SHARD_CORE_DIVISOR)


def parse_timings(output: str) -> dict[str, float]:
    """샤드 출력의 구분 마커 사이 모듈별 시간. 마커가 없으면 빈 매핑"""
    match = TIMINGS_RE.search(output)
    if match is None:
        return {}
    raw: dict[str, float] = json.loads(match.group(1))
    return {name: float(seconds) for name, seconds in raw.items()}


def load_timings(path: pathlib.Path | None) -> dict[str, float]:
    """이전 실행의 모듈별 시간 기록. 없거나 읽을 수 없으면 빈 매핑"""
    if path is None:
        return {}
    try:
        raw: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: float(seconds) for name, seconds in raw.items()}


def module_name(root: str, path: str) -> str:
    """저장소 루트 기준 파일 경로를 점 경로 모듈 이름으로 바꿈(`tests/core/test_x.py`는 `tests.core.test_x`)"""
    relative = pathlib.PurePath(os.path.relpath(path, root)).with_suffix("")
    return ".".join(relative.parts)


def discover_test_modules(root: str) -> dict[str, str]:
    """`tests/` 아래 모든 깊이의 `test_*.py`를 점 경로 모듈 이름에서 파일 경로로 매핑. 도우미 모듈은 이름 규칙으로 제외됨"""
    found = glob.glob(os.path.join(root, "tests", "**", "test_*.py"), recursive=True)
    return {module_name(root, path): path for path in sorted(found)}


def estimate_weights(test_modules: Mapping[str, str], records: Mapping[str, float]) -> dict[str, float]:
    """모듈별 배분 가중치. 기록이 있으면 기록 시간, 없으면 기록된 초당 비율로 파일 크기에서 추정

    - 기록이 하나도 없으면 파일 크기 자체를 가중치로 씀(상대 순서만 쓰므로 단위는 무관)
    """
    sizes = {name: os.path.getsize(path) for name, path in test_modules.items()}
    known = [name for name in sizes if name in records]
    if not known:
        return {name: float(size) for name, size in sizes.items()}
    ratio = sum(records[name] for name in known) / max(1, sum(sizes[name] for name in known))
    return {name: records[name] if name in records else sizes[name] * ratio for name in sizes}


def plan_shards(
    test_modules: Mapping[str, str],
    shard_count: int,
    records: Mapping[str, float] | None = None,
) -> list[list[str]]:
    """가중치 내림차순 탐욕 배분으로 테스트 모듈을 샤드에 나눔. 같은 가중치는 이름순으로 정해 재현 순서를 고정"""
    weights = estimate_weights(test_modules, records or {})
    ordered = sorted(weights, key=lambda name: (-weights[name], name))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    loads: list[float] = [0.0] * shard_count
    for name in ordered:
        target = loads.index(min(loads))
        shards[target].append(name)
        loads[target] += weights[name]
    return [shard for shard in shards if shard]


def shard_command(
    python_exe: str,
    modules: Sequence[str],
    unit_flag: str,
    timeout_sec: int = SHARD_TIMEOUT_SEC,
) -> list[str]:
    """faulthandler 래퍼로 단위 테스트 샤드 하나를 실행하는 명령. 단위 플래그(-b, -v)를 래퍼에 그대로 넘김"""
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


def failure_log_name(now: datetime) -> str:
    """실패 로그 파일 이름. 실행 시각을 마이크로초까지 붙여 같은 이름이 겹치지 않게 함"""
    return FAILURE_LOG_PREFIX + now.strftime(FAILURE_LOG_TIME_FORMAT) + ".log"


def prune_failure_logs(log_dir: pathlib.Path) -> None:
    """실패 로그 이름 형식의 파일 중 최근 LOG_KEEP_COUNT개만 남기고 나머지 삭제. 다른 파일은 건드리지 않음"""
    names = sorted(
        entry.name for entry in log_dir.iterdir() if entry.is_file() and FAILURE_LOG_RE.fullmatch(entry.name)
    )
    for name in names[:-LOG_KEEP_COUNT]:
        with contextlib.suppress(OSError):
            (log_dir / name).unlink()


def save_run_logs(
    last_path: pathlib.Path | None,
    results: Sequence[CommandResult],
    total_sec: float,
    now: datetime,
) -> pathlib.Path | None:
    """전체 로그를 last.log에 덮어쓰고, 실패가 있으면 시각 이름의 실패 로그를 따로 남김. 실패 로그 경로를 반환"""
    if last_path is None:
        return None
    text = render_log(results, total_sec)
    write_log(last_path, text)
    if not any(res.failed for res in results):
        return None
    failure_path = last_path.parent / failure_log_name(now)
    write_log(failure_path, text)
    prune_failure_logs(last_path.parent)
    return failure_path


def venv_script(root: str, name: str) -> str:
    """가상 환경 Scripts 디렉터리 아래 실행 파일 경로"""
    return os.path.join(root, ".venv", "Scripts", name)


def list_ruff_targets(root: str, ruff_exe: str) -> list[str]:
    """Ruff가 검사하는 파일의 절대 경로 목록. Ruff 설정의 제외 규칙을 그대로 따름"""
    completed = subprocess.run(
        [ruff_exe, "check", "--show-files"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return [os.path.join(root, line.strip()) for line in completed.stdout.splitlines() if line.strip()]


def digest_files(paths: Sequence[str]) -> dict[str, bytes]:
    """파일 경로별 내용 해시. 수정 전후 내용 비교에만 쓰므로 해시 함수는 충돌 방지 수준이면 충분"""
    return {path: hashlib.sha256(pathlib.Path(path).read_bytes()).digest() for path in paths}


def changed_files(before: dict[str, bytes], after: dict[str, bytes], root: str) -> list[str]:
    """내용이 달라진 파일의 저장소 기준 슬래시 경로를 정렬해 반환"""
    return sorted(
        pathlib.PurePath(os.path.relpath(path, root)).as_posix()
        for path, digest in after.items()
        if before.get(path) != digest
    )


def apply_ruff_fixes(root: str, ruff_exe: str, env: dict[str, str]) -> tuple[list[str], CommandResult]:
    """Ruff 안전 수정을 실행하고 고쳐진 파일 목록과 실행 결과를 반환

    - 고친 파일은 수정 전후 내용 해시 비교로 찾음. 수정 도구의 출력 형식에 기대지 않기 위함
    """
    targets = list_ruff_targets(root, ruff_exe)
    before = digest_files(targets)
    result = run_command(CommandSpec(label=RUFF_FIX_LABEL, command=[ruff_exe, "check", "--fix"]), root, env)
    after = digest_files(targets)
    return changed_files(before, after, root), result


@dataclass(frozen=True)
class Fingerprint:
    """검증 상태의 두 지문. 작업 트리 내용의 트리 해시와 Python, 패키지 환경의 해시"""

    tree: str
    env: str


@dataclass(frozen=True)
class VerifiedRecord:
    """통과한 검사 한 번의 기록. 통과 시각 라벨, 테스트 수, 지문"""

    verified_at: str
    tests: int
    fingerprint: Fingerprint


def short_hash(tree: str) -> str:
    """사람이 보는 짧은 트리 해시"""
    return tree[:SHORT_HASH_LEN]


def git_text(root: str, args: Sequence[str], env: dict[str, str] | None = None) -> str | None:
    """git 명령의 표준 출력. 저장소가 아니거나 명령이 실패하면 None"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def working_tree_hash(root: str) -> str | None:
    """추적 파일과 무시되지 않은 새 파일의 트리 해시. 커밋 여부와 무관하게 같은 내용이면 같은 값

    - 임시 인덱스는 HEAD에서 만들어 시각 정보가 없는 항목으로 시작하므로 `git add -A`가 모든 파일 내용을 해시함
    - 실제 인덱스의 시각 정보를 복사하면 복사본의 새 수정 시각 때문에 Git의 racy 보호가 꺼져 같은 크기의 수정을 놓칠 수 있음
    - HEAD가 없으면 빈 임시 인덱스로 시작
    - 실제 인덱스는 읽지도 바꾸지도 않음
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        has_head = git_text(root, ["rev-parse", "--verify", "--quiet", "HEAD"]) is not None
        if has_head and git_text(root, ["read-tree", "HEAD"], env) is None:
            return None
        if git_text(root, ["add", "-A"], env) is None:
            return None
        return git_text(root, ["write-tree"], env)


def installed_packages(site_dir: str) -> list[tuple[str, str]]:
    """site-packages의 dist-info 디렉터리 이름에서 패키지 이름과 버전을 읽음. pip을 실행하지 않아 빠름

    - 디렉터리가 없으면 빈 목록(환경 지문은 Python 버전만 반영)
    """
    try:
        with os.scandir(site_dir) as entries:
            found = [entry.name for entry in entries if entry.is_dir() and entry.name.endswith(".dist-info")]
    except OSError:
        return []
    packages: list[tuple[str, str]] = []
    for dist_name in found:
        name, _sep, version = dist_name[: -len(".dist-info")].rpartition("-")
        if name:
            packages.append((name, version))
    return sorted(packages)


def environment_hash(python_version: str, packages: Sequence[tuple[str, str]]) -> str:
    """Python 버전과 패키지 이름, 버전 목록의 해시. 패키지가 바뀌면 달라짐"""
    lines = [python_version, *(f"{name}=={version}" for name, version in sorted(packages))]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def current_fingerprint(root: str) -> Fingerprint | None:
    """작업 트리와 실행 환경의 지문. Git 저장소가 아니면 None"""
    tree = working_tree_hash(root)
    if tree is None:
        return None
    packages = installed_packages(sysconfig.get_paths()["purelib"])
    return Fingerprint(tree=tree, env=environment_hash(sys.version, packages))


def can_record(results: Sequence[CommandResult]) -> bool:
    """전체 검사가 실패나 스킵 없이 통과했는지. 스킵된 테스트가 있으면 기록하지 않음"""
    return not any(res.failed or "skipped=" in res.output for res in results)


def save_verified(verified_dir: pathlib.Path, fingerprint: Fingerprint, tests: int, now: datetime) -> None:
    """통과한 상태를 기록하고 최근 VERIFIED_KEEP_COUNT개만 남김"""
    record = {
        "verified_at": now.strftime(VERIFIED_TIME_LABEL_FORMAT),
        "tests": tests,
        "tree": fingerprint.tree,
        "env": fingerprint.env,
    }
    name = VERIFIED_FILE_PREFIX + now.strftime(VERIFIED_TIME_FORMAT) + ".json"
    write_log(verified_dir / name, json.dumps(record, ensure_ascii=False, indent=1))
    names = sorted(
        entry.name for entry in verified_dir.iterdir() if entry.is_file() and VERIFIED_FILE_RE.fullmatch(entry.name)
    )
    for old in names[:-VERIFIED_KEEP_COUNT]:
        with contextlib.suppress(OSError):
            (verified_dir / old).unlink()


def find_verified(verified_dir: pathlib.Path, fingerprint: Fingerprint) -> VerifiedRecord | None:
    """두 지문이 같은 가장 최근 검증 기록. 기록이 없거나 깨졌으면 None"""
    if not verified_dir.is_dir():
        return None
    for path in sorted(verified_dir.iterdir(), reverse=True):
        if not VERIFIED_FILE_RE.fullmatch(path.name):
            continue
        try:
            raw: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tests = raw.get("tests")
        record = VerifiedRecord(
            verified_at=str(raw.get("verified_at", "")),
            tests=tests if isinstance(tests, int) else 0,
            fingerprint=Fingerprint(tree=str(raw.get("tree", "")), env=str(raw.get("env", ""))),
        )
        if record.fingerprint == fingerprint:
            return record
    return None


def build_specs(
    root: str,
    unit_flag: str,
    records: Mapping[str, float],
) -> tuple[list[CommandSpec], list[CommandSpec]]:
    """정적 검사 세 단계와 단위 테스트 샤드 명령. 샤드 수는 코어 수에서, 배분은 이전 실행의 모듈별 시간에서 정함"""
    python_exe = venv_script(root, "python.exe")
    steps = [
        CommandSpec("[1/4] Running Ruff static linter...", [venv_script(root, "ruff.exe"), "check"]),
        CommandSpec(
            "[2/4] Running BasedPyright strict type checker...",
            [venv_script(root, "basedpyright.exe")],
        ),
        CommandSpec(
            "[3/4] Running repository hygiene and local-leak check...",
            [python_exe, os.path.join(root, "scripts", "verify_hygiene.py")],
        ),
    ]
    shard_count = default_shard_count(os.cpu_count())
    shards = plan_shards(discover_test_modules(root), shard_count, records)
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
    parser.add_argument("--force", action="store_true", help="검증 기록을 무시하고 전체 검사, 통과하면 기록 갱신")
    return parser.parse_args(argv)


def _save_run(last_path: pathlib.Path | None, results: Sequence[CommandResult], total_sec: float) -> str:
    """실행 로그를 저장하고 실패 로그 위치 또는 저장하지 않은 사유를 반환"""
    failure_path = save_run_logs(last_path, results, total_sec, datetime.now())
    return str(failure_path) if failure_path is not None else "저장하지 않음(Git 저장소가 아님)"


def save_timings(
    last_path: pathlib.Path | None,
    records: Mapping[str, float],
    results: Sequence[CommandResult],
) -> None:
    """이번 샤드 출력의 모듈별 시간을 이전 기록에 덮어써 저장. 측정값이 없으면 아무것도 쓰지 않음"""
    if last_path is None:
        return
    measured: dict[str, float] = {}
    for res in results:
        measured.update(parse_timings(res.output))
    if not measured:
        return
    merged = {**records, **measured}
    write_log(last_path.parent / TIMINGS_FILE_NAME, json.dumps(merged, ensure_ascii=False, indent=1))


def main(argv: Sequence[str] | None = None) -> int:
    """Ruff 안전 자동 수정, 지문 확인, 정적 검사, 위생 검사, 단위 테스트 샤드를 실행하고 종료 코드를 반환

    - 자동 수정은 가장 먼저 순차로 실행해, 지문과 이후 검사가 고친 내용을 보게 함
    - 같은 지문의 검증 기록이 있으면 전체 검사 없이 끝냄(`--force`는 기록을 무시)
    """
    args = _parse_args(argv)
    root: str = args.root
    unit_flag = "-v" if args.verbose else "-b"
    env: dict[str, str] = {**os.environ, "PYTHONUTF8": "1"}
    started = time.monotonic()

    last_path = resolve_log_path(root)
    changed, fix_result = apply_ruff_fixes(root, venv_script(root, "ruff.exe"), env)
    if fix_result.returncode not in (0, RUFF_VIOLATIONS_EXIT_CODE):
        log_text = _save_run(last_path, [fix_result], time.monotonic() - started)
        print(format_failure(fix_result, log_text), flush=True)
        return 1
    if changed:
        print(f"Ruff 자동 수정: {', '.join(changed)}", flush=True)

    fingerprint = current_fingerprint(root)
    verified_dir = last_path.parent / VERIFIED_DIR_NAME if last_path is not None else None
    if fingerprint is not None and verified_dir is not None and not args.force:
        verified = find_verified(verified_dir, fingerprint)
        if verified is not None:
            print(
                f"이미 검증됨: {verified.verified_at}, 테스트 {verified.tests}개, 트리 {short_hash(fingerprint.tree)}",
                flush=True,
            )
            return 0

    records = load_timings(last_path.parent / TIMINGS_FILE_NAME if last_path is not None else None)
    step_specs, shard_specs = build_specs(root, unit_flag, records)
    specs = [*step_specs, *shard_specs]
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda spec: run_command(spec, root, env), specs))
    total_sec = time.monotonic() - started

    log_text = _save_run(last_path, results, total_sec)
    save_timings(last_path, records, results[len(step_specs):])

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
    if fingerprint is not None and verified_dir is not None and can_record(results):
        save_verified(verified_dir, fingerprint, total, datetime.now())
    tree_line = f"검증한 트리: {short_hash(fingerprint.tree)}\n" if fingerprint is not None else ""
    print(
        f"Ran {total} tests in {len(shard_results)} parallel shards\n"
        f"Total time: {total_sec:.1f}s\n{tree_line}OK",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
