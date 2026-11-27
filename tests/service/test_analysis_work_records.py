"""작업 기록 테이블(출력 해상도 직접 지정, 표 선택 기억)의 저장과 범위별 정리 검증"""

import os
import sqlite3
import tempfile
import unittest

from src.core.workspace_paths import normalize_root_key
from src.service import analysis_work_records as records
from src.service.designation_content import file_content_identity


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


class TestManualTargetDesignations(_ConnTestCase):
    """출력 해상도 직접 지정이 내용 키를 남기고 새 경로로 옮기거나 복사되는지"""

    def setUp(self) -> None:
        super().setUp()
        self.video = os.path.join(self.root_a, "a.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"v" * 2048)
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "720p"}})

    def _tier(self, root_key: str, name: str) -> str | None:
        row = self.conn.execute(
            "SELECT tier FROM manual_target WHERE root_key = ? AND filename = ?",
            (root_key, name),
        ).fetchone()
        return None if row is None else str(row[0])

    def _identity(self, path: str) -> tuple[int, str]:
        stat = os.stat(path)
        return (stat.st_size, file_content_identity(path)[1])  # type: ignore[index]

    def test_saving_fills_the_content_key_of_a_new_row(self) -> None:
        row = self.conn.execute(
            "SELECT filesize_bytes, fingerprint FROM manual_target"
        ).fetchone()
        self.assertIsNotNone(row[0])
        self.assertIsNotNone(row[1])

    def test_sizes_list_only_rows_with_a_content_key(self) -> None:
        self.assertEqual(records.manual_target_sizes(self.conn), {os.stat(self.video).st_size})

    def test_rename_inside_a_folder_moves_the_row(self) -> None:
        renamed = os.path.join(self.root_a, "renamed.mp4")
        os.rename(self.video, renamed)
        result = records.sync_manual_target_designations(
            self.conn, found={renamed: self._identity(renamed)}, root_spec=self.root_a
        )
        self.assertEqual(result.moved, [((self.key_a, "a.mp4"), (self.key_a, "renamed.mp4"))])
        self.assertEqual(result.copied, [])
        self.assertIsNone(self._tier(self.key_a, "a.mp4"))
        self.assertEqual(self._tier(self.key_a, "renamed.mp4"), "720p")

    def test_move_to_another_work_folder_moves_the_row(self) -> None:
        moved = os.path.join(self.root_b, "a.mp4")
        os.rename(self.video, moved)
        result = records.sync_manual_target_designations(
            self.conn, found={moved: self._identity(moved)}, root_spec=self.root_b
        )
        self.assertEqual(result.moved, [((self.key_a, "a.mp4"), (self.key_b, "a.mp4"))])
        self.assertIsNone(self._tier(self.key_a, "a.mp4"))
        self.assertEqual(self._tier(self.key_b, "a.mp4"), "720p")

    def test_copy_keeps_the_original_and_is_independent(self) -> None:
        copy_path = os.path.join(self.root_a, "a copy.mp4")
        with open(self.video, "rb") as src, open(copy_path, "wb") as dst:
            dst.write(src.read())
        result = records.sync_manual_target_designations(
            self.conn, found={copy_path: self._identity(copy_path)}, root_spec=self.root_a
        )
        self.assertEqual(result.moved, [])
        self.assertEqual(result.copied, [((self.key_a, "a.mp4"), (self.key_a, "a copy.mp4"))])
        self.assertEqual(self._tier(self.key_a, "a.mp4"), "720p")
        self.assertEqual(self._tier(self.key_a, "a copy.mp4"), "720p")
        records.write_manual_targets(self.conn, {self.key_a: {"a copy.mp4": "720p"}})
        self.assertIsNone(self._tier(self.key_a, "a.mp4"))
        self.assertEqual(self._tier(self.key_a, "a copy.mp4"), "720p")

    def test_rewriting_the_same_map_keeps_the_content_key(self) -> None:
        before = self.conn.execute("SELECT fingerprint FROM manual_target").fetchone()[0]
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "keep"}})
        after = self.conn.execute(
            "SELECT tier, fingerprint FROM manual_target"
        ).fetchone()
        self.assertEqual(tuple(after), ("keep", before))

    def test_refresh_reads_the_current_content_of_the_given_rows(self) -> None:
        before = self.conn.execute("SELECT fingerprint FROM manual_target").fetchone()[0]
        with open(self.video, "wb") as handle:
            handle.write(b"w" * 2048)
        refreshed = records.refresh_manual_target_identities(self.conn, [(self.key_a, "a.mp4")])
        self.assertEqual(refreshed, 1)
        after = self.conn.execute("SELECT fingerprint FROM manual_target").fetchone()[0]
        self.assertNotEqual(after, before)
        self.assertEqual(after, self._identity(self.video)[1])

    def test_move_from_another_drive_letter_moves_the_row(self) -> None:
        """다른 드라이브 문자의 옛 행도 내용이 같으면 현재 작업 폴더의 새 경로로 옮김"""
        fingerprint = self._identity(self.video)[1]
        size = os.stat(self.video).st_size
        other_drive = "Z:/elsewhere"
        self.conn.execute(
            "UPDATE manual_target SET root_key = ? WHERE root_key = ?", (other_drive, self.key_a)
        )
        moved = os.path.join(self.root_b, "a.mp4")
        os.rename(self.video, moved)
        result = records.sync_manual_target_designations(
            self.conn, found={moved: (size, fingerprint)}, root_spec=self.root_b
        )
        self.assertEqual(result.moved, [((other_drive, "a.mp4"), (self.key_b, "a.mp4"))])
        self.assertIsNone(self._tier(other_drive, "a.mp4"))
        self.assertEqual(self._tier(self.key_b, "a.mp4"), "720p")

    def test_refresh_keeps_the_stored_key_of_a_missing_file(self) -> None:
        before = self.conn.execute("SELECT fingerprint FROM manual_target").fetchone()[0]
        os.remove(self.video)
        self.assertEqual(
            records.refresh_manual_target_identities(self.conn, [(self.key_a, "a.mp4")]), 0
        )
        after = self.conn.execute("SELECT fingerprint FROM manual_target").fetchone()[0]
        self.assertEqual(after, before)

    def test_same_size_copy_with_other_content_is_not_matched(self) -> None:
        other = os.path.join(self.root_b, "a.mp4")
        with open(other, "wb") as handle:
            handle.write(b"w" * 2048)
        result = records.sync_manual_target_designations(
            self.conn, found={other: self._identity(other)}, root_spec=self.root_b
        )
        self.assertFalse(result.changed)
        self.assertIsNone(self._tier(self.key_b, "a.mp4"))

    def test_content_changed_at_the_same_path_is_reported_stale(self) -> None:
        with open(self.video, "wb") as handle:
            handle.write(b"x" * 2048)
        result = records.sync_manual_target_designations(
            self.conn,
            found={self.video: self._identity(self.video)},
            root_spec=self.root_a,
        )
        self.assertEqual(result.stale, frozenset({(self.key_a, "a.mp4")}))

    def test_size_change_without_a_scan_is_reported_stale(self) -> None:
        """스캔 후보가 없어도 같은 경로의 크기가 바뀌면 옛 지정을 쓰지 않음"""
        with open(self.video, "wb") as handle:
            handle.write(b"x" * 4096)
        result = records.sync_manual_target_designations(
            self.conn, found={}, root_spec=self.root_a
        )
        self.assertEqual(result.stale, frozenset({(self.key_a, "a.mp4")}))
        self.assertFalse(result.changed)

    def test_unchanged_file_is_not_stale(self) -> None:
        self.assertEqual(
            records.sync_manual_target_designations(
                self.conn, found={}, root_spec=self.root_a
            ).stale,
            frozenset(),
        )
        self.assertEqual(
            records.sync_manual_target_designations(
                self.conn,
                found={self.video: self._identity(self.video)},
                root_spec=self.root_a,
            ).stale,
            frozenset(),
        )

    def test_empty_scan_changes_nothing(self) -> None:
        self.assertFalse(
            records.sync_manual_target_designations(
                self.conn, found={}, root_spec=self.root_a
            ).changed
        )
        self.assertEqual(self._tier(self.key_a, "a.mp4"), "720p")

    def test_data_cleanup_counts_and_clears_rows_created_by_a_copy(self) -> None:
        copy_path = os.path.join(self.root_a, "a copy.mp4")
        with open(self.video, "rb") as src, open(copy_path, "wb") as dst:
            dst.write(src.read())
        records.sync_manual_target_designations(
            self.conn, found={copy_path: self._identity(copy_path)}, root_spec=self.root_a
        )
        self.assertEqual(records.count_manual_targets(self.conn, [self.root_a]), 2)
        self.assertEqual(records.clear_manual_targets(self.conn, [self.root_a]), 2)
        self.assertEqual(records.read_manual_targets(self.conn, {}), {})


class TestManualTargetOldSchema(_ConnTestCase):
    """내용 키 열이 없던 예전 DB가 열을 덧붙이고 지금 파일로 내용 키를 채우는지"""

    def setUp(self) -> None:
        super().setUp()
        self.video = os.path.join(self.root_a, "a.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"v" * 1024)
        self.conn.execute(
            "CREATE TABLE manual_target (root_key TEXT NOT NULL, filename TEXT NOT NULL,"
            " tier TEXT NOT NULL, PRIMARY KEY (root_key, filename)) WITHOUT ROWID"
        )
        self.conn.execute(
            "INSERT INTO manual_target (root_key, filename, tier) VALUES (?, ?, ?)",
            (self.key_a, "a.mp4", "720p"),
        )

    def test_reading_an_old_row_keeps_the_map(self) -> None:
        self.assertEqual(
            records.read_manual_targets(self.conn, {}), {self.key_a: {"a.mp4": "720p"}}
        )

    def test_saving_fills_the_content_key_of_the_old_row(self) -> None:
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "720p"}})
        row = self.conn.execute(
            "SELECT filesize_bytes, fingerprint FROM manual_target"
        ).fetchone()
        stat = os.stat(self.video)
        self.assertEqual(row[0], stat.st_size)
        self.assertIsNotNone(row[1])

    def test_old_row_without_a_content_key_is_not_matched(self) -> None:
        """파일을 읽을 수 없어 내용 키를 채우지 못한 행은 종전처럼 경로로만 적용"""
        os.remove(self.video)
        records.write_manual_targets(self.conn, {self.key_a: {"a.mp4": "720p"}})
        self.assertEqual(records.sync_manual_target_designations(
            self.conn,
            found={os.path.join(self.root_b, "x.mp4"): (1024, "other")},
            root_spec=self.root_b,
        ).changed, False)


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


class TestManualTargetDesignationsMultiRoot(_ConnTestCase):
    """여러 작업 폴더의 라벨 붙은 지정 행도 정규화 키로 원래 파일을 찾아 복사, 이동을 가리는지"""

    def setUp(self) -> None:
        super().setUp()
        base = os.path.dirname(self.root_a)
        self.work_a = os.path.join(base, "Work_A")
        self.work_b = os.path.join(base, "Work_B")
        for path in (self.work_a, self.work_b):
            os.makedirs(path, exist_ok=True)
        self.spec = f"{self.work_a};{self.work_b}"
        self.key = normalize_root_key(self.spec)
        self.video = os.path.join(self.work_b, "a.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"m" * 4096)
        records.write_manual_targets(self.conn, {self.key: {"Work_B/a.mp4": "720p"}})

    def test_copy_in_multi_root_keeps_the_labeled_original(self) -> None:
        copy_path = os.path.join(self.work_a, "copy.mp4")
        with open(self.video, "rb") as src, open(copy_path, "wb") as dst:
            dst.write(src.read())
        stat = os.stat(copy_path)
        identity = (stat.st_size, file_content_identity(copy_path)[1])  # type: ignore[index]
        result = records.sync_manual_target_designations(
            self.conn, found={copy_path: identity}, root_spec=self.spec
        )
        self.assertEqual(result.moved, [])
        self.assertEqual(len(result.copied), 1)
        names = {str(row[0]) for row in self.conn.execute("SELECT filename FROM manual_target")}
        self.assertIn("Work_B/a.mp4", names)
