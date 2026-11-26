"""GUI 시작과 종료에서 진단 준비, 사고 보고서 조립, 표식 닫기를 조율하는 모듈

- 콘솔, GUI, 테스트가 같은 순서로 준비하도록 절차를 한 곳에 둠
- Qt에 의존하지 않으므로 사고 보고서 조립까지 테스트가 직접 실행 가능
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar

from src.core.diagnostics import native_log, report, session
from src.core.diagnostics.breadcrumbs import (
    BreadcrumbTrail,
    LogBreadcrumbHandler,
    attach_log_mirror,
    breadcrumbs_path,
    detach_log_mirror,
    read_breadcrumb_lines,
)
from src.core.diagnostics.dev_logging import DevLogSession, install_dev_log, remove_dev_log
from src.core.diagnostics.hooks import HookBundle, install_exception_hooks, restore_exception_hooks
from src.core.diagnostics.native_log import NativeLogSession
from src.core.diagnostics.paths import (
    BREADCRUMBS_MAX_BYTES,
    NATIVE_LOG_NAME,
    diagnostics_file,
    ensure_diagnostics_dir,
    trim_file_to_tail,
)
from src.core.diagnostics.report import CrashReport
from src.core.diagnostics.session import SessionInfo, read_session, session_path, write_session

logger = logging.getLogger("src.diagnostics")

_StepResult = TypeVar("_StepResult")


@dataclass
class DiagnosticsRuntime:
    """이번 실행의 진단 상태. 수명 동안 유지해야 faulthandler 파일이 닫히지 않음"""

    directory: str
    native_session: NativeLogSession | None = None
    hooks: HookBundle = field(default_factory=HookBundle)
    trail: BreadcrumbTrail | None = None
    log_mirror: LogBreadcrumbHandler | None = None
    session_info: SessionInfo | None = None
    pending_report: CrashReport | None = None
    dev_log: DevLogSession | None = None


def start_diagnostics(
    *,
    directory: str | None = None,
    developer_mode: bool = False,
    now: datetime | None = None,
) -> DiagnosticsRuntime:
    """로그 상한 정리, 네이티브 로그 연결, 예외 훅 설치, 표식 기록을 차례로 수행

    - 이전 실행이 비정상 종료였다면 사고 보고서를 먼저 조립해 두고 알림은 GUI가 띄움
    - 개발자 모드면 `dev.log` 상세 로그와 명령 기록 미러를 함께 연결
    - 어떤 단계가 실패해도 앱 시작을 막지 않음
    """
    target = _guard(lambda: ensure_diagnostics_dir(directory), "진단 폴더 준비", directory or "")
    runtime = DiagnosticsRuntime(directory=target)
    foreign_running = _guard(
        lambda: report.other_instance_running(target), "다른 인스턴스 확인", False
    )
    previous = _guard(lambda: report.detect_unclean_session(target), "이전 실행 표식 확인", None)
    if previous is not None:
        runtime.pending_report = _build_pending_report(target, previous, now)
    trail_path = breadcrumbs_path(target)
    _guard(lambda: trim_file_to_tail(trail_path, BREADCRUMBS_MAX_BYTES), "브레드크럼 정리", 0)
    trail = BreadcrumbTrail(trail_path)
    runtime.trail = trail
    runtime.log_mirror = _guard(lambda: attach_log_mirror(trail), "로그 미러 연결", None)
    if developer_mode:
        runtime.dev_log = _guard(lambda: install_dev_log(target), "개발 로그 설치", None)
    runtime.native_session = _guard(
        lambda: native_log.open_native_log(
            diagnostics_file(NATIVE_LOG_NAME, target),
            environment=session.environment_lines(),
            pid=os.getpid(),
            now=now,
        ),
        "네이티브 로그 연결",
        None,
    )
    runtime.hooks = _guard(install_exception_hooks, "예외 훅 설치", HookBundle())
    info = session.start_session(developer_mode, now=now)
    native_session = runtime.native_session
    if native_session is not None:
        info = session.with_native_log_offset(info, native_session.offset)
    runtime.session_info = info
    if foreign_running:
        # - 다른 인스턴스의 표식을 덮어쓰면 그 인스턴스의 사고를 다음 시작이 알아채지 못함
        logger.info("다른 인스턴스가 실행 중이어서 실행 표식을 남기지 않습니다")
    else:
        _guard(lambda: write_session(session_path(target), info), "실행 표식 기록", None)
    return runtime


def _guard(step: Callable[[], _StepResult], action: str, fallback: _StepResult) -> _StepResult:
    """진단 단계 하나를 실행하고 실패하면 기록만 남긴 뒤 대체값 반환

    - 진단 준비 실패가 앱 시작을 막지 않아야 한다는 계약을 한 곳에서 지킴
    """
    try:
        return step()
    except Exception:
        logger.warning("진단 단계를 건너뜁니다: %s", action, exc_info=True)
        return fallback


def _build_pending_report(
    target: str, previous: SessionInfo, now: datetime | None
) -> CrashReport | None:
    """이전 실행 사고 보고서를 만들고 실패하면 None. 조립 실패가 시작을 막지 않게 함"""
    try:
        return report.assemble_crash_report(
            target, previous, breadcrumbs=read_breadcrumb_lines(breadcrumbs_path(target)), now=now
        )
    except OSError:
        logger.warning("이전 실행 진단 보고서를 만들지 못했습니다", exc_info=True)
        return None


def record_breadcrumb(runtime: DiagnosticsRuntime | None, category: str, message: str) -> None:
    """런타임이 준비된 경우 사용자 동작 기록을 남김. 없으면 조용히 넘김"""
    if runtime is None or runtime.trail is None:
        return
    runtime.trail.add(category, message)


def shutdown_diagnostics(
    runtime: DiagnosticsRuntime | None, *, now: datetime | None = None
) -> None:
    """정상 종료 표식 기록, 예외 훅 원복, 네이티브 로그 닫기를 수행

    - 표식을 먼저 닫아 다음 시작이 정상 종료로 판단하게 함
    - 표식이 다른 인스턴스 소유면 닫지 않음(그 인스턴스의 사고를 감추지 않기 위함)
    - 훅 원복과 파일 닫기는 각각 실패해도 나머지 단계를 계속함
    """
    if runtime is None:
        return
    info = runtime.session_info
    if info is not None:
        path = session_path(runtime.directory)
        if _owns_session_marker(path, info.pid):
            try:
                write_session(path, session.close_session(info, now=now))
            except OSError:
                logger.warning("정상 종료 표식을 기록하지 못했습니다", exc_info=True)
    restore_exception_hooks(runtime.hooks)
    detach_log_mirror(runtime.log_mirror)
    runtime.log_mirror = None
    remove_dev_log(runtime.dev_log)
    runtime.dev_log = None
    if runtime.native_session is not None:
        runtime.native_session.close()


def _owns_session_marker(path: str, pid: int) -> bool:
    """표식이 이 프로세스 소유인지 확인. 표식이 없으면 쓸 수 있으므로 True"""
    current = read_session(path)
    return current is None or current.pid == pid

