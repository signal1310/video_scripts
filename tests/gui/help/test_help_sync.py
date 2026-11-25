"""F1 도움말 수식, 예시 숫자가 실제 평가 코드 출력과 어긋나지 않는지 검증하는 동기화 테스트"""

import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.encoder_specs import get_encode_codec_family
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.evaluation.quality_metrics import calc_optimal_bitrate
from src.core.evaluation.size_estimator import (
    calc_keyframe_increase_ratio,
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.quality_target import (
    PROTECT_CQ_DELTA_MAX,
    encoder_size_factor,
    format_cq_value,
    protect_cq_value,
)
from src.gui.help.help_concepts_page import build_concepts_html, example_video
from src.gui.help.help_guide_pages import build_guide_html
from src.gui.styles.palettes import LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])


def _strip_tags(html_text: str) -> str:
    """HTML 태그를 지워 화면에 보이는 글자만 남김"""
    return re.sub(r"<[^>]+>", "", html_text)


class HelpFormulaSyncTests(unittest.TestCase):
    """판정 원리 1, 3, 4, 6절 숫자가 실제 평가 함수 출력과 같은지 검증"""

    def setUp(self) -> None:
        self.config = ClassifierConfig()
        self.video = example_video(self.config)
        self.evaluation = VideoEvaluator.evaluate_video(self.video, self.config)
        self.html = build_concepts_html(LIGHT_PALETTE, self.config)
        self.text = _strip_tags(self.html)

    def test_optimal_bitrate_example_matches_calc_optimal_bitrate(self) -> None:
        """1절 예시의 B_opt 값이 encoder 인자를 넘긴 실제 함수 출력과 같은지 검증"""
        codec = self.config.encode.encode_codec
        family = get_encode_codec_family(codec)
        expected = calc_optimal_bitrate(
            self.video.width,
            self.video.height,
            self.config.bitrate_model,
            self.config.codec_weights,
            self.video.fps,
            family,
            self.config.encode.baseline_cq,
            encoder=codec,
        )
        without_encoder = calc_optimal_bitrate(
            self.video.width,
            self.video.height,
            self.config.bitrate_model,
            self.config.codec_weights,
            self.video.fps,
            family,
            self.config.encode.baseline_cq,
        )
        self.assertIn(f"{expected:,} kbps", self.text)
        self.assertEqual(without_encoder, expected)

    def test_optimal_bitrate_example_follows_the_output_codec_k(self) -> None:
        """기준 품질값이 다르면 encoder 인자 유무가 결과를 가르고, 도움말이 그 값을 쓰는지 검증"""
        config = ClassifierConfig()
        config.encode.baseline_cq = 26.0
        video = example_video(config)
        family = get_encode_codec_family(config.encode.encode_codec)
        with_encoder = calc_optimal_bitrate(
            video.width,
            video.height,
            config.bitrate_model,
            config.codec_weights,
            video.fps,
            family,
            config.encode.baseline_cq,
            encoder=config.encode.encode_codec,
        )
        without_encoder = calc_optimal_bitrate(
            video.width,
            video.height,
            config.bitrate_model,
            config.codec_weights,
            video.fps,
            family,
            config.encode.baseline_cq,
        )
        self.assertNotEqual(with_encoder, without_encoder)
        concepts = _strip_tags(build_concepts_html(LIGHT_PALETTE, config))
        self.assertIn(f"{with_encoder:,} kbps", concepts)

    def test_estimated_size_example_matches_evaluate_video(self) -> None:
        """3절 예상 용량, 절약 값이 VideoEvaluator 결과와 같은지 검증"""
        self.assertIn(f"{self.evaluation.expected_after_mb:,.1f} MB", self.text)
        self.assertIn(f"{self.evaluation.expected_delta_mb:,.1f} MB", self.text)
        self.assertIn(f"{self.evaluation.target_bitrate_ratio:.3f}배", self.text)

    def test_estimated_size_uses_estimate_after_size_mb_with_size_factor(self) -> None:
        """3절 상한 전 용량이 인코더 크기 보정을 반영한 실제 추정 함수와 같은지 검증"""
        cfg = self.config
        codec = cfg.encode.encode_codec
        output_cq = (
            self.evaluation.output_cq
            if self.evaluation.output_cq is not None
            else cfg.encode.baseline_cq
        )
        expected = estimate_after_size_mb(
            self.video,
            self.evaluation.target_bitrate_ratio,
            cfg.thresholds.keyframe_1s_increase_rate,
            cap_at_original=True,
            source_ratio=self.video.vid_kbps / self.evaluation.optimal_kbps,
            codec_weight_ratio=(
                cfg.get_codec_weight(get_encode_codec_family(codec))
                / cfg.get_codec_weight(self.video.codec)
            ),
            base_fps=cfg.bitrate_model.base_fps,
            output_delta_cq=output_cq - cfg.encode.baseline_cq,
            encoder=codec,
        )
        self.assertIn(f"= {expected:,.1f} MB", self.text)
        self.assertGreater(encoder_size_factor(codec), 1.0)
        self.assertIn(f"E = {encoder_size_factor(codec):.2f}", self.text)

    def test_keyframe_target_uses_calc_keyframe_increase_ratio(self) -> None:
        """3절 키프레임 목표 용량이 실제 증가율 함수 결과와 같은지 검증"""
        cfg = self.config
        stream_mb = estimate_video_stream_mb(self.video)
        ratio = calc_keyframe_increase_ratio(
            self.video.keyframe_interval,
            cfg.thresholds.keyframe_1s_increase_rate,
            fps=self.video.fps,
            base_fps=cfg.bitrate_model.base_fps,
            source_ratio=self.video.vid_kbps / self.evaluation.optimal_kbps,
        )
        target = (self.video.vid_size_MB - stream_mb) + stream_mb * (1.0 + ratio)
        self.assertIn(f"{ratio:.4f}", self.text)
        self.assertIn(f"{target:,.1f} MB", self.text)

    def test_savings_example_matches_evaluator_estimates(self) -> None:
        """4절 분당 절약이 평가 결과의 예상 용량과 예상 시간으로 계산되는지 검증"""
        potential = self.video.vid_size_MB - self.evaluation.expected_after_mb
        playback_min = self.video.duration / 60.0
        encode_min = self.evaluation.estimated_encode_sec / 60.0
        self.assertIn(f"{potential / playback_min:,.1f} MB/분", self.text)
        self.assertIn(f"{potential / encode_min:,.1f} MB/분", self.text)

    def test_protect_quality_value_uses_the_continuous_limit(self) -> None:
        """6절 보호 품질값 상한과 연속 규칙 문구가 코어 상수와 같은지 검증"""
        baseline = self.config.encode.baseline_cq
        self.assertIn(format_cq_value(protect_cq_value(baseline)), self.text)
        self.assertIn(format_cq_value(PROTECT_CQ_DELTA_MAX), self.text)
        self.assertIn("K", self.text)
        self.assertIn("보호 품질값 연속 규칙", self.text)


class HelpGuideSyncTests(unittest.TestCase):
    """프로그램 안내 탭이 실제 설정값과 최신 기능을 반영하는지 검증"""

    def setUp(self) -> None:
        self.config = ClassifierConfig()
        self.html = build_guide_html(LIGHT_PALETTE, None, self.config)
        self.text = _strip_tags(self.html)

    def test_guide_describes_four_stage_flow(self) -> None:
        """1~2단계 시절 문구 대신 4단계 흐름과 인코딩 시작 단계가 있는지 검증"""
        self.assertIn("기본 작업 흐름 (4단계)", self.text)
        self.assertIn("인코딩 시작, 결과 배치, 임시 파일 정리", self.text)
        self.assertNotIn("판정까지만 맡으며", self.text)
        self.assertNotIn("이동하거나 바꾸지 않습니다", self.text)

    def test_guide_lists_shipped_features(self) -> None:
        """5b, 5c, 5d, 6a, 직접 지정, FFmpeg, 크기 조절 방식 안내가 있는지 검증"""
        for phrase in (
            "사용자 지정 활성 화면",
            "화질 분석 축소 후보 자동 적용",
            "연속 보호 품질값",
            "최대 비트레이트",
            "품질값 자동 조정, 정밀 결정",
            "FFmpeg 명령 기록",
            "크기 조절 방식",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_decision_table_uses_current_quality_values(self) -> None:
        """판정표가 현재 화질 기준값과 보호 품질값을 쓰고 고정 CQ 문구를 쓰지 않는지 검증"""
        baseline = format_cq_value(self.config.encode.baseline_cq)
        protect = format_cq_value(protect_cq_value(self.config.encode.baseline_cq))
        self.assertIn(f"품질 {baseline}에서 최대 {protect}", self.text)
        self.assertNotIn("CQ26.0", self.text)
        self.assertNotIn("위험 시 품질", self.text)

    def test_auto_apply_setting_value_is_shown(self) -> None:
        """축소 후보 자동 적용 상태가 실제 설정값을 따르는지 검증"""
        expected = "사용" if self.config.encode.auto_apply_frame_target else "사용 안 함"
        self.assertIn(f"자동 적용 설정은 {expected}입니다", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
