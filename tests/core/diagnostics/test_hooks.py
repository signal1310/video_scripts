"""예외 훅 설치와 원복, Qt 메시지 레벨 매핑, 로깅 기록 검증"""

import logging
import sys
import threading
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from src.core.diagnostics import hooks as hooks_mod
from src.core.diagnostics.hooks import (
    DIAGNOSTICS_LOGGER_NAME,
    QT_LEVEL_CRITICAL,
    QT_LEVEL_DEBUG,
    QT_LEVEL_FATAL,
    QT_LEVEL_INFO,
    QT_LEVEL_WARNING,
    build_qt_message_context,
    format_qt_message,
    install_exception_hooks,
    log_qt_message,
    log_thread_uncaught,
    log_uncaught,
    log_unraisable,
    qt_level_to_logging,
    restore_exception_hooks,
)


class TestQtMessageMapping(unittest.TestCase):
    """Qt 메시지 레벨을 표준 logging 레벨로 옮기는 규칙 검증"""

    def test_level_mapping_covers_all_qt_levels(self) -> None:
        """DEBUG, INFO, WARNING, CRITICAL, FATAL이 표준 레벨로 대응하는지 검증"""
        self.assertEqual(qt_level_to_logging(QT_LEVEL_DEBUG), logging.DEBUG)
        self.assertEqual(qt_level_to_logging(QT_LEVEL_INFO), logging.INFO)
        self.assertEqual(qt_level_to_logging(QT_LEVEL_WARNING), logging.WARNING)
        self.assertEqual(qt_level_to_logging(QT_LEVEL_CRITICAL), logging.ERROR)
        self.assertEqual(qt_level_to_logging(QT_LEVEL_FATAL), logging.CRITICAL)

    def test_message_context_and_format(self) -> None:
        """위치 문구와 본문을 합치는 서식이 빈 값에서도 무너지지 않는지 검증"""
        context = build_qt_message_context("window.cpp", 42, "show", "qt.qpa.window")
        self.assertEqual(context, "qt.qpa.window, window.cpp:42 (show)")
        self.assertEqual(
            format_qt_message("창을 표시하지 못했습니다", context),
            "qt.qpa.window, window.cpp:42 (show): 창을 표시하지 못했습니다",
        )
        self.assertEqual(format_qt_message("  본문만  ", ""), "본문만")
        self.assertEqual(build_qt_message_context(None, 0, None, "qt.multimedia"), "qt.multimedia")
        self.assertEqual(build_qt_message_context("a.cpp", 0, None, ""), "a.cpp")

    def test_log_qt_message_writes_to_diagnostics_logger(self) -> None:
        """Qt 메시지가 진단 로거에 변환된 레벨로 기록되는지 검증"""
        with self.assertLogs(DIAGNOSTICS_LOGGER_NAME, level="DEBUG") as captured:
            log_qt_message(QT_LEVEL_FATAL, "접근 위반", "qt.qpa.window")
        self.assertIn("CRITICAL:src.diagnostics:qt.qpa.window: 접근 위반", captured.output)


class TestExceptionHooks(unittest.TestCase):
    """sys, threading, unraisable 훅 설치, 기록, 원복 검증"""

    def setUp(self) -> None:
        self._orig_excepthook = sys.excepthook
        self._orig_threading = threading.excepthook
        self._orig_unraisable = sys.unraisablehook
        # - 앞선 테스트가 남긴 활성 묶음을 지워 설치 복원 지점 계산을 결정적으로 만듦
        hooks_mod.hooks_mod_set_active(None)

    def tearDown(self) -> None:
        sys.excepthook = self._orig_excepthook
        threading.excepthook = self._orig_threading
        sys.unraisablehook = self._orig_unraisable
        hooks_mod.hooks_mod_set_active(None)

    def test_install_and_restore_returns_originals(self) -> None:
        """설치가 세 훅을 모두 바꾸고 원복이 설치 전 함수로 되돌리는지 검증"""
        before = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        bundle = install_exception_hooks()
        try:
            self.assertTrue(bundle.installed)
            self.assertIsNot(sys.excepthook, before[0])
            self.assertIsNot(threading.excepthook, before[1])
            self.assertIsNot(sys.unraisablehook, before[2])
        finally:
            restore_exception_hooks(bundle)
        self.assertIs(sys.excepthook, before[0])
        self.assertIs(threading.excepthook, before[1])
        self.assertIs(sys.unraisablehook, before[2])
        self.assertFalse(bundle.installed)

    def test_install_twice_keeps_original_chain(self) -> None:
        """두 번 설치해도 원복이 진짜 원본으로 돌아가는지 검증"""
        original = sys.excepthook
        first = install_exception_hooks()
        second = install_exception_hooks()
        try:
            self.assertIs(first, second)
        finally:
            restore_exception_hooks(second)
        self.assertIs(sys.excepthook, original)

    def test_excepthook_logs_and_chains_to_original(self) -> None:
        """설치한 excepthook이 로그를 남기고 원본 훅도 호출하는지 검증"""
        calls: list[tuple[str, str]] = []

        def original_hook(exc_type: type[BaseException], exc_value: BaseException, tb: object) -> None:
            calls.append((exc_type.__name__, str(exc_value)))

        sys.excepthook = original_hook
        bundle = install_exception_hooks()
        try:
            with self.assertLogs(DIAGNOSTICS_LOGGER_NAME, level="ERROR") as captured:
                exc = ValueError("처리되지 않은 값")
                sys.excepthook(type(exc), exc, exc.__traceback__)
        finally:
            restore_exception_hooks(bundle)
        self.assertTrue(any("처리되지 않은 예외: ValueError" in line for line in captured.output))
        self.assertEqual(calls, [("ValueError", "처리되지 않은 값")])

    def test_excepthook_skips_keyboard_interrupt_logging(self) -> None:
        """KeyboardInterrupt는 로그 없이 원본 훅으로만 넘기는지 검증"""
        calls: list[str] = []

        def original_hook(exc_type: type[BaseException], exc_value: BaseException, tb: object) -> None:
            calls.append(exc_type.__name__)

        sys.excepthook = original_hook
        bundle = install_exception_hooks()
        try:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        finally:
            restore_exception_hooks(bundle)
        self.assertEqual(calls, ["KeyboardInterrupt"])

    def test_thread_hook_logs_and_skips_system_exit(self) -> None:
        """스레드 훅이 스레드 이름과 예외를 기록하고 SystemExit은 건너뛰는지 검증"""
        thread = threading.Thread(target=lambda: None, name="작업 스레드")
        args = threading.ExceptHookArgs(
            (ValueError, ValueError("스레드 실패"), None, thread)
        )
        with self.assertLogs(DIAGNOSTICS_LOGGER_NAME, level="ERROR") as captured:
            kept = log_thread_uncaught(args)
        self.assertTrue(kept)
        self.assertTrue(any("스레드 '작업 스레드'" in line for line in captured.output))
        self.assertFalse(log_thread_uncaught(threading.ExceptHookArgs((SystemExit, SystemExit(), None, thread))))

    def test_unraisable_hook_logs_object_type(self) -> None:
        """무시된 예외 기록에 대상 객체 종류와 예외가 들어가는지 검증"""
        unraisable = SimpleNamespace(
            exc_type=RuntimeError,
            exc_value=RuntimeError("정리 실패"),
            exc_traceback=None,
            object=SimpleNamespace(),
        )
        with self.assertLogs(DIAGNOSTICS_LOGGER_NAME, level="ERROR") as captured:
            kept = log_unraisable(unraisable)
        self.assertTrue(kept)
        self.assertTrue(any("SimpleNamespace" in line for line in captured.output))
        self.assertFalse(log_unraisable(SimpleNamespace(exc_type=None)))

    def test_installed_thread_and_unraisable_hooks_log_and_chain(self) -> None:
        """설치한 스레드, 무시된 예외 훅이 기록한 뒤 원본 훅도 호출하는지 검증"""
        chained: list[str] = []
        threading.excepthook = lambda _args: chained.append("thread")
        sys.unraisablehook = lambda _unraisable: chained.append("unraisable")
        bundle = install_exception_hooks()
        thread = threading.Thread(target=lambda: None, name="체인 스레드")
        args = threading.ExceptHookArgs((ValueError, ValueError("스레드 실패"), None, thread))
        unraisable = SimpleNamespace(
            exc_type=RuntimeError,
            exc_value=RuntimeError("정리 실패"),
            exc_traceback=None,
            object=SimpleNamespace(),
        )
        try:
            with self.assertLogs(DIAGNOSTICS_LOGGER_NAME, level="ERROR") as captured:
                threading.excepthook(args)
                sys.unraisablehook(cast("sys.UnraisableHookArgs", unraisable))
        finally:
            restore_exception_hooks(bundle)
        self.assertEqual(chained, ["thread", "unraisable"])
        self.assertTrue(any("체인 스레드" in line for line in captured.output))
        self.assertTrue(any("RuntimeError" in line for line in captured.output))

    def test_log_uncaught_returns_false_for_keyboard_interrupt(self) -> None:
        """log_uncaught가 KeyboardInterrupt에 대해 기록하지 않음을 알리는지 검증"""
        self.assertFalse(log_uncaught(KeyboardInterrupt, KeyboardInterrupt(), None))

    def test_restore_without_install_does_nothing(self) -> None:
        """설치하지 않은 묶음을 원복해도 현재 훅을 건드리지 않는지 검증"""
        from src.core.diagnostics.hooks import HookBundle

        bundle = HookBundle()
        before = sys.excepthook
        restore_exception_hooks(bundle)
        self.assertIs(sys.excepthook, before)


class TestHookFallback(unittest.TestCase):
    """이미 설치된 훅을 다시 설치할 때의 복원 지점 유지 검증"""

    def test_already_installed_bundle_without_active_bundle(self) -> None:
        """설치 표식만 있고 활성 묶음이 없으면 빈 묶음을 돌려주는지 검증"""
        from src.core.diagnostics import hooks as hooks_mod

        previous = sys.excepthook
        try:
            sys.excepthook = lambda *args: None
            setattr(sys.excepthook, hooks_mod.INSTALL_MARKER_ATTR, True)
            with patch.object(hooks_mod, "_active_bundle", None):
                bundle = hooks_mod.install_exception_hooks()
            self.assertFalse(bundle.installed)
        finally:
            sys.excepthook = previous


if __name__ == "__main__":
    unittest.main()
