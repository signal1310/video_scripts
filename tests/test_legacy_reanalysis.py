"""저장 결과로 이전 형식을 표시 중인 작업 폴더에서 분석을 시작하면 이전 형식 행만 다시 읽고 표시가 풀리는지 검증"""

import contextlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core.exceptions import DomainInterruptedError, ErrorCode
from src.core.models import VideoProps
from src.core.probe_parser import PROBE_VERSION
from src.core.smart_action import SmartAction
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from src.service.cache import CacheRegistry
from src.service.classifier_service import VideoClassifierService
from tests._factories import make_video

LEGACY_VERSION: int = 2
LEGACY_NAMES: tuple[str, ...] = ("old_a.mp4", "old_b.mp4")
CURRENT_NAME: str = "new.mp4"
KEYFRAME_INTERVAL: float = 2.0


class LegacyRestoreReanalysisTests(unittest.TestCase):
    """이전 형식 행이 있는 저장 결과 표시 뒤 분석 한 번으로 이전 형식 표시와 DB 번호가 현재 형식이 되는지 확인"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)
        for name in LEGACY_NAMES:
            self._add(name, LEGACY_VERSION)
        self._add(CURRENT_NAME, PROBE_VERSION)
        self.probe_calls: list[list[str]] = []

    def _add(self, name: str, version: int) -> None:
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(name.encode("utf-8").ljust(64, b"."))
        stat = os.stat(path)
        entry = StoredEntry(
            name,
            stat.st_size,
            stat.st_mtime_ns,
            compute_fingerprint(path, stat.st_size),
            make_video(filename=name, keyframe_interval=KEYFRAME_INTERVAL),
        )
        self.store.apply((), [entry])
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE video_props SET probe_version = ? WHERE filename = ?", (version, name))
            conn.commit()

    def _versions(self) -> dict[str, int]:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            return {n: int(v) for n, v in conn.execute("SELECT filename, probe_version FROM video_props")}

    def _fake_probe(
        self, root_dir: str, names: list[str], include_keyframe: bool, **_kwargs: object
    ) -> list[VideoProps]:
        """ffprobe 대신 요청 이름마다 가짜 메타데이터를 만들고 요청된 이름을 기록"""
        self.probe_calls.append(list(names))
        return [
            make_video(filename=name, keyframe_interval=KEYFRAME_INTERVAL if include_keyframe else None)
            for name in names
        ]

    def _restored_service(self) -> VideoClassifierService:
        """저장 결과를 판정해 인메모리 캐시로 설치한 서비스, 분석은 아직 시작하지 않음"""
        stored = probe_mod.load_stored_rows(self.store, self.root)
        assert stored is not None
        self.assertEqual(stored.legacy_names, frozenset(LEGACY_NAMES))
        service = VideoClassifierService(self.root, registry=CacheRegistry(), store=self.store)
        service.install_stored_cache(stored)
        return service

    def test_analysis_rereads_only_legacy_rows_and_clears_legacy_marks(self) -> None:
        """이전 형식 N개 표시 뒤 분석하면 이전 형식 행만 다시 읽고, 표시와 DB 번호가 현재 형식으로 바뀌는지 검증"""
        service = self._restored_service()
        self.assertEqual(service.pending_count(include_keyframe=True), len(LEGACY_NAMES))

        with patch.object(probe_mod, "probe_files", side_effect=self._fake_probe):
            report = service.evaluate_all(ensure_keyframe=True)

        self.assertEqual(self.probe_calls, [sorted(LEGACY_NAMES)])
        cache = service.cache
        assert cache is not None
        self.assertEqual(cache.legacy_names, frozenset())
        by_name = {ev.video.filename: ev for ev in report.evaluations}
        for name in (*LEGACY_NAMES, CURRENT_NAME):
            self.assertNotEqual(by_name[name].smart_action, SmartAction.LEGACY_FORMAT, name)
        self.assertEqual(self._versions(), {name: PROBE_VERSION for name in (*LEGACY_NAMES, CURRENT_NAME)})

    def test_cancelled_analysis_keeps_legacy_display_and_stored_versions(self) -> None:
        """분석을 취소하면 이전 형식 표시와 DB 번호가 그대로 남는지 검증"""
        service = self._restored_service()
        before = self._versions()
        cancelled = DomainInterruptedError("분석 취소", code=ErrorCode.WORK_CANCELLED)

        with (
            patch.object(probe_mod, "probe_files", side_effect=cancelled),
            self.assertRaises(DomainInterruptedError),
        ):
            service.evaluate_all(ensure_keyframe=True)

        cache = service.cache
        assert cache is not None
        self.assertEqual(cache.legacy_names, frozenset(LEGACY_NAMES))
        self.assertEqual(self._versions(), before)


if __name__ == "__main__":
    unittest.main()
