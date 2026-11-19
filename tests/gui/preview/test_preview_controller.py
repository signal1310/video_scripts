"""미리 보기 컨트롤러의 방식 전환, 설정 저장, QuickLook 실패 안내와 재인코딩 확인 창 연결(고른 영상 고정, 창 부모 전환)을 검증

- 내장 미리 보기 창은 가짜로 바꿔 실제 미디어를 열지 않음
"""

import os
import tempfile
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.core.settings.ui_state import PREVIEW_BACKEND_BUILTIN, PREVIEW_BACKEND_QUICKLOOK, UiState
from src.gui.preview import preview_controller as module
from src.gui.preview.preview_controller import PreviewController
from src.gui.preview.preview_info_items import PreviewVideoInfo
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class _Harness:
    """컨트롤러와 함께 쓰는 가짜 표, 저장 호출 기록"""

    def __init__(self, parent: QWidget) -> None:
        self.ui = UiState()
        self.persisted: list[bool] = []
        self.table: Any = mock.MagicMock()
        self.table.table_model.evaluations = []
        self.controller = PreviewController(
            parent,
            self.table,
            lambda: self.ui,
            ClassifierConfig,
            lambda: self.persisted.append(True),
            lambda _volume, _muted: None,
            lambda _rate: None,
            lambda: {},
            lambda _key: False,
            lambda _text: None,
        )


class PreviewControllerTests(unittest.TestCase):
    """미리 보기 컨트롤러의 방식 전환, 저장, QuickLook, 확인 창 연결을 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.harness = _Harness(self.parent)
        self.controller: PreviewController = self.harness.controller

    def test_switching_to_quicklook_warms_it_up_and_saves(self) -> None:
        """QuickLook 방식으로 바꾸면 웜업하고 저장하며, 내장으로 되돌리면 웜업하지 않는지 검증"""
        with mock.patch.object(module, "warm_up_quicklook") as warm:
            self.controller.set_backend(PREVIEW_BACKEND_QUICKLOOK)
            warm.assert_called_once()
            self.assertEqual(self.harness.ui.preview_backend, PREVIEW_BACKEND_QUICKLOOK)
            self.controller.set_backend(PREVIEW_BACKEND_BUILTIN)
            warm.assert_called_once()
        self.assertEqual(len(self.harness.persisted), 2)

    def test_fullscreen_change_is_saved_for_the_next_start(self) -> None:
        """미리 보기 창의 전체 화면 전환이 UI 상태에 저장되는지 검증"""
        self.controller._on_fullscreen_changed(True)
        self.assertTrue(self.harness.ui.preview_fullscreen)
        self.assertEqual(self.harness.persisted, [True])

    def test_quicklook_missing_shows_install_guidance(self) -> None:
        """QuickLook 실행에 실패하면 설치 안내 경고를 띄우는지 검증"""
        self.harness.ui.preview_backend = PREVIEW_BACKEND_QUICKLOOK
        with (
            mock.patch.object(module, "open_with_quicklook", return_value=False),
            mock.patch.object(module.QMessageBox, "warning") as warning,
        ):
            self.controller.preview_path("clip.mp4")
        warning.assert_called_once()

    def test_dialog_preview_pins_the_chosen_video_and_moves_window_under_the_dialog(self) -> None:
        """확인 창의 미리 보기가 고른 영상을 정보 표시에 쓰고, 창의 부모를 확인 창으로 바꾸는지 검증"""
        chosen = make_eval(make_video(filename="chosen.mp4"))
        table_pick = make_eval(make_video(filename="table.mp4"))
        self.harness.table.get_evaluation_at.return_value = table_pick
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = True
            self.controller.preview_for_dialog(chosen, "C:/clip.mp4", owner)

        window.show_path.assert_called_once_with("C:/clip.mp4")
        window.setParent.assert_called_with(owner, Qt.WindowType.Window)
        info = window.set_video_info.call_args.args[0]
        self.assertIsInstance(info, PreviewVideoInfo)
        self.assertIs(info.ev, chosen)

    def test_table_focus_change_does_not_replace_the_chosen_video(self) -> None:
        """확인 창이 열려 있으면 표 포커스 변경이 미리 보기 영상을 바꾸지 않는지 검증"""
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        chosen = make_eval(make_video(filename="chosen.mp4"))
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = True
            self.controller.preview_for_dialog(chosen, "C:/chosen.mp4", owner)
            self.harness.table.current_video_path.return_value = "C:/other.mp4"
            self.controller.on_table_current_changed()
        window.load_path.assert_not_called()

    def test_releasing_the_dialog_returns_the_window_and_the_table_video(self) -> None:
        """확인 창이 닫히면 창을 메인 창으로 되돌려 숨기고, 정보 표시가 표의 포커스 영상으로 돌아가는지 검증"""
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        chosen = make_eval(make_video(filename="chosen.mp4"))
        table_pick = make_eval(make_video(filename="table.mp4"))
        self.harness.table.get_evaluation_at.return_value = table_pick
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = True
            self.controller.preview_for_dialog(chosen, "C:/chosen.mp4", owner)
            window.set_video_info.reset_mock()
            self.controller.release_dialog_owner()
            self.controller.refresh_video_info()

        window.hide.assert_called_once()
        window.setParent.assert_called_with(self.parent, Qt.WindowType.Window)
        info: PreviewVideoInfo = window.set_video_info.call_args.args[0]
        self.assertIs(info.ev, table_pick)

    def test_release_without_a_dialog_does_nothing(self) -> None:
        """확인 창 없이 해제를 부르면 아무 일도 하지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.release_dialog_owner()
        window_class.assert_not_called()

    def test_dialog_preview_keeps_the_video_loaded_after_moving_under_the_dialog(self) -> None:
        """실제 내장 창으로, 확인 창 아래로 옮긴 뒤에도 영상이 로드된 상태로 남는지 검증

        - 부모 변경이 창을 숨기면서 소스를 비우던 순서 문제를 막기 위한 회귀 테스트
        """
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.controller.preview_for_dialog(make_eval(make_video(filename="clip.mp4")), path, owner)
            window = self.controller.builtin_window
            assert window is not None
            self.assertEqual(window.current_path, path)
            self.assertIs(window.parentWidget(), owner)
            self.controller.release_dialog_owner()

    def test_dialog_preview_with_quicklook_skips_the_builtin_window(self) -> None:
        """QuickLook 방식이면 확인 창 미리 보기도 내장 창을 만들지 않고 QuickLook에 넘기는지 검증"""
        self.harness.ui.preview_backend = PREVIEW_BACKEND_QUICKLOOK
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with (
            mock.patch.object(module, "open_with_quicklook", return_value=True) as quicklook,
            mock.patch.object(module, "VideoPreviewWindow") as window_class,
        ):
            self.controller.preview_for_dialog(make_eval(make_video(filename="a.mp4")), "C:/a.mp4", owner)
        quicklook.assert_called_once_with("C:/a.mp4")
        window_class.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
