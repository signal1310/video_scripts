"""화질 분석 1패스 다중 규격 split SSIM 명령과 로그 수집 검증"""

import tempfile
import unittest
from pathlib import Path

from src.core.analysis.frame_analysis import ActiveRegion, candidate_tiers_for
from src.core.analysis.frame_fast_metrics import (
    build_split_ssim_command,
    clear_metric_logs,
    parse_ssim_logs,
    ssim_log_name,
    vmaf_log_name,
)


class TestLogNames(unittest.TestCase):
    """지점과 규격별 로그 이름 검증"""

    def test_log_names_separate_points_and_tiers(self) -> None:
        self.assertEqual(ssim_log_name(4, "720p"), "ssim_4_720p.log")
        self.assertEqual(vmaf_log_name(2), "vmaf_2.json")


class TestSplitSsimCommand(unittest.TestCase):
    """1패스 다중 규격 SSIM 명령 구성 검증"""

    def _build(
        self,
        tiers: int,
        *,
        bars: bool = False,
        fast_seek: bool = True,
    ) -> tuple[list[str], dict[str, str]]:
        candidates = candidate_tiers_for(1920, 1080)[:tiers]
        names = {tier.key: ssim_log_name(3, tier.key) for tier in candidates}
        region = ActiveRegion(1280, 720, 100, 20, bars)
        args = build_split_ssim_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=1.0,
            duration_sec=1.0,
            active_region=region,
            tiers=candidates,
            log_names=names,
            fast_keyframe_seek=fast_seek,
        )
        return args, names

    def test_single_tier_uses_one_split(self) -> None:
        args, names = self._build(1)
        chain = args[args.index("-filter_complex") + 1]
        self.assertIn("split=2[ref][d0]", chain)
        self.assertIn(f"ssim=stats_file={names['900p']}", chain)
        self.assertEqual(args.count("-map"), 1)
        self.assertIn("-noaccurate_seek", args)

    def test_multiple_tiers_share_one_input_decode(self) -> None:
        args, names = self._build(3)
        chain = args[args.index("-filter_complex") + 1]
        self.assertIn("split=4[base][d0][d1][d2]", chain)
        self.assertIn("[base]split=3[r0][r1][r2]", chain)
        for name in names.values():
            self.assertIn(name, chain)
        self.assertEqual(args.count("-map"), 3)

    def test_black_bars_are_cropped_first_and_precise_seek_is_accurate(self) -> None:
        args, _names = self._build(2, bars=True, fast_seek=False)
        chain = args[args.index("-filter_complex") + 1]
        self.assertTrue(chain.startswith("[0:v]crop=1280:720:100:20,split=3"))
        self.assertNotIn("-noaccurate_seek", args)

    def test_chains_use_the_requested_scale_algorithm(self) -> None:
        """규격 체인의 왕복 scale 필터가 지정한 보간 방식을 쓰는지 검증"""
        candidates = candidate_tiers_for(1920, 1080)[:2]
        names = {tier.key: ssim_log_name(0, tier.key) for tier in candidates}
        args = build_split_ssim_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            duration_sec=1.0,
            active_region=ActiveRegion(1280, 720, 0, 0, False),
            tiers=candidates,
            log_names=names,
            fast_keyframe_seek=True,
            algorithm="spline",
        )
        chain = args[args.index("-filter_complex") + 1]
        self.assertEqual(chain.count("flags=spline"), 4)
        self.assertNotIn("flags=bicubic", chain)


class TestSsimLogs(unittest.TestCase):
    """SSIM 로그 읽기와 이전 로그 삭제 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_dir = tmp.name

    def test_parse_reads_existing_logs_only(self) -> None:
        Path(self.work_dir, "a.log").write_text("n:1 Y:1 U:1 V:1 All:0.99700 (25.00)\n", encoding="utf-8")
        Path(self.work_dir, "bad.log").write_text("no stats", encoding="utf-8")
        scores = parse_ssim_logs(
            self.work_dir,
            {"720p": "a.log", "540p": "missing.log", "480p": "bad.log"},
        )
        self.assertEqual(scores, {"720p": (0.997, 25.0)})

    def test_clear_removes_existing_logs(self) -> None:
        Path(self.work_dir, "a.log").write_text("x", encoding="utf-8")
        clear_metric_logs(self.work_dir, ["a.log", "missing.log"])
        self.assertFalse(Path(self.work_dir, "a.log").exists())


if __name__ == "__main__":
    unittest.main()
