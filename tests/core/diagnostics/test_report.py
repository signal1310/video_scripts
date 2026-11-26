"""비정상 종료 감지, 사고 보고서 폴더 조립, 보고서 본문 검증"""

import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.paths import (
    APP_LOG_NAME,
    NATIVE_LOG_NAME,
    SESSION_FILENAME,
    diagnostics_file,
    list_report_dirs,
)
from src.core.diagnostics.report import (
    APP_LOG_TAIL_COPY_NAME,
    BREADCRUMBS_COPY_NAME,
    NATIVE_TAIL_COPY_NAME,
    PREVIOUS_SESSION_COPY_NAME,
    REPORT_TEXT_NAME,
    assemble_crash_report,
    build_report_text,
    detect_unclean_session,
)
from src.core.diagnostics.session import SessionInfo, close_session, write_session

_WHEN = datetime(2026, 4, 5, 6, 7, 8)


def _running_session(offset: int = 0) -> SessionInfo:
    """테스트용 진행 중(비정상 종료로 보일) 표식 생성"""
    return SessionInfo(
        pid=1234,
        started_at="2026-04-05 05:00:00",
        version="0.1.0",
        developer_mode=False,
        state="running",
        native_log_offset=offset,
    )


class TestUncleanDetection(unittest.TestCase):
    """표식 상태로 이전 실행의 비정상 종료를 판별하는지 검증"""

    def test_detects_unclean_and_ignores_clean_or_missing(self) -> None:
        """진행 중 표식만 비정상으로 보고 정상 종료 표식과 없는 표식은 넘기는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_unclean_session(tmp))
            write_session(diagnostics_file(SESSION_FILENAME, tmp), _running_session())
            detected = detect_unclean_session(tmp)
            self.assertIsNotNone(detected)
            assert detected is not None
            self.assertEqual(detected.pid, 1234)
            clean = close_session(_running_session(), now=_WHEN)
            write_session(diagnostics_file(SESSION_FILENAME, tmp), clean)
            self.assertIsNone(detect_unclean_session(tmp))


class TestCrashReportAssembly(unittest.TestCase):
    """사고 보고서 폴더 조립과 보관 상한 검증"""

    def test_assembles_folder_with_logs_and_report(self) -> None:
        """로그 꼬리, 네이티브 구간, 이전 표식, 브레드크럼, 보고서 본문이 모두 모이는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with open(diagnostics_file(APP_LOG_NAME, tmp), "w", encoding="utf-8") as handle:
                handle.write("\n".join(f"앱 로그 {index}" for index in range(5)) + "\n")
            native = diagnostics_file(NATIVE_LOG_NAME, tmp)
            with open(native, "w", encoding="utf-8") as handle:
                handle.write("이전 세션 이전 줄\n")
            offset = os.path.getsize(native)
            with open(native, "a", encoding="utf-8") as handle:
                handle.write("이전 세션 스택 줄\n")
            previous = _running_session(offset)
            write_session(diagnostics_file(SESSION_FILENAME, tmp), previous)
            crash = assemble_crash_report(
                tmp, previous, breadcrumbs=["2026-04-05 05:59:00 [분석] 시작"], now=_WHEN
            )
            files = sorted(os.listdir(crash.directory))
            with open(crash.report_path, encoding="utf-8") as handle:
                report_text = handle.read()
            native_copy_path = os.path.join(crash.directory, NATIVE_TAIL_COPY_NAME)
            with open(native_copy_path, encoding="utf-8") as handle:
                native_copy = handle.read()
        self.assertEqual(os.path.basename(crash.directory), "crash_20260405-060708")
        self.assertEqual(
            files,
            sorted(
                [
                    APP_LOG_TAIL_COPY_NAME,
                    BREADCRUMBS_COPY_NAME,
                    NATIVE_TAIL_COPY_NAME,
                    PREVIOUS_SESSION_COPY_NAME,
                    REPORT_TEXT_NAME,
                ]
            ),
        )
        self.assertIn("이전 실행이 정상적으로 끝나지 않아", report_text)
        self.assertIn("로컬 파일 경로", report_text)
        self.assertIn("이전 실행 PID: 1234", report_text)
        self.assertIn("[마지막 앱 로그]", report_text)
        self.assertIn("앱 로그 4", report_text)
        self.assertIn("이전 세션 스택 줄", report_text)
        self.assertIn("[사용자 동작 기록]", report_text)
        self.assertIn("[분석] 시작", report_text)
        self.assertEqual(native_copy, "이전 세션 스택 줄\n")

    def test_report_without_logs_uses_placeholder(self) -> None:
        """로그와 브레드크럼이 없으면 안내 문구를 넣고 표식 복사를 생략하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            crash = assemble_crash_report(tmp, _running_session(), now=_WHEN)
            with open(crash.report_path, encoding="utf-8") as handle:
                report_text = handle.read()
            app_copy = os.path.join(crash.directory, APP_LOG_TAIL_COPY_NAME)
            session_copy = os.path.join(crash.directory, PREVIOUS_SESSION_COPY_NAME)
            app_copy_size = os.path.getsize(app_copy)
            has_session_copy = os.path.exists(session_copy)
        self.assertIn("기록이 없습니다.", report_text)
        self.assertFalse(has_session_copy)
        self.assertEqual(app_copy_size, 0)

    def test_report_keeps_at_most_ten_folders(self) -> None:
        """사고 보고서가 상한을 넘으면 오래된 폴더가 지워지는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(11):
                os.makedirs(os.path.join(tmp, f"crash_202601{index + 1:02d}-000000"))
            crash = assemble_crash_report(tmp, _running_session(), now=_WHEN)
            remaining = list_report_dirs(tmp)
        self.assertEqual(len(remaining), 10)
        self.assertIn(crash.directory, remaining)

    def test_build_report_text_lists_sections_and_times(self) -> None:
        """보고서 본문이 세 구획과 시각, 폴더 정보를 담는지 검증"""
        text = build_report_text(
            _running_session(),
            now=_WHEN,
            app_tail=["한 줄"],
            native_lines=["스택 줄"],
            breadcrumbs=["기록 줄"],
            folder="C:/data/logs/crash_20260405-060708",
        )
        self.assertIn("이전 실행 시작: 2026-04-05 05:00:00", text)
        self.assertIn("종료 표시: 없음", text)
        self.assertIn("보고서 만든 시각: 2026-04-05 06:07:08", text)
        self.assertIn("보고서 폴더: C:/data/logs/crash_20260405-060708", text)
        for section in ("[마지막 앱 로그]", "[네이티브 스택 로그]", "[사용자 동작 기록]"):
            self.assertIn(section, text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
