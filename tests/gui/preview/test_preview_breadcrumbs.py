"""미리 보기 시작과 숨김이 사용자 동작 기록에 남는지 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.core.settings.ui_state import UiState
from src.gui.preview.preview_controller import PreviewController
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class PreviewBreadcrumbTests(GuiTestCase):
    """미리 보기 컨트롤러가 시작과 숨김을 한 줄로 남기는지 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        ui = UiState()
        self.controller = PreviewController(
            self.parent,
            mock.MagicMock(),
            lambda: ui,
            ClassifierConfig,
            lambda: None,
            lambda _volume, _muted: None,
            lambda _rate: None,
            lambda: {},
            lambda _key: False,
            lambda _text: None,
        )

    def test_preview_path_notes_the_start_with_the_basename(self) -> None:
        """미리 보기 요청이 파일 이름만 담은 시작 문구를 남기는지 검증"""
        with (
            mock.patch.object(PreviewController, "_preview_builtin") as opened,
            mock.patch("src.gui.preview.preview_controller.note_event") as noted,
        ):
            self.controller.preview_path("C:/media/clip.mp4")
        opened.assert_called_once_with("C:/media/clip.mp4")
        self.assertEqual(noted.call_args.args, ("미리 보기", "시작: clip.mp4"))

    def test_hiding_the_preview_notes_the_stop_with_the_current_file(self) -> None:
        """미리 보기 숨김이 재생 중이던 파일 이름으로 중지 문구를 남기는지 검증"""
        window = mock.MagicMock()
        window.current_path = "C:/media/clip.mp4"
        with mock.patch("src.gui.preview.preview_controller.note_event") as noted:
            self.controller._hide_preview(window)
        self.assertEqual(noted.call_args.args, ("미리 보기", "중지: clip.mp4"))
        window.hide.assert_called_once()


if __name__ == "__main__":
    unittest.main()
