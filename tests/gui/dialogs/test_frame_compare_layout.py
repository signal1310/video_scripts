"""화질 비교 창 배치 검증: 툴바, 메타 줄, 점수 줄, 캔버스·오버레이, 타임라인, 스타일과 아이콘

- 위젯 구성과 콜백 연결만 확인하고 동작은 창 테스트가 맡음
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_compare import ComparePlan, FrameSource, result_target
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _plan() -> ComparePlan:
    original = FrameSource(key="원본", path="a.mp4", label="원본", detail="3840x2160 1.42 GB")
    target = result_target("b.mp4", width=1920, height=1080, size_bytes=1024)
    return ComparePlan(original=original, targets=(target,), default_key="result")


class ToolbarTests(GuiTestCase):
    """툴바 구성과 모드, 배율 콜백"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.modes: list[str] = []
        self.zooms: list[str] = []
        self.toolbar = layout.build_toolbar(self.parent, self.modes.append, self.zooms.append)
        self.addCleanup(self.toolbar.filename.deleteLater)

    def test_mode_and_zoom_segments_have_approved_labels_and_tips(self) -> None:
        self.assertEqual(list(self.toolbar.mode_buttons), ["split", "toggle", "side"])
        self.assertEqual(
            [button.text() for button in self.toolbar.mode_buttons.values()], ["분할선", "즉시 전환", "좌우 비교"]
        )
        self.assertEqual(
            [button.text() for button in self.toolbar.zoom_buttons.values()], ["맞춤", "1:1", "200%", "400%"]
        )
        self.assertIn("세로 분할선", self.toolbar.mode_buttons["split"].toolTip())
        self.assertIn("원본 픽셀 100%", self.toolbar.zoom_buttons["1"].toolTip())
        self.assertEqual(self.toolbar.zoom_badge.toolTip(), layout.ZOOM_BADGE_TIP)

    def test_clicking_segments_calls_back_with_their_keys(self) -> None:
        self.toolbar.mode_buttons["toggle"].click()
        self.toolbar.zoom_buttons["400"].click()
        self.assertEqual(self.modes, ["toggle"])
        self.assertEqual(self.zooms, ["400"])

    def test_target_menu_fills_labels_checks_and_tooltips(self) -> None:
        calls: list[str] = []
        layout.fill_target_menu(self.toolbar.target_menu, _plan(), calls.append)
        actions = self.toolbar.target_menu.actions()
        self.assertEqual([action.text() for action in actions], ["결과"])
        self.assertTrue(actions[0].isChecked())
        self.assertIn("원본과 재인코딩 결과", actions[0].toolTip())
        actions[0].trigger()
        self.assertEqual(calls, ["result"])


class PanelTests(GuiTestCase):
    """메타 줄, 점수 줄, 캔버스와 타임라인 구성"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def test_meta_and_score_labels(self) -> None:
        meta = layout.build_meta_row(self.parent)
        self.assertEqual([meta.original.text(), meta.target.text(), meta.time.text()], ["", "", ""])
        self.assertEqual(meta.original.objectName(), "CompareMetaText")
        score = layout.build_score_label(self.parent)
        self.addCleanup(score.deleteLater)
        self.assertTrue(score.isHidden())
        self.assertEqual(score.objectName(), "CompareScore")

    def test_stage_wires_scale_and_side_callbacks(self) -> None:
        scales: list[float] = []
        sides: list[bool] = []
        stage = layout.build_stage(self.parent, scales.append, sides.append)
        self.addCleanup(stage.overlay.deleteLater)
        stage.canvas.scale_changed.emit(2.0)
        stage.canvas.side_changed.emit(True)
        self.assertEqual(scales, [2.0])
        self.assertEqual(sides, [True])
        self.assertEqual(stage.title.text(), "")
        self.assertTrue(stage.overlay.isHidden())
        self.assertEqual(stage.canvas.minimumHeight(), layout.CANVAS_MIN_HEIGHT_PX)

    def test_timeline_has_nine_marks_and_wires_controls(self) -> None:
        steps: list[int] = []
        marks: list[int] = []
        slider: list[int] = []
        timeline = layout.build_timeline(self.parent, steps.append, marks.append, slider.append)
        self.addCleanup(timeline.slider.deleteLater)
        self.assertEqual(
            [chip.text() for chip in timeline.mark_buttons], [f"{value}%" for value in range(10, 100, 10)]
        )
        self.assertIn("10% 지점", timeline.mark_buttons[0].toolTip())
        self.assertEqual(timeline.prev_button.accessibleName(), "이전 프레임")
        self.assertEqual(timeline.prev_button.toolTip(), layout.PREV_TIP)
        self.assertEqual(timeline.next_button.accessibleName(), "다음 프레임")
        self.assertIn("1/2/3 모드", timeline.shortcuts.text())
        timeline.prev_button.click()
        timeline.next_button.click()
        timeline.mark_buttons[2].click()
        timeline.slider.setRange(0, 100)
        timeline.slider.setValue(12)
        self.assertEqual(steps, [-1, 1])
        self.assertEqual(marks, [30])
        self.assertEqual(slider, [12])


class StyleTests(unittest.TestCase):
    """스타일 시트와 버튼 체크 해제, 아이콘 갱신"""

    def test_stylesheet_uses_palette_tokens(self) -> None:
        sheet = layout.build_stylesheet(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.bg_card, sheet)
        self.assertIn("QWidget#CompareSegmented QToolButton:checked", sheet)
        self.assertNotIn("#121212", sheet)

    def test_clear_button_checks_empties_an_exclusive_group(self) -> None:
        parent = QWidget()
        host, buttons = layout._segmented(layout.MODE_LABELS, layout.MODE_TIPS, lambda _key: None, parent)
        buttons["split"].setChecked(True)
        self.assertTrue(buttons["split"].isChecked())
        layout.clear_button_checks(buttons)
        self.assertFalse(any(button.isChecked() for button in buttons.values()))
        host.deleteLater()
        parent.deleteLater()

    def test_refresh_icons_sets_icons(self) -> None:
        parent = QWidget()
        toolbar = layout.build_toolbar(parent, lambda _key: None, lambda _key: None)
        timeline = layout.build_timeline(parent, lambda _step: None, lambda _p: None, lambda _v: None)
        layout.refresh_icons(toolbar, timeline, LIGHT_PALETTE)
        self.assertFalse(toolbar.target_button.icon().isNull())
        self.assertFalse(timeline.prev_button.icon().isNull())
        self.assertFalse(timeline.next_button.icon().isNull())
        parent.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
