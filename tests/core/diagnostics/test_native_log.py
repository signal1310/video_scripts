"""faulthandler 세션 파일 준비, 머리글 서식, 상한 자르기 검증"""

import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.native_log import (
    SESSION_HEADER_RULE,
    NativeLogSession,
    build_session_header,
    default_native_log_path,
    open_native_log,
)
from src.core.diagnostics.paths import NATIVE_LOG_MAX_BYTES


class _FakeEnable:
    """faulthandler.enable 대신 호출 여부와 인자를 기록하는 대역"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[object] = []
        self._error = error

    def __call__(self, handle: object) -> None:
        self.calls.append(handle)
        if self._error is not None:
            raise self._error


class TestNativeLogSession(unittest.TestCase):
    """세션 머리글과 faulthandler 연결, 실패 시 앱 시작 유지 검증"""

    def test_header_contains_time_pid_and_environment(self) -> None:
        """머리글에 세션 시작 시각, 프로세스, 환경 줄, 스택 안내가 모두 있는지 검증"""
        header = build_session_header(
            datetime(2026, 2, 3, 4, 5, 6), 4242, ["앱 버전: 0.1.0", "Python: 3.12.0"]
        )
        self.assertIn("=== 세션 시작 2026-02-03 04:05:06 (pid 4242) ===", header)
        self.assertIn("- 앱 버전: 0.1.0", header)
        self.assertIn("- Python: 3.12.0", header)
        self.assertIn(SESSION_HEADER_RULE, header)
        self.assertTrue(header.endswith("\n"))

    def test_open_native_log_writes_header_and_keeps_handle(self) -> None:
        """파일을 추가 모드로 열어 머리글을 남기고, 파일 객체를 유지하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logs", "native_crash.log")
            enable = _FakeEnable()
            session = open_native_log(
                path,
                environment=["앱 버전: 0.1.0"],
                pid=99,
                now=datetime(2026, 3, 4, 5, 6, 7),
                enable=enable,
            )
            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual(session.path, path)
            self.assertEqual(len(enable.calls), 1)
            session.close()
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        self.assertEqual(session.offset, 0)
        self.assertIn("pid 99", content)
        self.assertIn("- 앱 버전: 0.1.0", content)

    def test_open_native_log_appends_and_offsets_second_session(self) -> None:
        """두 번째 실행은 기존 파일 뒤에 덧붙이고 오프셋이 첫 세션 길이가 되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native_crash.log")
            first = open_native_log(
                path, environment=["첫 세션"], pid=1, enable=_FakeEnable()
            )
            self.assertIsNotNone(first)
            assert first is not None
            first.close()
            first_size = os.path.getsize(path)
            second = open_native_log(
                path, environment=["둘째 세션"], pid=2, enable=_FakeEnable()
            )
            self.assertIsNotNone(second)
            assert second is not None
            second.close()
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        self.assertEqual(second.offset, first_size)
        self.assertIn("첫 세션", content)
        self.assertIn("둘째 세션", content)

    def test_open_native_log_trims_oversized_file(self) -> None:
        """상한을 넘긴 로그를 열기 전에 줄여 두는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native_crash.log")
            line = ("이전 실행 스택 줄 " * 20) + "\n"
            with open(path, "w", encoding="utf-8") as handle:
                while os.path.getsize(path) <= NATIVE_LOG_MAX_BYTES:
                    handle.write(line)
            self.assertGreater(os.path.getsize(path), NATIVE_LOG_MAX_BYTES)
            session = open_native_log(path, environment=["정리"], pid=3, enable=_FakeEnable())
            self.assertIsNotNone(session)
            assert session is not None
            self.assertLess(session.offset, NATIVE_LOG_MAX_BYTES)
            session.close()
            self.assertLessEqual(os.path.getsize(path), NATIVE_LOG_MAX_BYTES)

    def test_open_native_log_returns_none_when_enable_fails(self) -> None:
        """faulthandler를 켜지 못하면 None을 돌려주고 예외를 밖으로 내지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native_crash.log")
            session = open_native_log(
                path,
                environment=["실패"],
                pid=4,
                enable=_FakeEnable(error=RuntimeError("이미 사용 중")),
            )
            self.assertIsNone(session)

    def test_close_is_idempotent(self) -> None:
        """닫기를 두 번 호출해도 예외가 없는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native_crash.log")
            session = NativeLogSession(path, 0)
            disables: list[int] = []
            session.close(lambda: disables.append(1))
            session.close(lambda: disables.append(1))
        self.assertEqual(len(disables), 2)

    def test_default_path_lives_under_data_logs(self) -> None:
        """기본 경로가 data/logs 아래 native_crash.log인지 검증"""
        path = default_native_log_path()
        self.assertTrue(path.endswith(os.path.join("logs", "native_crash.log")))


if __name__ == "__main__":
    unittest.main()
