"""출력 해상도 직접 지정의 전용 DB 테이블 읽기, 쓰기, 옛 설정 파일 이전, 분석 캐시 비우기와의 분리를 검증"""

import os
import tempfile
import unittest

from src.service.analysis_store import AnalysisStore


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


if __name__ == "__main__":
    unittest.main()
