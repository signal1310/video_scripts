from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

FORBIDDEN_EXACT_NAMES: frozenset[str] = frozenset({
    ".env",
    ".rootdir",
    "classify.ipynb",
})

FORBIDDEN_DIR_PARTS: frozenset[str] = frozenset({
    ".venv",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
})

FORBIDDEN_PREFIXES: tuple[str, ...] = (".video_cache",)

FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".pyc",
    ".pyo",
    ".log",
)

SCANNABLE_SUFFIXES: frozenset[str] = frozenset({
    ".py",
    ".ps1",
    ".toml",
    ".json",
    ".txt",
    ".md",
    ".yml",
    ".yaml",
    ".sh",
})

# 패턴 리터럴 자체 오탐 방지를 위해 경계/선행 문맥을 제한
LEAK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Windows 사용자 홈 절대 경로",
        re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'`]+", re.IGNORECASE),
    ),
    (
        "Windows 사용자 홈 슬래시 경로",
        re.compile(r"[A-Za-z]:/Users/[^/\s\"'`]+", re.IGNORECASE),
    ),
    (
        "Unix 사용자 홈 절대 경로",
        re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home)/[A-Za-z0-9._-]+"),
    ),
    (
        "사설 IPv4 주소",
        re.compile(
            r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    (
        "풀 스레드/프로세스 수 하드코딩(max_workers)",
        re.compile(r"(?:Process|Thread)PoolExecutor\([^)]*max_workers\s*="),
    ),
)

WINDOWS_DRIVE_IN_SETTINGS_RE: re.Pattern[str] = re.compile(
    r"[A-Za-z]:[/\\][^\"']+"
)

IGNORE_MARKER: str = "hygiene: ignore"


@dataclass(frozen=True)
class HygieneViolation:
    """저장소 위생 또는 로컬 환경 정보 유출 위반 항목."""

    path: str
    line: int | None
    reason: str
    snippet: str = ""

    def format(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line is not None else self.path
        tail = f" -> {self.snippet}" if self.snippet else ""
        return f"[HYGIENE FAIL] {loc} ({self.reason}){tail}"


def is_forbidden_path(rel_posix_path: str) -> str | None:
    """추적·스테이징되어서는 안 되는 경로인지 검사하고, 위반 사유를 반환."""
    norm = rel_posix_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm.removeprefix("./")
    p = pathlib.PurePosixPath(norm)
    name = p.name

    if norm in FORBIDDEN_EXACT_NAMES or name in FORBIDDEN_EXACT_NAMES:
        return "로컬 환경/플레이그라운드 전용 파일"
    if any(part in FORBIDDEN_DIR_PARTS for part in p.parts):
        return "가상환경 또는 캐시 디렉터리"
    if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return "로컬 영속 캐시 파일"
    if any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return "바이트코드/로그/DB 파일"
    return None


def scan_text(rel_posix_path: str, text: str) -> list[HygieneViolation]:
    """파일 본문에서 로컬 환경 유추 가능한 경로·IP·하드웨어/스레드 하드코딩을 검사."""
    violations: list[HygieneViolation] = []
    norm = rel_posix_path.replace("\\", "/")

    for idx, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARKER in line:
            continue
        for reason, pattern in LEAK_RULES:
            if pattern.search(line):
                violations.append(
                    HygieneViolation(
                        path=norm,
                        line=idx,
                        reason=reason,
                        snippet=line.strip()[:120],
                    )
                )
        if norm == ".vscode/settings.json" and WINDOWS_DRIVE_IN_SETTINGS_RE.search(line):
            violations.append(
                HygieneViolation(
                    path=norm,
                    line=idx,
                    reason="IDE 설정 내 절대 드라이브 경로(${workspaceFolder} 사용 권장)",
                    snippet=line.strip()[:120],
                )
            )
    return violations


def _git_paths(args: list[str]) -> list[str]:
    out = subprocess.check_output(
        ["git", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def collect_target_paths(staged_only: bool) -> list[str]:
    """검사 대상 상대 경로(POSIX 형식) 목록을 수집."""
    if staged_only:
        return _git_paths(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])

    tracked = _git_paths(["ls-files"])
    untracked = _git_paths(["ls-files", "--others", "--exclude-standard"])
    # 중복 제거 및 순서 보존
    seen: set[str] = set()
    merged: list[str] = []
    for rel in [*tracked, *untracked]:
        norm = rel.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            merged.append(norm)
    return merged


def run_hygiene_check(
    repo_root: pathlib.Path,
    staged_only: bool = False,
) -> tuple[list[HygieneViolation], int]:
    """대상 파일 목록에 대해 금지 경로와 로컬 정보 유출 규칙을 검증."""
    paths = collect_target_paths(staged_only=staged_only)
    violations: list[HygieneViolation] = []

    for rel in paths:
        forbidden_reason = is_forbidden_path(rel)
        if forbidden_reason is not None:
            violations.append(HygieneViolation(path=rel, line=None, reason=forbidden_reason))
            continue

        file_path = repo_root / rel
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SCANNABLE_SUFFIXES:
            continue

        text = file_path.read_text(encoding="utf-8", errors="replace")
        violations.extend(scan_text(rel, text))

    return violations, len(paths)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 저장소 위생 및 로컬 환경 정보 유출 여부를 점검."""
    parser = argparse.ArgumentParser(
        description="비대상 파일 및 로컬 환경 정보 유출 여부를 점검합니다."
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Git 스테이징 영역(--cached) 파일만 대상으로 검사합니다.",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    violations, checked_count = run_hygiene_check(repo_root, staged_only=bool(args.staged))

    if violations:
        for item in violations:
            print(item.format(), file=sys.stderr)
        return 1

    scope = "staged" if args.staged else "workspace"
    print(f"[hygiene] OK ({scope}, {checked_count} files verified, 0 violations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
