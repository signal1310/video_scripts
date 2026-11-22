"""내장 미리 보기 창 검증: 화질 비교 요청의 경로와 시점 전달, 볼륨 저장 연결, 구간 제한"""

import os
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.preview.video_preview_window import VideoPreviewWindow, clamp_seek_position
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _make_window(saved: list[tuple[int, bool]] | None = None) -> VideoPreviewWindow:
    state = UiState()
    saved = saved if saved is not None else []

    def set_audio(volume: int, muted: bool) -> None:
        saved.append((volume, muted))

    return VideoPreviewWindow(
        None,
        lambda: state.preview_seek_seconds,
        lambda: (state.preview_volume, state.preview_muted),
        set_audio,
        lambda: DEFAULT_SHORTCUTS,
        lambda _text: False,
        lambda: state.preview_info,
        lambda: None,
        lambda: state.preview_playback_rate,
        lambda _rate: None,
        lambda: state.preview_fullscreen,
        lambda _fullscreen: None,
    )


class CompareRequestTests(GuiTestCase):
    """미리 보기 조작 바의 화질 비교 요청"""

    def setUp(self) -> None:
        self.saved: list[tuple[int, bool]] = []
        self.window = _make_window(self.saved)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        _APP.processEvents()

    def test_current_path_and_position_are_forwarded(self) -> None:
        requests: list[tuple[str, float]] = []
        self.window.compare_requested.connect(lambda path, pos: requests.append((path, pos)))
        self.window._path = "C:/videos/a.mp4"
        with mock.patch.object(cast("object", self.window._player), "position", return_value=1500):
            self.window._controls.compare_button.click()
        self.assertEqual(requests, [("C:/videos/a.mp4", 1.5)])

    def test_no_path_means_no_request(self) -> None:
        requests: list[tuple[str, float]] = []
        self.window.compare_requested.connect(lambda path, pos: requests.append((path, pos)))
        self.window._path = ""
        self.window._controls.compare_button.click()
        self.assertEqual(requests, [])

    def test_volume_change_is_saved_through_the_audio_callback(self) -> None:
        self.window._controls._slider_volume.setValue(42)
        self.window._on_volume_changed(42)
        self.assertEqual(self.saved[-1], (42, False))


class ClampSeekTests(unittest.TestCase):
    """구간 이동 위치 제한"""

    def test_clamp_keeps_position_inside_duration(self) -> None:
        self.assertEqual(clamp_seek_position(1000, 500, 3000), 1500)
        self.assertEqual(clamp_seek_position(1000, -5000, 3000), 0)
        self.assertEqual(clamp_seek_position(1000, 5000, 3000), 3000)
        self.assertEqual(clamp_seek_position(1000, 500, 0), 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
