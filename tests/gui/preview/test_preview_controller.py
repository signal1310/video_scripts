"""미리 보기 컨트롤러의 방식 전환, 설정 저장, QuickLook 실패 안내와 재인코딩 확인 창 연결(고른 영상 고정, 창 부모 전환)을 검증

- 내장 미리 보기 창은 가짜로 바꿔 실제 미디어를 열지 않음
"""

import os
import tempfile
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import PREVIEW_BACKEND_BUILTIN, PREVIEW_BACKEND_QUICKLOOK, UiState
from src.gui.preview import preview_controller as module
from src.gui.preview.preview_controller import PreviewController
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.video_preview_window import PREVIEW_WINDOW_FLAGS
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

        window.show_path_at.assert_called_once_with("C:/clip.mp4", 0.0, None, auto_play=True)
        window.setParent.assert_called_with(owner, PREVIEW_WINDOW_FLAGS)
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
        window.setParent.assert_called_with(None, PREVIEW_WINDOW_FLAGS)
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

    def test_dialog_owner_round_trip_keeps_the_always_on_top_flag(self) -> None:
        """확인 창 아래로 옮겼다가 독립 창으로 되돌린 뒤에도 항상 맨 위 플래그가 유지되는지 검증"""
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.controller.preview_for_dialog(make_eval(make_video(filename="clip.mp4")), path, owner)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(window.close)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.controller.release_dialog_owner()
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertIsNone(window.parentWidget())

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

    def test_seek_seconds_and_compare_handler_forwarding(self) -> None:
        """구간 이동 간격은 UI 상태에서 읽고, 비교 요청은 등록한 함수로 경로와 시점을 넘김"""
        self.harness.ui.preview_seek_seconds = 10
        self.assertEqual(self.controller.seek_seconds, 10)
        calls: list[tuple[str, float]] = []
        self.controller.set_compare_handler(lambda path, pos: calls.append((path, pos)))
        self.controller._on_compare_requested("C:/a.mp4", 12.5)
        self.assertEqual(calls, [("C:/a.mp4", 12.5)])
        self.controller.set_compare_handler(lambda _path, _pos: None)
        self.controller._on_compare_requested("C:/b.mp4", 0.0)

    def test_shortcuts_property_returns_a_copy(self) -> None:
        """화질 비교 창이 같은 단축키 설정을 읽되 사본을 받는지 검증"""
        self.harness.controller._get_shortcuts = lambda: {"preview.toggle_playback": "K"}
        shortcuts = dict(self.controller.shortcuts)
        shortcuts["preview.toggle_playback"] = "X"
        self.assertEqual(self.controller.shortcuts["preview.toggle_playback"], "K")

    def test_sync_shortcuts_forwards_to_the_builtin_window(self) -> None:
        """단축키 변경이 열린 내장 미리 보기 창으로 전달되는지 검증"""
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.controller.preview_for_dialog(
                make_eval(make_video(filename="clip.mp4")), path, owner
            )
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(window.close)
            effective = dict(DEFAULT_SHORTCUTS)
            effective["preview.toggle_playback"] = "X"
            self.controller.sync_shortcuts(effective)
            self.assertEqual(window._controls._btn_toggle.toolTip(), "재생 [X]")
            self.controller.release_dialog_owner()

    def test_shared_playback_state_reads_and_saves_through_the_ui(self) -> None:
        """화질 비교 창과 공유하는 음량, 음소거, 배속은 미리 보기와 같은 저장 경로를 씀"""
        self.harness.ui.preview_volume = 40
        self.harness.ui.preview_muted = True
        self.harness.ui.preview_playback_rate = 2.0
        self.assertEqual(self.controller.get_audio(), (40, True))
        audio: list[tuple[int, bool]] = []
        self.controller._set_audio = lambda volume, muted: audio.append((volume, muted))
        self.controller.save_audio(65, False)
        self.assertEqual(audio, [(65, False)])
        self.assertEqual(self.controller.get_playback_rate(), 2.0)
        rates: list[float] = []
        self.controller._set_playback_rate = lambda rate: rates.append(rate)
        self.controller.save_playback_rate(0.5)
        self.assertEqual(rates, [0.5])

    def test_builtin_window_compare_signal_is_connected(self) -> None:
        """내장 창을 만들면 화질 비교 신호가 컨트롤러를 거쳐 등록 함수로 전달됨"""
        calls: list[tuple[str, float]] = []
        self.controller.set_compare_handler(lambda path, pos: calls.append((path, pos)))
        window = mock.Mock()
        window.current_path = ""
        with mock.patch.object(module, "VideoPreviewWindow", return_value=window):
            self.controller._preview_builtin("C:/a.mp4")
        connected = window.compare_requested.connect.call_args.args[0]
        connected("C:/a.mp4", 3.0)
        self.assertEqual(calls, [("C:/a.mp4", 3.0)])

    def test_builtin_window_is_created_without_an_owner(self) -> None:
        """내장 창이 메인 창을 소유자로 두지 않아 다른 창의 Z 순서를 건드리지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller._preview_builtin("C:/a.mp4")
        self.assertNotIn(self.parent, window_class.call_args.args)
        self.assertEqual(len(window_class.call_args.args), 11)
        window_class.return_value.show_path_at.assert_called_once_with(
            "C:/a.mp4", 0.0, None, auto_play=True
        )

    def test_preview_path_at_forwards_time_and_geometry(self) -> None:
        """화질 비교 창에서 돌아올 때 시점과 창 영역, 재생 상태를 내장 창에 넘기는지 검증"""
        geometry = QRect(20, 30, 640, 360)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.preview_path_at("C:/a.mp4", 12.5, geometry, was_playing=False)
        window_class.return_value.show_path_at.assert_called_once_with(
            "C:/a.mp4", 12.5, geometry, auto_play=False
        )

    def test_preview_path_at_with_quicklook_ignores_position(self) -> None:
        """QuickLook 방식이면 시점과 영역을 버리고 QuickLook에 파일만 넘기는지 검증"""
        self.harness.ui.preview_backend = PREVIEW_BACKEND_QUICKLOOK
        with mock.patch.object(module, "open_with_quicklook", return_value=True) as quicklook:
            self.controller.preview_path_at("C:/a.mp4", 12.5, QRect(0, 0, 10, 10))
        quicklook.assert_called_once_with("C:/a.mp4")

    def test_hide_and_close_windows_handle_builtin_window_and_popups(self) -> None:
        """전환 때 미리 보기 창을 숨기고, 종료 정리에서 창과 설정 팝업을 닫는지 검증"""
        window = mock.Mock()
        window.isVisible.return_value = True
        popup = mock.Mock()
        self.controller._window = window
        self.controller._window_popup = popup
        self.controller._main_popup = mock.Mock()
        self.controller.hide_builtin()
        window.hide.assert_called_once()
        self.controller.close_windows()
        window.close.assert_called_once()
        popup.close.assert_called_once()
        self.controller._main_popup.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
