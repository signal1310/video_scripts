"""포착 예외 훅(sys, threading, unraisable) 설치와 원복, Qt 메시지 레벨 매핑 모듈

- 설치한 훅의 원본을 보관해 테스트가 원상 복구할 수 있게 함
- 기존 동작(stderr 출력)을 유지하면서 표준 logging에도 같은 내용을 남김
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

DIAGNOSTICS_LOGGER_NAME: str = "src.diagnostics"
INSTALL_MARKER_ATTR: str = "_video_scripts_diagnostics_hook"
QT_LEVEL_DEBUG: int = 0
QT_LEVEL_INFO: int = 1
QT_LEVEL_WARNING: int = 2
QT_LEVEL_CRITICAL: int = 3
QT_LEVEL_FATAL: int = 4
UNCAUGHT_LOG_FORMAT: str = "처리되지 않은 예외: {exc_type}: {exc_value}"
THREAD_UNCAUGHT_LOG_FORMAT: str = "스레드 '{thread}' 처리되지 않은 예외: {exc_type}: {exc_value}"
UNRAISABLE_LOG_FORMAT: str = "정리 중 무시된 예외({object}): {exc_type}: {exc_value}"

logger = logging.getLogger(DIAGNOSTICS_LOGGER_NAME)


class HookBundle:
    """설치 전 원본 훅을 보관해 원복을 보장하는 묶음"""

    def __init__(self) -> None:
        self.excepthook: Callable[..., Any] | None = None
        self.threading_excepthook: Callable[..., Any] | None = None
        self.unraisablehook: Callable[..., Any] | None = None
        self.installed: bool = False


# - 마지막으로 설치한 묶음. 중복 설치 시 복원 지점을 유지하려고 보관
_active_bundle: HookBundle | None = None


def hooks_mod_set_active(bundle: HookBundle | None) -> None:
    """중복 설치 판단용 활성 묶음 교체(테스트가 상태를 초기화할 때도 사용)"""
    global _active_bundle
    _active_bundle = bundle


def qt_level_to_logging(level: int) -> int:
    """Qt 메시지 레벨을 표준 logging 레벨로 변환. ERROR 이상이 fatal"""
    if level >= QT_LEVEL_FATAL:
        return logging.CRITICAL
    if level >= QT_LEVEL_CRITICAL:
        return logging.ERROR
    if level >= QT_LEVEL_WARNING:
        return logging.WARNING
    if level >= QT_LEVEL_INFO:
        return logging.INFO
    return logging.DEBUG


def build_qt_message_context(
    file: str | None, line: int, function: str | None, category: str
) -> str:
    """Qt 메시지 위치(file:line, 함수)와 분류 문구 생성. 위치가 없으면 분류만 남김"""
    parts: list[str] = []
    if category:
        parts.append(category)
    if file:
        location = f"{file}:{line}" if line > 0 else file
        if function:
            location = f"{location} ({function})"
        parts.append(location)
    return ", ".join(parts)


def format_qt_message(message: str, context: str) -> str:
    """Qt 메시지 본문과 발생 위치 문구를 한 줄로 합침"""
    text = message.strip()
    return f"{context}: {text}" if context else text


def log_qt_message(level: int, message: str, context: str = "") -> None:
    """Qt 메시지를 레벨에 맞춰 표준 logging으로 기록하고 즉시 flush

    - Qt 치명 메시지는 프로세스가 곧 중단되므로 flush하지 않으면 파일에 남지 않음
    """
    logger.log(qt_level_to_logging(level), "%s", format_qt_message(message, context))
    flush_handlers(logger)


def flush_handlers(target: logging.Logger) -> None:
    """로거와 상위 로거의 파일 핸들러를 즉시 flush"""
    current: logging.Logger | None = target
    while current is not None:
        for handler in current.handlers:
            try:
                handler.flush()
            except (OSError, ValueError):
                continue
        current = current.parent if current.propagate else None


def log_uncaught(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> bool:
    """처리되지 않은 예외를 스택과 함께 기록하고 기록 여부 반환. KeyboardInterrupt는 제외"""
    if issubclass(exc_type, KeyboardInterrupt):
        return False
    logger.error(
        UNCAUGHT_LOG_FORMAT.format(exc_type=exc_type.__name__, exc_value=exc_value),
        exc_info=(exc_type, exc_value, exc_tb),
    )
    flush_handlers(logger)
    return True


def log_thread_uncaught(args: threading.ExceptHookArgs) -> bool:
    """스레드 처리되지 않은 예외를 기록하고 기록 여부 반환. SystemExit은 제외"""
    if args.exc_type is SystemExit:
        return False
    thread_name = args.thread.name if args.thread is not None else "알 수 없음"
    exc_value = args.exc_value if args.exc_value is not None else args.exc_type()
    logger.error(
        THREAD_UNCAUGHT_LOG_FORMAT.format(
            thread=thread_name, exc_type=args.exc_type.__name__, exc_value=exc_value
        ),
        exc_info=(args.exc_type, exc_value, args.exc_traceback),
    )
    flush_handlers(logger)
    return True


def log_unraisable(unraisable: Any) -> bool:
    """소멸자 등에서 무시된 예외를 기록하고 기록 여부 반환"""
    exc_type = getattr(unraisable, "exc_type", None)
    if exc_type is None:
        return False
    target = getattr(unraisable, "object", None)
    object_name = type(target).__name__ if target is not None else "알 수 없음"
    exc_value = getattr(unraisable, "exc_value", None)
    logger.error(
        UNRAISABLE_LOG_FORMAT.format(
            object=object_name,
            exc_type=getattr(exc_type, "__name__", str(exc_type)),
            exc_value=exc_value,
        ),
        exc_info=(exc_type, exc_value, getattr(unraisable, "exc_traceback", None)),
    )
    flush_handlers(logger)
    return True


def current_hook_bundle() -> HookBundle | None:
    """이 프로세스가 지금 설치해 둔 훅 묶음. 없으면 None

    - 중복 설치를 받아 간 시작이 실패할 때, 자기 설치가 아닌 공유 묶음을 원복하지 않도록 구분하는 데 씀
    """
    return _active_bundle


def install_exception_hooks() -> HookBundle:
    """sys, threading, unraisable 훅을 설치하고 원본을 담은 묶음 반환

    - 원본은 묶음에 보관하며 체인은 원래 출력(예: stderr)을 그대로 수행
    - 이미 설치한 상태에서 다시 호출해도 이전 원본을 덮어써 복원 지점을 잃지 않음
    """
    bundle = HookBundle()
    if getattr(sys.excepthook, INSTALL_MARKER_ATTR, False):
        return _already_installed_bundle()
    bundle.excepthook = sys.excepthook
    bundle.threading_excepthook = threading.excepthook
    bundle.unraisablehook = sys.unraisablehook

    def excepthook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        log_uncaught(exc_type, exc_value, exc_tb)
        if bundle.excepthook is not None:
            bundle.excepthook(exc_type, exc_value, exc_tb)

    def threading_excepthook(args: threading.ExceptHookArgs) -> None:
        log_thread_uncaught(args)
        if bundle.threading_excepthook is not None:
            bundle.threading_excepthook(args)

    def unraisablehook(unraisable: Any) -> None:
        log_unraisable(unraisable)
        if bundle.unraisablehook is not None:
            bundle.unraisablehook(unraisable)

    for hook in (excepthook, threading_excepthook, unraisablehook):
        setattr(hook, INSTALL_MARKER_ATTR, True)
    sys.excepthook = excepthook
    threading.excepthook = threading_excepthook
    sys.unraisablehook = unraisablehook
    bundle.installed = True
    hooks_mod_set_active(bundle)
    return bundle


def _already_installed_bundle() -> HookBundle:
    """이미 설치된 훅을 그대로 두고 현재 묶음 반환. 묶음이 없으면 빈 묶음"""
    bundle = _active_bundle
    if bundle is not None:
        return bundle
    return HookBundle()


def restore_exception_hooks(bundle: HookBundle) -> None:
    """묶음에 보관한 원본 훅으로 되돌림. 설치 전이면 아무것도 하지 않음"""
    global _active_bundle
    if not bundle.installed:
        return
    if bundle.excepthook is not None:
        sys.excepthook = bundle.excepthook
    if bundle.threading_excepthook is not None:
        threading.excepthook = bundle.threading_excepthook
    if bundle.unraisablehook is not None:
        sys.unraisablehook = bundle.unraisablehook
    bundle.installed = False
    if _active_bundle is bundle:
        _active_bundle = None
