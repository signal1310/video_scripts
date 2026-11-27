"""화질 분석 1패스 다중 규격 split SSIM 명령과 로그 수집 검증"""

import tempfile
import unittest
from pathlib import Path

from src.core.analysis.frame_analysis import ActiveRegion, candidate_tiers_for
from src.core.analysis.frame_fast_metrics import (
    build_split_ssim_command,
    clear_metric_logs,
    moving_score_stats,
    parse_ssim_logs,
    ssim_log_name,
    vmaf_log_name,
)
from src.core.quality_probe import SsimStats


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
        self.assertEqual(set(scores), {"720p"})
        self.assertAlmostEqual(scores["720p"].score, 0.997)
        # - -10 * log10(1 - 0.997) = 25.2288dB
        self.assertAlmostEqual(scores["720p"].db, 25.2288, places=4)

    def test_parse_keeps_frame_scores_and_static_markers(self) -> None:
        """축소 왕복 로그는 같은 화면(inf) 프레임을 평균에서 1로 넣고 프레임 목록에는 None으로 남김"""
        loss_score = round(1.0 - 10.0 ** (-11.0 / 10.0), 6)
        lines = [f"n:{index} Y:1 U:1 V:1 All:1.000000 (inf)\n" for index in range(29)]
        lines += [f"n:29 Y:1 U:1 V:1 All:{loss_score:.6f} (11.00)\n"]
        Path(self.work_dir, "static.log").write_text("".join(lines), encoding="utf-8")
        scores = parse_ssim_logs(self.work_dir, {"720p": "static.log"})
        self.assertAlmostEqual(scores["720p"].score, (29 + loss_score) / 30, places=6)
        # - -10 * log10(1 - 0.997352) = 25.7712dB이며 프레임별 dB 평균 11dB와 구분됨
        self.assertAlmostEqual(scores["720p"].db, 25.7712, places=3)
        self.assertEqual(scores["720p"].frame_scores[:29], (None,) * 29)

    def test_moving_population_uses_frames_moving_in_any_tier(self) -> None:
        """같은 화면이 아닌 프레임만 모은 공통 모집단으로 규격 점수를 다시 계산하는지 검증

        - 1440p는 29프레임이 같은 화면이고 1080p는 24프레임만 같음
        - 모집단은 두 규격 중 한 곳에서라도 움직인 프레임이므로 1080p의 움직이는 프레임 손실이
          1440p 점수에도 1.0으로 들어가 정지 프레임이 손실을 가리지 못함
        """
        static_1440 = [None] * 29 + [0.99]
        static_1080: list[float | None] = [None] * 24 + [0.9995] * 5 + [0.985]
        population = moving_score_stats(
            {
                "1440p": SsimStats(0.9967, 0.0, tuple(static_1440)),
                "1080p": SsimStats(0.99975, 0.0, tuple(static_1080)),
            }
        )
        self.assertAlmostEqual(population["1440p"].score, (5 * 1.0 + 0.99) / 6, places=6)
        self.assertAlmostEqual(population["1080p"].score, (5 * 0.9995 + 0.985) / 6, places=6)
        self.assertEqual(population["1440p"].frame_scores, (1.0, 1.0, 1.0, 1.0, 1.0, 0.99))

    def test_all_identical_frames_leave_an_empty_population(self) -> None:
        """모든 규격에서 모든 프레임이 같은 화면이면 정지 지점으로 보아 빈 사전을 반환"""
        stats = SsimStats(1.0, 100.0, (None, None))
        self.assertEqual(moving_score_stats({"720p": stats, "480p": stats}), {})
        self.assertEqual(moving_score_stats({}), {})


    def test_clear_removes_existing_logs(self) -> None:
        Path(self.work_dir, "a.log").write_text("x", encoding="utf-8")
        clear_metric_logs(self.work_dir, ["a.log", "missing.log"])
        self.assertFalse(Path(self.work_dir, "a.log").exists())


if __name__ == "__main__":
    unittest.main()
