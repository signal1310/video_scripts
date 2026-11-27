"""미리 보기 조작 바 검증: 화질 비교 버튼의 위치, 접근성 이름, 툴팁, 신호와 볼륨 슬라이더 연결"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from src.gui.preview.video_preview_controls import PlaybackControls
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class CompareButtonTests(GuiTestCase):
    """화질 비교 버튼과 설정 버튼의 순서"""

    def setUp(self) -> None:
        self.controls = PlaybackControls()
        self.addCleanup(self.controls.deleteLater)
        self.controls.show()
        _APP.processEvents()

    def test_compare_button_has_accessible_name_and_approved_tooltip(self) -> None:
        button = self.controls.compare_button
        self.assertEqual(button.accessibleName(), "화질 비교")
        self.assertEqual(button.toolTip(), "현재 재생 시점으로 화질 비교 창으로 전환합니다.")
        self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertFalse(button.icon().isNull())

    def test_compare_button_sits_right_before_settings_after_the_divider(self) -> None:
        layout_obj = self.controls.layout()
        assert layout_obj is not None
        widgets: list[QWidget | None] = []
        for index in range(layout_obj.count()):
            item = layout_obj.itemAt(index)
            widgets.append(None if item is None else item.widget())
        compare_index = widgets.index(self.controls.compare_button)
        settings = widgets[compare_index + 1]
        divider = widgets[compare_index - 1]
        volume = widgets[compare_index - 2]
        assert settings is not None
        assert divider is not None
        assert volume is not None
        self.assertIs(settings, self.controls.settings_button)
        self.assertEqual(divider.objectName(), "PreviewBarDivider")
        self.assertIs(volume, self.controls._slider_volume)
        self.assertIs(widgets[-1], self.controls.guide_button)

    def test_frame_pin_and_fullscreen_buttons_emit_requests(self) -> None:
        """한 프레임 이동, 핀, 전체화면 버튼이 공용 신호로 요청을 내보내는지 검증"""
        steps: list[int] = []
        pins: list[bool] = []
        fullscreens: list[bool] = []
        self.controls.frame_step_requested.connect(steps.append)
        self.controls.always_on_top_toggled.connect(pins.append)
        self.controls.fullscreen_requested.connect(lambda: fullscreens.append(True))
        prev_button = self.controls.findChild(QToolButton, "PreviewPrevButton")
        next_button = self.controls.findChild(QToolButton, "PreviewNextButton")
        pin_button = self.controls.findChild(QToolButton, "PreviewPinButton")
        fullscreen_button = self.controls.findChild(QToolButton, "PreviewFullscreenButton")
        assert prev_button is not None
        assert next_button is not None
        assert pin_button is not None
        assert fullscreen_button is not None
        prev_button.click()
        next_button.click()
        pin_button.click()
        pin_button.click()
        fullscreen_button.click()
        self.assertEqual(steps, [-1, 1])
        self.assertEqual(pins, [False, True])
        self.assertEqual(fullscreens, [True])

    def test_fullscreen_and_pin_states_update_icons_and_names(self) -> None:
        """전체화면과 핀 상태가 아이콘, 접근성 이름, 툴팁에 반영되는지 검증"""
        fullscreen_button = self.controls.findChild(QToolButton, "PreviewFullscreenButton")
        pin_button = self.controls.findChild(QToolButton, "PreviewPinButton")
        assert fullscreen_button is not None
        assert pin_button is not None
        self.controls.set_fullscreen(True)
        self.assertEqual(fullscreen_button.accessibleName(), "전체화면 해제")
        self.assertIn("해제", fullscreen_button.toolTip())
        self.controls.set_fullscreen(False)
        self.assertEqual(fullscreen_button.accessibleName(), "전체화면 전환")
        self.controls.set_always_on_top(False)
        self.assertEqual(pin_button.accessibleName(), "항상 위에 표시")
        self.assertIn("앱의 다른 창보다 위에", pin_button.toolTip())
        self.controls.set_always_on_top(True)
        self.assertEqual(pin_button.accessibleName(), "항상 위에 표시 해제")
        self.assertIn("해제합니다", pin_button.toolTip())

    def test_compare_mode_keeps_all_buttons_and_shows_ms_time(self) -> None:
        """비교 모드가 버튼을 숨기지 않고 밀리초 시각과 안내 목록만 바꾸는지 검증"""
        controls = PlaybackControls()
        self.addCleanup(controls.deleteLater)
        controls.set_compare_mode(
            True, guide_title="화질 비교 창 단축키", guide_actions=("preview.toggle_playback",)
        )
        self.assertFalse(controls.compare_button.isHidden())
        self.assertTrue(controls.compare_button.isChecked())
        self.assertFalse(controls.settings_button.isHidden())
        pin_button = controls.findChild(QToolButton, "PreviewPinButton")
        assert pin_button is not None
        self.assertFalse(pin_button.isHidden())
        controls.set_duration(65_500)
        controls.set_position(30_250)
        time_label = controls.findChild(QLabel, "PreviewTimeLabel")
        assert time_label is not None
        self.assertEqual(time_label.text(), "00:30.250 / 01:05.500")
        controls.set_compare_mode(
            False, guide_title="미리 보기 창 단축키", guide_actions=("preview.toggle_playback",)
        )
        self.assertFalse(controls.compare_button.isChecked())
        self.assertEqual(time_label.text(), "00:30 / 01:05")

    def test_guide_mode_can_replace_the_shortcut_list(self) -> None:
        """안내 제목과 명령 목록을 바꾸면 열린 안내가 새 목록으로 다시 채워지는지 검증"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS

        controls = PlaybackControls(None, lambda: DEFAULT_SHORTCUTS)
        self.addCleanup(controls.deleteLater)
        controls.set_guide_mode("화질 비교 창 단축키", ("preview.toggle_playback",))
        guide = controls.guide_button
        guide.open_guide()
        _APP.processEvents()
        self.assertTrue(guide.is_guide_open())
        popover = guide._popover
        assert popover is not None
        self.assertEqual(popover._title.text(), "화질 비교 창 단축키")
        self.assertEqual(len(popover._entries), 1)
        guide.close_guide()

    def test_shortcut_tooltips_survive_show_and_state_refreshes(self) -> None:
        """표시와 재생, 음소거, 배속 변경 뒤에도 사용자 지정 단축키 툴팁이 유지되는지 검증"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS

        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["preview.toggle_playback"] = "X"
        shortcuts["preview.toggle_mute"] = "N"
        self.controls.set_shortcut_tooltips(shortcuts)
        self.controls.hide()
        self.controls.show()
        _APP.processEvents()
        self.assertIn("[X]", self.controls._btn_toggle.toolTip())
        self.controls.set_playing(True)
        self.assertIn("[X]", self.controls._btn_toggle.toolTip())
        self.assertIn("일시중지", self.controls._btn_toggle.toolTip())
        self.controls.toggle_mute()
        self.assertIn("[N]", self.controls._btn_volume.toolTip())
        self.assertIn("음소거 해제", self.controls._btn_volume.toolTip())
        self.controls.set_audio_state(40, False)
        self.assertIn("[N]", self.controls._btn_volume.toolTip())
        self.controls.set_playback_rate(2.0)
        self.assertIn("현재 2배", self.controls._btn_rate.toolTip())
        self.assertIn("[[", self.controls._btn_rate.toolTip())
        self.assertIn("]]", self.controls._btn_rate.toolTip())

    def test_shortcut_tooltips_use_current_keys(self) -> None:
        """사용자 지정 단축키가 재생, 음소거, 비교 버튼 툴팁에 반영되는지 검증"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS

        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["preview.toggle_playback"] = "X"
        shortcuts["preview.toggle_mute"] = "N"
        shortcuts["classify.compare_selected"] = "Ctrl+Shift+V"
        self.controls.set_shortcut_tooltips(shortcuts)
        self.assertIn("[X]", self.controls._btn_toggle.toolTip())
        self.assertIn("[N]", self.controls._btn_volume.toolTip())
        self.assertIn("(Ctrl+Shift+V)", self.controls.compare_button.toolTip())
        self.assertEqual(self.controls.guide_button.accessibleName(), "단축키 안내")

    def test_guide_actions_include_the_crop_preview_toggle(self) -> None:
        """단축키 안내 표에 여백 미리 보기 명령이 들어가는지 검증"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
        from src.gui.preview.video_preview_controls import PREVIEW_GUIDE_ACTIONS

        self.assertIn("preview.toggle_crop", PREVIEW_GUIDE_ACTIONS)
        controls = PlaybackControls(None, lambda: DEFAULT_SHORTCUTS)
        self.addCleanup(controls.deleteLater)
        guide = controls.guide_button
        guide.open_guide()
        _APP.processEvents()
        popover = guide._popover
        assert popover is not None
        self.assertIn("여백 미리 보기", [entry.label for entry in popover._entries])
        guide.close_guide()

    def test_guide_actions_include_the_background_cycle(self) -> None:
        """단축키 안내 표에 배경 색 전환 명령이 Shift+B로 들어가는지 검증"""
        from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
        from src.gui.preview.video_preview_controls import PREVIEW_GUIDE_ACTIONS

        self.assertIn("preview.cycle_background", PREVIEW_GUIDE_ACTIONS)
        controls = PlaybackControls(None, lambda: DEFAULT_SHORTCUTS)
        self.addCleanup(controls.deleteLater)
        guide = controls.guide_button
        guide.open_guide()
        _APP.processEvents()
        popover = guide._popover
        assert popover is not None
        entries = {entry.label: entry.key for entry in popover._entries}
        self.assertEqual(entries.get("배경 색 전환"), "Shift+B")
        guide.close_guide()

    def test_crop_toggle_keeps_its_bottom_bar_slot_before_fullscreen(self) -> None:
        """여백 미리 보기 토글이 하단 바에서 전체화면 버튼 바로 앞에만 있는지 검증"""
        controls = PlaybackControls()
        self.addCleanup(controls.deleteLater)
        controls.set_crop_available(True)
        layout_obj = controls.layout()
        assert layout_obj is not None
        widgets: list[QWidget | None] = []
        for index in range(layout_obj.count()):
            item = layout_obj.itemAt(index)
            widgets.append(None if item is None else item.widget())
        fullscreen = controls.findChild(QToolButton, "PreviewFullscreenButton")
        assert fullscreen is not None
        crop_index = widgets.index(controls.crop_button)
        self.assertIs(widgets[crop_index + 1], fullscreen)
        self.assertFalse(controls.crop_button.isHidden())
        # - 편집을 켜도 하단 바 구성이 그대로이고, 두 아이콘 버튼은 여기에 없음
        controls.crop_button.setChecked(True)
        _APP.processEvents()
        self.assertEqual(layout_obj.count(), len(widgets))
        for name in ("PreviewCropResetButton", "PreviewCropFullButton"):
            self.assertIsNone(controls.findChild(QToolButton, name))

    def test_clicking_compare_emits_the_request(self) -> None:
        requests: list[bool] = []
        self.controls.compare_requested.connect(lambda: requests.append(True))
        self.controls.compare_button.click()
        self.assertEqual(requests, [True])

    def test_volume_slider_change_updates_state_and_emits(self) -> None:
        changes: list[int] = []
        self.controls.volume_changed.connect(changes.append)
        self.controls._slider_volume.setValue(37)
        _APP.processEvents()
        self.assertEqual(changes, [37])
        self.assertEqual(self.controls.audio_state(), (37, False))
        self.assertEqual(self.controls._btn_volume.toolTip(), "음소거")


if __name__ == "__main__":
    unittest.main(verbosity=2)
