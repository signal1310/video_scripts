"""판정 원리 탭의 앵커 절과 내부 고정 기준값 안내 표 검증"""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import (
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    SSIM_CONFIRMED_FAIL_OFFSET,
    SSIM_CONFIRMED_PASS_OFFSET,
    VMAF_CONFIRMED_OFFSET,
    VMAF_SUBSAMPLE,
    VR_MIN_LONG_SIDE,
)
from src.core.analysis.frame_scan import BLACK_FRAME_PIXEL_RATIO, BLACK_LUMA_THRESHOLD
from src.core.config import (
    PRECISE_QUALITY_MODE_SELECTIVE,
    ClassifierConfig,
    EncodeConfig,
    ThresholdsConfig,
)
from src.core.encode.encode_gate import DURATION_TOLERANCE_MIN_SEC, DURATION_TOLERANCE_RATIO
from src.core.encode.precise_tune_policy import (
    PRECISE_TUNE_BOUNDARY_MARGIN,
    PRECISE_TUNE_COMPARE_TOLERANCE,
    should_precise_tune,
)
from src.core.encoder_specs import resolve_level_cap_kbps
from src.core.evaluation.classification_policy import CODEC_UPGRADE_MIN_GAIN_RATIO
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.evaluation.quality_metrics import (
    SAME_OR_BETTER_CODEC_OVER_MARGIN,
    TEN_BIT_SDR_OVER_MARGIN,
    calc_optimal_pair,
)
from src.core.evaluation.size_estimator import (
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.models import BYTES_PER_MB
from src.core.probe_parser import BITS_PER_BYTE, BITS_PER_KBIT
from src.core.quality_target import (
    MAXRATE_HEADROOM_MULTIPLIER,
    corrected_target_kbps,
    general_maxrate_kbps,
)
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.smart_action import SmartAction
from src.gui.help import help_concepts_page
from src.gui.help.help_concepts_page import build_concepts_html, example_video
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._factories import make_eval

_APP = QApplication.instance() or QApplication([])


class TestHelpConceptsPage(unittest.TestCase):
    """판정 원리 탭의 절 앵커, 화질 분석 원리, 내부 기준값 표 검증"""

    def _html(self, cfg: ClassifierConfig | None = None) -> str:
        return build_concepts_html(
            LIGHT_PALETTE, cfg or ClassifierConfig(), DEFAULT_SHORTCUTS
        )

    def test_section_anchors_exist_for_linked_cards(self) -> None:
        """설정 화면 카드의 물음표가 가리키는 앵커가 본문에 모두 있는지 검증"""
        html = self._html()
        for anchor in (
            "optimal-bitrate",
            "ratio",
            "size",
            "savings",
            "time",
            "order",
            "downscale",
            "frame-analysis",
            "internal-thresholds",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(f'<a name="{anchor}"></a>', html)

    def test_frame_analysis_section_uses_current_setting_values(self) -> None:
        """화질 분석 절이 현재 축소 후보 기준과 자동 적용 설정을 반영하는지 검증"""
        cfg = ClassifierConfig(
            encode=EncodeConfig(
                roundtrip_vmaf_threshold=97.5,
                roundtrip_ssim_threshold=0.994,
                active_area_no_bars_ratio=0.85,
                auto_apply_frame_target=False,
            )
        )
        html = self._html(cfg)
        self.assertIn("97.5점", html)
        self.assertIn("0.994", html)
        self.assertIn("85%", html)
        self.assertIn("화질 분석 축소 후보 자동 적용: 사용 안 함", html)

    def test_internal_thresholds_table_reads_core_constants(self) -> None:
        """내부 기준값 표가 코어 상수를 그대로 표시하는지 검증"""
        html = self._html()
        self.assertIn(f"평균 휘도 {BLACK_LUMA_THRESHOLD} 미만", html)
        self.assertIn(f"{BLACK_FRAME_PIXEL_RATIO:.0%}", html)
        self.assertIn(f"{EDGE_SNAP_RATIO:.0%} 또는 {EDGE_SNAP_MIN_PX}px", html)
        self.assertIn(f"장변 {VR_MIN_LONG_SIDE}px 이상", html)
        self.assertIn(f">{VMAF_SUBSAMPLE}<", html)
        self.assertIn(f"{DURATION_TOLERANCE_MIN_SEC:g}초", html)
        self.assertIn(f"{DURATION_TOLERANCE_RATIO:.1%}", html)
        self.assertIn("+2 FPS", html)

    def test_internal_thresholds_table_lists_encoder_slopes(self) -> None:
        """인코더별 기울기와 크기 보정 표가 코어 정의를 반영하는지 검증"""
        html = self._html()
        self.assertIn("hevc_nvenc", html)
        self.assertIn("크기 보정", html)
        self.assertIn("화질 기준값 비트레이트 기울기", html)

    def test_outline_counts_nine_steps(self) -> None:
        """개요가 본문 아홉 절과 1~9 번호를 그대로 안내하는지 검증"""
        html = self._html()
        self.assertIn("아홉 단계", html)
        self.assertNotIn("여덟 단계", html)
        for title in (
            "최적 비트레이트",
            "BR 비율",
            "예상 용량과 절약",
            "절약 선별",
            "예상 시간",
            "판정 순서",
            "최대 해상도 축소 인코딩 판정",
            "화질 분석과 검은 여백 감지 원리",
            "내부 고정 기준값 안내",
        ):
            with self.subTest(title=title):
                self.assertIn(f"<li><b>{title}</b>", html)
        for heading in (
            "7. 최대 해상도 축소 인코딩 판정",
            "8. 화질 분석과 검은 여백 감지 원리",
            "9. 내부 고정 기준값 안내",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, html)

    def test_concepts_docstring_matches_nine_sections(self) -> None:
        """판정 원리 문서 문자열이 아홉 단계 구성을 설명하는지 검증"""
        self.assertIn("아홉 단계", build_concepts_html.__doc__ or "")

    def test_vr_protection_uses_the_long_side(self) -> None:
        """VR 자동 보호 기준이 단변이 아니라 장변 기준으로 표기되는지 검증"""
        html = self._html()
        self.assertIn(f"장변 {VR_MIN_LONG_SIDE}px 이상", html)
        self.assertNotIn(f"단변 {VR_MIN_LONG_SIDE}px", html)

    def test_ratio_and_order_sections_follow_current_settings(self) -> None:
        """판별 여유와 코덱 전환 최소 배수가 고정 상수 대신 현재 설정값으로 표시되는지 검증"""
        cfg = ClassifierConfig(
            thresholds=ThresholdsConfig(
                same_or_better_codec_over_margin=0.42,
                ten_bit_sdr_over_margin=0.37,
                codec_upgrade_min_gain_ratio=2.75,
            )
        )
        html = self._html(cfg)
        self.assertIn("+0.42", html)
        self.assertIn("+0.37", html)
        self.assertIn("<code>2.75배</code>", html)
        self.assertIn("≥ 2.75</code>", html)
        for constant in (
            f"+{SAME_OR_BETTER_CODEC_OVER_MARGIN:.2f}",
            f"+{TEN_BIT_SDR_OVER_MARGIN:.2f}",
            f"{CODEC_UPGRADE_MIN_GAIN_RATIO:.2f}배</code>",
            f"≥ {CODEC_UPGRADE_MIN_GAIN_RATIO:.2f}</code>",
        ):
            with self.subTest(constant=constant):
                self.assertNotIn(constant, html)

    def test_early_confirm_shows_both_ssim_offsets(self) -> None:
        """조기 확정 여유폭이 SSIM 통과, 탈락 오프셋을 모두 보여 주는지 검증"""
        html = self._html()
        self.assertIn(
            f"SSIM +{SSIM_CONFIRMED_PASS_OFFSET:.3f} / -{SSIM_CONFIRMED_FAIL_OFFSET:.3f}", html
        )
        self.assertIn(f"VMAF +{VMAF_CONFIRMED_OFFSET:.1f} / -{VMAF_CONFIRMED_OFFSET:.1f}", html)

    def test_maxrate_text_matches_applied_scope(self) -> None:
        """최대 비트레이트 적용 범위와 레벨 상한 한정 표기가 코드와 같은지 검증"""
        cfg = ClassifierConfig()
        html = self._html(cfg)
        self.assertIn("크기를 줄이지 않는 일반 인코딩은 명시 최대 비트레이트 없이 인코딩합니다", html)
        self.assertIn("최대 비트레이트가 있으면 영상 스트림 용량 = min", html)
        # - 레벨 상한은 NVENC 계열에만 있으므로 다른 인코더는 상한 없음으로 계산됨
        codec = cfg.encode.encode_codec
        self.assertIsNotNone(resolve_level_cap_kbps(codec, 3840, 2160, 60.0))
        self.assertIsNone(resolve_level_cap_kbps("libx265", 3840, 2160, 60.0))
        self.assertIn("NVENC 계열 인코더 레벨 상한", html)
        self.assertIn("NVENC 계열의 레벨 상한만 적용", html)

    def test_estimated_size_example_includes_the_maxrate_cap_term(self) -> None:
        """3절 예시가 상한 항을 포함해 항 최솟값으로 닫히고 평가 값과 같은지 검증"""
        cfg = ClassifierConfig()
        cfg.encode.max_resolution_tier = "1080p"
        html = self._html(cfg)
        video = example_video(cfg)
        evaluation = VideoEvaluator.evaluate_video(video, cfg)
        self.assertTrue(evaluation.is_downscaled)
        self.assertIn("위 4개 항 중 가장 작은 값", html)
        self.assertIn(f"<b>{evaluation.expected_after_mb:,.1f} MB</b>", html)
        self.assertNotIn("위 3개 항 중 가장 작은 값", html)

    def test_estimated_size_example_cap_term_arithmetic_adds_up(self) -> None:
        """상한 항의 인용 식이 실제 상한 전 영상 스트림으로 계산한 값과 일치하는지 검증"""
        cfg = ClassifierConfig()
        cfg.encode.max_resolution_tier = "1080p"
        video = example_video(cfg)
        evaluation = VideoEvaluator.evaluate_video(video, cfg)
        html = self._html(cfg)
        self.assertTrue(evaluation.is_downscaled)
        tier = evaluation.target_tier
        assert tier is not None
        other_mb = video.vid_size_MB - estimate_video_stream_mb(video)
        source_ratio = video.vid_kbps / calc_optimal_pair(
            replace(
                video,
                width=evaluation.target_width,
                height=evaluation.target_height,
            ),
            cfg,
        )[0]
        stream_after_mb = (
            estimate_after_size_mb(
                video,
                evaluation.target_bitrate_ratio,
                cfg.thresholds.keyframe_1s_increase_rate,
                cap_at_original=True,
                source_ratio=source_ratio,
                codec_weight_ratio=cfg.get_codec_weight("hevc")
                / cfg.get_codec_weight(video.codec),
                base_fps=cfg.bitrate_model.base_fps,
                output_delta_cq=(evaluation.output_cq or cfg.encode.baseline_cq)
                - cfg.encode.baseline_cq,
                encoder=cfg.encode.encode_codec,
            )
            - other_mb
        )
        cap_kbps = general_maxrate_kbps(
            corrected_target_kbps(
                evaluation.target_optimal_kbps, cfg.encode.encode_codec
            ),
            tier.max_bitrate_kbps,
        )
        cap_stream_mb = (
            cap_kbps * BITS_PER_KBIT * video.duration / BITS_PER_BYTE / BYTES_PER_MB
        )
        # - 인용 식과 표시 수치를 그대로 맞대어 상한 항이 실제 산술과 어긋나지 않는지 확인
        self.assertLess(cap_stream_mb, stream_after_mb)
        self.assertIn(f"min(상한 전 영상 스트림 {stream_after_mb:,.1f}, ", html)
        self.assertIn(
            f"= {other_mb + min(stream_after_mb, cap_stream_mb):,.1f} MB<br/>",
            html,
        )

    def test_passed_decision_example_separates_encoding_minimum(self) -> None:
        """원본 보존 판정이면 인코딩 최솟값과 판정 예상 용량을 원본 용량으로 구분하는지 검증"""
        cfg = ClassifierConfig()
        cfg.thresholds.keyframe_interval_sec = 20.0
        cfg.savings.min_saved_mb = 99999.0
        video = example_video(cfg)
        evaluation = VideoEvaluator.evaluate_video(video, cfg)
        self.assertFalse(evaluation.smart_action.is_encode)
        html = self._html(cfg)
        self.assertIn(f"판정 예상 용량 = <b>{video.vid_size_MB:,.1f} MB</b>", html)
        self.assertIn("(원본 보존 판정, 인코딩 예상은 참고값)", html)
        self.assertIn("절약 ΔS = <b>0.0 MB</b>", html)

    def test_cap_term_names_the_level_cap_without_explicit_maxrate(self) -> None:
        """명시 최대 비트레이트 없이 레벨 상한만 적용되면 상한 항 이름이 레벨 상한인지 검증"""
        cfg = ClassifierConfig()
        video = example_video(cfg)
        evaluation = VideoEvaluator.evaluate_video(video, cfg)
        self.assertIsNone(evaluation.target_tier)
        self.assertIsNotNone(
            resolve_level_cap_kbps(
                cfg.encode.encode_codec, video.width, video.height, video.fps
            )
        )
        html = self._html(cfg)
        self.assertIn("레벨 상한 = 60.0 + min(상한 전 영상 스트림", html)
        self.assertNotIn("최대 비트레이트 상한 = 60.0 + min(", html)

    def test_downscale_section_uses_the_headroom_constant(self) -> None:
        """해상도 되돌림 문구가 여유 배수를 상수에서 읽는지 검증"""
        html = self._html()
        self.assertIn(f"{MAXRATE_HEADROOM_MULTIPLIER:.1f}배 올림 중 작은 값", html)
        self.assertIn(
            f"(규격 상한이 없으면 {MAXRATE_HEADROOM_MULTIPLIER:.1f}배 올림 값)", html
        )
        # - 상수 값을 바꿨을 때 문구도 함께 바뀌어야 하드코딩이 아님을 확인
        with patch.object(help_concepts_page, "MAXRATE_HEADROOM_MULTIPLIER", 1.7):
            patched = self._html()
        self.assertIn("1.7배 올림 중 작은 값", patched)
        self.assertIn("(규격 상한이 없으면 1.7배 올림 값)", patched)
        self.assertNotIn("1.5배 올림 중 작은 값", patched)

    def test_precise_tune_band_text_matches_should_precise_tune(self) -> None:
        """문서의 정밀 결정 비율 구간이 should_precise_tune 실제 경계와 일치하는지 검증"""
        cfg = ClassifierConfig()
        html = self._html(cfg)
        over = cfg.thresholds.over_bitrate_ratio
        low = cfg.thresholds.low_bitrate_ratio
        limit = PRECISE_TUNE_BOUNDARY_MARGIN + PRECISE_TUNE_COMPARE_TOLERANCE
        self.assertIn("원본 코덱 기준 비트레이트 비율", html)
        self.assertIn(f"과화질 {over:.2f}배, 저화질 {low:.2f}배", html)
        self.assertIn(f"(과화질은 {over + limit:.2f}배까지)", html)
        self.assertIn("유효 상한(동급 코덱, 10비트 여유를 더한 값)과는 별개", html)
        for ratio, expected in (
            (over + limit, True),
            (over + PRECISE_TUNE_BOUNDARY_MARGIN * 2.0, False),
            (over + 0.48, False),
            (low - limit, True),
        ):
            ev = make_eval(smart_action=SmartAction.ENCODE_OVER, bitrate_ratio=ratio)
            with self.subTest(ratio=ratio):
                self.assertEqual(
                    should_precise_tune(ev, PRECISE_QUALITY_MODE_SELECTIVE, cfg), expected
                )

    def test_downscale_comparison_uses_the_distinct_term(self) -> None:
        """정밀 결정 뒤 해상도 비교가 화질 비교 창의 축소 비교와 구분되는지 검증"""
        html = self._html()
        self.assertIn("원본/축소 해상도 비교", html)
        self.assertNotIn("아직 판정에 연결하지 않았습니다", html)
        self.assertNotIn("비교하는 축소 비교를", html)

    def test_no_retired_terms_in_concepts_html(self) -> None:
        """판정 원리 본문에 폐기어와 가운뎃점이 남지 않았는지 검증"""
        html = self._html()
        for retired in ("프레임레이트", "교정", "·", "무결성 검사"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
