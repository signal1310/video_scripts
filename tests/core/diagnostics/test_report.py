"""비정상 종료 감지, 사고 보고서 폴더 조립, 보고서 본문 검증"""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from src.core.diagnostics.paths import (
    APP_LOG_NAME,
    NATIVE_LOG_NAME,
    SESSION_FILENAME,
    diagnostics_file,
    instance_native_log_name,
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
    clear_finished_sessions,
    detect_unclean_sessions,
    discard_reported_session,
    remove_instance_native_log,
)
from src.core.diagnostics.session import (
    SessionInfo,
    close_session,
    instance_session_path,
    write_session,
)

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
            self.assertEqual(detect_unclean_sessions(tmp), [])
            write_session(diagnostics_file(SESSION_FILENAME, tmp), _running_session())
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=False):
                detected = detect_unclean_sessions(tmp)
            self.assertEqual([info.pid for _path, info in detected], [1234])
            clean = close_session(_running_session(), now=_WHEN)
            write_session(diagnostics_file(SESSION_FILENAME, tmp), clean)
            self.assertEqual(detect_unclean_sessions(tmp), [])

    def test_live_foreign_pid_is_treated_as_another_instance(self) -> None:
        """표식 주인이 살아 있는 다른 프로세스면 이전 실행 사고로 보고하지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(diagnostics_file(SESSION_FILENAME, tmp), _running_session())
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=True):
                self.assertEqual(detect_unclean_sessions(tmp, own_pid=9999), [])
                own = detect_unclean_sessions(tmp, own_pid=1234)
            self.assertEqual([info.pid for _path, info in own], [1234])

    def test_instance_markers_are_scanned_together(self) -> None:
        """인스턴스별 표식 여러 개를 함께 읽어 죽은 인스턴스만 골라내는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            first = SessionInfo(
                pid=111,
                started_at="2026-04-05 05:00:00",
                version="0.1.0",
                developer_mode=False,
                state="running",
            )
            second = SessionInfo(
                pid=222,
                started_at="2026-04-05 05:30:00",
                version="0.1.0",
                developer_mode=False,
                state="running",
            )
            write_session(instance_session_path(111, tmp), first)
            write_session(instance_session_path(222, tmp), second)
            with patch("src.core.diagnostics.report.is_pid_alive", side_effect=lambda pid: pid == 111):
                unclean = detect_unclean_sessions(tmp, own_pid=999)
            self.assertEqual([info.pid for _path, info in unclean], [222])

    def test_marker_without_pid_is_not_reported(self) -> None:
        """pid가 없는 표식은 사고로 보고하지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            write_session(
                diagnostics_file(SESSION_FILENAME, tmp),
                SessionInfo(
                    pid=0,
                    started_at="2026-04-05 05:00:00",
                    version="0.1.0",
                    developer_mode=False,
                    state="running",
                ),
            )
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=False):
                self.assertEqual(detect_unclean_sessions(tmp), [])

    def test_clean_finished_sessions_are_removed_with_their_log(self) -> None:
        """주인이 죽은 정상 종료 표식과 그 표식이 가리키는 네이티브 로그를 지우는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            info = SessionInfo(
                pid=1234,
                started_at="2026-04-05 05:00:00",
                version="0.1.0",
                developer_mode=False,
                state="running",
                native_log_name=instance_native_log_name(1234),
            )
            write_session(instance_session_path(1234, tmp), close_session(info, now=_WHEN))
            native = diagnostics_file(instance_native_log_name(1234), tmp)
            with open(native, "w", encoding="utf-8") as handle:
                handle.write("스택\n")
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=False):
                removed = clear_finished_sessions(tmp)
            self.assertEqual(removed, [instance_session_path(1234, tmp)])
            self.assertFalse(os.path.exists(instance_session_path(1234, tmp)))
            self.assertFalse(os.path.exists(native))

    def test_clean_finished_session_without_log_name_keeps_pid_log(self) -> None:
        """네이티브 로그 이름이 없는 정상 종료 표식은 pid로 이름을 추정해 로그를 지우지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            clean = close_session(_running_session(), now=_WHEN)
            write_session(instance_session_path(1234, tmp), clean)
            native = diagnostics_file(instance_native_log_name(1234), tmp)
            with open(native, "w", encoding="utf-8") as handle:
                handle.write("미보고 스택\n")
            with patch("src.core.diagnostics.report.is_pid_alive", return_value=False):
                removed = clear_finished_sessions(tmp)
            self.assertEqual(removed, [instance_session_path(1234, tmp)])
            self.assertTrue(os.path.exists(native))

    def test_remove_instance_native_log_rejects_non_instance_names(self) -> None:
        """표식의 네이티브 로그 이름이 경로이거나 다른 형식이면 로그를 지우지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "logs")
            os.makedirs(logs)
            victim = os.path.join(tmp, "victim.txt")
            with open(victim, "w", encoding="utf-8") as handle:
                handle.write("남겨야 함\n")
            shared = diagnostics_file(NATIVE_LOG_NAME, logs)
            with open(shared, "w", encoding="utf-8") as handle:
                handle.write("옛 공용 스택\n")
            for name in ("../victim.txt", victim, NATIVE_LOG_NAME, "native_crash-1.txt", ""):
                info = SessionInfo(
                    pid=1,
                    started_at="시작",
                    version="1.0",
                    developer_mode=False,
                    state="clean",
                    native_log_name=name,
                )
                remove_instance_native_log(logs, info)
            self.assertTrue(os.path.exists(victim))
            self.assertTrue(os.path.exists(shared))

    def test_discard_reported_session_keeps_the_legacy_shared_log(self) -> None:
        """사고 보고 뒤 표식은 지우되 옛 공용 네이티브 로그는 남기는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            previous = _running_session()
            write_session(instance_session_path(1234, tmp), previous)
            shared = diagnostics_file(NATIVE_LOG_NAME, tmp)
            with open(shared, "w", encoding="utf-8") as handle:
                handle.write("옛 스택\n")
            discard_reported_session(tmp, instance_session_path(1234, tmp), previous)
            self.assertFalse(os.path.exists(instance_session_path(1234, tmp)))
            self.assertTrue(os.path.exists(shared))


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
