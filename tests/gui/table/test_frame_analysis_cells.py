"""화질 분석 셀 아이콘, 플래그 배경, 접근성 이름, 확인 행 툴팁 검증"""

import unittest

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.models import ActiveFrame
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.table.frame_analysis_cells import (
    CELL_ICON_SIZE_PX,
    REVIEW_WARNING_ICON_NAME,
    FrameAnalysisCells,
)
from src.gui.table.frame_analysis_columns import (
    ACK_ICON_NAME,
    ACK_MANUAL,
    ACK_RESET,
    ACTIVE_FRAME_MANUAL_ICON,
    ACTIVE_FRAME_MANUAL_NAME,
    ACTIVE_FRAME_MARGINS_VARY_TEXT,
    CROP_REVIEW_TEXT,
    MARGINS_VARY_ICON_NAME,
    FrameAnalysisResults,
    acknowledged_tooltip,
    crop_review_tooltip,
    margins_vary_tooltip,
)
from tests._factories import make_eval, make_video

_NAME_COLUMN: int = 0
_ACTIVE_FRAME_COLUMN: int = 19


def _result(region: ActiveRegion | None = None) -> FrameAnalysisResult:
    """활성 화면 영역만 다른 화질 분석 결과"""
    return FrameAnalysisResult(
        active_region=region or ActiveRegion(1836, 1008, 42, 36, True),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier=None,
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )
class FrameAnalysisCellsTests(unittest.TestCase):
    """셀 아이콘 순서, 플래그 배경 우선순위, 확인 행 표시 수명 검증"""

    def setUp(self) -> None:
        self.view = FrameAnalysisResults()
        self.crop_enabled = True
        self.cells = FrameAnalysisCells(
            self.view,
            crop_enabled=lambda: self.crop_enabled,
            name_column=_NAME_COLUMN,
            active_frame_column=_ACTIVE_FRAME_COLUMN,
        )
        self.review_ev = make_eval(make_video(filename="review.mp4"))
        self.vary_ev = make_eval(make_video(filename="vary.mp4"))
        self.both_ev = make_eval(make_video(filename="both.mp4"))
        self.view.set_results(
            {
                "review.mp4": _result(
                    ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True)
                ),
                "vary.mp4": _result(ActiveRegion(1836, 1008, 42, 36, True, margins_vary=True)),
                "both.mp4": _result(
                    ActiveRegion(
                        1836, 1008, 42, 36, True, margins_vary=True, dark_outliers_ignored=True
                    )
                ),
            }
        )
        self.view.set_review_flagged(["review.mp4", "both.mp4"])
        self.view.set_vary_flagged(["vary.mp4", "both.mp4"])

    def _names(self, ev: object, col: int) -> list[str]:
        """셀에 그릴 아이콘 이름 목록"""
        specs = self.cells.icon_specs(ev, col, palette=DARK_PALETTE)  # type: ignore[arg-type]
        return [name for name, _color in specs]

    def test_warning_icon_precedes_vary_and_wins_the_background(self) -> None:
        specs = self.cells.icon_specs(self.both_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE)
        self.assertEqual([REVIEW_WARNING_ICON_NAME, MARGINS_VARY_ICON_NAME], self._names(self.both_ev, _ACTIVE_FRAME_COLUMN))
        self.assertEqual(DARK_PALETTE.badge_pending[1], specs[0][1])
        self.assertEqual(
            DARK_PALETTE.badge_pending[0],
            self.cells.background(self.both_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE),
        )
        self.assertEqual(
            CELL_ICON_SIZE_PX * 2,
            self.cells.icon_width(self.both_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE),
        )

    def test_name_cell_keeps_only_the_warning_when_both_flags_apply(self) -> None:
        self.assertEqual([REVIEW_WARNING_ICON_NAME], self._names(self.both_ev, _NAME_COLUMN))
        self.assertIsNone(self.cells.icon_width(self.both_ev, _NAME_COLUMN, palette=DARK_PALETTE))
        self.assertIsNone(self.cells.background(self.both_ev, _NAME_COLUMN, palette=DARK_PALETTE))

    def test_vary_only_cell_uses_the_taupe_background(self) -> None:
        self.assertEqual(
            [(MARGINS_VARY_ICON_NAME, DARK_PALETTE.fg_vary_icon)],
            self.cells.icon_specs(self.vary_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE),
        )
        self.assertEqual(
            DARK_PALETTE.badge_vary[0],
            self.cells.background(self.vary_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE),
        )
        self.assertEqual([], self._names(self.vary_ev, _NAME_COLUMN))

    def test_manual_frame_icon_needs_no_flag_and_hides_the_manual_name_column_icon(self) -> None:
        self.view.set_review_flagged([])
        self.view.set_vary_flagged([])
        self.view.set_manual_frames(
            {"vary.mp4": ActiveFrame(width=1836, height=1008, x=42, y=36)}
        )
        self.assertTrue(self.cells.manual_applies("vary.mp4"))
        self.assertEqual([ACTIVE_FRAME_MANUAL_ICON], self._names(self.vary_ev, _ACTIVE_FRAME_COLUMN))
        # - 이름 셀은 플래그가 없으면 아이콘을 그리지 않음
        self.assertEqual([], self._names(self.vary_ev, _NAME_COLUMN))

    def test_accessible_name_reports_review_before_vary(self) -> None:
        self.assertEqual(CROP_REVIEW_TEXT, self.cells.accessible_name(self.both_ev, _ACTIVE_FRAME_COLUMN))
        self.assertEqual(
            ACTIVE_FRAME_MARGINS_VARY_TEXT,
            self.cells.accessible_name(self.vary_ev, _ACTIVE_FRAME_COLUMN),
        )
        self.assertIsNone(self.cells.accessible_name(self.vary_ev, _NAME_COLUMN))

    def test_review_tooltip_appends_the_vary_sentence_when_both_flags_apply(self) -> None:
        result = self.view.result("both.mp4")
        assert result is not None
        tip = self.cells.tooltip(self.both_ev, _ACTIVE_FRAME_COLUMN, row_tooltip=lambda: "row")
        self.assertEqual(f"{crop_review_tooltip(result.active_region)}\n{margins_vary_tooltip()}", tip)
        self.assertEqual(
            margins_vary_tooltip(),
            self.cells.tooltip(self.vary_ev, _ACTIVE_FRAME_COLUMN, row_tooltip=lambda: "row"),
        )

    def test_name_tooltip_prepends_review_text_to_the_row_tooltip(self) -> None:
        result = self.view.result("review.mp4")
        assert result is not None
        tip = self.cells.tooltip(self.review_ev, _NAME_COLUMN, row_tooltip=lambda: "row")
        self.assertEqual(f"{crop_review_tooltip(result.active_region)}\n\nrow", tip)
        self.assertIsNone(
            self.cells.tooltip(self.vary_ev, _NAME_COLUMN, row_tooltip=lambda: "row")
        )

    def test_acknowledged_icon_shows_only_while_the_chip_filter_is_active(self) -> None:
        self.cells.set_acknowledged({"review.mp4": ACK_MANUAL}, active=True)
        self.assertEqual(
            [(ACK_ICON_NAME, DARK_PALETTE.text_muted)],
            self.cells.icon_specs(self.review_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE),
        )
        self.assertEqual(
            acknowledged_tooltip(ACK_MANUAL),
            self.cells.tooltip(self.review_ev, _ACTIVE_FRAME_COLUMN, row_tooltip=lambda: "row"),
        )
        self.assertEqual(
            f"{acknowledged_tooltip(ACK_MANUAL)}\n\nrow",
            self.cells.tooltip(self.review_ev, _NAME_COLUMN, row_tooltip=lambda: "row"),
        )
        self.assertIsNone(
            self.cells.background(self.review_ev, _ACTIVE_FRAME_COLUMN, palette=DARK_PALETTE)
        )

        self.cells.set_acknowledged({"review.mp4": ACK_MANUAL}, active=False)
        self.assertIsNone(self.cells.acknowledged_kind("review.mp4"))
        self.assertEqual([REVIEW_WARNING_ICON_NAME], self._names(self.review_ev, _ACTIVE_FRAME_COLUMN))

    def test_set_acknowledged_reports_whether_the_cells_need_redrawing(self) -> None:
        self.assertTrue(self.cells.set_acknowledged({"review.mp4": ACK_RESET}, active=True))
        self.assertFalse(self.cells.set_acknowledged({"review.mp4": ACK_RESET}, active=True))
        self.assertTrue(self.cells.set_acknowledged({}, active=True))

    def test_keep_full_icon_requires_crop_enabled(self) -> None:
        self.view.set_review_flagged([])
        self.view.set_vary_flagged([])
        self.view.set_crop_excluded(["review.mp4"])
        self.assertTrue(self.cells.keep_full_applies(self.review_ev))
        self.crop_enabled = False
        self.assertFalse(self.cells.keep_full_applies(self.review_ev))

    def test_manual_name_is_not_reported_when_a_vr_protected_result_wins(self) -> None:
        protected = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=True,
        )
        self.view.set_results({"vary.mp4": protected})
        self.view.set_manual_frames({"vary.mp4": ActiveFrame(width=1000, height=800, x=0, y=0)})
        self.assertFalse(self.cells.manual_applies("vary.mp4"))
        self.assertNotEqual(
            ACTIVE_FRAME_MANUAL_NAME, self.cells.accessible_name(self.vary_ev, _ACTIVE_FRAME_COLUMN)
        )


if __name__ == "__main__":
    unittest.main()

