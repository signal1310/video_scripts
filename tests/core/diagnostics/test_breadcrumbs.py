"""사용자 동작 기록 링 버퍼, 파일 덧붙이기, 상한 축약, 로그 미러 검증"""

import logging
import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.breadcrumbs import (
    BREADCRUMB_BUFFER_SIZE,
    MIRROR_HANDLER_NAME,
    TRIM_CHECK_INTERVAL,
    BreadcrumbTrail,
    LogBreadcrumbHandler,
    attach_log_mirror,
    base_name,
    breadcrumbs_path,
    detach_log_mirror,
    format_event,
    format_list_event,
    read_breadcrumb_lines,
)


class TestBreadcrumbTrail(unittest.TestCase):
    """이벤트 기록이 메모리 링 버퍼와 파일에 함께 남는지 검증"""

    def test_add_keeps_only_ring_buffer_size(self) -> None:
        """상한을 넘는 이벤트는 가장 오래된 것부터 버리는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "breadcrumbs.log")
            trail = BreadcrumbTrail(path, capacity=3)
            for index in range(5):
                trail.add("인코딩", f"이벤트 {index}")
            events = trail.events()
            with open(path, encoding="utf-8") as handle:
                written = handle.read().splitlines()
        self.assertEqual(len(events), 3)
        self.assertTrue(events[-1].endswith("[인코딩] 이벤트 4"))
        self.assertEqual(len(written), 5)
        self.assertEqual(trail.path, path)

    def test_add_writes_category_and_message_in_one_line(self) -> None:
        """기록 줄에 시각, 분류, 내용이 한 줄로 들어가는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "breadcrumbs.log")
            trail = BreadcrumbTrail(path)
            line = trail.add("미리 보기", "clip.mp4", now=datetime(2026, 7, 8, 9, 10, 11))
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        self.assertEqual(line, "2026-07-08 09:10:11 [미리 보기] clip.mp4")
        self.assertEqual(content, line + "\n")

    def test_add_survives_unwritable_path(self) -> None:
        """기록 파일을 쓸 수 없어도 메모리 기록은 남기고 예외를 내지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            trail = BreadcrumbTrail(os.path.join(tmp, "missing-dir", "breadcrumbs.log"))
            trail.add("오류", "기록 실패")
            self.assertEqual(len(trail.events()), 1)

    def test_rewrite_keeps_recent_events_when_file_grows(self) -> None:
        """파일이 상한을 넘으면 메모리에 남은 최근 이벤트만 다시 쓰는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "breadcrumbs.log")
            trail = BreadcrumbTrail(path, capacity=5)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("이전 기록\n" * 40_000)
            for index in range(TRIM_CHECK_INTERVAL):
                trail.add("정리", f"이벤트 {index}")
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        self.assertTrue(content.endswith(f"[정리] 이벤트 {TRIM_CHECK_INTERVAL - 1}\n"))
        self.assertNotIn("이전 기록", content)
        self.assertEqual(len(content.splitlines()), 5)

    def test_read_breadcrumb_lines_returns_tail(self) -> None:
        """브레드크럼 파일 꼬리를 줄 단위로 읽는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = breadcrumbs_path(tmp)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(f"줄 {index}" for index in range(10)) + "\n")
            self.assertEqual(read_breadcrumb_lines(path, 2), ["줄 8", "줄 9"])
            self.assertEqual(read_breadcrumb_lines(os.path.join(tmp, "none.log")), [])
        self.assertEqual(BREADCRUMB_BUFFER_SIZE, 200)


class TestBreadcrumbFormatting(unittest.TestCase):
    """이벤트 문구 서식과 경로 축약 검증"""

    def test_format_event_defaults_category(self) -> None:
        """분류가 비면 기타로 채우고 시각 형식을 지키는지 검증"""
        line = format_event("  ", "내용", now=datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(line, "2026-01-01 00:00:00 [기타] 내용")

    def test_base_name_hides_folder_structure(self) -> None:
        """경로에서 파일 이름만 남기고 빈 값은 안내 문구로 바꾸는지 검증"""
        self.assertEqual(base_name(os.path.join("C:", "videos", "clip.mp4")), "clip.mp4")
        self.assertEqual(base_name(""), "알 수 없음")

    def test_format_list_event_includes_count(self) -> None:
        """여러 항목 동작이 개수와 함께 표현되는지 검증"""
        self.assertEqual(format_list_event("분석 시작", 12), "분석 시작 12건")


class TestLogMirror(unittest.TestCase):
    """이미 남기는 업무 로그를 브레드크럼으로 복사하는 필터와 연결 수명주기 검증"""

    def test_should_mirror_filters_by_logger_and_level(self) -> None:
        """목록 모듈은 INFO 이상, 그 밖의 모듈은 WARNING 이상만 통과하는지 검증"""
        mirrored = "src.gui.controllers.task_runner"
        other = "src.gui.table.table_model"
        self.assertTrue(LogBreadcrumbHandler._should_mirror(_record(mirrored, logging.INFO)))
        self.assertFalse(LogBreadcrumbHandler._should_mirror(_record(mirrored, logging.DEBUG)))
        self.assertFalse(LogBreadcrumbHandler._should_mirror(_record(other, logging.INFO)))
        self.assertTrue(LogBreadcrumbHandler._should_mirror(_record(other, logging.WARNING)))

    def test_handler_appends_formatted_message(self) -> None:
        """핸들러가 로거 이름 꼬리를 분류로 쓰고 메시지만 남기는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            trail = BreadcrumbTrail(breadcrumbs_path(tmp))
            handler = LogBreadcrumbHandler(trail)
            self.addCleanup(handler.close)
            record = _record("src.gui.controllers.task_runner", logging.INFO, "정밀 분석 시작")
            handler.emit(record)
            events = trail.events()
        self.assertEqual(len(events), 1)
        self.assertIn("[task runner] 정밀 분석 시작", events[0])

    def test_attach_and_detach_log_mirror(self) -> None:
        """로그 미러를 앱 루트 로거에 중복 없이 붙이고 떼는지 검증"""
        app_logger = logging.getLogger("src")
        before = list(app_logger.handlers)
        self.addCleanup(_restore_handlers, app_logger, before)
        with tempfile.TemporaryDirectory() as tmp:
            trail = BreadcrumbTrail(breadcrumbs_path(tmp))
            handler = attach_log_mirror(trail)
            duplicate = attach_log_mirror(trail)
            attached = [h for h in app_logger.handlers if h.get_name() == MIRROR_HANDLER_NAME]
            self.assertEqual(len(attached), 1)
            detach_log_mirror(handler)
            detach_log_mirror(duplicate)
            self.assertFalse(any(h.get_name() == MIRROR_HANDLER_NAME for h in app_logger.handlers))
        detach_log_mirror(None)


def _record(name: str, level: int, message: str = "메시지") -> logging.LogRecord:
    """지정 로거 이름과 레벨의 로그 레코드 생성"""
    return logging.LogRecord(name, level, __file__, 1, message, None, None)


def _restore_handlers(logger: logging.Logger, handlers: list[logging.Handler]) -> None:
    """테스트 전 핸들러 목록으로 되돌림"""
    logger.handlers = handlers


if __name__ == "__main__":
    unittest.main()
