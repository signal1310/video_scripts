import math
import unittest

from src.core.config import BitrateModelConfig, CodecWeightsConfig
from src.core.evaluation import quality_metrics
from src.core.resolution_tiers import (
    RESOLUTION_TIERS,
    TIER_BOUNDARIES,
    Orientation,
    ResolutionTier,
    build_scale_expression,
    calc_scaled_dimensions,
    copy_manual_targets,
    find_source_tier,
    is_meaningful_downscale,
    orientation_of,
)

_TIER_BY_KEY = {tier.key: tier for tier in RESOLUTION_TIERS}
_SAMPLE_SIDES = (
    320, 360, 480, 640, 720, 854, 960, 1000, 1079, 1080, 1279, 1280, 1440, 1600, 1919, 1920,
    2048, 2159, 2160, 2560, 3000, 3839, 3840, 4096, 7680,
)


def _ffmpeg_emulated_dimensions(iw: int, ih: int, tier: ResolutionTier) -> tuple[int, int]:
    """build_scale_expression의 식을 Python float로 재현한 출력 크기"""
    long_side, short_side = float(tier.long_side), float(tier.short_side)
    landscape = iw >= ih
    fit = min(
        1.0,
        min(
            (long_side if landscape else short_side) / iw,
            (short_side if landscape else long_side) / ih,
        ),
    )
    return (
        math.trunc(iw * fit / 4 + 0.000001) * 4,
        math.trunc(ih * fit / 4 + 0.000001) * 4,
    )


class TestCalcScaledDimensions(unittest.TestCase):
    """출력 크기 계산의 규격 예시, 방향, 확대 금지, 4의 배수 내림을 검증"""

    def test_documented_examples(self) -> None:
        """계획 2절 예시와 Phase 0 시험 영상의 기대 출력 크기 일치"""
        cases = [
            ((1920, 1080, "480p"), (852, 480)),
            ((2560, 1080, "720p"), (1280, 540)),
            ((1080, 1920, "720p"), (720, 1280)),
            ((2048, 1080, "1080p"), (1920, 1012)),
            ((854, 480, "480p"), (852, 480)),
        ]
        for (width, height, key), expected in cases:
            with self.subTest(width=width, height=height, key=key):
                self.assertEqual(calc_scaled_dimensions(width, height, _TIER_BY_KEY[key]), expected)

    def test_square_is_landscape_and_keeps_size_within_box(self) -> None:
        """정방형은 가로형으로 보고 1080p 박스(1920x1080)에 맞춰 원본 크기를 유지"""
        self.assertIs(orientation_of(1080, 1080), Orientation.LANDSCAPE)
        self.assertEqual(calc_scaled_dimensions(1080, 1080, _TIER_BY_KEY["1080p"]), (1080, 1080))

    def test_never_upscales(self) -> None:
        """원본이 규격보다 작으면 확대하지 않고 4의 배수 내림만 적용"""
        self.assertEqual(calc_scaled_dimensions(640, 360, _TIER_BY_KEY["1440p"]), (640, 360))
        self.assertEqual(calc_scaled_dimensions(1278, 718, _TIER_BY_KEY["720p"]), (1276, 716))


class TestDownscaleJudgement(unittest.TestCase):
    """95% 화소 기준의 직전, 직후 경계와 원본 규격 판정을 검증"""

    def test_area_ratio_boundary(self) -> None:
        """1280x735는 95% 초과로 축소 아님, 1280x740은 95% 미만으로 축소"""
        tier = _TIER_BY_KEY["720p"]
        self.assertFalse(is_meaningful_downscale(1280, 735, tier))
        self.assertTrue(is_meaningful_downscale(1280, 740, tier))

    def test_four_multiple_loss_is_not_downscale(self) -> None:
        """864x480은 480p로 4의 배수 내림(852x472)만 생겨 축소로 보지 않음"""
        tier = _TIER_BY_KEY["480p"]
        self.assertEqual(calc_scaled_dimensions(864, 480, tier), (852, 472))
        self.assertFalse(is_meaningful_downscale(864, 480, tier))

    def test_source_tier_examples(self) -> None:
        """원본을 축소 없이 담는 가장 작은 규격과 세로형, 장변 초과 사례 판정"""
        cases = [
            ((864, 480), "480p"),
            ((854, 480), "480p"),
            ((2560, 1080), "1440p"),
            ((1080, 1920), "1080p"),
            ((1920, 1080), "1080p"),
            ((1930, 1080), "1080p"),
        ]
        for (width, height), expected_key in cases:
            with self.subTest(width=width, height=height):
                tier = find_source_tier(width, height)
                self.assertIsNotNone(tier)
                assert tier is not None
                self.assertEqual(tier.key, expected_key)

    def test_source_tier_none_above_top_tier(self) -> None:
        """최상위 4K 박스를 넘는 원본은 규격이 없어 None"""
        self.assertIsNone(find_source_tier(7680, 4320))
        self.assertIsNone(find_source_tier(4096, 2160))

    def test_tiny_source_within_box_is_not_downscale(self) -> None:
        """박스 안의 작은 원본은 4의 배수 내림 손실이 커도 축소가 아니고 최하위 규격으로 판정"""
        tier = _TIER_BY_KEY["360p"]
        for width, height in ((1, 1), (50, 50), (62, 62)):
            with self.subTest(width=width, height=height):
                self.assertFalse(is_meaningful_downscale(width, height, tier))
                source_tier = find_source_tier(width, height)
                assert source_tier is not None
                self.assertEqual(source_tier.key, "360p")


class TestFfmpegExpression(unittest.TestCase):
    """FFmpeg 식이 Phase 0 검증 문자열과 같고, Python 계산과 격자 전체에서 일치함을 검증"""

    def test_720p_expression_matches_phase0_string(self) -> None:
        """720p 식 문자열이 Phase 0 검증에 쓰인 식과 한 글자도 다르지 않음"""
        expected = (
            "scale='trunc(iw*min(1,min(if(gte(iw,ih),1280,720)/iw,if(gte(iw,ih),720,1280)/ih))/4+0.000001)*4'"
            ":'trunc(ih*min(1,min(if(gte(iw,ih),1280,720)/iw,if(gte(iw,ih),720,1280)/ih))/4+0.000001)*4'"
        )
        self.assertEqual(build_scale_expression(_TIER_BY_KEY["720p"]), expected)

    def test_float_emulation_matches_exact_dimensions(self) -> None:
        """모든 규격과 해상도 격자에서 float로 재현한 식 결과가 Fraction 계산과 같음"""
        for tier in RESOLUTION_TIERS:
            for width in _SAMPLE_SIDES:
                for height in _SAMPLE_SIDES:
                    with self.subTest(tier=tier.key, width=width, height=height):
                        self.assertEqual(
                            _ffmpeg_emulated_dimensions(width, height, tier),
                            calc_scaled_dimensions(width, height, tier),
                        )


class TestTierDefinitions(unittest.TestCase):
    """규격 경계, 방향 박스, 최대 비트레이트 상수가 기준 정의와 맞는지 검증"""

    def test_boundaries_are_unique_sorted_and_cover_all_tiers(self) -> None:
        """경계 16개가 겹치지 않고 오름차순이며 단변과 장변이 모두 포함됨"""
        values = [boundary.value for boundary in TIER_BOUNDARIES]
        self.assertEqual(len(values), 16)
        self.assertEqual(values, sorted(set(values)))
        self.assertEqual(sum(1 for b in TIER_BOUNDARIES if b.is_long_side), 8)

    def test_box_orientation(self) -> None:
        """가로형은 장변이 너비, 세로형은 장변이 높이"""
        tier = _TIER_BY_KEY["720p"]
        self.assertEqual(tier.box(Orientation.LANDSCAPE), (1280, 720))
        self.assertEqual(tier.box(Orientation.PORTRAIT), (720, 1280))

    def test_max_bitrate_is_hevc60_optimal_times_1_5_rounded_up(self) -> None:
        """최대 비트레이트가 기본 설정 HEVC 60fps 최적값 x 1.5의 100k 단위 올림과 같음"""
        bitrate_model = BitrateModelConfig()
        codec_weights = CodecWeightsConfig()
        for tier in RESOLUTION_TIERS:
            with self.subTest(tier=tier.key):
                optimal = quality_metrics.calc_optimal_bitrate(
                    tier.long_side, tier.short_side, bitrate_model, codec_weights, 60.0, "hevc"
                )
                expected = math.ceil(optimal * 1.5 / 100) * 100
                self.assertEqual(tier.max_bitrate_kbps, expected)

    def test_bufsize_is_twice_max_bitrate(self) -> None:
        """VBV 버퍼 크기가 최대 비트레이트의 2배"""
        for tier in RESOLUTION_TIERS:
            with self.subTest(tier=tier.key):
                self.assertEqual(tier.bufsize_kbps, tier.max_bitrate_kbps * 2)


class TestCopyManualTargets(unittest.TestCase):
    """직접 지정 맵 복사가 원본과 독립적이고 빈 폴더 제외 옵션을 따르는지 검증"""

    def test_copy_is_independent_of_source(self) -> None:
        """복사본을 바꿔도 원본의 안쪽 맵이 바뀌지 않음을 검증"""
        source = {"C:/work": {"a.mp4": "720p"}}
        copied = copy_manual_targets(source)
        copied["C:/work"]["a.mp4"] = "keep"
        copied["C:/other"] = {"b.mp4": "480p"}
        self.assertEqual(source, {"C:/work": {"a.mp4": "720p"}})

    def test_empty_folders_are_kept_by_default_and_dropped_on_request(self) -> None:
        """항목이 없는 폴더는 기본으로 유지하고 drop_empty일 때만 제외함을 검증"""
        source = {"C:/work": {"a.mp4": "720p"}, "C:/empty": {}}
        self.assertEqual(copy_manual_targets(source), source)
        self.assertEqual(copy_manual_targets(source, drop_empty=True), {"C:/work": {"a.mp4": "720p"}})


if __name__ == "__main__":
    unittest.main()
