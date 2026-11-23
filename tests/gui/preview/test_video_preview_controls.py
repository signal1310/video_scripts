"""미리 보기 조작 바 검증: 화질 비교 버튼의 위치, 접근성 이름, 툴팁, 신호와 볼륨 슬라이더 연결"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

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

    def test_compare_button_sits_right_before_settings_after_volume(self) -> None:
        layout_obj = self.controls.layout()
        assert layout_obj is not None
        widgets: list[QWidget | None] = []
        for index in range(layout_obj.count()):
            item = layout_obj.itemAt(index)
            widgets.append(None if item is None else item.widget())
        compare_index = widgets.index(self.controls.compare_button)
        settings = widgets[compare_index + 1]
        volume = widgets[compare_index - 1]
        assert settings is not None
        assert volume is not None
        self.assertIs(settings, self.controls.settings_button)
        self.assertIs(volume, self.controls._slider_volume)
        self.assertIs(widgets[-1], self.controls.guide_button)

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
