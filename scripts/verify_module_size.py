"""모듈 줄 수 상한 검사 스크립트

- 소프트 상한 초과는 보고만 함
- 하드 상한 초과는 실패. 화이트리스트(exempt)에 등록된 파일만 예외
- 동결(frozen) 파일은 기록된 상한을 넘을 수 없고, --write는 상한만 낮춤
- 화이트리스트 등록과 기록 줄 수 갱신은 사용자 승인으로만 수행하며 스크립트는 항목을 자동 추가하지 않음
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field

SOFT_LIMIT: int = 500
HARD_LIMIT: int = 1000
REVIEW_GROWTH_PERCENT: int = 20
SCAN_DIRS: tuple[str, ...] = ("src", "scripts")
EXCEPTIONS_REL_PATH: str = "scripts/module_size_exceptions.json"
FROZEN_KEY: str = "frozen"
EXEMPT_KEY: str = "exempt"
JSON_INDENT: int = 2


@dataclass(frozen=True)
class ExemptEntry:
    """화이트리스트 항목(분류, 등록 사유, 검토 당시 줄 수)"""

    category: str
    reason: str
    lines: int


@dataclass(frozen=True)
class Exceptions:
    """예외 파일: 동결 상한(경로별 최대 줄 수)과 화이트리스트 항목"""

    frozen: dict[str, int] = field(default_factory=dict)
    exempt: dict[str, ExemptEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class SizeReport:
    """줄 수 검사 결과(실패, 보고, 예외 파일 정리 후보)"""

    failures: list[str]
    warnings: list[str]
    stale: list[str]


def count_lines(file_path: pathlib.Path) -> int:
    """파일의 물리적 줄 수(빈 줄, 주석 포함) 반환"""
    return len(file_path.read_text(encoding="utf-8", errors="replace").splitlines())


def collect_module_sizes(repo_root: pathlib.Path) -> dict[str, int]:
    """검사 대상 디렉터리의 `.py` 파일별 줄 수를 POSIX 상대 경로 기준으로 수집"""
    sizes: dict[str, int] = {}
    for scan_dir in SCAN_DIRS:
        for file_path in sorted((repo_root / scan_dir).rglob("*.py")):
            if "__pycache__" in file_path.parts:
                continue
            sizes[file_path.relative_to(repo_root).as_posix()] = count_lines(file_path)
    return sizes


def _require_str_map(raw: object, key: str) -> dict[str, object]:
    if not isinstance(raw, dict) or not all(isinstance(name, str) for name in raw):
        raise ValueError(f"예외 파일 '{key}' 형식 오류: {EXCEPTIONS_REL_PATH}")
    return dict(raw)


def _parse_exempt_entry(path: str, value: object) -> ExemptEntry:
    if not isinstance(value, dict):
        raise ValueError(f"화이트리스트 항목 형식 오류: {path}")
    category = value.get("category")
    reason = value.get("reason")
    lines = value.get("lines")
    if not isinstance(category, str) or not category:
        raise ValueError(f"화이트리스트 분류 누락: {path}")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"화이트리스트 등록 사유 누락: {path}")
    if not isinstance(lines, int) or isinstance(lines, bool):
        raise ValueError(f"화이트리스트 기록 줄 수 누락: {path}")
    return ExemptEntry(category=category, reason=reason, lines=lines)


def load_exceptions(repo_root: pathlib.Path) -> Exceptions:
    """예외 파일 파일을 읽고, 파일이 없으면 빈 목록 반환"""
    path = repo_root / EXCEPTIONS_REL_PATH
    if not path.is_file():
        return Exceptions()
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"예외 파일 형식 오류: {EXCEPTIONS_REL_PATH}")

    frozen_raw = _require_str_map(raw.get(FROZEN_KEY, {}), FROZEN_KEY)
    frozen: dict[str, int] = {}
    for file_path, cap in frozen_raw.items():
        if not isinstance(cap, int) or isinstance(cap, bool):
            raise ValueError(f"동결 기준선 형식 오류: {file_path}")
        frozen[file_path] = cap

    exempt_raw = _require_str_map(raw.get(EXEMPT_KEY, {}), EXEMPT_KEY)
    exempt = {file_path: _parse_exempt_entry(file_path, value) for file_path, value in exempt_raw.items()}
    return Exceptions(frozen=frozen, exempt=exempt)


def _exceeds_review_growth(lines: int, recorded: int) -> bool:
    """기록 줄 수 대비 허용 증가율을 넘었는지 정수 연산으로 판별"""
    return lines * 100 > recorded * (100 + REVIEW_GROWTH_PERCENT)


def check_sizes(sizes: dict[str, int], exceptions: Exceptions) -> SizeReport:
    """줄 수를 상한, 동결 기준선, 화이트리스트와 대조해 결과 분류"""
    failures: list[str] = []
    warnings: list[str] = []
    stale: list[str] = []

    for path, lines in sorted(sizes.items()):
        exempt = exceptions.exempt.get(path)
        if exempt is not None:
            if _exceeds_review_growth(lines, exempt.lines):
                warnings.append(
                    f"{path}: {lines}줄 (화이트리스트 기록 {exempt.lines}줄 대비 "
                    f"{REVIEW_GROWTH_PERCENT}% 초과 증가, 재검토 필요)"
                )
            elif lines <= SOFT_LIMIT:
                stale.append(f"{path}: {lines}줄 (소프트 상한 이하, 화이트리스트 해제 가능)")
            continue

        cap = exceptions.frozen.get(path)
        if cap is not None:
            if lines > cap:
                failures.append(f"{path}: {lines}줄 (동결 기준선 {cap}줄 초과)")
            elif lines < cap:
                stale.append(f"{path}: {lines}줄 (동결 기준선 {cap}줄 하향 가능)")
            continue

        if lines > HARD_LIMIT:
            failures.append(f"{path}: {lines}줄 (하드 상한 {HARD_LIMIT}줄 초과, 화이트리스트 등록 필요)")
        elif lines > SOFT_LIMIT:
            warnings.append(f"{path}: {lines}줄 (소프트 상한 {SOFT_LIMIT}줄 초과, 책임 혼재 여부 검토)")

    registered = set(exceptions.frozen) | set(exceptions.exempt)
    stale.extend(f"{path}: 파일 없음 (예외 파일에서 제거 필요)" for path in sorted(registered) if path not in sizes)
    return SizeReport(failures=failures, warnings=warnings, stale=stale)


def ratchet_frozen(sizes: dict[str, int], frozen: dict[str, int]) -> dict[str, int]:
    """동결 기준선을 현재 줄 수 이하로만 낮추고, 파일이 사라진 항목만 제거"""
    return {path: min(cap, sizes[path]) for path, cap in frozen.items() if path in sizes}


def write_exceptions(repo_root: pathlib.Path, exceptions: Exceptions) -> None:
    """예외 파일을 경로 정렬 JSON으로 저장"""
    payload = {
        FROZEN_KEY: dict(sorted(exceptions.frozen.items())),
        EXEMPT_KEY: {
            path: {"category": entry.category, "reason": entry.reason, "lines": entry.lines}
            for path, entry in sorted(exceptions.exempt.items())
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=JSON_INDENT) + "\n"
    (repo_root / EXCEPTIONS_REL_PATH).write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 모듈 줄 수 상한을 점검하고 필요 시 동결 기준선을 하향"""
    parser = argparse.ArgumentParser(description="모듈 줄 수 상한과 예외 파일을 점검합니다.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="동결 기준선을 현재 줄 수 이하로 낮춥니다(항목 추가, 상향, 화이트리스트 변경은 하지 않습니다).",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    sizes = collect_module_sizes(repo_root)
    exceptions = load_exceptions(repo_root)

    if args.write:
        exceptions = Exceptions(
            frozen=ratchet_frozen(sizes, exceptions.frozen),
            exempt=exceptions.exempt,
        )
        write_exceptions(repo_root, exceptions)

    report = check_sizes(sizes, exceptions)
    for line in report.warnings:
        print(f"[module-size WARN] {line}")
    for line in report.stale:
        print(f"[module-size INFO] {line}")
    for line in report.failures:
        print(f"[module-size FAIL] {line}", file=sys.stderr)
    if report.failures:
        return 1

    print(
        f"[module-size] OK ({len(sizes)} files, 소프트 초과 보고 {len(report.warnings)}건, "
        f"화이트리스트 {len(exceptions.exempt)}건, 동결 {len(exceptions.frozen)}건)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
