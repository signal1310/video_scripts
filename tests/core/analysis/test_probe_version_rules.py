"""측정 버전별 행 재사용 규칙(probe_version_rules) 단위 테스트"""

import unittest

from src.core.analysis.probe_version_rules import (
    ROW_CURRENT,
    ROW_LEGACY,
    ROW_REREAD,
    ROW_UPGRADED,
    row_reuse_action,
)
from src.core.probe_parser import PROBE_VERSION


class TestProbeVersionRules(unittest.TestCase):
    """5 -> 6 번호만 올림 규칙과 이전 버전 재사용/재읽기 판정을 검증"""

    def test_current_version_is_six_and_v5_upgrades_without_reread(self) -> None:
        self.assertEqual(PROBE_VERSION, 6)
        for name in ("clip.mp4", "clip.mkv", "clip.webm", "clip.mov"):
            with self.subTest(name=name):
                self.assertEqual(row_reuse_action(6, name, 6), ROW_CURRENT)
                self.assertEqual(row_reuse_action(5, name, 6), ROW_UPGRADED)

    def test_v4_rereads_mkv_and_webm_only(self) -> None:
        self.assertEqual(row_reuse_action(4, "clip.mkv", 6), ROW_REREAD)
        self.assertEqual(row_reuse_action(4, "clip.webm", 6), ROW_REREAD)
        self.assertEqual(row_reuse_action(4, "clip.mp4", 6), ROW_UPGRADED)

    def test_too_old_or_future_version_is_legacy(self) -> None:
        self.assertEqual(row_reuse_action(2, "clip.mp4", 6), ROW_LEGACY)
        self.assertEqual(row_reuse_action(99, "clip.mp4", 6), ROW_LEGACY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
