"""비정상 종료 흔적을 모아 사고 폴더와 보고서 문구를 만드는 모듈

- 다음 시작 때 이전 실행 표식이 닫히지 않았으면 로그 꼬리, 네이티브 스택, 표식, 브레드크럼을 한 폴더에 모음
- 보고서는 사용자가 바로 이해할 수 있게 한국어 존댓말로 쓰고, 로컬 경로가 들어갈 수 있음을 머리글에 밝힘
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime

from src.core.diagnostics.paths import (
    APP_LOG_NAME,
    MAX_KEPT_REPORTS,
    NATIVE_LOG_NAME,
    REPORT_TAIL_LINES,
    SESSION_FILENAME,
    create_report_dir,
    diagnostics_file,
    prune_reports,
    read_span,
    read_tail_lines,
)
from src.core.diagnostics.session import SessionInfo, read_session
from src.core.paths import write_utf8_file

PREVIOUS_SESSION_COPY_NAME: str = "previous_session.json"
REPORT_TEXT_NAME: str = "report.txt"
NATIVE_TAIL_COPY_NAME: str = "native_crash_tail.log"
APP_LOG_TAIL_COPY_NAME: str = "app_tail.log"
BREADCRUMBS_COPY_NAME: str = "breadcrumbs_tail.log"
NATIVE_SPAN_MAX_BYTES: int = 256 * 1024
# - 보고서 머리글: 사용자가 폴더를 공유할 때 로컬 경로가 들어 있을 수 있음을 알림
REPORT_HEADER: str = (
    "이전 실행이 정상적으로 끝나지 않아 자동으로 모은 진단 보고서입니다.\n"
    "이 폴더에는 로컬 파일 경로와 실행 환경 정보가 들어 있을 수 있으므로, "
    "외부에 공유할 때 내용을 확인해 주십시오.\n"
)


@dataclass(frozen=True)
class CrashReport:
    """만들어진 사고 보고서 폴더와 보고서 파일 경로"""

    directory: str
    report_path: str


def detect_unclean_session(directory: str) -> SessionInfo | None:
    """이전 실행 표식이 정상 종료로 닫히지 않았으면 그 표식 반환"""
    previous = read_session(diagnostics_file(SESSION_FILENAME, directory))
    if previous is None or previous.is_clean:
        return None
    return previous


def build_report_text(
    previous: SessionInfo,
    *,
    now: datetime,
    app_tail: list[str],
    native_lines: list[str],
    breadcrumbs: list[str],
    folder: str,
) -> str:
    """사고 요약, 시각, 마지막 로그 줄을 담은 보고서 본문 생성"""
    lines = [REPORT_HEADER.rstrip(), ""]
    lines.append("무엇이 있었나: 이전 실행이 정상 종료 표시를 남기지 않고 끝났습니다.")
    lines.append(f"이전 실행 시작: {previous.started_at or '알 수 없음'}")
    lines.append(f"이전 실행 종료 표시: {previous.ended_at or '없음(비정상 종료)'}")
    lines.append(f"이전 실행 PID: {previous.pid} / 앱 버전: {previous.version or '알 수 없음'}")
    lines.append(f"보고서 만든 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"보고서 폴더: {folder}")
    lines.append("")
    lines.extend(_section("마지막 앱 로그", app_tail))
    lines.extend(_section("네이티브 스택 로그", native_lines))
    lines.extend(_section("사용자 동작 기록", breadcrumbs))
    return "\n".join(lines) + "\n"


def _section(title: str, body: list[str]) -> list[str]:
    """제목과 본문 줄 목록을 만듬. 본문이 없으면 안내 한 줄"""
    lines = [f"[{title}]"]
    lines.extend(body if body else ["기록이 없습니다."])
    lines.append("")
    return lines


def assemble_crash_report(
    directory: str,
    previous: SessionInfo,
    *,
    breadcrumbs: list[str] | None = None,
    now: datetime | None = None,
) -> CrashReport:
    """이전 실행 흔적을 새 사고 폴더에 복사하고 보고서 본문을 씀

    - 보고서 폴더는 진단 폴더 아래에만 만들고, 지난 보고서가 상한을 넘으면 오래된 것부터 지움
    - 원본 로그는 자르지 않고 읽기만 해 이후 수집을 방해하지 않음
    """
    stamp = now or datetime.now()
    app_tail = read_tail_lines(diagnostics_file(APP_LOG_NAME, directory), REPORT_TAIL_LINES)
    native_lines = _native_session_lines(
        diagnostics_file(NATIVE_LOG_NAME, directory), previous
    )
    folder = create_report_dir(directory, stamp)
    _copy_previous_session(directory, folder)
    _write_report_files(folder, app_tail, native_lines, breadcrumbs or [])
    report_path = os.path.join(folder, REPORT_TEXT_NAME)
    write_utf8_file(
        report_path,
        build_report_text(
            previous,
            now=stamp,
            app_tail=app_tail,
            native_lines=native_lines,
            breadcrumbs=breadcrumbs or [],
            folder=folder,
        ),
    )
    prune_reports(directory, MAX_KEPT_REPORTS)
    return CrashReport(directory=folder, report_path=report_path)


def _native_session_lines(native_log: str, previous: SessionInfo) -> list[str]:
    """이전 실행이 쓴 네이티브 로그 구간을 읽음. 시작 위치를 모르면 꼬리로 대체"""
    if previous.native_log_offset > 0:
        raw = read_span(native_log, previous.native_log_offset, max_bytes=NATIVE_SPAN_MAX_BYTES)
        text = raw.decode("utf-8", "replace").strip()
        return text.splitlines() if text else []
    return read_tail_lines(native_log, REPORT_TAIL_LINES, max_bytes=NATIVE_SPAN_MAX_BYTES)


def _copy_previous_session(directory: str, folder: str) -> None:
    """이전 실행 표식을 사고 폴더에 복사. 원본 표식은 다음 실행이 덮어씀"""
    source = diagnostics_file(SESSION_FILENAME, directory)
    if not os.path.isfile(source):
        return
    try:
        shutil.copyfile(source, os.path.join(folder, PREVIOUS_SESSION_COPY_NAME))
    except OSError:
        return


def _write_report_files(
    folder: str, app_tail: list[str], native_lines: list[str], breadcrumbs: list[str]
) -> None:
    """로그 꼬리, 네이티브 구간, 브레드크럼을 사고 폴더의 개별 파일로 기록"""
    targets = (
        (APP_LOG_TAIL_COPY_NAME, app_tail),
        (NATIVE_TAIL_COPY_NAME, native_lines),
        (BREADCRUMBS_COPY_NAME, breadcrumbs),
    )
    for name, lines in targets:
        body = "\n".join(lines) + "\n" if lines else ""
        try:
            write_utf8_file(os.path.join(folder, name), body)
        except OSError:
            continue
