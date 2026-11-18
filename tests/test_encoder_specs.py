"""인코더 정의 단일 기준, 설정 선택지 파생, NVENC 레벨 상한 계산과 상한의 예상 용량 반영을 검증하는 단위 테스트"""

import unittest
from unittest import mock

from src.core import evaluator as evaluator_module
from src.core.config import ENCODE_CODEC_CHOICES, VALID_ENCODE_CODECS, ClassifierConfig, EncodeConfig
from src.core.encoder_specs import ENCODER_SPECS, get_encoder_spec, resolve_level_cap_kbps
from src.core.evaluator import VideoEvaluator
from src.core.size_estimator import combine_video_caps_kbps, estimate_after_size_mb
from tests._factories import make_video


class TestEncoderSpecsSingleSource(unittest.TestCase):
    """설정 선택지와 계열 매핑이 인코더 정의 표에서만 파생되는지 검증"""

    def test_choices_are_derived_from_available_specs(self) -> None:
        """설정 선택지가 사용 가능한 인코더와 정확히 일치하고 AV1 계열과 미확정 인코더를 포함하지 않음을 검증"""
        available = tuple((s.label, s.name) for s in ENCODER_SPECS if s.available)
        self.assertEqual(ENCODE_CODEC_CHOICES, available)
        self.assertEqual(VALID_ENCODE_CODECS, tuple(name for _, name in available))
        for name in ("av1_nvenc", "libsvtav1", "hevc_qsv", "hevc_amf"):
            self.assertNotIn(name, VALID_ENCODE_CODECS)

    def test_labels_drop_shana_reference_wording(self) -> None:
        """선택지 라벨에서 샤나 기준 문구가 빠지고 계열이 정의 표와 일치함을 검증"""
        for label, name in ENCODE_CODEC_CHOICES:
            with self.subTest(name=name):
                self.assertNotIn("샤나", label)
                spec = get_encoder_spec(name)
                assert spec is not None
                self.assertEqual(spec.label, label)

    def test_unavailable_encoder_is_rejected_on_load(self) -> None:
        """저장 설정에 사용 불가 인코더가 있으면 기본 인코더로 대체됨을 검증"""
        cfg = ClassifierConfig.from_dict({"analysis": {"encode": {"encode_codec": "av1_nvenc"}}})
        self.assertEqual(cfg.encode.encode_codec, "hevc_nvenc")


def _video(name: str, kbps: int, duration: float, fps: float, width: int, height: int):
    """비트레이트와 길이에 맞춰 파일 용량을 일관되게 맞춘 테스트 영상 생성"""
    size_mb = kbps * duration / 8 / 1024
    return make_video(
        filename=name,
        vid_kbps=kbps,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        vid_size_MB=size_mb,
    )


class TestLevelCap(unittest.TestCase):
    """NVENC 자동 레벨 선택 결과와 레벨 상한 값을 1초 합성 클립 관측과 맞춰 검증"""

    def test_observed_nvenc_levels_map_to_expected_caps(self) -> None:
        """관측한 해상도, FPS별 레벨 선택(HEVC Main, H.264 High)의 최대 비트레이트가 기대값과 같음을 검증"""
        cases = (
            ("hevc_nvenc", 1280, 720, 30.0, 10_000),  # HEVC 3.1
            ("hevc_nvenc", 1920, 1080, 30.0, 12_000),  # HEVC 4.0
            ("hevc_nvenc", 1920, 1080, 60.0, 20_000),  # HEVC 4.1
            ("hevc_nvenc", 2560, 1440, 60.0, 25_000),  # HEVC 5.0
            ("hevc_nvenc", 3840, 2160, 30.0, 25_000),  # HEVC 5.0
            ("hevc_nvenc", 3840, 2160, 60.0, 40_000),  # HEVC 5.1
            ("hevc_nvenc", 7680, 4320, 30.0, 60_000),  # HEVC 6.0
            ("h264_nvenc", 1280, 720, 30.0, 17_500),  # H.264 3.1
            ("h264_nvenc", 1920, 1080, 30.0, 25_000),  # H.264 4.0
            ("h264_nvenc", 1920, 1080, 60.0, 62_500),  # H.264 4.2
            ("h264_nvenc", 2560, 1440, 60.0, 300_000),  # H.264 5.1
            ("h264_nvenc", 3840, 2160, 30.0, 300_000),  # H.264 5.1
            ("h264_nvenc", 3840, 2160, 60.0, 300_000),  # H.264 5.2
        )
        for encoder, width, height, fps, expected in cases:
            with self.subTest(encoder=encoder, size=(width, height), fps=fps):
                self.assertEqual(resolve_level_cap_kbps(encoder, width, height, fps), expected)

    def test_non_nvenc_and_invalid_inputs_have_no_cap(self) -> None:
        """libx265는 레벨 상한을 적용하지 않고, 해상도나 FPS가 0 이하이면 상한이 없음을 검증"""
        self.assertIsNone(resolve_level_cap_kbps("libx265", 3840, 2160, 60.0))
        self.assertIsNone(resolve_level_cap_kbps("hevc_nvenc", 0, 1080, 30.0))
        self.assertIsNone(resolve_level_cap_kbps("hevc_nvenc", 1920, 1080, 0.0))

    def test_combined_cap_uses_the_smaller_limit(self) -> None:
        """규격 maxrate와 레벨 상한이 함께 있으면 더 작은 값을, 한쪽만 있으면 그 값을 적용함을 검증"""
        self.assertEqual(combine_video_caps_kbps(8000, 40000), 8000)
        self.assertEqual(combine_video_caps_kbps(None, 40000), 40000)
        self.assertIsNone(combine_video_caps_kbps(None, None))

    def test_cap_reduces_expected_stream_size_only_when_binding(self) -> None:
        """상한이 스트림 비트레이트보다 클 때는 크기가 같고, 작을 때만 예상 크기가 줄어듦을 검증"""
        vid = _video("big.mp4", 90_000, 60.0, 60.0, 3840, 2160)
        uncapped = estimate_after_size_mb(vid, 0.5, 0.06, cap_at_original=False)
        loose = estimate_after_size_mb(vid, 0.5, 0.06, cap_at_original=False, max_video_kbps=300_000)
        tight = estimate_after_size_mb(vid, 0.5, 0.06, cap_at_original=False, max_video_kbps=40_000)
        self.assertAlmostEqual(uncapped, loose)
        self.assertLess(tight, uncapped)


class TestLevelCapJudgment(unittest.TestCase):
    """레벨 상한이 예상 용량에만 반영되고 판정을 바꾸지 않으며, NVENC가 아닌 인코더는 불변임을 고정"""

    _SAMPLES = (
        ("uhd60_high.mp4", 90_000, 60.0),
        ("fhd30_mid.mp4", 30_000, 30.0),
        ("hd30_low.mp4", 2_000, 30.0),
    )

    def _actions(self, cfg: ClassifierConfig, capped: bool) -> list[str]:
        vids = [
            _video(name, kbps, 60.0, fps, *((3840, 2160) if kbps > 50_000 else (1920, 1080)))
            for name, kbps, fps in self._SAMPLES
        ]
        if capped:
            return [VideoEvaluator.evaluate_video(v, cfg).smart_action.value for v in vids]
        with mock.patch.object(evaluator_module, "resolve_level_cap_kbps", return_value=None):
            return [VideoEvaluator.evaluate_video(v, cfg).smart_action.value for v in vids]

    def test_default_baseline_judgments_are_unchanged(self) -> None:
        """기본 기준 비트레이트에서는 레벨 상한이 있어도 판정이 바뀌지 않음을 검증"""
        cfg = ClassifierConfig()
        self.assertEqual(self._actions(cfg, True), self._actions(cfg, False))

    def test_non_nvenc_target_is_unchanged_even_with_high_baseline(self) -> None:
        """libx265 목표에서는 기준 비트레이트를 높여도 레벨 상한 영향이 없음을 검증"""
        from src.core.config import BitrateModelConfig

        cfg = ClassifierConfig(
            encode=EncodeConfig(encode_codec="libx265"),
            bitrate_model=BitrateModelConfig(base_target_kbps=8000),
        )
        self.assertEqual(self._actions(cfg, True), self._actions(cfg, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
