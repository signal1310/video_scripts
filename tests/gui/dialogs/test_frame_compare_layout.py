"""화질 비교 배치 검증: 캔버스와 알약, 하단 한 줄 재생 바, 전체화면과 단축키 안내 버튼"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QWidget

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class StageTests(GuiTestCase):
    """캔버스와 알약, 라벨 여백"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

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

    def test_loading_pill_is_transparent_and_centered_on_top(self) -> None:
        """로딩 알약이 마우스를 통과시키고 캔버스 상단 가운데에 놓이는지 검증"""
        stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(stage.canvas.deleteLater)
        stage.canvas.resize(500, 400)
        pill = stage.pill
        self.assertEqual(pill.text(), layout.LOADING_TEXT)
        self.assertTrue(pill.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        pill.show()
        self.assertEqual(pill.y(), layout.PILL_MARGIN_PX)
        self.assertEqual(pill.x(), (500 - pill.width()) // 2)
        stage.canvas.resize(620, 400)
        QApplication.sendEvent(stage.canvas, QEvent(QEvent.Type.Resize))
        self.assertEqual(pill.x(), (620 - pill.width()) // 2)
        pill.hide()

    def test_canvas_top_inset_moves_labels_down(self) -> None:
        """상단 오버레이가 뜰 때 원본 라벨이 그만큼 아래로 비켜나는지 검증"""
        stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(stage.canvas.deleteLater)
        stage.canvas.resize(600, 400)
        stage.canvas.set_labels("원본", "720p 축소 비교", "")
        stage.canvas.set_top_inset(1)
        stage.canvas.set_top_inset(0)
        base_y = stage.canvas._left_label.y()
        stage.canvas.set_top_inset(60)
        self.assertEqual(stage.canvas._left_label.y(), base_y + 60)
        stage.canvas.set_top_inset(0)
        self.assertEqual(stage.canvas._left_label.y(), base_y)



class TimelineTests(GuiTestCase):
    """하단 한 줄 재생 바 구성과 콜백"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.steps: list[int] = []
        self.positions: list[int] = []
        self.toggles: list[bool] = []
        self.rates: list[float] = []
        self.mutes: list[bool] = []
        self.volumes: list[int] = []
        self.fullscreens: list[bool] = []
        self.timeline = layout.build_timeline(
            self.parent,
            self.steps.append,
            self.positions.append,
            lambda: self.toggles.append(True),
            self.rates.append,
            lambda: self.mutes.append(True),
            self.volumes.append,
            lambda: self.fullscreens.append(True),
            lambda: DEFAULT_SHORTCUTS,
        )
        self.addCleanup(self.timeline.slider.deleteLater)

    def test_timeline_is_one_row_without_marks_or_shortcut_text(self) -> None:
        """대표 시점 칩과 고정 단축키 문구가 사라지고 프레임 이동 버튼이 남았는지 검증"""
        parent = self.timeline.slider.parentWidget()
        assert parent is not None
        self.assertIsNone(parent.findChild(QWidget, "CompareMarkChip"))
        self.assertIsNone(parent.findChild(QWidget, "CompareShortcuts"))
        self.assertEqual(self.timeline.prev_button.accessibleName(), "이전 프레임")
        self.assertEqual(self.timeline.next_button.accessibleName(), "다음 프레임")
        self.assertEqual(len(parent.findChildren(QFrame, "CompareBarDivider")), 1)

    def test_playback_controls_expose_accessible_names_and_callbacks(self) -> None:
        timeline = self.timeline
        self.assertEqual(timeline.toggle_button.accessibleName(), "재생")
        self.assertEqual(timeline.rate_button.accessibleName(), "재생 배속")
        self.assertEqual(timeline.mute_button.accessibleName(), "음소거")
        self.assertEqual(timeline.fullscreen_button.accessibleName(), "전체화면 전환")
        self.assertEqual(timeline.guide_button.accessibleName(), "단축키 안내")
        self.assertEqual(timeline.guide_button.toolTip(), "단축키 안내")
        self.assertEqual(timeline.volume_slider.maximum(), 100)
        timeline.toggle_button.click()
        timeline.mute_button.click()
        timeline.fullscreen_button.click()
        timeline.rate_actions[2.0].trigger()
        timeline.volume_slider.setValue(40)
        self.assertEqual(self.toggles, [True])
        self.assertEqual(self.mutes, [True])
        self.assertEqual(self.fullscreens, [True])
        self.assertEqual(self.rates, [2.0])
        self.assertEqual(self.volumes, [40])

    def test_frame_and_slider_callbacks(self) -> None:
        self.timeline.prev_button.click()
        self.timeline.next_button.click()
        self.timeline.slider.setRange(0, 100)
        self.timeline.slider.setValue(12)
        self.assertEqual(self.steps, [-1, 1])
        self.assertEqual(self.positions, [12])

    def test_fullscreen_button_icon_and_color_follow_palette(self) -> None:
        from src.gui.dialogs import frame_compare_styles as styles

        styles.refresh_timeline_icons(self.timeline, LIGHT_PALETTE)
        self.assertFalse(self.timeline.fullscreen_button.icon().isNull())
        styles.update_fullscreen_button(self.timeline, True)
        self.assertEqual(self.timeline.fullscreen_button.accessibleName(), "전체화면 해제")
        styles.update_fullscreen_button(self.timeline, False)
        self.assertEqual(self.timeline.fullscreen_button.accessibleName(), "전체화면 전환")


if __name__ == "__main__":
    unittest.main(verbosity=2)
