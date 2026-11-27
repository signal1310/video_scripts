"""작업 기록 테이블(출력 해상도 직접 지정, 표 선택 기억)의 저장과 범위별 정리 검증"""

import os
import sqlite3
import tempfile
import unittest

from src.service import analysis_work_records as records


class _ConnTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = sqlite3.connect(os.path.join(tmp.name, "cache.sqlite3"), isolation_level=None)
        self.addCleanup(self.conn.close)
        roots = tempfile.TemporaryDirectory()
        self.addCleanup(roots.cleanup)
        self.root_a = os.path.join(roots.name, "root_dir_a")
        self.root_b = os.path.join(roots.name, "root_dir_b")
        for path in (self.root_a, self.root_b):
            os.makedirs(path, exist_ok=True)
        self.key_a = os.path.normcase(os.path.abspath(self.root_a))
        self.key_b = os.path.normcase(os.path.abspath(self.root_b))


class TestManualTargets(_ConnTestCase):
    """직접 지정 맵 저장, 옛 맵 이전, 범위별 건수와 비우기"""

    def test_read_moves_legacy_map_when_the_table_is_first_created(self) -> None:
        legacy = {self.key_a: {"a.mp4": "720p"}}
        self.assertEqual(records.read_manual_targets(self.conn, legacy), legacy)
        self.assertEqual(records.read_manual_targets(self.conn, {self.key_b: {"b.mp4": "keep"}}), legacy)

    def test_legacy_map_is_kept_when_the_table_already_exists(self) -> None:
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "720p"}})
        self.assertEqual(
            records.read_manual_targets(self.conn, {self.key_b: {"b.mp4": "1080p"}}),
            {self.key_a: {"a.mp4": "720p"}},
        )

    def test_write_replaces_the_whole_map(self) -> None:
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "720p"}, self.key_b: {"b.mp4": "keep"}})
        records.write_manual_targets(self.conn, {self.key_b: {"b.mp4": "keep"}})
        self.assertEqual(records.read_manual_targets(self.conn, {}), {self.key_b: {"b.mp4": "keep"}})

    def test_counts_and_clears_only_rows_inside_the_scope(self) -> None:
        records.write_manual_targets(
            self.conn, {self.key_a: {"a.mp4": "720p"}, self.key_b: {"b.mp4": "1080p"}}
        )
        self.assertEqual(records.count_manual_targets(self.conn, [self.root_a]), 1)
        self.assertEqual(records.clear_manual_targets(self.conn, [self.root_a]), 1)
        self.assertEqual(records.read_manual_targets(self.conn, {}), {self.key_b: {"b.mp4": "1080p"}})
        self.assertEqual(records.count_manual_targets(self.conn), 1)

    def test_out_of_scope_key_is_not_counted(self) -> None:
        records.write_manual_targets(self.conn, {"C:/elsewhere": {"x.mp4": "720p"}})
        self.assertEqual(records.count_manual_targets(self.conn, [self.root_a]), 0)
        self.assertEqual(records.clear_manual_targets(self.conn, [self.root_a]), 0)
        self.assertEqual(records.count_manual_targets(self.conn), 1)


class TestTableSelections(_ConnTestCase):
    """표 선택 기억 저장과 범위별 건수와 비우기"""

    def test_write_replaces_only_the_given_root_key(self) -> None:
        records.write_table_selection(self.conn, self.key_a, ["a.mp4", "sub/a2.mp4"])
        records.write_table_selection(self.conn, self.key_b, ["b.mp4"])
        self.assertEqual(records.read_table_selection(self.conn, self.key_a), {"a.mp4", "sub/a2.mp4"})
        records.write_table_selection(self.conn, self.key_a, ["a.mp4"])
        self.assertEqual(records.read_table_selection(self.conn, self.key_a), {"a.mp4"})
        self.assertEqual(records.read_table_selection(self.conn, self.key_b), {"b.mp4"})

    def test_empty_filenames_are_not_stored(self) -> None:
        records.write_table_selection(self.conn, self.key_a, ["", "a.mp4"])
        self.assertEqual(records.read_table_selection(self.conn, self.key_a), {"a.mp4"})

    def test_counts_and_clears_only_rows_inside_the_scope(self) -> None:
        records.write_table_selection(self.conn, self.key_a, ["a.mp4"])
        records.write_table_selection(self.conn, self.key_b, ["b.mp4"])
        self.assertEqual(records.count_table_selections(self.conn, [self.root_a]), 1)
        self.assertEqual(records.clear_table_selections(self.conn, [self.root_a]), 1)
        self.assertEqual(records.read_table_selection(self.conn, self.key_b), {"b.mp4"})
        self.assertEqual(records.count_table_selections(self.conn), 1)


if __name__ == "__main__":
    unittest.main()
