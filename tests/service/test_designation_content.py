"""영상별 지정 행의 내용 키 기록과 새 경로 이관 판정 검증"""

import os
import sqlite3
import tempfile
import unittest

from src.service.designation_content import (
    CROP_TABLE,
    MANUAL_FRAME_TABLE,
    VR_TABLE,
    ContentIdentity,
    ContentMove,
    designation_rows,
    file_content_identity,
    fill_missing_fingerprints,
    mismatched_paths,
    optional_text,
    plan_content_moves,
    stored_identity,
    sync_path_designations,
    value_indexes,
)
from src.service.frame_analysis_schema import (
    FRAME_ANALYSIS_CROP_TABLE,
    FRAME_ANALYSIS_VR_TABLE,
)
from src.service.frame_analysis_schema import (
    FRAME_ANALYSIS_MANUAL_FRAME_TABLE as SCHEMA_MANUAL_FRAME_TABLE,
)


def _write(path: str, payload: bytes) -> str:
    """파일을 만들고 그 내용 키를 돌려줌"""
    with open(path, "wb") as handle:
        handle.write(payload)
    identity = file_content_identity(path)
    assert identity is not None
    return identity[1]


class _TableCase(unittest.TestCase):
    """경로 지정 표를 실제 스키마로 만들어 쓰는 테스트 기반"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.conn.close)
        for statement in (
            f"CREATE TABLE {VR_TABLE} (source_path TEXT PRIMARY KEY, manual_vr INTEGER NOT NULL,"
            " filesize_bytes INTEGER, mtime_ns INTEGER, fingerprint TEXT) WITHOUT ROWID",
            f"CREATE TABLE {CROP_TABLE} (source_path TEXT PRIMARY KEY, exclude_crop INTEGER NOT NULL,"
            " filesize_bytes INTEGER, mtime_ns INTEGER, fingerprint TEXT) WITHOUT ROWID",
            f"CREATE TABLE {MANUAL_FRAME_TABLE} (source_path TEXT PRIMARY KEY, manual_x INTEGER NOT NULL,"
            " manual_y INTEGER NOT NULL, manual_width INTEGER NOT NULL, manual_height INTEGER NOT NULL,"
            " filesize_bytes INTEGER, mtime_ns INTEGER, fingerprint TEXT) WITHOUT ROWID",
        ):
            self.conn.execute(statement)

    def path(self, name: str) -> str:
        return os.path.join(self._tmp.name, name)

    def add_crop(self, path: str, *, fingerprint: str | None = None, excluded: int = 1) -> None:
        try:
            stat = os.stat(path)
        except OSError:
            size = mtime = None
        else:
            size, mtime = stat.st_size, stat.st_mtime_ns
        self.conn.execute(
            f"INSERT INTO {CROP_TABLE} (source_path, exclude_crop, filesize_bytes, mtime_ns, fingerprint)"
            " VALUES (?, ?, ?, ?, ?)",
            (path, excluded, size, mtime, fingerprint),
        )

    def crop_paths(self) -> set[str]:
        return {str(row[0]) for row in self.conn.execute(f"SELECT source_path FROM {CROP_TABLE}")}


class TestTableNamesMatchSchema(unittest.TestCase):
    """이 모듈이 스키마 모듈과 같은 표 이름을 쓰는지 검증"""

    def test_table_names_match_the_schema_module(self) -> None:
        self.assertEqual(VR_TABLE, FRAME_ANALYSIS_VR_TABLE)
        self.assertEqual(CROP_TABLE, FRAME_ANALYSIS_CROP_TABLE)
        self.assertEqual(MANUAL_FRAME_TABLE, SCHEMA_MANUAL_FRAME_TABLE)


class TestValueLayout(unittest.TestCase):
    """표별 값 위치가 실제 열 순서와 맞는지 검증"""

    def test_value_indexes_of_each_table(self) -> None:
        self.assertEqual(value_indexes(VR_TABLE), (2, 3, 4))
        self.assertEqual(value_indexes(CROP_TABLE), (2, 3, 4))
        self.assertEqual(value_indexes(MANUAL_FRAME_TABLE), (5, 6, 7))

    def test_stored_identity_needs_both_size_and_fingerprint(self) -> None:
        self.assertEqual(stored_identity((1, 3, 4, "fp"), table=CROP_TABLE), (3, "fp"))
        self.assertIsNone(stored_identity((1, None, 4, "fp"), table=CROP_TABLE))
        self.assertIsNone(stored_identity((1, 3, 4, None), table=CROP_TABLE))


class TestContentIdentity(_TableCase):
    """내용 키를 파일에서 읽고 비교하는 동작 검증"""

    def test_identity_of_a_readable_file(self) -> None:
        path = self.path("a.mp4")
        _write(path, b"DATA")
        stat = os.stat(path)
        identity = file_content_identity(path)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity[0], stat.st_size)

    def test_identity_of_a_missing_file_is_none(self) -> None:
        self.assertIsNone(file_content_identity(self.path("missing.mp4")))

    def test_same_content_gives_the_same_fingerprint(self) -> None:
        first = _write(self.path("a.mp4"), b"SAME")
        second = _write(self.path("b.mp4"), b"SAME")
        self.assertEqual(first, second)

    def test_mismatched_paths_reports_only_different_content(self) -> None:
        same = self.path("same.mp4")
        changed = self.path("changed.mp4")
        unknown = self.path("unknown.mp4")
        fingerprint = _write(same, b"ORIGINAL")
        _write(changed, b"OTHER!!!")
        _write(unknown, b"UNKNOWN!")
        self.add_crop(same, fingerprint=fingerprint)
        self.add_crop(changed, fingerprint=fingerprint)
        self.add_crop(unknown)
        rows = self.conn.execute(
            f"SELECT source_path, fingerprint FROM {CROP_TABLE}"
        ).fetchall()
        entries = [(str(row[0]), None if row[1] is None else str(row[1])) for row in rows]
        self.assertEqual(mismatched_paths(entries), {changed})


class TestPlanContentMoves(unittest.TestCase):
    """내용이 같은 행과 새 경로의 짝짓기 검증"""

    def test_missing_row_identity_is_not_paired(self) -> None:
        moves = plan_content_moves(
            [("old.mp4", None)],
            [("new.mp4", (4, "fp"))],
            source_still_present=lambda old: False,
        )
        self.assertEqual(moves, [])

    def test_gone_source_moves_and_present_source_copies(self) -> None:
        moves = plan_content_moves(
            [("gone.mp4", (4, "fp")), ("here.mp4", (5, "fp2"))],
            [("new.mp4", (4, "fp")), ("copy.mp4", (5, "fp2"))],
            source_still_present=lambda old: old == "here.mp4",
        )
        self.assertEqual(
            [(move.source_id, move.target_id, move.moved) for move in moves],
            [("gone.mp4", "new.mp4", True), ("here.mp4", "copy.mp4", False)],
        )

    def test_gone_row_is_used_before_a_present_row(self) -> None:
        moves = plan_content_moves(
            [("here.mp4", (4, "fp")), ("gone.mp4", (4, "fp"))],
            [("new.mp4", (4, "fp"))],
            source_still_present=lambda old: old == "here.mp4",
        )
        self.assertEqual(
            [(move.source_id, move.target_id, move.moved) for move in moves],
            [("gone.mp4", "new.mp4", True)],
        )

    def test_one_row_is_used_for_one_target_only(self) -> None:
        moves = plan_content_moves(
            [("old.mp4", (4, "fp"))],
            [("a.mp4", (4, "fp")), ("b.mp4", (4, "fp"))],
            source_still_present=lambda old: True,
        )
        self.assertEqual([move.target_id for move in moves], ["a.mp4"])

    def test_drive_letter_paths_are_paired_by_content(self) -> None:
        moves = plan_content_moves(
            [("F:/Bv/_재분류/p/Solo cam - video 2.mp4", (4, "fp"))],
            [("C:/work/renamed.mp4", (4, "fp"))],
            source_still_present=lambda old: False,
        )
        self.assertEqual(
            [(move.source_id, move.target_id, move.moved) for move in moves],
            [("F:/Bv/_재분류/p/Solo cam - video 2.mp4", "C:/work/renamed.mp4", True)],
        )

    def test_row_ids_can_be_tuples(self) -> None:
        moves = plan_content_moves(
            [(("C:/w", "old.mp4"), (4, "fp"))],
            [(("C:/w", "new.mp4"), (4, "fp"))],
            source_still_present=lambda old: False,
        )
        self.assertEqual(moves[0].target_id, ("C:/w", "new.mp4"))


class TestFillMissingFingerprints(_TableCase):
    """지문이 없던 지정 행을 채우는 동작 검증"""

    def test_fills_with_the_scan_identity_without_reading(self) -> None:
        path = self.path("a.mp4")
        _write(path, b"DATA")
        self.add_crop(path)
        filled = fill_missing_fingerprints(self.conn, table=CROP_TABLE, found={path: (4, "scanned")})
        self.assertEqual(filled, 1)
        row = self.conn.execute(
            f"SELECT filesize_bytes, fingerprint FROM {CROP_TABLE} WHERE source_path = ?", (path,)
        ).fetchone()
        self.assertEqual(tuple(row), (4, "scanned"))

    def test_fills_from_the_file_when_the_scan_does_not_know_it(self) -> None:
        path = self.path("a.mp4")
        fingerprint = _write(path, b"DATA")
        self.add_crop(path)
        self.assertEqual(fill_missing_fingerprints(self.conn, table=CROP_TABLE), 1)
        row = self.conn.execute(
            f"SELECT fingerprint FROM {CROP_TABLE} WHERE source_path = ?", (path,)
        ).fetchone()
        self.assertEqual(row[0], fingerprint)

    def test_unreadable_row_stays_null(self) -> None:
        path = self.path("missing.mp4")
        self.add_crop(path)
        self.assertEqual(fill_missing_fingerprints(self.conn, table=CROP_TABLE), 0)
        row = self.conn.execute(
            f"SELECT fingerprint FROM {CROP_TABLE} WHERE source_path = ?", (path,)
        ).fetchone()
        self.assertIsNone(row[0])


class TestSyncPathDesignations(_TableCase):
    """이름 변경, 폴더 이동, 복사에 지정이 따라가는지 검증"""

    def test_rename_inside_a_folder_moves_the_row(self) -> None:
        old = self.path("Solo cam - video 2.mp4")
        fingerprint = _write(old, b"VIDEO")
        self.add_crop(old, fingerprint=fingerprint)
        new = self.path("renamed.mp4")
        os.rename(old, new)
        moves = sync_path_designations(self.conn, table=CROP_TABLE, found={new: (5, fingerprint)})
        self.assertEqual([(move.source_id, move.target_id, move.moved) for move in moves], [(old, new, True)])
        self.assertEqual(self.crop_paths(), {new})

    def test_moving_to_another_folder_keeps_the_row(self) -> None:
        old = self.path("a.mp4")
        fingerprint = _write(old, b"VIDEO")
        self.add_crop(old, fingerprint=fingerprint)
        other_dir = os.path.join(self._tmp.name, "other")
        os.makedirs(other_dir)
        new = os.path.join(other_dir, "a.mp4")
        os.rename(old, new)
        sync_path_designations(self.conn, table=CROP_TABLE, found={new: (5, fingerprint)})
        self.assertEqual(self.crop_paths(), {new})

    def test_copy_keeps_the_original_and_gives_the_copy_an_independent_row(self) -> None:
        original = self.path("a.mp4")
        fingerprint = _write(original, b"VIDEO")
        self.add_crop(original, fingerprint=fingerprint)
        copy_path = self.path("a copy.mp4")
        with open(original, "rb") as src, open(copy_path, "wb") as dst:
            dst.write(src.read())
        moves = sync_path_designations(self.conn, table=CROP_TABLE, found={copy_path: (5, fingerprint)})
        self.assertEqual(
            [(move.source_id, move.target_id, move.moved) for move in moves],
            [(original, copy_path, False)],
        )
        self.assertEqual(self.crop_paths(), {original, copy_path})
        self.conn.execute(f"DELETE FROM {CROP_TABLE} WHERE source_path = ?", (original,))
        self.assertEqual(self.crop_paths(), {copy_path})

    def test_path_that_already_has_a_designation_is_left_alone(self) -> None:
        original = self.path("a.mp4")
        fingerprint = _write(original, b"VIDEO")
        self.add_crop(original, fingerprint=fingerprint, excluded=1)
        copy_path = self.path("a copy.mp4")
        _write(copy_path, b"VIDEO")
        self.add_crop(copy_path, fingerprint=fingerprint, excluded=0)
        found: dict[str, ContentIdentity] = {copy_path: (5, fingerprint)}
        self.assertEqual(sync_path_designations(self.conn, table=CROP_TABLE, found=found), [])
        row = self.conn.execute(
            f"SELECT exclude_crop FROM {CROP_TABLE} WHERE source_path = ?", (copy_path,)
        ).fetchone()
        self.assertEqual(row[0], 0)

    def test_new_content_with_a_different_fingerprint_is_not_matched(self) -> None:
        old = self.path("a.mp4")
        fingerprint = _write(old, b"VIDEO")
        self.add_crop(old, fingerprint=fingerprint)
        unrelated = self.path("b.mp4")
        _write(unrelated, b"OTHER VIDEO")
        self.assertEqual(
            sync_path_designations(self.conn, table=CROP_TABLE, found={unrelated: (11, "other")}), []
        )
        self.assertEqual(self.crop_paths(), {old})

    def test_manual_frame_values_follow_the_file(self) -> None:
        old = self.path("a.mp4")
        fingerprint = _write(old, b"VIDEO")
        stat = os.stat(old)
        self.conn.execute(
            f"INSERT INTO {MANUAL_FRAME_TABLE} (source_path, manual_x, manual_y, manual_width,"
            " manual_height, filesize_bytes, mtime_ns, fingerprint)"
            " VALUES (?, 2, 4, 508, 900, ?, ?, ?)",
            (old, stat.st_size, stat.st_mtime_ns, fingerprint),
        )
        new = self.path("moved.mp4")
        os.rename(old, new)
        sync_path_designations(self.conn, table=MANUAL_FRAME_TABLE, found={new: (5, fingerprint)})
        rows = designation_rows(self.conn, MANUAL_FRAME_TABLE)
        self.assertEqual([path for path, _values in rows], [new])
        self.assertEqual(rows[0][1][:4], (2, 4, 508, 900))

    def test_empty_scan_changes_nothing(self) -> None:
        path = self.path("a.mp4")
        _write(path, b"VIDEO")
        self.add_crop(path)
        self.assertEqual(sync_path_designations(self.conn, table=CROP_TABLE, found={}), [])
        self.assertEqual(self.crop_paths(), {path})

    def test_missing_row_fingerprint_is_filled_before_matching(self) -> None:
        path = self.path("a.mp4")
        fingerprint = _write(path, b"VIDEO")
        self.add_crop(path)
        _write(path, b"DATA")
        identity = file_content_identity(path)
        assert identity is not None
        sync_path_designations(self.conn, table=CROP_TABLE, found={path: identity})
        stored = self.conn.execute(
            f"SELECT fingerprint FROM {CROP_TABLE} WHERE source_path = ?", (path,)
        ).fetchone()
        self.assertEqual(stored[0], identity[1])
        self.assertNotEqual(stored[0], fingerprint)


class TestExportsAndTypes(_TableCase):
    """모듈이 내보내는 값의 구성 검증"""

    def test_content_move_is_frozen(self) -> None:
        move = ContentMove("old", "new", True)
        with self.assertRaises(AttributeError):
            move.moved = False  # type: ignore[misc]

    def test_optional_text_reads_sqlite_null(self) -> None:
        self.assertIsNone(optional_text(None))
        self.assertEqual(optional_text(3), "3")

    def test_vr_table_moves_like_the_crop_table(self) -> None:
        old = self.path("a.mp4")
        fingerprint = _write(old, b"VIDEO")
        stat = os.stat(old)
        self.conn.execute(
            f"INSERT INTO {VR_TABLE} (source_path, manual_vr, filesize_bytes, mtime_ns, fingerprint)"
            " VALUES (?, 1, ?, ?, ?)",
            (old, stat.st_size, stat.st_mtime_ns, fingerprint),
        )
        new = self.path("b.mp4")
        os.rename(old, new)
        sync_path_designations(self.conn, table=VR_TABLE, found={new: (5, fingerprint)})
        rows = self.conn.execute(f"SELECT source_path, manual_vr FROM {VR_TABLE}").fetchall()
        self.assertEqual([tuple(row) for row in rows], [(new, 1)])


if __name__ == "__main__":
    unittest.main()


