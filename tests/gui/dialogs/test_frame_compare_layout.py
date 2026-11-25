"""화질 비교 배치 검증: 캔버스와 알약, 하단 한 줄 재생 바, 전체화면과 단축키 안내 버튼"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QSlider, QToolButton, QWidget

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.preview.video_preview_controls import PlaybackControls
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
    """화질 비교 모드의 공용 하단 재생 바 구성과 신호"""

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
        self.timeline = PlaybackControls(
            self.parent,
            lambda: DEFAULT_SHORTCUTS,
            show_ms=True,
            guide_title=layout.GUIDE_TITLE,
            guide_actions=layout.COMPARE_GUIDE_ACTIONS,
        )
        self.addCleanup(self.timeline.deleteLater)
        self.timeline.frame_step_requested.connect(self.steps.append)
        self.timeline.seek_requested.connect(self.positions.append)
        self.timeline.toggle_requested.connect(lambda: self.toggles.append(True))
        self.timeline.rate_changed.connect(self.rates.append)
        self.timeline.mute_toggled.connect(self.mutes.append)
        self.timeline.volume_changed.connect(self.volumes.append)
        self.timeline.fullscreen_requested.connect(lambda: self.fullscreens.append(True))

    def _button(self, name: str) -> QToolButton:
        button = self.timeline.findChild(QToolButton, name)
        assert button is not None
        return button

    def test_timeline_is_one_row_without_marks_or_shortcut_text(self) -> None:
        """대표 시점 칩과 고정 단축키 문구가 사라지고 프레임 이동 버튼이 남았는지 검증"""
        self.assertIsNone(self.timeline.findChild(QWidget, "CompareMarkChip"))
        self.assertIsNone(self.timeline.findChild(QWidget, "CompareShortcuts"))
        self.assertEqual(self._button("PreviewPrevButton").accessibleName(), "이전 프레임")
        self.assertEqual(self._button("PreviewNextButton").accessibleName(), "다음 프레임")
        self.assertEqual(len(self.timeline.findChildren(QFrame, "PreviewBarDivider")), 1)

    def test_playback_controls_expose_accessible_names_and_callbacks(self) -> None:
        timeline = self.timeline
        toggle = self._button("PreviewToggleButton")
        mute = self._button("PreviewVolumeButton")
        fullscreen = self._button("PreviewFullscreenButton")
        rate = self._button("PreviewRateButton")
        self.assertEqual(toggle.accessibleName(), "재생")
        self.assertEqual(rate.accessibleName(), "재생 배속")
        self.assertEqual(mute.accessibleName(), "음소거")
        self.assertEqual(fullscreen.accessibleName(), "전체화면 전환")
        self.assertEqual(timeline.guide_button.accessibleName(), "단축키 안내")
        self.assertEqual(timeline.guide_button.toolTip(), "단축키 안내")
        toggle.click()
        mute.click()
        fullscreen.click()
        timeline._rate_actions[2.0].trigger()
        volume = timeline.findChild(QSlider, "PreviewVolumeSlider")
        assert volume is not None
        volume.setValue(40)
        self.assertEqual(self.toggles, [True])
        self.assertEqual(self.mutes, [True])
        self.assertEqual(self.fullscreens, [True])
        self.assertEqual(self.rates, [2.0])
        self.assertEqual(self.volumes, [40])

    def test_frame_step_buttons_sit_together_left_of_the_slider(self) -> None:
        """한 프레임 이동 버튼 두 개가 재생 버튼 옆에 나란히 붙어 있는지 검증"""
        row = self.timeline.layout()
        assert row is not None
        order: list[QWidget] = []
        for index in range(row.count()):
            item = row.itemAt(index)
            assert item is not None
            widget = item.widget()
            assert widget is not None
            order.append(widget)
        self.assertEqual(order[0], self._button("PreviewToggleButton"))
        self.assertEqual(order[1], self._button("PreviewPrevButton"))
        self.assertEqual(order[2], self._button("PreviewNextButton"))
        self.assertEqual(order[3], self.timeline.findChild(QSlider, "PreviewPositionSlider"))
        self.assertEqual(order[4], self.timeline.findChild(QLabel, "PreviewTimeLabel"))
        self.assertEqual(order[5], self._button("PreviewRateButton"))
        self.assertEqual(order[6], self._button("PreviewVolumeButton"))
        self.assertEqual(order[7], self.timeline.findChild(QSlider, "PreviewVolumeSlider"))
        self.assertEqual(order[8].objectName(), "PreviewBarDivider")
        self.assertEqual(order[12], self._button("PreviewCropButton"))
        self.assertEqual(order[13], self.timeline.crop_actions)
        self.assertEqual(order[14], self._button("PreviewFullscreenButton"))
        self.assertEqual(order[15], self.timeline.guide_button)

    def test_frame_and_slider_callbacks(self) -> None:
        self._button("PreviewPrevButton").click()
        self._button("PreviewNextButton").click()
        slider = self.timeline.findChild(QSlider, "PreviewPositionSlider")
        assert slider is not None
        slider.setRange(0, 100)
        slider.sliderMoved.emit(12)
        self.assertEqual(self.steps, [-1, 1])
        self.assertEqual(self.positions, [12])

    def test_fullscreen_button_icon_and_name_follow_the_state(self) -> None:
        fullscreen = self._button("PreviewFullscreenButton")
        self.assertFalse(fullscreen.icon().isNull())
        self.timeline.set_fullscreen(True)
        self.assertEqual(fullscreen.accessibleName(), "전체화면 해제")
        self.timeline.set_fullscreen(False)
        self.assertEqual(fullscreen.accessibleName(), "전체화면 전환")


if __name__ == "__main__":
    unittest.main(verbosity=2)
