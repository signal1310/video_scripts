"""진단 시작/종료 조율: 사고 보고서 준비, 표식 기록, 훅 원복 검증"""

import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.paths import NATIVE_LOG_NAME, SESSION_FILENAME, diagnostics_file
from src.core.diagnostics.runtime import (
    DiagnosticsRuntime,
    record_breadcrumb,
    shutdown_diagnostics,
    start_diagnostics,
)
from src.core.diagnostics.session import SessionInfo, read_session, write_session

_WHEN = datetime(2026, 6, 7, 8, 9, 10)


class TestStartupAndShutdown(unittest.TestCase):
    """시작 절차와 종료 절차가 표식과 보고서를 올바르게 다루는지 검증"""

    def test_start_creates_session_native_log_and_trail(self) -> None:
        """처음 시작이 표식, 네이티브 로그, 브레드크럼 기록기를 준비하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN)
            try:
                self.assertEqual(runtime.directory, tmp)
                self.assertIsNone(runtime.pending_report)
                self.assertIsNotNone(runtime.native_session)
                store = read_session(diagnostics_file(SESSION_FILENAME, tmp))
                self.assertIsNotNone(store)
                assert store is not None
                self.assertFalse(store.is_clean)
                self.assertEqual(store.started_at, "2026-06-07 08:09:10")
                self.assertEqual(store.native_log_offset, 0)
                record_breadcrumb(runtime, "분석", "시작")
                assert runtime.trail is not None
                self.assertEqual(len(runtime.trail.events()), 1)
                self.assertTrue(os.path.isfile(diagnostics_file(NATIVE_LOG_NAME, tmp)))
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)

    def test_shutdown_marks_session_clean_and_closes_log(self) -> None:
        """종료가 표식을 정상 종료로 바꾸고 네이티브 로그를 닫는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN)
            shutdown_diagnostics(runtime, now=_WHEN)
            store = read_session(diagnostics_file(SESSION_FILENAME, tmp))
        self.assertIsNotNone(store)
        assert store is not None
        self.assertTrue(store.is_clean)
        self.assertEqual(store.ended_at, "2026-06-07 08:09:10")
        assert runtime.native_session is not None
        self.assertFalse(runtime.native_session.is_open)

    def test_second_start_assembles_report_for_unclean_previous_run(self) -> None:
        """비정상 종료 표식이 남아 있으면 다음 시작이 사고 보고서를 준비하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(
                diagnostics_file(SESSION_FILENAME, tmp),
                SessionInfo(
                    pid=777,
                    started_at="2026-06-07 07:00:00",
                    version="0.1.0",
                    developer_mode=False,
                    state="running",
                ),
            )
            runtime = start_diagnostics(directory=tmp, now=_WHEN)
            try:
                self.assertIsNotNone(runtime.pending_report)
                assert runtime.pending_report is not None
                with open(runtime.pending_report.report_path, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("이전 실행 PID: 777", text)
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)
            subsequent = start_diagnostics(directory=tmp, now=_WHEN)
            try:
                self.assertIsNone(subsequent.pending_report)
            finally:
                shutdown_diagnostics(subsequent, now=_WHEN)

    def test_record_and_shutdown_tolerate_missing_runtime(self) -> None:
        """런타임이 없으면 기록과 종료가 아무 일도 하지 않는지 검증"""
        record_breadcrumb(None, "분석", "무시")
        shutdown_diagnostics(None)
        runtime = DiagnosticsRuntime(directory="")
        record_breadcrumb(runtime, "분석", "무시")
        shutdown_diagnostics(runtime)


if __name__ == "__main__":
    unittest.main()
