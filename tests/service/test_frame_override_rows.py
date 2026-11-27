"""경로별 직접 지정 행의 파일 식별 검증과 스키마 열 추가 검증"""

import os
import sqlite3
import tempfile
import unittest
from typing import cast
from unittest import mock

from src.core.models import ActiveFrame
from src.service.frame_override_rows import (
    add_missing_columns,
    file_identity,
    identity_check,
    optional_int,
    validated_crop_exclusion_rows,
    validated_manual_rows,
    validated_review_ack_rows,
    write_row_changes,
)


class _StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.clip = os.path.join(tmp.name, "clip.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"v" * 1024)

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn


class TestSchemaColumns(_StoreTestCase):
    def test_add_missing_columns_adds_only_missing_ones(self) -> None:
        """없는 열만 덧붙이고 이미 있는 열은 다시 만들지 않음"""
        conn = self._connection()
        conn.execute("CREATE TABLE sample (source_path TEXT PRIMARY KEY, keep INTEGER)")
        add_missing_columns(
            conn,
            (
                ("sample", "keep", "INTEGER"),
                ("sample", "filesize_bytes", "INTEGER"),
                ("sample", "mtime_ns", "INTEGER"),
            ),
        )
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(sample)")]
        self.assertEqual(columns, ["source_path", "keep", "filesize_bytes", "mtime_ns"])

    def test_duplicate_column_from_a_stale_view_is_ignored(self) -> None:
        """다른 연결이 먼저 만든 열을 덧붙이려다 난 중복 열 오류는 넘어감"""
        conn = self._connection()
        conn.execute("CREATE TABLE sample (source_path TEXT PRIMARY KEY)")
        conn.commit()
        stale = [tuple(row) for row in conn.execute("PRAGMA table_info(sample)")]
        other = self._connection()
        other.execute("ALTER TABLE sample ADD COLUMN filesize_bytes INTEGER")
        other.commit()
        fake = mock.MagicMock(spec=sqlite3.Connection)
        fake.execute.side_effect = lambda sql, *params: (
            stale if sql.startswith("PRAGMA") else conn.execute(sql, *params)
        )
        add_missing_columns(
            cast("sqlite3.Connection", fake), (("sample", "filesize_bytes", "INTEGER"),)
        )
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(sample)")]
        self.assertEqual(columns, ["source_path", "filesize_bytes"])

    def test_other_operational_error_is_raised(self) -> None:
        """중복 열이 아닌 오류는 그대로 올림"""
        conn = self._connection()
        conn.execute("CREATE TABLE sample (source_path TEXT PRIMARY KEY)")

        def _execute(sql: str, *_params: object) -> list[tuple[int, str]]:
            if sql.startswith("PRAGMA"):
                return [(0, "source_path")]
            raise sqlite3.OperationalError("disk I/O error")

        fake = mock.MagicMock(spec=sqlite3.Connection)
        fake.execute.side_effect = _execute
        with self.assertRaises(sqlite3.OperationalError):
            add_missing_columns(
                cast("sqlite3.Connection", fake), (("sample", "filesize_bytes", "INTEGER"),)
            )


class TestIdentityCheck(_StoreTestCase):
    def test_file_identity_reads_size_and_mtime(self) -> None:
        stat = os.stat(self.clip)
        self.assertEqual(file_identity(self.clip), (stat.st_size, stat.st_mtime_ns))

    def test_file_identity_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(file_identity(os.path.join(self.tmp_dir, "none.mp4")))

    def test_identity_check_marks_changed_file_and_stamps_legacy_row(self) -> None:
        """지금 값과 다르면 바뀐 것으로 보고, 저장 값이 없으면 채울 값을 돌려줌"""
        stat = os.stat(self.clip)
        self.assertEqual(
            identity_check(self.clip, stat.st_size, stat.st_mtime_ns), (False, None, True)
        )
        self.assertEqual(
            identity_check(self.clip, stat.st_size + 1, stat.st_mtime_ns), (True, None, True)
        )
        self.assertEqual(
            identity_check(self.clip, None, None),
            (False, (stat.st_size, stat.st_mtime_ns, self.clip), True),
        )

    def test_identity_check_keeps_row_when_file_is_unreadable(self) -> None:
        """지금 파일을 읽지 못하면 비교할 수 없으므로 바뀌지 않은 것으로 보고 읽지 못했음을 알림"""
        with mock.patch(
            "src.service.frame_override_rows.os.stat", side_effect=OSError("missing")
        ):
            self.assertEqual(identity_check(self.clip, 1, 1), (False, None, False))

    def test_identity_check_treats_a_partial_identity_as_changed(self) -> None:
        """한 열만 있는 행은 채우는 조건(두 열 모두 NULL)과 달리 바뀐 것으로 보아 걸러 냄"""
        stat = os.stat(self.clip)
        self.assertEqual(
            identity_check(self.clip, stat.st_size, None),
            (True, None, True),
        )
        self.assertEqual(
            identity_check(self.clip, None, stat.st_mtime_ns),
            (True, None, True),
        )

    def test_identity_check_compares_with_the_given_identity_override(self) -> None:
        """다른 파일의 식별 값을 넘기면 경로의 파일 대신 그 값과 맞춰 봄"""
        stat = os.stat(self.clip)
        self.assertEqual(
            identity_check(self.clip, stat.st_size, stat.st_mtime_ns, (1, 2)),
            (True, None, True),
        )
        self.assertEqual(
            identity_check(self.clip, stat.st_size, stat.st_mtime_ns, (stat.st_size, stat.st_mtime_ns)),
            (False, None, True),
        )
        self.assertEqual(
            identity_check(self.clip, None, None, (1, 2)), (False, (1, 2, self.clip), True)
        )

    def test_optional_int_reads_null_and_number(self) -> None:
        self.assertIsNone(optional_int(None))
        self.assertEqual(optional_int(7), 7)


class TestValidatedRows(_StoreTestCase):
    def test_manual_row_inside_frame_is_kept(self) -> None:
        rows = [(self.clip, 1600, 1000, 260, 40, None, None)]
        frames, dropped, stamped = validated_manual_rows(rows, {self.clip: (1920, 1080)})
        self.assertEqual(frames, {self.clip: ActiveFrame(1600, 1000, 260, 40)})
        self.assertEqual(dropped, [])
        self.assertEqual(len(stamped), 1)

    def test_manual_row_with_one_identity_column_is_ignored_but_kept(self) -> None:
        """식별 값이 한 열만 있는 행은 정상 조회에서 바뀐 행처럼 건너뛰되 지우지 않음(채우지도 않음)"""
        stat = os.stat(self.clip)
        rows = [(self.clip, 1600, 1000, 260, 40, stat.st_size, None)]
        frames, dropped, stamped = validated_manual_rows(rows, {self.clip: (1920, 1080)})
        self.assertEqual(frames, {})
        self.assertEqual(dropped, [])
        self.assertEqual(stamped, [])

    def test_manual_row_of_another_file_is_skipped_without_cleanup(self) -> None:
        """다른 파일의 식별 값을 지정한 조회는 맞지 않는 행을 지우지도, 채우지도 않고 건너뜀"""
        stat = os.stat(self.clip)
        rows = [(self.clip, 1600, 1000, 260, 40, stat.st_size, stat.st_mtime_ns)]
        frames, dropped, stamped = validated_manual_rows(
            rows, {self.clip: (1920, 1080)}, identity_overrides={self.clip: (1, 2)}
        )
        self.assertEqual(frames, {})
        self.assertEqual(dropped, [])
        self.assertEqual(stamped, [])
        _, dropped, stamped = validated_manual_rows(
            rows,
            {self.clip: (1920, 1080)},
            identity_overrides={self.clip: (stat.st_size, stat.st_mtime_ns)},
        )
        self.assertEqual(dropped, [])
        self.assertEqual(stamped, [])

    def test_manual_row_outside_frame_is_dropped(self) -> None:
        rows = [(self.clip, 1600, 1000, 260, 40, None, None)]
        frames, dropped, _stamped = validated_manual_rows(rows, {self.clip: (1280, 720)})
        self.assertEqual(frames, {})
        self.assertEqual(dropped, rows)

    def test_manual_row_covering_the_whole_frame_is_dropped(self) -> None:
        rows = [(self.clip, 1920, 1080, 0, 0, None, None)]
        frames, dropped, _stamped = validated_manual_rows(rows, {self.clip: (1920, 1080)})
        self.assertEqual(frames, {})
        self.assertEqual(dropped, rows)

    def test_manual_row_of_an_unreadable_file_is_kept_without_a_stamp(self) -> None:
        """파일을 읽지 못하면 프레임 크기 검증과 식별 값 채우기를 건너뛰고 행을 그대로 둠"""
        rows = [(self.clip, 1600, 1000, 260, 40, None, None)]
        with mock.patch(
            "src.service.frame_override_rows.os.stat", side_effect=OSError("missing")
        ):
            frames, dropped, stamped = validated_manual_rows(rows, {self.clip: (1280, 720)})
        self.assertEqual(frames, {self.clip: ActiveFrame(1600, 1000, 260, 40)})
        self.assertEqual(dropped, [])
        self.assertEqual(stamped, [])

    def test_manual_row_of_a_deleted_file_survives_the_cleanup_write(self) -> None:
        """파일을 지운 뒤 조회해도 정리 쓰기가 행을 지우지 않아 다시 읽힘"""
        conn = self._connection()
        conn.execute(
            "CREATE TABLE frame_analysis_manual_active_frame ("
            "source_path TEXT PRIMARY KEY, manual_x INTEGER NOT NULL,"
            " manual_y INTEGER NOT NULL, manual_width INTEGER NOT NULL,"
            " manual_height INTEGER NOT NULL, filesize_bytes INTEGER, mtime_ns INTEGER)"
            " WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO frame_analysis_manual_active_frame VALUES (?, 260, 40, 1600, 1000, 9, 9)",
            (self.clip,),
        )
        conn.commit()
        os.remove(self.clip)
        rows = conn.execute(
            "SELECT source_path, manual_width, manual_height, manual_x, manual_y,"
            " filesize_bytes, mtime_ns FROM frame_analysis_manual_active_frame"
        ).fetchall()
        frames, dropped, stamped = validated_manual_rows(rows, {self.clip: (1280, 720)})
        write_row_changes(
            conn,
            table="frame_analysis_manual_active_frame",
            drop_columns=(
                "source_path",
                "manual_width",
                "manual_height",
                "manual_x",
                "manual_y",
                "filesize_bytes",
                "mtime_ns",
            ),
            dropped=dropped,
            stamped=stamped,
        )
        self.assertEqual(frames, {self.clip: ActiveFrame(1600, 1000, 260, 40)})
        self.assertEqual(
            conn.execute("SELECT source_path, manual_width FROM frame_analysis_manual_active_frame")
            .fetchall(),
            [(self.clip, 1600)],
        )

    def test_manual_row_with_unknown_frame_size_is_kept(self) -> None:
        """지금 프레임 크기를 모르면 저장 사각형을 정규화하지 않고 그대로 둠"""
        rows = [(self.clip, 1601, 999, 261, 41, None, None)]
        frames, dropped, stamped = validated_manual_rows(rows, {self.clip: (0, 0)})
        self.assertEqual(frames, {self.clip: ActiveFrame(1601, 999, 261, 41)})
        self.assertEqual(dropped, [])
        self.assertEqual(len(stamped), 1)

    def test_crop_exclusion_row_of_changed_file_is_ignored_but_kept(self) -> None:
        """같은 경로의 영상이 바뀐 잘라내기 제외 행은 이번 조회에서 건너뛰되 지우지 않음"""
        stat = os.stat(self.clip)
        rows = [(self.clip, 1, stat.st_size + 1, stat.st_mtime_ns)]
        kept, stamped = validated_crop_exclusion_rows(rows)
        self.assertEqual(kept, [])
        self.assertEqual(stamped, [])

    def test_crop_exclusion_row_of_same_file_is_kept(self) -> None:
        stat = os.stat(self.clip)
        rows = [(self.clip, 1, stat.st_size, stat.st_mtime_ns)]
        kept, stamped = validated_crop_exclusion_rows(rows)
        self.assertEqual(kept, [self.clip])
        self.assertEqual(stamped, [])

    def test_crop_exclusion_row_with_one_identity_column_is_ignored_but_kept(self) -> None:
        """잘라내기 제외도 식별 값이 한 열만 있는 행은 바뀐 행처럼 건너뛰되 지우지 않음"""
        stat = os.stat(self.clip)
        rows = [(self.clip, 1, None, stat.st_mtime_ns)]
        kept, stamped = validated_crop_exclusion_rows(rows)
        self.assertEqual(kept, [])
        self.assertEqual(stamped, [])

    def test_crop_exclusion_row_of_another_file_is_skipped_without_cleanup(self) -> None:
        """다른 파일의 식별 값과 맞춰 보는 조회는 맞지 않는 행을 지우지 않고 건너뜀"""
        stat = os.stat(self.clip)
        rows = [(self.clip, 1, stat.st_size, stat.st_mtime_ns)]
        kept, stamped = validated_crop_exclusion_rows(rows, identity_overrides={self.clip: (1, 2)})
        self.assertEqual(kept, [])
        self.assertEqual(stamped, [])
        kept, _stamped = validated_crop_exclusion_rows(
            rows,
            identity_overrides={self.clip: (stat.st_size, stat.st_mtime_ns)},
        )
        self.assertEqual(kept, [self.clip])

    def test_crop_exclusion_row_without_identity_is_not_stamped_from_an_override(self) -> None:
        """예전 스키마 잘라내기 제외 행은 다른 파일을 띄우는 조회에서도 식별 값을 채우지 않음

        - 보관 원본의 식별 값으로 채우면 그 값이 남아 다음 정상 조회가 결과 파일과 비교해 이 행을 무시함
        """
        rows = [(self.clip, 1, None, None)]
        kept, stamped = validated_crop_exclusion_rows(rows, identity_overrides={self.clip: (1, 2)})
        self.assertEqual(kept, [self.clip])
        self.assertEqual(stamped, [])

    def test_review_ack_row_of_another_file_is_skipped_without_cleanup(self) -> None:
        """여백 확인 표시도 다른 파일의 식별 값과 맞춰 보는 조회에서는 맞지 않는 행을 지우지 않음

        - 원본 보관/교체 모드에서 화질 비교를 열 때 확인 표시가 지워지면 여백 확인 필요 경고가 되살아남
        """
        stat = os.stat(self.clip)
        stored = (1600, 1000, 260, 40)
        rows = [(self.clip, *stored, stat.st_size, stat.st_mtime_ns)]
        regions = {self.clip: stored}
        kept, dropped, stamped = validated_review_ack_rows(
            rows, regions, identity_overrides={self.clip: (1, 2)}
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [])
        self.assertEqual(stamped, [])
        kept, dropped, _stamped = validated_review_ack_rows(
            rows,
            regions,
            identity_overrides={self.clip: (stat.st_size, stat.st_mtime_ns)},
        )
        self.assertEqual(kept, [self.clip])
        self.assertEqual(dropped, [])

    def test_review_ack_row_of_changed_file_is_dropped(self) -> None:
        """같은 경로의 영상이 바뀐 확인 표시 행은 정상 조회에서 지움"""
        stat = os.stat(self.clip)
        stored = (1600, 1000, 260, 40)
        rows = [(self.clip, *stored, stat.st_size + 1, stat.st_mtime_ns)]
        kept, dropped, stamped = validated_review_ack_rows(rows, {self.clip: stored})
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [rows[0]])
        self.assertEqual(stamped, [])


class TestWriteRowChanges(_StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        conn = self._connection()
        conn.execute(
            "CREATE TABLE sample (source_path TEXT PRIMARY KEY, keep INTEGER,"
            " filesize_bytes INTEGER, mtime_ns INTEGER) WITHOUT ROWID"
        )
        conn.execute("INSERT INTO sample VALUES (?, 1, NULL, NULL)", (self.clip,))
        conn.commit()

    def test_missing_identity_is_stamped(self) -> None:
        conn = self._connection()
        write_row_changes(
            conn,
            table="sample",
            drop_columns=("source_path", "keep", "filesize_bytes", "mtime_ns"),
            dropped=[],
            stamped=[(10, 20, self.clip)],
        )
        self.assertEqual(
            conn.execute("SELECT filesize_bytes, mtime_ns FROM sample").fetchall(),
            [(10, 20)],
        )

    def test_stamp_does_not_overwrite_a_newer_identity(self) -> None:
        """조회 뒤 새로 저장해 식별 값이 생긴 행은 지금 값으로 덮지 않음"""
        conn = self._connection()
        conn.execute("UPDATE sample SET filesize_bytes = 5, mtime_ns = 6")
        conn.commit()
        write_row_changes(
            conn,
            table="sample",
            drop_columns=("source_path", "keep", "filesize_bytes", "mtime_ns"),
            dropped=[],
            stamped=[(10, 20, self.clip)],
        )
        self.assertEqual(
            conn.execute("SELECT filesize_bytes, mtime_ns FROM sample").fetchall(),
            [(5, 6)],
        )

    def test_dropped_rows_are_deleted(self) -> None:
        conn = self._connection()
        write_row_changes(
            conn,
            table="sample",
            drop_columns=("source_path", "keep", "filesize_bytes", "mtime_ns"),
            dropped=[(self.clip, 1, None, None)],
            stamped=[],
        )
        self.assertEqual(conn.execute("SELECT source_path FROM sample").fetchall(), [])

    def test_row_changed_after_the_select_is_kept(self) -> None:
        """조회 때와 값이 달라진 행은 지우지 않아 새로 저장한 지정을 잃지 않음"""
        conn = self._connection()
        write_row_changes(
            conn,
            table="sample",
            drop_columns=("source_path", "keep", "filesize_bytes", "mtime_ns"),
            dropped=[(self.clip, 2, None, None)],
            stamped=[],
        )
        self.assertEqual(conn.execute("SELECT source_path FROM sample").fetchall(), [(self.clip,)])

    def test_nothing_to_write_skips_the_transaction(self) -> None:
        """바꿀 행이 없으면 쓰기 트랜잭션을 열지 않음"""
        conn = self._connection()
        conn.execute("BEGIN")
        write_row_changes(
            conn,
            table="sample",
            drop_columns=("source_path",),
            dropped=[],
            stamped=[],
        )
        conn.execute("ROLLBACK")
