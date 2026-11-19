"""코덱 그룹 별칭, 정적 가중치 및 CodecWeightsConfig 조회 동작 검증 테스트"""

import unittest

from src.core.codec_groups import (
    CODEC_WEIGHT_GROUPS,
    DEFAULT_CODEC_WEIGHT_FALLBACK,
    find_codec_group,
    get_static_codec_weight,
)
from src.core.config import ClassifierConfig, CodecWeightsConfig


class TestCodecGroups(unittest.TestCase):
    """코덱 그룹 별칭 조회와 정적 가중치 기준 정의를 검증"""

    def test_find_codec_group_maps_aliases_and_returns_none_for_unknown(self) -> None:
        """등록된 별칭은 해당 그룹 이름을, 미등록 별칭은 None을 반환함을 검증"""
        for group, aliases in CODEC_WEIGHT_GROUPS.items():
            for alias in aliases:
                with self.subTest(group=group, alias=alias):
                    self.assertEqual(find_codec_group(alias), group)
        self.assertIsNone(find_codec_group("unknown_codec"))

    def test_get_static_codec_weight_returns_defined_or_fallback(self) -> None:
        """정적 가중치 코덱은 고정 계수를, 미등록 코덱은 기본 계수를 반환함을 검증"""
        self.assertAlmostEqual(get_static_codec_weight("mpeg2video"), 1.60)
        self.assertAlmostEqual(get_static_codec_weight("prores"), 2.50)
        self.assertAlmostEqual(get_static_codec_weight("unknown_codec"), DEFAULT_CODEC_WEIGHT_FALLBACK)

    def test_codec_weight_groups_single_source_of_truth(self) -> None:
        """CodecWeightsConfig와 CODEC_WEIGHT_GROUPS가 코덱 별칭 가중치를 기준 정의로 제공함을 검증"""
        cfg = ClassifierConfig()
        default_legacy = CodecWeightsConfig().legacy
        self.assertAlmostEqual(cfg.get_codec_weight("mpeg4"), default_legacy)
        self.assertAlmostEqual(cfg.get_codec_weight("vp8"), default_legacy)

        custom = CodecWeightsConfig(av1=0.55)
        for alias in CODEC_WEIGHT_GROUPS["av1"]:
            self.assertAlmostEqual(custom.get_weight(alias), 0.55)
        self.assertAlmostEqual(custom.get_weight("mjpeg"), 2.50)
        self.assertAlmostEqual(custom.get_weight("unknown_codec"), DEFAULT_CODEC_WEIGHT_FALLBACK)
