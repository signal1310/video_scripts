"""분류 판정 정책 체인과 개별 정책 분기를 직접 검증하는 단위 테스트"""

import unittest

from src.core.config import SAVINGS_BASIS_ENCODE, ClassifierConfig, EncodeConfig, SavingsConfig
from src.core.evaluation.classification_policy import (
    DEFAULT_CLASSIFICATION_POLICIES,
    LEGACY_COMPAT_CODECS,
    DownscaleEstimate,
    EvaluationContext,
    make_legacy_format_policy,
    should_use_low_quality_preset,
)
from src.core.models import ActiveFrame, QualityZone
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.smart_action import DownscaleReason, SmartAction
from tests._factories import make_video


def _make_context(**overrides: object) -> EvaluationContext:
    """기본 판정 문맥을 생성하고 지정 필드만 덮어씀"""
    defaults: dict[str, object] = {
        "video": make_video(filename="sample.mp4", codec="h264", duration=120.0),
        "optimal_kbps": 1500,
        "bitrate_ratio": 1.0,
        "target_optimal_kbps": 1200,
        "target_bitrate_ratio": 1.0,
        "quality_zone": QualityZone.OPTIMAL,
        "is_keyframe_exceeded": False,
        "estimated_encode_sec": 30.0,
        "potential_saved_mb": 20.0,
        "config": ClassifierConfig(),
        "exception_rules": (),
        "source_codec_weight": 1.0,
        "target_codec_weight": 0.8,
    }
    defaults.update(overrides)
    return EvaluationContext(**defaults)  # type: ignore[arg-type]


def _run_policies(ctx: EvaluationContext) -> SmartAction:
    """기본 정책 체인을 순서대로 실행해 첫 결정의 SmartAction을 반환"""
    for policy in DEFAULT_CLASSIFICATION_POLICIES:
        decision = policy(ctx)
        if decision is not None:
            return decision.smart_action
    raise AssertionError("일치하는 분류 정책이 없습니다")


class TestClassificationPolicy(unittest.TestCase):
    """판정 정책 체인의 우선순위와 주요 분기를 검증"""

    def test_legacy_compat_codecs_include_expected_codecs(self) -> None:
        """구형 코덱 호환 대상 집합에 주요 미지원 코덱이 포함되어 있음을 검증"""
        self.assertTrue({"wmv3", "vc1", "vp8", "flv1", "theora"}.issubset(LEGACY_COMPAT_CODECS))

    def test_make_legacy_format_policy(self) -> None:
        """이전 형식 파일 이름 집합에 포함된 영상만 LEGACY_FORMAT으로 판정됨을 검증"""
        policy = make_legacy_format_policy(frozenset({"legacy.mp4"}))
        matched = policy(_make_context(video=make_video(filename="legacy.mp4")))
        unmatched = policy(_make_context(video=make_video(filename="normal.mp4")))
        assert matched is not None
        self.assertEqual(matched.smart_action, SmartAction.LEGACY_FORMAT)
        self.assertIsNone(unmatched)

    def test_exception_already_encoded_and_hdr_precedence(self) -> None:
        """예외 규칙, 이미 인코딩(직접 지정 예외 포함), HDR 보존 우선순위를 검증"""
        self.assertEqual(
            _run_policies(_make_context(is_exception_matched=True)),
            SmartAction.PASS_EXCEPTION,
        )
        encoded_vid = make_video(has_encode_tag=True)
        self.assertEqual(
            _run_policies(_make_context(video=encoded_vid)),
            SmartAction.PASS_ALREADY_ENCODED,
        )
        manual_downscale = DownscaleEstimate(
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            reason=DownscaleReason.MANUAL,
            target_width=1280,
            target_height=720,
            target_optimal_kbps=1000,
            target_bitrate_ratio=1.2,
            source_ratio_at_target=1.2,
            after_mb=15.0,
            potential_saved_mb=20.0,
            encode_sec=20.0,
        )
        self.assertEqual(
            _run_policies(_make_context(video=encoded_vid, downscale=manual_downscale)),
            SmartAction.ENCODE_DOWNSCALE,
        )
        self.assertEqual(
            _run_policies(_make_context(video=make_video(is_hdr=True))),
            SmartAction.PASS_HDR,
        )

    def test_manual_active_frame_and_output_tier_policy(self) -> None:
        """사용자 지정 활성 화면과 직접 지정 축소가 화질 등급, 절약 선별보다 먼저 적용되는지 검증"""
        manual_frame = ActiveFrame(1920, 800, 0, 140)
        manual_downscale = DownscaleEstimate(
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            reason=DownscaleReason.MANUAL,
            target_width=1280,
            target_height=720,
            target_optimal_kbps=1000,
            target_bitrate_ratio=1.2,
            source_ratio_at_target=1.2,
            after_mb=15.0,
            potential_saved_mb=0.5,
            encode_sec=20.0,
        )
        for zone in QualityZone:
            with self.subTest(zone=zone):
                self.assertEqual(
                    _run_policies(
                        _make_context(
                            quality_zone=zone,
                            potential_saved_mb=0.5,
                            manual_active_frame=manual_frame,
                        )
                    ),
                    SmartAction.ENCODE_MANUAL,
                )
                self.assertEqual(
                    _run_policies(
                        _make_context(
                            quality_zone=zone,
                            potential_saved_mb=0.5,
                            manual_active_frame=manual_frame,
                            downscale=manual_downscale,
                        )
                    ),
                    SmartAction.ENCODE_DOWNSCALE,
                )
        # - 출력 해상도 직접 지정만 있어도 절약 미달과 무관하게 축소 인코딩
        self.assertEqual(
            _run_policies(
                _make_context(potential_saved_mb=0.5, downscale=manual_downscale)
            ),
            SmartAction.ENCODE_DOWNSCALE,
        )
        # - 자동 축소 후보는 직접 지정 정책을 지나 기존 절약 선별 유지
        auto_downscale = DownscaleEstimate(
            tier=RESOLUTION_TIER_BY_KEY["720p"],
            reason=DownscaleReason.MAX_RESOLUTION,
            target_width=1280,
            target_height=720,
            target_optimal_kbps=1000,
            target_bitrate_ratio=1.2,
            source_ratio_at_target=1.2,
            after_mb=15.0,
            potential_saved_mb=0.5,
            encode_sec=20.0,
        )
        self.assertEqual(
            _run_policies(_make_context(potential_saved_mb=0.5, downscale=auto_downscale)),
            SmartAction.PASS_LOW_SAVED,
        )

    def test_manual_active_frame_is_ignored_when_crop_disabled(self) -> None:
        """전역 잘라내기 설정이 꺼져 있으면 직접 지정 정책과 이미 인코딩 예외가 적용되지 않음"""
        manual_frame = ActiveFrame(1920, 800, 0, 140)
        disabled = ClassifierConfig(encode=EncodeConfig(crop_black_bars=False))
        ctx = _make_context(
            manual_active_frame=manual_frame, config=disabled, target_codec_weight=1.0
        )
        self.assertEqual(_run_policies(ctx), SmartAction.PASS_OPTIMAL)
        self.assertEqual(
            _run_policies(
                _make_context(
                    video=make_video(has_encode_tag=True),
                    manual_active_frame=manual_frame,
                    config=disabled,
                )
            ),
            SmartAction.PASS_ALREADY_ENCODED,
        )

    def test_legacy_compat_intra_and_zone_policies(self) -> None:
        """구형 코덱 호환, 인트라 전용 코덱, 과화질/적정/저화질 구간 분기를 검증"""
        compat_cfg = ClassifierConfig(encode=EncodeConfig(force_legacy_compat=True))
        self.assertEqual(
            _run_policies(_make_context(video=make_video(codec="wmv3"), config=compat_cfg)),
            SmartAction.ENCODE_COMPAT,
        )
        self.assertEqual(
            _run_policies(_make_context(source_codec_weight=2.5, potential_saved_mb=20.0)),
            SmartAction.ENCODE_CODEC_UPGRADE,
        )
        self.assertEqual(
            _run_policies(_make_context(source_codec_weight=2.5, potential_saved_mb=1.0)),
            SmartAction.PASS_CODEC_SAVED,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.OVER, potential_saved_mb=20.0)),
            SmartAction.ENCODE_OVER,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.OVER, potential_saved_mb=1.0)),
            SmartAction.PASS_LOW_SAVED,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.OPTIMAL, is_keyframe_exceeded=True)),
            SmartAction.ENCODE_OPT_KF,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.OPTIMAL, target_codec_weight=1.0)),
            SmartAction.PASS_OPTIMAL,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.LOW, is_keyframe_exceeded=True)),
            SmartAction.ENCODE_LOW_KF,
        )
        self.assertEqual(
            _run_policies(_make_context(quality_zone=QualityZone.LOW)),
            SmartAction.PASS_LOW,
        )

    def test_savings_basis_encode_and_low_quality_preset(self) -> None:
        """인코딩 시간 기준 분당 절약 선별과 저화질 프리셋 라우팅 조건을 검증"""
        encode_basis_cfg = ClassifierConfig(
            savings=SavingsConfig(
                min_saved_mb=1.0,
                min_mb_per_min=10.0,
                exempt_saved_mb=5.0,
                selection_basis=SAVINGS_BASIS_ENCODE,
            )
        )
        self.assertEqual(
            _run_policies(
                _make_context(
                    quality_zone=QualityZone.OVER,
                    potential_saved_mb=6.0,
                    estimated_encode_sec=60.0,
                    config=encode_basis_cfg,
                )
            ),
            SmartAction.PASS_LOW_PER_MIN,
        )
        self.assertTrue(should_use_low_quality_preset(QualityZone.LOW, 0.7, 0.7, 1.0, 0.8))
        self.assertTrue(should_use_low_quality_preset(QualityZone.OPTIMAL, 0.85, 0.9, 1.3, 0.8))
        self.assertFalse(should_use_low_quality_preset(QualityZone.OVER, 1.5, 1.5, 1.0, 0.8))
