"""분석 캐시 측정 버전별 재사용 규칙이 판정과 분석 경로(재사용, 번호 올림, 다시 읽기, 이전 형식)에 맞게 적용되는지 검증"""

import contextlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core.probe_parser import PROBE_VERSION
from src.core.probe_version_rules import (
    ROW_LEGACY,
    ROW_REREAD,
    ROW_UPGRADED,
    row_reuse_action,
)
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from tests._factories import make_video


class RowReuseRuleTests(unittest.TestCase):
    """버전과 확장자로 행을 어떻게 쓸지 정하는 규칙 표를 고정"""

    def test_current_version_rows_are_reused_as_is(self) -> None:
        self.assertEqual(row_reuse_action(PROBE_VERSION, "a.mp4"), "current")

    def test_version_four_rows_are_upgraded_except_matroska_and_webm(self) -> None:
        """4에서 5: MP4, MOV 행은 번호만 올리고, MKV와 WebM 행은 길이를 다시 읽음"""
        self.assertEqual(row_reuse_action(4, "a.mp4"), ROW_UPGRADED)
        self.assertEqual(row_reuse_action(4, "a.MOV"), ROW_UPGRADED)
        self.assertEqual(row_reuse_action(4, "a.mkv"), ROW_REREAD)
        self.assertEqual(row_reuse_action(4, "a.MKV"), ROW_REREAD)
        self.assertEqual(row_reuse_action(4, "a.webm"), ROW_REREAD)

    def test_version_three_rows_are_upgraded_and_matroska_is_reread(self) -> None:
        """3에서 4: 표식 값이 없던 행은 열 기본값(표식 없음)으로 올림. 4 이전 MKV는 길이 수정 대상이라 다시 읽음"""
        self.assertEqual(row_reuse_action(3, "a.mp4"), ROW_UPGRADED)
        self.assertEqual(row_reuse_action(3, "a.mkv"), ROW_REREAD)

    def test_older_or_newer_versions_are_legacy(self) -> None:
        """규칙이 없는 너무 오래된 번호와 현재보다 큰 번호는 이전 형식으로 분류"""
        self.assertEqual(row_reuse_action(2, "a.mp4"), ROW_LEGACY)
        self.assertEqual(row_reuse_action(1, "a.mp4"), ROW_LEGACY)
        self.assertEqual(row_reuse_action(PROBE_VERSION + 1, "a.mp4"), ROW_LEGACY)


class AnalysisReuseFlowTests(unittest.TestCase):
    """저장된 행의 번호를 낮춰 두고 분석 한 번을 돌려 재사용, 번호 올림, 다시 읽기, 이전 형식이 맞게 처리되는지 확인"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def _add(self, name: str, version: int) -> None:
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(name.encode("utf-8").ljust(16, b"."))
        stat = os.stat(path)
        entry = StoredEntry(
            name,
            stat.st_size,
            stat.st_mtime_ns,
            compute_fingerprint(path, stat.st_size),
            make_video(filename=name, keyframe_interval=2.0),
        )
        self.store.apply((), [entry])
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE video_props SET probe_version = ? WHERE filename = ?", (version, name))
            conn.commit()

    def _version_of(self, name: str) -> int:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            return int(conn.execute("SELECT probe_version FROM video_props WHERE filename = ?", (name,)).fetchone()[0])

    def test_analysis_reuses_upgrades_rereads_and_skips_legacy_as_designed(self) -> None:
        """MP4 4형, MKV 4형, 3형 MP4, 2형은 각각 재사용과 번호 올림, 다시 읽기, 다시 읽기로 처리되는지 검증"""
        self._add("keep.mp4", 4)
        self._add("long.mkv", 4)
        self._add("old.mp4", 3)
        self._add("legacy.mp4", 2)
        probed: list[list[str]] = []

        def fake_probe(root: str, names: list[str], include_keyframe: bool, **_kwargs: object) -> list[object]:
            probed.append(sorted(names))
            return [make_video(filename=name, keyframe_interval=2.0) for name in names]

        with patch.object(probe_mod, "probe_files", side_effect=fake_probe):
            result = probe_mod.probe_directory_with_store(self.root, include_keyframe=True, store=self.store)

        self.assertEqual(sorted(v.filename for v in result), ["keep.mp4", "legacy.mp4", "long.mkv", "old.mp4"])
        self.assertEqual(probed, [["legacy.mp4", "long.mkv"]])
        self.assertEqual(self._version_of("keep.mp4"), PROBE_VERSION)
        self.assertEqual(self._version_of("old.mp4"), PROBE_VERSION)

    def test_pending_count_and_restore_mode_follow_the_same_rules(self) -> None:
        """예상 대상 수와 복원 판정이 이전 형식 행을 결과로 세지 않고, 다시 읽을 행은 대상으로 세는지 검증"""
        self._add("legacy.mp4", 2)
        self.assertEqual(probe_mod.count_pending_files(self.store, self.root, include_keyframe=True), 1)
        self.assertIsNone(probe_mod.resolve_restore_keyframe_mode(self.store, self.root))
        self._add("long.mkv", 4)
        self.assertEqual(probe_mod.count_pending_files(self.store, self.root, include_keyframe=True), 2)
        self.assertTrue(probe_mod.resolve_restore_keyframe_mode(self.store, self.root))


if __name__ == "__main__":
    unittest.main()
