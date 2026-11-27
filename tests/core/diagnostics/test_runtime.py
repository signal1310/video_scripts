"""진단 시작/종료 조율: 사고 보고서 준비, 인스턴스별 표식 기록, 훅 원복 검증"""

import os
import tempfile
import unittest
from contextlib import AbstractContextManager
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.core.diagnostics.hooks import restore_exception_hooks
from src.core.diagnostics.paths import diagnostics_file, instance_native_log_name
from src.core.diagnostics.report import NATIVE_TAIL_COPY_NAME
from src.core.diagnostics.runtime import (
    DiagnosticsRuntime,
    record_breadcrumb,
    shutdown_diagnostics,
    start_diagnostics,
)
from src.core.diagnostics.session import (
    SessionInfo,
    close_session,
    instance_session_path,
    read_session,
    session_path,
    write_session,
)

_WHEN = datetime(2026, 6, 7, 8, 9, 10)
_LATER = datetime(2026, 6, 7, 9, 0, 0)


def _running(pid: int, started_at: str = "2026-06-07 07:00:00") -> SessionInfo:
    """테스트용 진행 중(비정상 종료로 보일) 표식 생성"""
    return SessionInfo(
        pid=pid,
        started_at=started_at,
        version="0.1.0",
        developer_mode=False,
        state="running",
    )


def _alive(*live_pids: int) -> AbstractContextManager[MagicMock]:
    """주어진 pid만 살아 있다고 답하는 생존 확인 대역"""
    return patch(
        "src.core.diagnostics.report.is_pid_alive",
        side_effect=lambda pid: pid in live_pids,
    )


def _simulate_crash(runtime: DiagnosticsRuntime) -> None:
    """정상 종료 표식을 쓰지 않고 프로세스가 사라진 상태를 흉내 냄"""
    restore_exception_hooks(runtime.hooks)
    if runtime.native_session is not None:
        runtime.native_session.close()


class TestStartupAndShutdown(unittest.TestCase):
    """시작 절차와 종료 절차가 표식과 보고서를 올바르게 다루는지 검증"""

    def test_start_creates_session_native_log_and_trail(self) -> None:
        """처음 시작이 인스턴스 표식, 네이티브 로그, 브레드크럼 기록기를 준비하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=4321)
            try:
                self.assertEqual(runtime.directory, tmp)
                self.assertIsNone(runtime.pending_report)
                self.assertIsNotNone(runtime.native_session)
                store = read_session(instance_session_path(4321, tmp))
                self.assertIsNotNone(store)
                assert store is not None
                self.assertFalse(store.is_clean)
                self.assertEqual(store.pid, 4321)
                self.assertEqual(store.started_at, "2026-06-07 08:09:10")
                self.assertEqual(store.native_log_offset, 0)
                self.assertEqual(store.native_log_name, instance_native_log_name(4321))
                record_breadcrumb(runtime, "분석", "시작")
                assert runtime.trail is not None
                self.assertEqual(len(runtime.trail.events()), 1)
                self.assertTrue(
                    os.path.isfile(diagnostics_file(instance_native_log_name(4321), tmp))
                )
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)

    def test_stale_shared_marker_with_own_pid_is_reported_once(self) -> None:
        """이번 실행과 같은 pid를 가진 옛 공용 표식도 보고한 뒤 지워 다시 보고하지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(session_path(tmp), _running(4242))
            with _alive():
                first = start_diagnostics(directory=tmp, now=_WHEN, pid=4242)
            try:
                self.assertIsNotNone(first.pending_report)
                self.assertFalse(os.path.exists(session_path(tmp)))
            finally:
                shutdown_diagnostics(first, now=_WHEN)
            with _alive():
                second = start_diagnostics(directory=tmp, now=_LATER, pid=4242)
            try:
                self.assertIsNone(second.pending_report)
            finally:
                shutdown_diagnostics(second, now=_LATER)

    def test_unreported_crash_native_log_survives_session_cleanup(self) -> None:
        """정리 대상인 옛 정상 표식이 미보고 사고의 pid 기반 네이티브 로그를 지우지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(session_path(tmp), close_session(_running(616161), now=_WHEN))
            crash = SessionInfo(
                pid=616161,
                started_at="2026-06-07 07:00:00",
                version="0.1.0",
                developer_mode=False,
                state="running",
                native_log_name=instance_native_log_name(616161),
            )
            write_session(instance_session_path(616161, tmp), crash)
            native = diagnostics_file(instance_native_log_name(616161), tmp)
            with open(native, "w", encoding="utf-8") as handle:
                handle.write("사고 스택\n")
            with _alive():
                runtime = start_diagnostics(directory=tmp, now=_LATER, pid=3003)
            try:
                self.assertIsNotNone(runtime.pending_report)
                assert runtime.pending_report is not None
                tail = os.path.join(
                    runtime.pending_report.directory, NATIVE_TAIL_COPY_NAME
                )
                with open(tail, encoding="utf-8") as handle:
                    self.assertIn("사고 스택", handle.read())
                self.assertFalse(os.path.exists(session_path(tmp)))
            finally:
                shutdown_diagnostics(runtime, now=_LATER)

    def test_shutdown_marks_session_clean_and_closes_log(self) -> None:
        """종료가 자기 표식을 정상 종료로 바꾸고 네이티브 로그를 닫는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=4321)
            shutdown_diagnostics(runtime, now=_WHEN)
            store = read_session(instance_session_path(4321, tmp))
        self.assertIsNotNone(store)
        assert store is not None
        self.assertTrue(store.is_clean)
        self.assertEqual(store.ended_at, "2026-06-07 08:09:10")
        assert runtime.native_session is not None
        self.assertFalse(runtime.native_session.is_open)

    def test_shutdown_closes_native_log_before_writing_clean_marker(self) -> None:
        """네이티브 로그를 닫은 뒤에 정상 종료 표식을 써 그 창의 스택을 잃지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=4321)
            states: list[bool] = []
            real_write = write_session

            def record(path: str, info: SessionInfo) -> None:
                assert runtime.native_session is not None
                states.append(runtime.native_session.is_open)
                real_write(path, info)

            with patch("src.core.diagnostics.runtime.write_session", record):
                shutdown_diagnostics(runtime, now=_WHEN)
        self.assertEqual(states, [False])

    def test_second_start_assembles_report_for_unclean_previous_run(self) -> None:
        """비정상 종료 표식이 남아 있으면 다음 시작이 사고 보고서를 준비하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(session_path(tmp), _running(777))
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=False):
                runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=4321)
            try:
                self.assertIsNotNone(runtime.pending_report)
                assert runtime.pending_report is not None
                with open(runtime.pending_report.report_path, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("이전 실행 PID: 777", text)
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)
            subsequent = start_diagnostics(directory=tmp, now=_LATER, pid=4322)
            try:
                self.assertIsNone(subsequent.pending_report)
            finally:
                shutdown_diagnostics(subsequent, now=_LATER)

    def test_overlapping_instances_report_the_crashed_one_once(self) -> None:
        """겹쳐 실행된 A, B 가운데 B만 죽으면 다음 시작이 B의 사고를 한 번만 보고하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with _alive(1001):
                first = start_diagnostics(directory=tmp, now=_WHEN, pid=1001)
            with _alive(1001):
                second = start_diagnostics(directory=tmp, now=_WHEN, pid=2002)
            # - A는 정상 종료, B는 표식만 남긴 채 사라진 상태
            shutdown_diagnostics(first, now=_WHEN)
            _simulate_crash(second)
            with _alive():
                reporter = start_diagnostics(directory=tmp, now=_LATER, pid=3003)
            try:
                self.assertIsNotNone(reporter.pending_report)
                assert reporter.pending_report is not None
                with open(reporter.pending_report.report_path, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("이전 실행 PID: 2002", text)
                self.assertNotIn("이전 실행 PID: 1001", text)
            finally:
                shutdown_diagnostics(reporter, now=_LATER)
            # - 사고를 보고한 표식은 지워져 다음 시작이 같은 사고를 다시 알리지 않음
            self.assertFalse(os.path.exists(instance_session_path(2002, tmp)))
            with _alive():
                later = start_diagnostics(directory=tmp, now=_LATER, pid=3004)
            try:
                self.assertIsNone(later.pending_report)
            finally:
                shutdown_diagnostics(later, now=_LATER)

    def test_clean_overlapping_instances_report_nothing(self) -> None:
        """겹쳐 실행된 두 인스턴스가 모두 정상 종료하면 다음 시작이 아무것도 보고하지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with _alive(1001):
                first = start_diagnostics(directory=tmp, now=_WHEN, pid=1001)
            with _alive(1001):
                second = start_diagnostics(directory=tmp, now=_WHEN, pid=2002)
            shutdown_diagnostics(first, now=_WHEN)
            shutdown_diagnostics(second, now=_WHEN)
            with _alive():
                runtime = start_diagnostics(directory=tmp, now=_LATER, pid=3003)
            try:
                self.assertIsNone(runtime.pending_report)
            finally:
                shutdown_diagnostics(runtime, now=_LATER)
            # - 정상 종료한 인스턴스의 표식은 다음 시작이 정리함
            self.assertFalse(os.path.exists(instance_session_path(1001, tmp)))
            self.assertFalse(os.path.exists(instance_session_path(2002, tmp)))

    def test_other_instance_marker_is_kept_and_not_reported(self) -> None:
        """다른 인스턴스가 실행 중이면 그 표식을 그대로 두고 자기 표식만 쓰는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(instance_session_path(777, tmp), _running(777))
            with _alive(777):
                runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=900)
                try:
                    self.assertIsNone(runtime.pending_report)
                    kept = read_session(instance_session_path(777, tmp))
                    assert kept is not None
                    self.assertEqual(kept.pid, 777)
                    self.assertEqual(kept.state, "running")
                    own = read_session(instance_session_path(900, tmp))
                    assert own is not None
                    self.assertEqual(own.state, "running")
                finally:
                    shutdown_diagnostics(runtime, now=_WHEN)
            final = read_session(instance_session_path(777, tmp))
        assert final is not None
        self.assertEqual(final.pid, 777)
        self.assertFalse(final.is_clean)

    def test_shutdown_does_not_touch_another_instance_marker(self) -> None:
        """종료가 자기 표식만 닫고 다른 인스턴스 표식은 그대로 두는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with _alive(777):
                runtime = start_diagnostics(directory=tmp, now=_WHEN, pid=900)
                write_session(instance_session_path(777, tmp), _running(777))
                shutdown_diagnostics(runtime, now=_WHEN)
            after = read_session(instance_session_path(777, tmp))
            own = read_session(instance_session_path(900, tmp))
        assert after is not None
        self.assertEqual(after.pid, 777)
        self.assertFalse(after.is_clean)
        assert own is not None
        self.assertTrue(own.is_clean)

    def test_record_and_shutdown_tolerate_missing_runtime(self) -> None:
        """런타임이 없으면 기록과 종료가 아무 일도 하지 않는지 검증"""
        record_breadcrumb(None, "분석", "무시")
        shutdown_diagnostics(None)
        runtime = DiagnosticsRuntime(directory="")
        record_breadcrumb(runtime, "분석", "무시")
        shutdown_diagnostics(runtime)

    def test_developer_mode_installs_and_removes_the_dev_log(self) -> None:
        """개발자 모드 시작이 dev.log를 만들고 종료가 핸들러를 떼는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, developer_mode=True, now=_WHEN)
            try:
                self.assertIsNotNone(runtime.dev_log)
                assert runtime.dev_log is not None
                self.assertTrue(os.path.isfile(runtime.dev_log.path))
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)
            self.assertIsNone(runtime.dev_log)

    def test_default_start_leaves_the_dev_log_off(self) -> None:
        """개발자 모드가 아니면 dev.log를 만들지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = start_diagnostics(directory=tmp, now=_WHEN)
            try:
                self.assertIsNone(runtime.dev_log)
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)

    def test_start_survives_unwritable_diagnostics_path(self) -> None:
        """진단 폴더 상위가 파일이어도 시작이 예외 없이 런타임을 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("파일")
            target = os.path.join(blocker, "logs")
            with self.assertLogs("src.diagnostics", level="WARNING"):
                runtime = start_diagnostics(directory=target, now=_WHEN)
            try:
                self.assertEqual(runtime.directory, target)
                self.assertIsNotNone(runtime.session_info)
                self.assertIsNone(runtime.pending_report)
            finally:
                shutdown_diagnostics(runtime, now=_WHEN)
            self.assertFalse(os.path.isdir(target))


if __name__ == "__main__":
    unittest.main()
