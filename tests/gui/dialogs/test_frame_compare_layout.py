"""화질 비교 창 배치 검증: 툴바, 캔버스와 오버레이, 타임라인 재생 조작, 아이콘, 배율 입력

- 위젯 구성과 콜백 연결만 확인하고 동작은 창 테스트가 맡음
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_compare import ComparePlan, FrameSource, result_target
from src.core.settings.ui_state import VALID_PREVIEW_PLAYBACK_RATES
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _plan() -> ComparePlan:
    original = FrameSource(key="원본", path="a.mp4", label="원본", detail="3840x2160 1.42 GB")
    target = result_target("b.mp4", width=1920, height=1080, size_bytes=1024)
    return ComparePlan(original=original, targets=(target,), default_key="result")


def _image() -> QImage:
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    return image


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

    def test_zoom_input_shows_percent_and_accepts_ten_to_eight_hundred(self) -> None:
        spin = self.toolbar.zoom_input
        self.assertEqual(spin.text(), "100%")
        self.assertEqual((spin.minimum(), spin.maximum()), (10, 800))
        self.assertEqual(spin.accessibleName(), "화면 배율")
        self.assertEqual(spin.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        self.assertEqual(spin.toolTip(), layout.ZOOM_BADGE_TIP)
        self.assertIn("10%~800%", layout.ZOOM_BADGE_TIP)

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


class StageTests(GuiTestCase):
    """캔버스와 오버레이 구성, 배율 입력 연결"""

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

    def test_zoom_helpers_sync_input_and_clear_preset_checks(self) -> None:
        toolbar = layout.build_toolbar(self.parent, lambda _key: None, lambda _key: None)
        stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(stage.canvas.deleteLater)
        self.assertFalse(layout.apply_zoom_input(stage.canvas, toolbar))
        stage.canvas.set_images(_image(), _image())
        layout.sync_zoom_input(stage.canvas, toolbar)
        self.assertGreater(toolbar.zoom_input.value(), 0)
        toolbar.zoom_input.setValue(250)
        self.assertTrue(layout.apply_zoom_input(stage.canvas, toolbar))
        self.assertAlmostEqual(stage.canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in toolbar.zoom_buttons.values()))
        layout.manual_zoom(stage.canvas, toolbar, 1)
        self.assertAlmostEqual(stage.canvas.scale(), 2.5 * 1.25)
        self.assertEqual(toolbar.zoom_input.value(), round(stage.canvas.scale() * 100))


class TimelineTests(GuiTestCase):
    """타임라인의 재생 조작, 대표 시점, 단축키 안내"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.steps: list[int] = []
        self.marks: list[int] = []
        self.positions: list[int] = []
        self.toggles: list[bool] = []
        self.rates: list[float] = []
        self.mutes: list[bool] = []
        self.volumes: list[int] = []
        self.timeline = layout.build_timeline(
            self.parent,
            self.steps.append,
            self.marks.append,
            self.positions.append,
            lambda: self.toggles.append(True),
            self.rates.append,
            lambda: self.mutes.append(True),
            self.volumes.append,
        )
        self.addCleanup(self.timeline.slider.deleteLater)

    def test_timeline_has_nine_marks_and_wires_controls(self) -> None:
        self.assertEqual(
            [chip.text() for chip in self.timeline.mark_buttons],
            [f"{value}%" for value in range(10, 100, 10)],
        )
        self.assertIn("10% 지점", self.timeline.mark_buttons[0].toolTip())
        self.assertEqual(self.timeline.prev_button.accessibleName(), "이전 프레임")
        self.assertEqual(self.timeline.prev_button.toolTip(), layout.PREV_TIP)
        self.assertEqual(self.timeline.next_button.accessibleName(), "다음 프레임")
        parent = self.timeline.shortcuts.parentWidget()
        assert parent is not None
        self.assertIsNone(parent.findChild(QWidget, "CompareMarksHint"))
        self.timeline.prev_button.click()
        self.timeline.next_button.click()
        self.timeline.mark_buttons[2].click()
        self.timeline.slider.setRange(0, 100)
        self.timeline.slider.setValue(12)
        self.assertEqual(self.steps, [-1, 1])
        self.assertEqual(self.marks, [30])
        self.assertEqual(self.positions, [12])

    def test_playback_controls_expose_accessible_names_and_callbacks(self) -> None:
        timeline = self.timeline
        self.assertEqual(timeline.toggle_button.accessibleName(), "재생")
        self.assertEqual(timeline.toggle_button.toolTip(), layout.PLAY_TOGGLE_TIP)
        self.assertEqual(timeline.rate_button.accessibleName(), "재생 배속")
        self.assertIn("현재 1배입니다.", timeline.rate_button.toolTip())
        self.assertEqual(list(timeline.rate_actions), list(VALID_PREVIEW_PLAYBACK_RATES))
        self.assertEqual(timeline.mute_button.accessibleName(), "음소거")
        self.assertEqual(timeline.mute_button.toolTip(), layout.MUTE_TIP)
        self.assertEqual(timeline.volume_slider.maximum(), 100)
        self.assertEqual(timeline.volume_slider.value(), 100)
        self.assertEqual(timeline.volume_slider.toolTip(), layout.VOLUME_TIP)
        timeline.toggle_button.click()
        timeline.mute_button.click()
        timeline.rate_actions[2.0].trigger()
        timeline.volume_slider.setValue(40)
        self.assertEqual(self.toggles, [True])
        self.assertEqual(self.mutes, [True])
        self.assertEqual(self.rates, [2.0])
        self.assertEqual(self.volumes, [40])

    def test_shortcut_guide_mentions_playback_keys(self) -> None:
        text = self.timeline.shortcuts.text()
        self.assertIn("K 재생/일시중지", text)
        self.assertIn("[/]/\\ 배속", text)
        self.assertIn("M 음소거", text)
        self.assertIn("Shift+Space 미리 보기 전환", text)

    def test_playback_button_states_follow_engine_values(self) -> None:
        layout.refresh_playback_buttons(self.timeline, playing=True, muted=True, rate=2.0)
        self.assertEqual(self.timeline.toggle_button.accessibleName(), "일시중지")
        self.assertEqual(self.timeline.mute_button.accessibleName(), "음소거 해제")
        self.assertEqual(self.timeline.mute_button.toolTip(), layout.UNMUTE_TIP)
        self.assertIn("현재 2배입니다.", self.timeline.rate_button.toolTip())
        self.assertTrue(self.timeline.rate_actions[2.0].isChecked())
        layout.refresh_playback_buttons(self.timeline, playing=False, muted=False, rate=1.0)
        self.assertEqual(self.timeline.toggle_button.accessibleName(), "재생")
        self.assertEqual(self.timeline.mute_button.accessibleName(), "음소거")
        self.assertTrue(self.timeline.rate_actions[1.0].isChecked())


class IconTests(GuiTestCase):
    """테마 아이콘과 버튼 체크 해제"""

    def test_clear_button_checks_empties_an_exclusive_group(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        host, buttons = layout._segmented(layout.MODE_LABELS, layout.MODE_TIPS, lambda _key: None, parent)
        buttons["split"].setChecked(True)
        self.assertTrue(buttons["split"].isChecked())
        layout.clear_button_checks(buttons)
        self.assertFalse(any(button.isChecked() for button in buttons.values()))
        self.addCleanup(host.deleteLater)

    def test_refresh_icons_sets_icons(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        toolbar = layout.build_toolbar(parent, lambda _key: None, lambda _key: None)
        timeline = layout.build_timeline(
            parent,
            lambda _step: None,
            lambda _percent: None,
            lambda _value: None,
            lambda: None,
            lambda _rate: None,
            lambda: None,
            lambda _value: None,
        )
        self.addCleanup(timeline.slider.deleteLater)
        layout.refresh_icons(toolbar, timeline, LIGHT_PALETTE)
        self.assertFalse(toolbar.target_button.icon().isNull())
        self.assertFalse(timeline.prev_button.icon().isNull())
        self.assertFalse(timeline.next_button.icon().isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)

