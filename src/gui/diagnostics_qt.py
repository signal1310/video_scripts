"""GUI 프로세스에서 진단 런타임과 Qt 메시지 핸들러, 종료 처리를 잇는 어댑터

- Qt에 의존하는 부분만 이 모듈에 두고, 파일과 표식 처리는 core의 진단 모듈에 맡김
- Qt 메시지 핸들러는 진단 로거로 보낸 뒤 기존 핸들러(기본 stderr)로 그대로 넘겨 출력을 잃지 않음
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QCoreApplication, QtMsgType, qInstallMessageHandler

from src.core.diagnostics.breadcrumbs import BreadcrumbTrail
from src.core.diagnostics.hooks import build_qt_message_context, log_qt_message
from src.core.diagnostics.runtime import (
    DiagnosticsRuntime,
    record_breadcrumb,
    shutdown_diagnostics,
    start_diagnostics,
)

QT_MSG_LEVELS: dict[QtMsgType, int] = {
    QtMsgType.QtDebugMsg: 0,
    QtMsgType.QtInfoMsg: 1,
    QtMsgType.QtWarningMsg: 2,
    QtMsgType.QtCriticalMsg: 3,
    QtMsgType.QtFatalMsg: 4,
}

_runtime: DiagnosticsRuntime | None = None
_previous_handler: Callable[..., Any] | None = None
_handler_installed: bool = False


def start_gui_diagnostics(*, developer_mode: bool = False) -> DiagnosticsRuntime:
    """진단 런타임을 시작하고 Qt 메시지 핸들러와 종료 훅을 설치

    - 이미 시작한 경우 같은 런타임을 돌려줘 재진입해도 표식을 덮어쓰지 않음
    """
    global _runtime
    if _runtime is not None:
        return _runtime
    _runtime = start_diagnostics(developer_mode=developer_mode)
    install_qt_message_handler()
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(stop_gui_diagnostics)
    return _runtime


def current_runtime() -> DiagnosticsRuntime | None:
    """시작한 진단 런타임 반환. 시작 전이면 None"""
    return _runtime


def current_trail() -> BreadcrumbTrail | None:
    """사용자 동작 기록기 반환. 시작 전이면 None"""
    return _runtime.trail if _runtime is not None else None


def note_event(category: str, message: str) -> None:
    """사용자 동작 한 건을 기록(진단 미시작 시 무시)"""
    record_breadcrumb(_runtime, category, message)


def install_qt_message_handler() -> None:
    """Qt 메시지를 진단 로거로 보내는 핸들러를 설치하고 이전 핸들러를 보관

    - 이전 핸들러는 처음 설치할 때만 보관해, 재설치가 자기 자신으로의 체인을 만들지 않게 함
    """
    global _previous_handler, _handler_installed
    if _handler_installed:
        return
    _previous_handler = qInstallMessageHandler(handle_qt_message)
    _handler_installed = True


def handle_qt_message(mode: QtMsgType, context: Any, message: str | None) -> None:
    """Qt 메시지를 레벨 매핑해 로깅에 남기고, 이전 핸들러로도 전달"""
    text = message or ""
    level = QT_MSG_LEVELS.get(mode, 2)
    file_name = getattr(context, "file", None)
    function = getattr(context, "function", None)
    category = getattr(context, "category", "")
    detail = build_qt_message_context(file_name, getattr(context, "line", 0), function, category)
    log_qt_message(level, text, detail)
    if _previous_handler is not None:
        _previous_handler(mode, context, text)


def stop_gui_diagnostics() -> None:
    """정상 종료 표식을 남기고 진단 자원을 닫음. 두 번 호출해도 안전"""
    global _runtime
    runtime, _runtime = _runtime, None
    shutdown_diagnostics(runtime)


def open_folder(path: str) -> bool:
    """탐색기에서 진단 폴더를 열고 성공 여부 반환

    - Windows는 os.startfile로 탐색기를, 그 밖의 플랫폼은 xdg-open을 씀
    """
    if not os.path.isdir(path):
        return False
    try:
        _open_path(path)
    except OSError:
        return False
    return True


def _open_path(path: str) -> None:
    """플랫폼 기본 프로그램으로 폴더를 엶. 실패는 OSError로 전달"""
    starter = getattr(os, "startfile", None)
    if starter is not None:
        starter(path)
        return
    subprocess.Popen(["xdg-open", path])


def diagnostics_folder() -> str:
    """진단 로그 폴더 경로 반환. 시작 전이면 데이터 폴더 아래 기본 경로"""
    if _runtime is not None:
        return _runtime.directory
    from src.core.diagnostics.paths import diagnostics_dir

    return diagnostics_dir()


def open_crash_report_folder(report_directory: str) -> bool:
    """사고 보고서 폴더를 열고 실패하면 진단 폴더로 대체"""
    return open_folder(report_directory) or open_folder(diagnostics_folder())
