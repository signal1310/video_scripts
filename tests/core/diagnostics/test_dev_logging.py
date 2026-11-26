"""개발 로그: 순환 핸들러 설치, 명령 기록 미러, 정리 동작 검증"""

import logging
import os
import tempfile
import unittest
from datetime import datetime

from src.core import command_history
from src.core.command_history import CommandRecord, record_finish, record_start
from src.core.diagnostics.dev_logging import (
    DEV_LOG_HANDLER_NAME,
    ELAPSED_TEMPLATE,
    RUNNING_CODE_TEXT,
    UNKNOWN_ELAPSED_TEXT,
    UNKNOWN_TARGET,
    CommandLogMirror,
    format_command_record,
    format_elapsed,
    install_dev_log,
    remove_dev_log,
)

APP_LOGGER_NAME = "src"


def flush_app_handlers() -> None:
    """앱 루트 로거 핸들러의 버퍼를 비워 파일에 확실히 기록되게 함"""
    for handler in logging.getLogger(APP_LOGGER_NAME).handlers:
        handler.flush()


def _record(
    *,
    seq: int = 1,
    stage: str = "화질 분석",
    target: str = "clip.mp4",
    exit_code: int | None = 0,
    elapsed: float | None = 1.25,
) -> CommandRecord:
    """테스트용 명령 기록 생성"""
    return CommandRecord(
        seq=seq,
        timestamp=datetime(2026, 6, 7, 8, 9, 10),
        stage=stage,
        target_name=target,
        args=("ffmpeg", "-i", "in.mp4"),
        display="ffmpeg -i in.mp4",
        elapsed_sec=elapsed,
        exit_code=exit_code,
    )


class FormatTests(unittest.TestCase):
    """명령 기록 한 줄 문구 생성 검증"""

    def test_elapsed_text_uses_two_decimals_or_placeholder(self) -> None:
        """소요 시간이 있으면 초로, 없으면 안내 문구로 표시하는지 검증"""
        self.assertEqual(format_elapsed(1.234), ELAPSED_TEMPLATE.format(elapsed=1.234))
        self.assertEqual(format_elapsed(None), UNKNOWN_ELAPSED_TEXT)

    def test_finished_record_shows_exit_code_and_target(self) -> None:
        """끝난 명령은 종료 코드와 대상 이름, 명령줄을 한 줄에 담는지 검증"""
        line = format_command_record(_record())
        self.assertIn("화질 분석", line)
        self.assertIn("clip.mp4", line)
        self.assertIn("ffmpeg -i in.mp4", line)
        self.assertIn("종료 코드 0", line)

    def test_running_record_and_missing_target_use_placeholders(self) -> None:
        """진행 중 기록은 실행 중, 대상이 없으면 안내 문구로 채우는지 검증"""
        line = format_command_record(_record(target="", exit_code=None))
        self.assertIn(RUNNING_CODE_TEXT, line)
        self.assertIn(UNKNOWN_TARGET, line)


class CommandLogMirrorTests(unittest.TestCase):
    """링 버퍼 변경을 DEBUG 한 줄로 옮기는 리스너 검증"""

    def test_mirror_writes_latest_record_once(self) -> None:
        """새 기록이 오면 한 줄을 남기고, 같은 번호는 다시 남기지 않는지 검증"""
        records: list[CommandRecord] = [_record(seq=1)]
        logger = logging.getLogger("src.diagnostics.dev_logging.tests")
        mirror = CommandLogMirror(latest=lambda: records[-1] if records else None, target_logger=logger)
        with self.assertLogs(logger, level="DEBUG") as logs:
            mirror()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("FFmpeg 명령", logs.output[0])

        with self.assertNoLogs(logger, level="DEBUG"):
            mirror()

        records.append(_record(seq=2, target="next.mp4"))
        with self.assertLogs(logger, level="DEBUG") as second:
            mirror()
        self.assertIn("next.mp4", second.output[0])

    def test_mirror_reads_only_the_latest_record_per_notify(self) -> None:
        """알림 한 번에 최신 기록 한 건만 읽어 전체 복사 비용이 들지 않는지 검증"""
        logger = logging.getLogger("src.diagnostics.dev_logging.latest")
        calls: list[int] = []

        def latest() -> CommandRecord:
            calls.append(1)
            return _record(seq=1)

        mirror = CommandLogMirror(latest=latest, target_logger=logger)
        with self.assertLogs(logger, level="DEBUG"):
            mirror()
        self.assertEqual(len(calls), 1)

    def test_mirror_is_silent_without_records(self) -> None:
        """기록이 없으면 아무것도 남기지 않는지 검증"""
        logger = logging.getLogger("src.diagnostics.dev_logging.empty")
        mirror = CommandLogMirror(latest=lambda: None, target_logger=logger)
        with self.assertNoLogs(logger, level="DEBUG"):
            mirror()


class InstallDevLogTests(unittest.TestCase):
    """dev.log 핸들러 설치와 정리 검증"""

    def tearDown(self) -> None:
        root = logging.getLogger(APP_LOGGER_NAME)
        for handler in list(root.handlers):
            if handler.get_name() == DEV_LOG_HANDLER_NAME:
                root.removeHandler(handler)
                handler.close()

    def test_install_writes_debug_lines_for_app_modules_only(self) -> None:
        """설치 뒤 앱 모듈 DEBUG는 dev.log에 남고 다른 로거 기록은 남지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            session = install_dev_log(tmp)
            try:
                self.assertTrue(os.path.isfile(session.path))
                logging.getLogger("src.some.module").debug("상세 기록")
                logging.getLogger("outside.module").debug("외부 기록")
                flush_app_handlers()
                with open(session.path, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("상세 기록", text)
                self.assertNotIn("외부 기록", text)
            finally:
                remove_dev_log(session)

    def test_install_mirrors_recorded_commands_to_the_file(self) -> None:
        """명령 기록을 남기면 리스너가 dev.log에 한 줄을 쓰는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            session = install_dev_log(tmp)
            try:
                handle = record_start("화질 분석", ["ffmpeg", "-i", "clip.mp4"], target="clip.mp4")
                record_finish(handle, 0, elapsed_sec=0.5)
                flush_app_handlers()
                with open(session.path, encoding="utf-8") as file_handle:
                    text = file_handle.read()
                self.assertIn("clip.mp4", text)
                self.assertIn("종료 코드 0", text)
            finally:
                remove_dev_log(session)
                command_history.clear()

    def test_install_restores_the_previous_logger_level_on_remove(self) -> None:
        """정리하면 앱 루트 로거 레벨이 설치 전 값으로 돌아가는지 검증"""
        root = logging.getLogger(APP_LOGGER_NAME)
        with tempfile.TemporaryDirectory() as tmp:
            original = root.level
            session = install_dev_log(tmp)
            try:
                self.assertEqual(root.level, logging.DEBUG)
                self.assertEqual(session.previous_level, original)
            finally:
                remove_dev_log(session)
            self.assertEqual(root.level, original)

    def test_install_twice_keeps_a_single_handler(self) -> None:
        """두 번 설치해도 dev.log 핸들러가 하나만 남는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            first = install_dev_log(tmp)
            second = install_dev_log(tmp)
            try:
                handlers = [
                    handler
                    for handler in logging.getLogger(APP_LOGGER_NAME).handlers
                    if handler.get_name() == DEV_LOG_HANDLER_NAME
                ]
                self.assertEqual(len(handlers), 1)
                self.assertIsNone(second.handler)
            finally:
                remove_dev_log(first)
                remove_dev_log(second)

    def test_unwritable_directory_returns_session_without_handler(self) -> None:
        """폴더를 만들 수 없으면 핸들러 없이 세션만 돌려주고 예외를 내지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocked")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("파일이라 폴더를 만들 수 없음")
            session = install_dev_log(os.path.join(blocker, "logs"))
            try:
                self.assertIsNone(session.handler)
                self.assertIsNotNone(session.listener)
            finally:
                remove_dev_log(session)

    def test_remove_is_safe_twice_and_for_missing_session(self) -> None:
        """정리를 두 번 하거나 세션이 없으면 예외 없이 끝나는지 검증"""
        remove_dev_log(None)
        with tempfile.TemporaryDirectory() as tmp:
            session = install_dev_log(tmp)
            remove_dev_log(session)
            remove_dev_log(session)
        self.assertIsNone(session.handler)


if __name__ == "__main__":
    unittest.main()
