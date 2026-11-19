"""출력 해상도 직접 지정의 전용 DB 테이블 읽기, 쓰기, 옛 설정 파일 이전, 분석 캐시 비우기와의 분리를 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.config import ClassifierConfig
from src.core.models import VideoProps
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from src.service.cache import CacheRegistry
from src.service.session import ClassifierSession, normalize_root_key


class TestManualTargetTable(unittest.TestCase):
    """전용 테이블이 분석 캐시 수명과 무관하게 작업 폴더별 지정을 보관하는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "video_cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def test_first_read_moves_legacy_map_into_table(self) -> None:
        """테이블이 처음 만들어질 때만 옛 설정 파일의 맵을 옮기고, 이후에는 테이블 내용만 읽음을 검증"""
        legacy = {"C:/work": {"a.mp4": "720p"}}
        self.assertEqual(self.store.read_manual_targets(legacy), legacy)
        other_legacy = {"C:/work": {"z.mp4": "keep"}}
        self.assertEqual(self.store.read_manual_targets(other_legacy), legacy)

    def test_write_replaces_whole_map(self) -> None:
        """쓰기는 기존 내용을 모두 바꾸어 지워진 항목이 다시 나타나지 않음을 검증"""
        self.store.read_manual_targets({})
        self.store.write_manual_targets({"C:/a": {"x.mp4": "720p"}, "C:/b": {"y.mp4": "keep"}})
        self.store.write_manual_targets({"C:/b": {"y.mp4": "keep"}})
        self.assertEqual(self.store.read_manual_targets({}), {"C:/b": {"y.mp4": "keep"}})

    def test_clearing_analysis_cache_keeps_manual_targets(self) -> None:
        """분석 캐시를 비워도 직접 지정은 남아 있음을 검증"""
        self.store.read_manual_targets({})
        self.store.write_manual_targets({"C:/work": {"a.mp4": "720p"}})
        self.store.clear()
        self.assertEqual(self.store.read_manual_targets({}), {"C:/work": {"a.mp4": "720p"}})

    def test_unusable_db_falls_back_to_legacy_map(self) -> None:
        """DB 파일이 디렉터리로 막혀 열 수 없으면 옛 맵 사본을 돌려줘 지정이 사라지지 않음을 검증"""
        blocked = AnalysisStore(os.path.join(self._tmp.name, "blocked", "x", "db.sqlite3"))
        os.makedirs(os.path.join(self._tmp.name, "blocked"), exist_ok=True)
        with open(os.path.join(self._tmp.name, "blocked", "x"), "w", encoding="utf-8") as f:
            f.write("not a directory")
        self.addCleanup(blocked.close)
        legacy = {"C:/work": {"a.mp4": "720p"}}
        self.assertEqual(blocked.read_manual_targets(legacy), legacy)

    def test_rename_reused_by_fingerprint_migrates_manual_target_key(self) -> None:
        """외부 이름 변경이 지문 매칭으로 복구될 때 출력 해상도 직접 지정 키도 새 이름으로 이관됨을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        video_path = os.path.join(work.name, "big.mp4")
        payload = b"VIDEO_DATA_FOR_RENAME_MIGRATION"
        with open(video_path, "wb") as f:
            f.write(payload)
        st = os.stat(video_path)
        props = VideoProps("big.mp4", 1920, 1080, 0, 30.0, 9000, 128, 120.0, 500.0, "h264", 1.0)
        self.store.apply(
            (),
            [StoredEntry("big.mp4", st.st_size, st.st_mtime_ns, compute_fingerprint(video_path, st.st_size), props)],
        )

        session = ClassifierSession(
            root_dir=work.name,
            config=ClassifierConfig(),
            registry=CacheRegistry(),
            store=self.store,
        )
        session.set_manual_target_tier(["big.mp4"], "720p")

        renamed_path = os.path.join(work.name, "big [보존].mp4")
        os.rename(video_path, renamed_path)

        with patch("src.core.analysis.prober.probe_files", return_value=[]) as probe_mock:
            report = session.get_summary_report(ensure_keyframe=False)

        probe_mock.assert_not_called()
        self.assertEqual([ev.video.filename for ev in report.evaluations], ["big [보존].mp4"])
        self.assertEqual(report.evaluations[0].manual_target, "720p")
        self.assertEqual(session.manual_target_tiers, {"big [보존].mp4": "720p"})
        self.assertIn(normalize_root_key(work.name), session.manual_target_store)


if __name__ == "__main__":
    unittest.main()
