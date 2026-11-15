"""모듈 줄 수 상한 검사 스크립트

- 하드 상한 초과는 실패, 소프트 상한 초과는 보고
- 기존 초과 파일은 기준선에 동결하고 줄 수 증가만 실패 처리
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

SOFT_LIMIT: int = 500
HARD_LIMIT: int = 800
SCAN_DIRS: tuple[str, ...] = ("src", "scripts")
BASELINE_REL_PATH: str = "scripts/module_size_baseline.json"
BASELINE_FILES_KEY: str = "files"
JSON_INDENT: int = 2


@dataclass(frozen=True)
class SizeReport:
    """줄 수 검사 결과(실패, 보고, 기준선 정리 후보)"""

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


def load_baseline(repo_root: pathlib.Path) -> dict[str, int]:
    """기준선 파일의 경로별 허용 줄 수를 반환, 파일이 없으면 빈 매핑"""
    baseline_path = repo_root / BASELINE_REL_PATH
    if not baseline_path.is_file():
        return {}
    raw: object = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"기준선 형식 오류: {BASELINE_REL_PATH}")
    files = raw.get(BASELINE_FILES_KEY)
    if not isinstance(files, dict):
        raise ValueError(f"기준선 '{BASELINE_FILES_KEY}' 형식 오류: {BASELINE_REL_PATH}")
    baseline: dict[str, int] = {}
    for key, value in files.items():
        if not isinstance(key, str) or not isinstance(value, int):
            raise ValueError(f"기준선 항목 형식 오류: {key!r}")
        baseline[key] = value
    return baseline


def check_sizes(sizes: dict[str, int], baseline: dict[str, int]) -> SizeReport:
    """줄 수를 하드, 소프트 상한과 기준선에 대조해 결과 분류"""
    failures: list[str] = []
    warnings: list[str] = []
    stale: list[str] = []

    for path, lines in sorted(sizes.items()):
        cap = baseline.get(path)
        if lines > HARD_LIMIT:
            failures.append(f"{path}: {lines}줄 (하드 상한 {HARD_LIMIT}줄 초과)")
        elif cap is not None and lines > cap:
            failures.append(f"{path}: {lines}줄 (기준선 {cap}줄 초과)")
        elif cap is not None and lines <= SOFT_LIMIT:
            stale.append(f"{path}: {lines}줄 (소프트 상한 {SOFT_LIMIT}줄 이하, 기준선 제거 가능)")
        elif cap is not None and lines < cap:
            stale.append(f"{path}: {lines}줄 (기준선 {cap}줄 하향 가능)")
        elif cap is None and lines > SOFT_LIMIT:
            warnings.append(f"{path}: {lines}줄 (소프트 상한 {SOFT_LIMIT}줄 초과, 책임 혼재 여부 검토)")

    stale.extend(f"{path}: 파일 없음 (기준선 제거 필요)" for path in sorted(baseline) if path not in sizes)
    return SizeReport(failures=failures, warnings=warnings, stale=stale)


def ratchet_baseline(sizes: dict[str, int], baseline: dict[str, int]) -> dict[str, int]:
    """기준선을 현재 줄 수 이하로만 낮추고 소프트 상한 이하이거나 사라진 파일은 제거"""
    return {
        path: min(cap, sizes[path])
        for path, cap in baseline.items()
        if path in sizes and sizes[path] > SOFT_LIMIT
    }


def write_baseline(repo_root: pathlib.Path, baseline: dict[str, int]) -> None:
    """기준선을 경로 정렬 JSON으로 저장"""
    payload = {BASELINE_FILES_KEY: dict(sorted(baseline.items()))}
    text = json.dumps(payload, ensure_ascii=False, indent=JSON_INDENT) + "\n"
    (repo_root / BASELINE_REL_PATH).write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 모듈 줄 수 상한을 점검하고 필요 시 기준선을 하향"""
    parser = argparse.ArgumentParser(description="모듈 줄 수 상한과 기준선을 점검합니다.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="기준선을 현재 줄 수 이하로 낮춥니다(항목 추가, 상향은 하지 않습니다).",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    sizes = collect_module_sizes(repo_root)
    baseline = load_baseline(repo_root)

    if args.write:
        baseline = ratchet_baseline(sizes, baseline)
        write_baseline(repo_root, baseline)

    report = check_sizes(sizes, baseline)
    for line in report.warnings:
        print(f"[module-size WARN] {line}")
    for line in report.stale:
        print(f"[module-size INFO] {line}")
    for line in report.failures:
        print(f"[module-size FAIL] {line}", file=sys.stderr)
    if report.failures:
        return 1

    print(f"[module-size] OK ({len(sizes)} files, 소프트 초과 보고 {len(report.warnings)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
