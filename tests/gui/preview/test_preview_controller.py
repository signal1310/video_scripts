"""미리 보기 컨트롤러의 설정 저장과 재인코딩 확인 창 연결(고른 영상 고정, 창 부모 전환)을 검증

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

from src.core.analysis.frame_compare import ComparePlan, FrameSource
from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.dialogs.frame_compare_meta import CompareSession
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
    """미리 보기 컨트롤러의 저장과 확인 창 연결을 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.harness = _Harness(self.parent)
        self.controller: PreviewController = self.harness.controller

    def test_fullscreen_change_is_saved_for_the_next_start(self) -> None:
        """미리 보기 창의 전체 화면 전환이 UI 상태에 저장되는지 검증"""
        self.controller._on_fullscreen_changed(True)
        self.assertTrue(self.harness.ui.preview_fullscreen)
        self.assertEqual(self.harness.persisted, [True])

    def test_always_on_top_change_is_saved_for_the_next_start(self) -> None:
        """미리 보기 창의 항상 위에 표시 전환이 UI 상태에 저장되는지 검증"""
        self.controller._on_always_on_top_changed(False)
        self.assertFalse(self.harness.ui.preview_always_on_top)
        self.assertEqual(self.harness.persisted, [True])

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
        self.assertEqual(window.setParent.call_args.args[0], owner)
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
        self.assertIsNone(window.setParent.call_args.args[0])
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

    def test_seek_seconds_reads_the_ui(self) -> None:
        """구간 이동 간격은 UI 상태에서 읽는지 검증"""
        self.harness.ui.preview_seek_seconds = 10
        self.assertEqual(self.controller.seek_seconds, 10)

    def test_attach_compare_applies_the_extractor_to_an_open_window(self) -> None:
        """비교 연결이 이미 열린 창에도 프레임 추출기를 넘기는지 검증"""
        window = mock.Mock()
        self.controller._window = window
        builder = mock.Mock(return_value=mock.Mock())
        extractor = mock.Mock()
        self.controller.attach_compare(builder, extractor)
        window.set_compare_extractor.assert_called_once_with(extractor)

    def test_open_compare_builds_a_session_and_shows_it_in_the_same_window(self) -> None:
        """비교 모드 진입이 세션을 만들어 같은 창의 비교 페이지로 넘기는지 검증"""
        session = mock.Mock()
        builder = mock.Mock(return_value=session)
        extractor = mock.Mock()
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            self.controller.attach_compare(builder, extractor)
            self.controller.open_compare("C:/a.mp4", 12.5, auto_play=True)
        builder.assert_called_once_with("C:/a.mp4", 12.5)
        window_class.assert_called_once()
        window.set_compare_extractor.assert_called_once_with(extractor)
        window.show_compare_session.assert_called_once_with(session, auto_play=True)

    def test_open_compare_ignores_empty_path_or_missing_builder(self) -> None:
        """비교 세션 생성기나 경로가 없으면 창을 만들지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.open_compare("C:/a.mp4")
        window_class.assert_not_called()
        self.controller.attach_compare(mock.Mock(), mock.Mock())
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.open_compare("")
        window_class.assert_not_called()

    def test_toggle_compare_leaves_the_compare_mode_when_visible(self) -> None:
        """보이는 창이 비교 모드면 일반 모드로 되돌리는지 검증"""
        window = mock.Mock()
        window.isVisible.return_value = True
        window.compare_mode = True
        self.controller._window = window
        self.controller.toggle_compare()
        window.leave_compare.assert_called_once()

    def test_toggle_compare_enters_compare_with_the_current_time_and_state(self) -> None:
        """일반 모드면 현재 경로, 시점, 재생 상태로 비교 모드에 들어가는지 검증"""
        window = mock.Mock()
        window.isVisible.return_value = True
        window.compare_mode = False
        window.current_path = "C:/a.mp4"
        window.position_sec = 7.5
        window.is_playing = True
        self.controller._window = window
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller.toggle_compare()
        open_compare.assert_called_once_with("C:/a.mp4", 7.5, auto_play=True)

    def test_toggle_compare_without_a_window_opens_the_selected_video(self) -> None:
        """창이 없으면 표의 가장 위 선택 영상으로 비교 모드를 여는지 검증"""
        self.harness.table.topmost_selected_video_row.return_value = 0
        self.harness.table.video_path_at_row.return_value = "C:/pick.mp4"
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller.toggle_compare()
        open_compare.assert_called_once_with("C:/pick.mp4")

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

    def test_builtin_window_compare_signal_opens_the_same_window_compare_mode(self) -> None:
        """내장 창을 만들면 화질 비교 신호가 컨트롤러를 거쳐 같은 창의 비교 모드로 이어짐"""
        window = mock.Mock()
        window.current_path = ""
        window.is_playing = True
        with mock.patch.object(module, "VideoPreviewWindow", return_value=window):
            self.controller._preview_builtin("C:/a.mp4")
        connected = window.compare_requested.connect.call_args.args[0]
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            connected("C:/a.mp4", 3.0)
        open_compare.assert_called_once_with("C:/a.mp4", 3.0, auto_play=True)

    def test_compare_request_without_a_path_does_nothing(self) -> None:
        """경로 없는 비교 요청은 창을 열지 않는지 검증"""
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller._on_compare_requested("", 0.0)
        open_compare.assert_not_called()

    def test_table_focus_change_in_compare_mode_loads_a_new_session(self) -> None:
        """비교 모드에서 표 포커스가 바뀌면 새 비교 세션을 같은 뷰에 올리는지 검증"""
        paths = ["C:/a.mp4", "C:/b.mp4"]
        window = mock.Mock()
        window.isVisible.return_value = True
        window.compare_mode = True
        window.current_path = paths[0]
        self.controller._window = window
        self.harness.table.current_video_path.return_value = paths[1]
        self.harness.ui.preview_seek_seconds = 15
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller.on_table_current_changed()
        open_compare.assert_called_once_with(paths[1], 15)

    def test_releasing_the_dialog_owner_clears_the_compare_mode(self) -> None:
        """확인 창이 닫히면 비교 모드를 일반 모드로 되돌리고 창을 숨기는지 검증"""
        window = mock.Mock()
        window.compare_mode = True
        self.controller._window = window
        self.controller._dialog_owner = self.parent
        self.controller.release_dialog_owner()
        window.leave_compare.assert_called_once()
        window.hide.assert_called_once()
        self.assertIsNone(window.setParent.call_args.args[0])

    def test_dialog_preview_switches_to_compare_and_releases_on_close(self) -> None:
        """확인 창 아래 미리 보기에서도 비교 모드로 바꾸고, 닫으면 일반 모드로 정리하는지 검증"""
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            plan = ComparePlan(
                original=FrameSource(key="원본", path=path, label="원본"),
                targets=(),
                default_key=None,
                reason="비교할 대상이 없습니다.",
            )
            session = CompareSession(
                plan=plan, duration_sec=0.0, fps=0.0, filename="clip.mp4", source_path=path
            )
            self.controller.attach_compare(lambda _path, _time: session, mock.Mock())
            self.controller.preview_for_dialog(
                make_eval(make_video(filename="clip.mp4")), path, owner
            )
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(window.close)
            self.controller.open_compare(path, 1.5)
            _APP.processEvents()
            self.assertTrue(window.compare_mode)
            self.assertIs(window.parentWidget(), owner)
            self.controller.release_dialog_owner()
            _APP.processEvents()
            self.assertFalse(window.compare_mode)

    def test_builtin_window_is_created_without_an_owner(self) -> None:
        """내장 창이 메인 창을 소유자로 두지 않아 다른 창의 Z 순서를 건드리지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller._preview_builtin("C:/a.mp4")
        self.assertNotIn(self.parent, window_class.call_args.args)
        self.assertEqual(len(window_class.call_args.args), 13)
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
