"""사용자가 요청할 때 로그 꼬리와 실행 정보를 zip 하나로 묶는 진단 보고서 모듈

- 비정상 종료와 무관하게 언제든 만들 수 있으며 원본 로그는 자르지 않고 읽기만 함
- zip에는 로컬 경로가 들어갈 수 있으므로 머리글에서 공유 전 확인을 안내
- 보관 개수를 넘으면 오래된 zip부터 지워 데이터 폴더가 무한히 커지지 않게 함
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime

from src.core.diagnostics.paths import (
    APP_LOG_NAME,
    BREADCRUMBS_NAME,
    DEV_LOG_NAME,
    NATIVE_LOG_INSTANCE_PREFIX,
    NATIVE_LOG_NAME,
    NATIVE_LOG_SUFFIX,
    REPORT_TAIL_LINES,
    SESSION_FILENAME,
    diagnostics_file,
    read_tail_lines,
)
from src.core.diagnostics.session import environment_lines, existing_session_markers
from src.core.paths import write_utf8_file

REPORT_STEM: str = "diagnostic_"
REPORT_TIME_FORMAT: str = "%Y%m%d-%H%M%S"
REPORT_SUFFIX: str = ".zip"
MAX_KEPT_ARCHIVES: int = 5
SUMMARY_NAME: str = "report.txt"
APP_TAIL_NAME: str = "app_tail.log"
DEV_TAIL_NAME: str = "dev_tail.log"
BREADCRUMBS_TAIL_NAME: str = "breadcrumbs_tail.log"
NATIVE_TAIL_NAME: str = "native_crash_tail.log"
REPORT_HEADER: str = (
    "사용자가 요청해 만든 진단 보고서입니다.\n"
    "이 zip에는 로컬 파일 경로와 실행 환경 정보가 들어 있을 수 있으므로, "
    "외부에 공유할 때 내용을 확인해 주십시오.\n"
)


@dataclass(frozen=True)
class DiagnosticReport:
    """만들어진 진단 보고서 zip의 폴더와 파일 경로"""

    folder: str
    archive: str


def report_archive_name(when: datetime) -> str:
    """시각 기준 zip 파일 이름 생성"""
    return f"{REPORT_STEM}{when.strftime(REPORT_TIME_FORMAT)}{REPORT_SUFFIX}"


def resolve_archive_path(directory: str, when: datetime) -> str:
    """같은 이름의 zip이 있으면 뒤에 번호를 붙인 경로 반환"""
    base = os.path.join(directory, report_archive_name(when))
    candidate = base
    suffix = 2
    while os.path.exists(candidate):
        stem, ext = os.path.splitext(base)
        candidate = f"{stem}_{suffix}{ext}"
        suffix += 1
    return candidate


def format_report_text(*, now: datetime, archive: str) -> str:
    """보고서 요약 본문 생성: 만든 시각, zip 경로, 실행 환경"""
    lines = [REPORT_HEADER.rstrip(), ""]
    lines.append(f"만든 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"진단 파일: {os.path.basename(archive)}")
    lines.append("")
    lines.append("[실행 환경]")
    lines.extend(environment_lines())
    return "\n".join(lines) + "\n"


def collected_files(directory: str, *, now: datetime, archive: str) -> dict[str, str]:
    """zip에 넣을 파일 이름별 본문 생성. 없는 로그는 빈 본문으로 둠

    - 인스턴스별 실행 표식과 네이티브 로그도 파일 이름 그대로 모아 동시 실행 사고 자료를 남김
    """
    tails = [
        (APP_TAIL_NAME, diagnostics_file(APP_LOG_NAME, directory)),
        (DEV_TAIL_NAME, diagnostics_file(DEV_LOG_NAME, directory)),
        (BREADCRUMBS_TAIL_NAME, diagnostics_file(BREADCRUMBS_NAME, directory)),
        (NATIVE_TAIL_NAME, diagnostics_file(NATIVE_LOG_NAME, directory)),
    ]
    files = {SUMMARY_NAME: format_report_text(now=now, archive=archive)}
    for name, path in tails:
        files[name] = _lines_to_text(read_tail_lines(path, REPORT_TAIL_LINES))
    files[SESSION_FILENAME] = _read_text(diagnostics_file(SESSION_FILENAME, directory))
    for path in existing_session_markers(directory):
        name = os.path.basename(path)
        if name not in files:
            files[name] = _read_text(path)
    for name in _instance_native_log_names(directory):
        files[name] = _lines_to_text(
            read_tail_lines(diagnostics_file(name, directory), REPORT_TAIL_LINES)
        )
    return files


def _instance_native_log_names(directory: str) -> list[str]:
    """진단 폴더에 있는 인스턴스별 네이티브 로그 파일 이름 목록"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(
        name
        for name in names
        if name.startswith(NATIVE_LOG_INSTANCE_PREFIX) and name.endswith(NATIVE_LOG_SUFFIX)
    )


def _lines_to_text(lines: list[str]) -> str:
    """줄 목록을 개행으로 이은 본문으로 변환. 비면 빈 문자열"""
    return "\n".join(lines) + "\n" if lines else ""


def _read_text(path: str) -> str:
    """작은 텍스트 파일 본문 반환. 읽지 못하면 빈 문자열"""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def create_diagnostic_report(
    directory: str, *, now: datetime | None = None
) -> DiagnosticReport:
    """로그 꼬리와 실행 정보를 모아 zip으로 만들고 보관 개수를 정리

    - zip은 진단 폴더 안에만 만들고, 임시 폴더에 모은 파일을 넣은 뒤 지움
    - 원본 로그는 자르거나 옮기지 않음
    """
    stamp = now or datetime.now()
    os.makedirs(directory, exist_ok=True)
    archive = resolve_archive_path(directory, stamp)
    files = collected_files(directory, now=stamp, archive=archive)
    staging = tempfile.mkdtemp(dir=directory, prefix=".report_")
    try:
        for name, body in files.items():
            write_utf8_file(os.path.join(staging, name), body)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name in files:
                bundle.write(os.path.join(staging, name), arcname=name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    prune_diagnostic_archives(directory, MAX_KEPT_ARCHIVES)
    return DiagnosticReport(folder=directory, archive=archive)


def list_archives(directory: str) -> list[str]:
    """진단 폴더의 보고서 zip 목록을 이름(시간) 오름차순으로 반환"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = [
        os.path.join(directory, name)
        for name in names
        if name.startswith(REPORT_STEM) and name.endswith(REPORT_SUFFIX)
    ]
    return sorted(found, key=lambda path: os.path.basename(path))


def prune_diagnostic_archives(directory: str, keep: int = MAX_KEPT_ARCHIVES) -> list[str]:
    """오래된 zip을 지워 keep개만 남기고 지운 경로 목록 반환"""
    archives = list_archives(directory)
    if keep < 0 or len(archives) <= keep:
        return []
    removable = archives[: len(archives) - keep] if keep else archives
    removed: list[str] = []
    for path in removable:
        with contextlib.suppress(OSError):
            os.remove(path)
            removed.append(path)
    return removed
