"""진단 경로, 꼬리 읽기, 보고서 폴더 보관 규칙 검증"""

import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.paths import (
    MAX_KEPT_REPORTS,
    create_report_dir,
    diagnostics_file,
    ensure_diagnostics_dir,
    list_report_dirs,
    prune_reports,
    read_span,
    read_tail_lines,
    report_dir_name,
    trim_file_to_tail,
)


class TestTrimAndTail(unittest.TestCase):
    """파일 상한 자르기와 꼬리 읽기가 줄 경계와 UTF-8 경계를 지키는지 검증"""

    def test_trim_keeps_tail_and_respects_line_boundary(self) -> None:
        """상한을 넘긴 파일을 절반 크기의 줄 경계로 줄이고 남은 크기를 반환하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native_crash.log")
            lines = [f"줄 번호 {index:04d} 입니다" for index in range(400)]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            original_size = os.path.getsize(path)
            kept = trim_file_to_tail(path, 4096)
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        self.assertLess(kept, original_size)
        self.assertLessEqual(kept, 4096)
        self.assertTrue(content.startswith("줄 번호"))
        self.assertIn("줄 번호 0399 입니다", content)
        self.assertNotIn("줄 번호 0000", content)

    def test_trim_ignores_small_or_missing_file(self) -> None:
        """상한 이하이거나 없는 파일은 크기 그대로 두는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tiny.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("한 줄\n")
            self.assertEqual(trim_file_to_tail(path, 1024), os.path.getsize(path))
            missing = os.path.join(tmp, "missing.log")
            self.assertEqual(trim_file_to_tail(missing, 1024), 0)

    def test_read_tail_lines_returns_last_lines(self) -> None:
        """꼬리 읽기가 마지막 limit 줄만 반환하고 없는 파일은 빈 목록인지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(f"줄 {index}" for index in range(20)) + "\n")
            self.assertEqual(read_tail_lines(path, 3), ["줄 17", "줄 18", "줄 19"])
            self.assertEqual(read_tail_lines(path, 0), [])
            self.assertEqual(read_tail_lines(os.path.join(tmp, "none.log"), 5), [])
        self.assertEqual(read_span(os.path.join(tmp, "none.log"), 0), b"")

    def test_read_span_reads_from_offset_with_cap(self) -> None:
        """오프셋 이후 바이트를 읽고 상한을 넘지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native.log")
            with open(path, "wb") as handle:
                handle.write(b"ABCDEFGHIJ")
            self.assertEqual(read_span(path, 4), b"EFGHIJ")
            self.assertEqual(read_span(path, 4, max_bytes=2), b"EF")
            self.assertEqual(read_span(path, -5), b"ABCDEFGHIJ")


class TestReportFolders(unittest.TestCase):
    """보고서 폴더 이름, 생성, 목록, 보관 상한 검증"""

    def test_report_dir_name_and_duplicate_suffix(self) -> None:
        """같은 시각 이름이 이미 있으면 번호를 붙여 새 폴더를 만드는지 검증"""
        when = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(report_dir_name(when), "crash_20260102-030405")
        with tempfile.TemporaryDirectory() as tmp:
            first = create_report_dir(tmp, when)
            second = create_report_dir(tmp, when)
            diagnostics = ensure_diagnostics_dir(tmp)
            self.assertEqual(os.path.basename(first), "crash_20260102-030405")
            self.assertEqual(os.path.basename(second), "crash_20260102-030405_2")
            self.assertEqual(diagnostics, tmp)
            self.assertEqual(diagnostics_file("app.log", tmp), os.path.join(tmp, "app.log"))

    def test_prune_reports_keeps_newest_and_ignores_other_dirs(self) -> None:
        """보관 상한을 넘는 가장 오래된 보고서만 지우고 다른 폴더는 두는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            for second in range(5):
                path = os.path.join(tmp, f"crash_2026010{second + 1}-000000")
                os.makedirs(path)
                with open(os.path.join(path, "report.txt"), "w", encoding="utf-8") as handle:
                    handle.write("내용\n")
            os.makedirs(os.path.join(tmp, "keep_me"))
            removed = prune_reports(tmp, keep=2)
            remaining = [os.path.basename(p) for p in list_report_dirs(tmp)]
        self.assertEqual(
            [os.path.basename(path) for path in removed],
            ["crash_20260101-000000", "crash_20260102-000000", "crash_20260103-000000"],
        )
        self.assertEqual(remaining, ["crash_20260104-000000", "crash_20260105-000000"])
        self.assertEqual(MAX_KEPT_REPORTS, 10)

    def test_prune_reports_with_zero_keep_removes_all(self) -> None:
        """keep이 0이면 보고서를 모두 지우는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "crash_20260101-000000"))
            self.assertEqual(len(prune_reports(tmp, keep=0)), 1)
            self.assertEqual(list_report_dirs(tmp), [])
            self.assertEqual(prune_reports(os.path.join(tmp, "none"), keep=3), [])


if __name__ == "__main__":
    unittest.main()
