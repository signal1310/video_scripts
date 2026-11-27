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
from src.core.diagnostics.hooks import (
    HookBundle,
    current_hook_bundle,
    install_exception_hooks,
    restore_exception_hooks,
)
from src.core.diagnostics.native_log import NativeLogSession
from src.core.diagnostics.paths import (
    BREADCRUMBS_MAX_BYTES,
    diagnostics_file,
    ensure_diagnostics_dir,
    instance_native_log_name,
    trim_file_to_tail,
)
from src.core.diagnostics.report import CrashReport
from src.core.diagnostics.session import (
    SessionInfo,
    instance_session_path,
    read_session,
    write_session,
)

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
    marker_path: str | None = None
    pending_report: CrashReport | None = None
    reported_marker: str | None = None
    reported_session: SessionInfo | None = None
    dev_log: DevLogSession | None = None
    # - 이번 시작이 직접 설치한 훅 묶음인지 여부. 중복 설치로 빌려 온 묶음은
    #   시작 실패 정리에서 원복하지 않아 먼저 살아 있는 런타임의 감시를 지키지 않음
    hooks_owned: bool = True


def start_diagnostics(
    *,
    directory: str | None = None,
    developer_mode: bool = False,
    now: datetime | None = None,
    pid: int | None = None,
) -> DiagnosticsRuntime:
    """로그 상한 정리, 네이티브 로그 연결, 예외 훅 설치, 표식 기록을 차례로 수행

    - 죽은 인스턴스가 남긴 표식이 있으면 사고 보고서를 먼저 조립해 두고 알림은 GUI가 띄움
    - 이번 인스턴스도 자기 표식과 네이티브 로그를 따로 남겨 동시 실행 중에도 서로 덮어쓰지 않음
    - 정상 종료한 인스턴스의 표식은 지워 다음 시작이 다시 읽지 않게 함
    - 어떤 단계가 실패해도 앱 시작을 막지 않음
    """
    owner = os.getpid() if pid is None else pid
    target = _guard(lambda: ensure_diagnostics_dir(directory), "진단 폴더 준비", directory or "")
    runtime = DiagnosticsRuntime(directory=target)
    previous = _guard(
        lambda: report.detect_unclean_sessions(target, own_pid=owner), "이전 실행 표식 확인", []
    )
    if previous:
        marker_path, previous_info = previous[-1]
        runtime.pending_report = _build_pending_report(target, previous_info, now)
        if runtime.pending_report is not None:
            runtime.reported_marker = marker_path
            runtime.reported_session = previous_info
    # - 감지보다 먼저 정리하면 미보고 사고의 pid 기반 로그를 같은 pid의 정상 표식 정리가 지울 수 있음
    _guard(lambda: report.clear_finished_sessions(target), "정상 종료 표식 정리", [])
    trail_path = breadcrumbs_path(target)
    _guard(lambda: trim_file_to_tail(trail_path, BREADCRUMBS_MAX_BYTES), "브레드크럼 정리", 0)
    trail = BreadcrumbTrail(trail_path)
    runtime.trail = trail
    runtime.log_mirror = _guard(lambda: attach_log_mirror(trail), "로그 미러 연결", None)
    if developer_mode:
        runtime.dev_log = _guard(lambda: install_dev_log(target), "개발 로그 설치", None)
    native_name = instance_native_log_name(owner)
    runtime.native_session = _guard(
        lambda: native_log.open_native_log(
            diagnostics_file(native_name, target),
            environment=session.environment_lines(),
            pid=owner,
            now=now,
        ),
        "네이티브 로그 연결",
        None,
    )
    already_installed = _guard(current_hook_bundle, "예외 훅 상태 확인", None)
    runtime.hooks = _guard(install_exception_hooks, "예외 훅 설치", HookBundle())
    # - 이미 설치된 묶음을 그대로 돌려받았으면 이번 시작 소유가 아니라, 정리할 때 원복하지 않음
    runtime.hooks_owned = runtime.hooks is not already_installed
    try:
        info = session.start_session(developer_mode, now=now, pid=owner)
        native_session = runtime.native_session
        if native_session is not None:
            info = session.with_native_log_offset(
                info, native_session.offset, native_log_name=native_name
            )
        runtime.session_info = info
        runtime.marker_path = instance_session_path(owner, target)
        _guard(lambda: write_session(runtime.marker_path or "", info), "실행 표식 기록", None)
    except Exception:
        # - 여기서 실패하면 런타임을 돌려주지 않으므로 종료 정리가 닿지 않아, 설치한
        #   로그 미러와 예외 훅을 이 자리에서 되돌려야 프로세스에 남지 않음
        _abort_startup(runtime)
        raise
    # - 보고한 표식이 방금 쓴 자기 표식과 같은 파일이면(같은 pid 재사용) 지우지 않음
    if (
        runtime.reported_marker is not None
        and runtime.reported_session is not None
        and runtime.reported_marker != runtime.marker_path
    ):
        reported_marker = runtime.reported_marker
        reported_session = runtime.reported_session
        _guard(
            lambda: report.discard_reported_session(target, reported_marker, reported_session),
            "보고한 표식 정리",
            None,
        )
    return runtime


def _abort_startup(runtime: DiagnosticsRuntime) -> None:
    """시작 도중 실패했을 때 이미 설치한 진단 자원을 이 자리에서 되돌림

    - 런타임을 돌려주지 못하면 종료 정리가 불리지 않으므로, 예외 훅과 로그 미러,
      개발 로그, 네이티브 로그를 여기서 놓아야 같은 프로세스에 남지 않음
    - 되돌리기 한 단계가 실패해도 나머지를 계속 시도함
    - 중복 설치로 빌려 온 묶음은 다른 런타임이 아직 쓰고 있으므로 원복하지 않음
    """
    if runtime.hooks_owned:
        _guard(lambda: restore_exception_hooks(runtime.hooks), "예외 훅 원복", None)
    _guard(lambda: detach_log_mirror(runtime.log_mirror), "로그 미러 해제", None)
    runtime.log_mirror = None
    _guard(lambda: remove_dev_log(runtime.dev_log), "개발 로그 해제", None)
    runtime.dev_log = None
    native_session = runtime.native_session
    if native_session is not None:
        _guard(native_session.close, "네이티브 로그 닫기", None)


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

    - 자기 표식만 닫아 다른 인스턴스의 사고를 감추지 않음
    - 훅 원복과 파일 닫기는 각각 실패해도 나머지 단계를 계속함
    - 표식을 먼저 clean으로 쓰면 그 창에서 난 네이티브 스택이 보고 없이 사라지므로
      faulthandler를 닫은 뒤에 기록함
    """
    if runtime is None:
        return
    # - 중복 설치로 빌려 온 묶음은 원래 런타임이 원복하므로 여기서 건드리지 않음
    if runtime.hooks_owned:
        restore_exception_hooks(runtime.hooks)
    detach_log_mirror(runtime.log_mirror)
    runtime.log_mirror = None
    remove_dev_log(runtime.dev_log)
    runtime.dev_log = None
    if runtime.native_session is not None:
        runtime.native_session.close()
    info = runtime.session_info
    if info is not None and runtime.marker_path is not None:
        path = runtime.marker_path
        if _owns_session_marker(path, info.pid):
            try:
                write_session(path, session.close_session(info, now=now))
            except OSError:
                logger.warning("정상 종료 표식을 기록하지 못했습니다", exc_info=True)


def _owns_session_marker(path: str, pid: int) -> bool:
    """표식이 이 프로세스 소유인지 확인. 표식이 없으면 쓸 수 있으므로 True"""
    current = read_session(path)
    return current is None or current.pid == pid

