"""GUI 시작과 종료에서 진단 준비, 사고 보고서 조립, 표식 닫기를 조율하는 모듈

- 콘솔, GUI, 테스트가 같은 순서로 준비하도록 절차를 한 곳에 둠
- Qt에 의존하지 않으므로 사고 보고서 조립까지 테스트가 직접 실행 가능
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from src.core.diagnostics import native_log, report, session
from src.core.diagnostics.breadcrumbs import (
    BreadcrumbTrail,
    LogBreadcrumbHandler,
    attach_log_mirror,
    breadcrumbs_path,
    detach_log_mirror,
    read_breadcrumb_lines,
)
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
from src.core.diagnostics.session import SessionInfo, session_path, write_session

logger = logging.getLogger("src.diagnostics")


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


def start_diagnostics(
    *,
    directory: str | None = None,
    developer_mode: bool = False,
    now: datetime | None = None,
) -> DiagnosticsRuntime:
    """로그 상한 정리, 네이티브 로그 연결, 예외 훅 설치, 표식 기록을 차례로 수행

    - 이전 실행이 비정상 종료였다면 사고 보고서를 먼저 조립해 두고 알림은 GUI가 띄움
    - 어떤 단계가 실패해도 앱 시작을 막지 않음
    """
    target = ensure_diagnostics_dir(directory)
    runtime = DiagnosticsRuntime(directory=target)
    previous = report.detect_unclean_session(target)
    if previous is not None:
        runtime.pending_report = _build_pending_report(target, previous, now)
    trail_path = breadcrumbs_path(target)
    trim_file_to_tail(trail_path, BREADCRUMBS_MAX_BYTES)
    runtime.trail = BreadcrumbTrail(trail_path)
    runtime.log_mirror = attach_log_mirror(runtime.trail)
    runtime.native_session = native_log.open_native_log(
        diagnostics_file(NATIVE_LOG_NAME, target),
        environment=session.environment_lines(),
        pid=os.getpid(),
        now=now,
    )
    runtime.hooks = install_exception_hooks()
    info = session.start_session(developer_mode, now=now)
    if runtime.native_session is not None:
        info = session.with_native_log_offset(info, runtime.native_session.offset)
    runtime.session_info = info
    write_session(session_path(target), info)
    return runtime


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
    - 훅 원복과 파일 닫기는 각각 실패해도 나머지 단계를 계속함
    """
    if runtime is None:
        return
    info = runtime.session_info
    if info is not None:
        try:
            write_session(session_path(runtime.directory), session.close_session(info, now=now))
        except OSError:
            logger.warning("정상 종료 표식을 기록하지 못했습니다", exc_info=True)
    restore_exception_hooks(runtime.hooks)
    detach_log_mirror(runtime.log_mirror)
    runtime.log_mirror = None
    if runtime.native_session is not None:
        runtime.native_session.close()

