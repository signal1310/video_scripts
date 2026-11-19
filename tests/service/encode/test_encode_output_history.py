"""재인코딩 결과 지문과 태그 이름 이력(encode_output_history) 단위 테스트"""

import contextlib
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

from src.core.encode_fingerprint import compute_encode_fingerprint
from src.core.encode_tag import ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.mp4_xtra import XTRA_KEYWORDS_NAME, XTRA_RATING_NAME, text_item, uint64_item, write_mp4_xtra
from src.service.analysis_store import AnalysisStore
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_output_history import (
    EncodeOutputHistory,
    query_known_tag_names,
    record_done_output,
)


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _mp4_bytes(mdat_payload: bytes = b"\x01\x02\x03\x04" * 16) -> bytes:
    return _box(b"ftyp", b"isom") + _box(b"mdat", mdat_payload) + _box(b"moov", b"")


class TestEncodeOutputHistory(unittest.TestCase):
    """결과 지문 기록과 조회, 이동/이름 변경/탐색기 편집 후 일치, 보존 정책을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.db_path = os.path.join(self.root, "video_cache.sqlite3")
        self.history = EncodeOutputHistory(self.db_path, sleep=lambda _s: None)

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_record_and_match_across_move_rename_and_xtra_edit(self) -> None:
        """이동, 이름 변경, 탐색기 Xtra 편집(moov 변경) 뒤에도 지문이 일치함을 검증"""
        mp4 = self._write("out.mp4", _mp4_bytes())
        fp = compute_encode_fingerprint(mp4)
        self.history.record_output(fingerprint=fp, output_path=mp4, encoder="hevc_nvenc", quality=22.0)
        self.assertEqual(len(self.history.find_by_size(fp.size)), 1)

        moved = os.path.join(self.root, "moved_name.mp4")
        os.replace(mp4, moved)
        write_mp4_xtra(moved, [text_item(XTRA_KEYWORDS_NAME, ["사용자태그"]), uint64_item(XTRA_RATING_NAME, 50)])

        other = self._write("other.mp4", _mp4_bytes(b"\xff" * 80))
        other_fp = compute_encode_fingerprint(other)
        matched = self.history.match_fingerprints({moved: fp.size, other: other_fp.size})
        self.assertEqual(matched, {moved})
        self.assertEqual(self.history.match_fingerprints({}), set())

    def test_tag_name_history_records_visible_only_and_deduplicates(self) -> None:
        """visible로 쓴 태그 이름만 이력에 남고 중복은 무시됨을 검증"""
        mkv = self._write("out.mkv", b"mkv-content")
        record_done_output(
            self.history,
            output_path=mkv,
            encoder="libx265",
            quality=22.0,
            tag_mode=ENCODE_TAG_MODE_VISIBLE,
            tag_name="first_tag",
        )
        record_done_output(
            self.history,
            output_path=mkv,
            encoder="libx265",
            quality=22.0,
            tag_mode=ENCODE_TAG_MODE_VISIBLE,
            tag_name="first_tag",
        )
        record_done_output(
            self.history,
            output_path=mkv,
            encoder="libx265",
            quality=22.0,
            tag_mode=ENCODE_TAG_MODE_OFF,
            tag_name="ignored_off_tag",
        )
        self.assertEqual(query_known_tag_names(self.history), ("first_tag",))

    def test_fingerprint_failure_logs_warning_without_raising(self) -> None:
        """mdat이 없는 MP4에서 지문 계산이 실패해도 예외를 내지 않고 태그 이력은 남김을 검증"""
        no_mdat = self._write("no_mdat.mp4", _box(b"ftyp", b"isom") + _box(b"moov", b""))
        with self.assertLogs("src.service.encode.encode_output_history", level="WARNING"):
            record_done_output(
                self.history,
                output_path=no_mdat,
                encoder="libx264",
                quality=23.0,
                tag_mode=ENCODE_TAG_MODE_VISIBLE,
                tag_name="re_encoded",
            )
        self.assertEqual(self.history.list_tag_names(), ("re_encoded",))

    def test_analysis_clear_and_batch_delete_preserve_output_and_tag_history(self) -> None:
        """분석 캐시 비우기, 스키마 재생성, 배치 삭제가 결과 이력과 태그 이름 이력을 지우지 않음을 검증"""
        mkv = self._write("keep.mkv", b"keep-data")
        record_done_output(
            self.history,
            output_path=mkv,
            encoder="hevc_nvenc",
            quality=22.0,
            tag_mode=ENCODE_TAG_MODE_VISIBLE,
            tag_name="kept_tag",
        )
        fp = compute_encode_fingerprint(mkv)

        store = AnalysisStore(self.db_path)
        store.clear()
        store.close()

        encode_hist = EncodeHistory(self.db_path, sleep=lambda _s: None)
        batch_id = encode_hist.create_batch(self.root, "{}", [])
        encode_hist.delete_batch(batch_id)
        encode_hist.close()

        self.assertEqual(self.history.match_fingerprints({mkv: fp.size}), {mkv})
        self.assertEqual(self.history.list_tag_names(), ("kept_tag",))

    def test_schema_recreation_and_corrupt_db_preservation(self) -> None:
        """분석 테이블 재생성 시 이력이 유지되고, 손상 DB 보존(.corrupt-*) 시에도 파일이 보존됨을 검증"""
        mkv = self._write("corrupt_test.mkv", b"data")
        record_done_output(
            self.history,
            output_path=mkv,
            encoder="hevc_nvenc",
            quality=22.0,
            tag_mode=ENCODE_TAG_MODE_VISIBLE,
            tag_name="corrupt_kept",
        )
        self.history.close()

        # 1. video_analysis 스키마 불일치로 분석 테이블만 재생성해도 이력 테이블은 유지됨
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DROP TABLE IF EXISTS video_analysis")
            conn.execute("CREATE TABLE video_analysis (broken INTEGER)")
            conn.commit()
        store = AnalysisStore(self.db_path)
        store.clear()
        store.close()

        reopened = EncodeOutputHistory(self.db_path, sleep=lambda _s: None)
        try:
            self.assertEqual(reopened.list_tag_names(), ("corrupt_kept",))
        finally:
            reopened.close()

        # 2. 손상(DatabaseError) 시 .corrupt-* 보존본이 생성되고 기존 테이블이 남음
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        with open(self.db_path, "r+b") as handle:
            handle.seek(0)
            handle.write(b"NOT_A_VALID_SQLITE_HEADER")
        store2 = AnalysisStore(self.db_path)
        store2.clear()
        store2.close()

        preserved = [f for f in os.listdir(self.root) if ".corrupt-" in f and not f.endswith(("-wal", "-shm"))]
        self.assertEqual(len(preserved), 1)
        self.assertGreater(os.path.getsize(os.path.join(self.root, preserved[0])), 0)

    def test_locked_error_retries_and_succeeds(self) -> None:
        """SQLite 잠금(OperationalError) 발생 시 짧은 대기 후 재시도해 성공함을 검증"""
        sleeps: list[float] = []
        hist = EncodeOutputHistory(self.db_path, sleep=sleeps.append)
        real_conn = hist._connection()
        calls = 0

        class _FlakyConn:
            def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise sqlite3.OperationalError("database is locked")
                return real_conn.execute(sql, params)

        try:
            with mock.patch.object(hist, "_connection", return_value=_FlakyConn()):
                hist.record_tag_name("retried_tag")
            self.assertEqual(sleeps, [0.05])
            self.assertEqual(hist.list_tag_names(), ("retried_tag",))
        finally:
            hist.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
