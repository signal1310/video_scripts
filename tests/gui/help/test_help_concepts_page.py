"""판정 원리 탭의 앵커 절과 내부 고정 기준값 안내 표 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import (
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    VMAF_SUBSAMPLE,
    VR_MIN_LONG_SIDE,
)
from src.core.analysis.frame_scan import BLACK_FRAME_PIXEL_RATIO, BLACK_LUMA_THRESHOLD
from src.core.config import ClassifierConfig, EncodeConfig
from src.core.encode.encode_gate import DURATION_TOLERANCE_MIN_SEC, DURATION_TOLERANCE_RATIO
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.help.help_concepts_page import build_concepts_html
from src.gui.styles.palettes import LIGHT_PALETTE

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
        self.assertIn(f"단변 {VR_MIN_LONG_SIDE}px 이상", html)
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

    def test_outline_counts_eight_steps(self) -> None:
        """개요가 화질 분석, 내부 기준값을 포함한 여덟 단계를 안내하는지 검증"""
        html = self._html()
        self.assertIn("여덟 단계", html)
        self.assertIn("<li><b>화질 분석</b>", html)
        self.assertIn("<li><b>내부 기준값</b>", html)

    def test_no_retired_terms_in_concepts_html(self) -> None:
        """판정 원리 본문에 폐기어와 가운뎃점이 남지 않았는지 검증"""
        html = self._html()
        for retired in ("프레임레이트", "교정", "·", "무결성 검사"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
