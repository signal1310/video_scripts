"""미리 보기 검증: 단축키 기본값, 방식 설정 정규화, QuickLook 활성화, 내장 창 방향키와 닫기, 음소거 전환, 표와의 선택 동기화"""

import os
import tempfile
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QSlider, QWidget

from src.core.shortcuts import DEFAULT_SHORTCUTS
from src.core.state_codec import parse_state_mapping
from src.core.ui_state import (
    DEFAULT_PREVIEW_BACKEND,
    DEFAULT_PREVIEW_MUTED,
    DEFAULT_PREVIEW_SEEK_SECONDS,
    DEFAULT_PREVIEW_VOLUME,
    PREVIEW_BACKEND_QUICKLOOK,
    VALID_PREVIEW_SEEK_SECONDS,
)
from src.gui.preview_controller import PreviewController
from src.gui.widgets.quicklook_launcher import open_with_quicklook, warm_up_quicklook
from src.gui.widgets.video_preview_controls import PlaybackControls, format_playback_time
from src.gui.widgets.video_preview_window import VideoPreviewWindow, clamp_seek_position
from src.gui.widgets.video_table_view import VideoTableView

_APP = QApplication.instance() or QApplication([])


def _press(widget: QWidget, key: Qt.Key) -> None:
    """위젯에 키 누름 이벤트를 직접 보내 창 수준 단축키를 거치지 않고 위젯의 keyPressEvent만 검증"""
    QApplication.sendEvent(
        widget, QKeyEvent(QEvent.Type.KeyPress, key.value, Qt.KeyboardModifier.NoModifier)
    )


def _make_videos(test: unittest.TestCase, count: int) -> list[str]:
    """빈 영상 파일을 임시 폴더에 만들고 경로 목록 반환, 폴더 정리는 테스트 종료 시 등록"""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    paths: list[str] = []
    for index in range(count):
        path = os.path.join(tmp.name, f"clip{index}.mp4")
        with open(path, "wb"):
            pass
        paths.append(path)
    return paths


class PreviewSettingTests(unittest.TestCase):
    """미리 보기 단축키 기본값과 방식 설정의 저장값 정규화를 검증"""

    def test_space_is_default_key_of_preview_command(self) -> None:
        self.assertEqual(DEFAULT_SHORTCUTS["classify.preview_selected"], "Space")

    def test_unknown_backend_falls_back_to_builtin(self) -> None:
        state = parse_state_mapping({"ui": {"preview_backend": "bogus"}})
        self.assertEqual(state.ui.preview_backend, DEFAULT_PREVIEW_BACKEND)

    def test_quicklook_choice_is_kept(self) -> None:
        state = parse_state_mapping({"ui": {"preview_backend": PREVIEW_BACKEND_QUICKLOOK}})
        self.assertEqual(state.ui.preview_backend, PREVIEW_BACKEND_QUICKLOOK)

    def test_seek_seconds_outside_choices_fall_back_to_default(self) -> None:
        for raw in (7, "5", True, None):
            with self.subTest(raw=raw):
                state = parse_state_mapping({"ui": {"preview_seek_seconds": raw}})
                self.assertEqual(state.ui.preview_seek_seconds, DEFAULT_PREVIEW_SEEK_SECONDS)

    def test_valid_seek_seconds_are_kept(self) -> None:
        for seconds in VALID_PREVIEW_SEEK_SECONDS:
            with self.subTest(seconds=seconds):
                state = parse_state_mapping({"ui": {"preview_seek_seconds": seconds}})
                self.assertEqual(state.ui.preview_seek_seconds, seconds)


class PreviewAudioStateTests(unittest.TestCase):
    """미리 보기 음량과 음소거 상태의 저장값 정규화를 검증"""

    def test_defaults_when_absent(self) -> None:
        state = parse_state_mapping({})
        self.assertEqual(state.ui.preview_volume, DEFAULT_PREVIEW_VOLUME)
        self.assertEqual(state.ui.preview_muted, DEFAULT_PREVIEW_MUTED)

    def test_out_of_range_volume_falls_back_to_default(self) -> None:
        for raw in (-1, 101, "50", True, None):
            with self.subTest(raw=raw):
                state = parse_state_mapping({"ui": {"preview_volume": raw}})
                self.assertEqual(state.ui.preview_volume, DEFAULT_PREVIEW_VOLUME)

    def test_valid_volume_and_mute_are_kept(self) -> None:
        state = parse_state_mapping({"ui": {"preview_volume": 35, "preview_muted": True}})
        self.assertEqual(state.ui.preview_volume, 35)
        self.assertTrue(state.ui.preview_muted)


class PlaybackMathTests(unittest.TestCase):
    """구간 이동 위치 제한과 재생 시간 표기가 경계에서 맞는지 검증"""

    def test_seek_is_clamped_to_video_range(self) -> None:
        self.assertEqual(clamp_seek_position(2_000, -5_000, 60_000), 0)
        self.assertEqual(clamp_seek_position(58_000, 5_000, 60_000), 60_000)
        self.assertEqual(clamp_seek_position(10_000, 5_000, 60_000), 15_000)

    def test_seek_is_ignored_when_duration_is_unknown(self) -> None:
        self.assertEqual(clamp_seek_position(3_000, 5_000, 0), 3_000)

    def test_time_label_uses_hours_only_when_needed(self) -> None:
        self.assertEqual(format_playback_time(0), "00:00")
        self.assertEqual(format_playback_time(83_999), "01:23")
        self.assertEqual(format_playback_time(3_723_000), "1:02:03")


_SAMPLE_WINDOWS_PATH: str = r"C:\videos\sample.mp4"
_QUICKLOOK_EXE_PATH: str = r"C:\ql\QuickLook.exe"
_QUICKLOOK_LAUNCHER = "src.gui.widgets.quicklook_launcher"


class OpenWithQuickLookTests(unittest.TestCase):
    """Store 판 활성화를 먼저 시도하고, 실패하면 PATH의 QuickLook.exe로 넘기며, 둘 다 없으면 실패를 알리는지 검증"""

    def test_store_activation_success_skips_exe_fallback(self) -> None:
        with (
            mock.patch(f"{_QUICKLOOK_LAUNCHER}._activate_store_quicklook", return_value=True) as activate,
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.subprocess.Popen") as popen,
        ):
            self.assertTrue(open_with_quicklook(_SAMPLE_WINDOWS_PATH))
        activate.assert_called_once_with(f'"{os.path.normpath(_SAMPLE_WINDOWS_PATH)}"')
        popen.assert_not_called()

    def test_falls_back_to_path_exe_when_store_activation_fails(self) -> None:
        with (
            mock.patch(f"{_QUICKLOOK_LAUNCHER}._activate_store_quicklook", return_value=False),
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.shutil.which", return_value=_QUICKLOOK_EXE_PATH),
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.subprocess.Popen") as popen,
        ):
            self.assertTrue(open_with_quicklook(_SAMPLE_WINDOWS_PATH))
        popen.assert_called_once()

    def test_returns_false_when_neither_store_nor_path_exe_is_available(self) -> None:
        with (
            mock.patch(f"{_QUICKLOOK_LAUNCHER}._activate_store_quicklook", return_value=False),
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.shutil.which", return_value=None),
        ):
            self.assertFalse(open_with_quicklook(_SAMPLE_WINDOWS_PATH))


class _FakeTable:
    """표의 행 번호, 포커스 행, 가장 위 선택 행을 흉내 내는 대역"""

    def __init__(self, paths: list[str], selected_top: int | None) -> None:
        self.paths = paths
        self.selected_top = selected_top
        self.current: int | None = selected_top

    def topmost_selected_video_row(self) -> int | None:
        return self.selected_top

    def set_current_row(self, row: int) -> None:
        self.current = row

    def video_path_at_row(self, row: int) -> str | None:
        return self.paths[row] if 0 <= row < len(self.paths) else None

    def current_video_path(self) -> str | None:
        return None if self.current is None else self.video_path_at_row(self.current)

    def move_current_row(self, step: int) -> None:
        if self.current is not None and self.video_path_at_row(self.current + step) is not None:
            self.current += step


def _controller(parent: QWidget, table: _FakeTable, messages: list[str]) -> PreviewController:
    return PreviewController(
        parent,
        cast("VideoTableView", table),
        lambda: DEFAULT_PREVIEW_BACKEND,
        lambda: DEFAULT_PREVIEW_SEEK_SECONDS,
        lambda: (DEFAULT_PREVIEW_VOLUME, DEFAULT_PREVIEW_MUTED),
        lambda _v, _m: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _t: False,
        messages.append,
    )


class QuickLookWarmUpTests(unittest.TestCase):
    """QuickLook 방식일 때만, 실행 중이 아닐 때만 인수 없이 상주 활성화하는지 검증"""

    def test_warm_up_launches_only_when_quicklook_is_not_running(self) -> None:
        with (
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.is_quicklook_running", return_value=False),
            mock.patch(f"{_QUICKLOOK_LAUNCHER}._activate_store_quicklook", return_value=True) as activate,
        ):
            warm_up_quicklook()
        activate.assert_called_once_with("")

    def test_warm_up_skips_when_quicklook_is_already_running(self) -> None:
        with (
            mock.patch(f"{_QUICKLOOK_LAUNCHER}.is_quicklook_running", return_value=True),
            mock.patch(f"{_QUICKLOOK_LAUNCHER}._activate_store_quicklook") as activate,
        ):
            warm_up_quicklook()
        activate.assert_not_called()

    def test_controller_warms_up_only_for_quicklook_backend(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        for backend, expected_calls in ((DEFAULT_PREVIEW_BACKEND, 0), (PREVIEW_BACKEND_QUICKLOOK, 1)):
            with mock.patch("src.gui.preview_controller.warm_up_quicklook") as warm:
                controller = PreviewController(
                    parent,
                    cast("VideoTableView", _FakeTable([], None)),
                    lambda b=backend: b,
                    lambda: DEFAULT_PREVIEW_SEEK_SECONDS,
                    lambda: (DEFAULT_PREVIEW_VOLUME, DEFAULT_PREVIEW_MUTED),
                    lambda _v, _m: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _t: False,
                    lambda _t: None,
                )
                controller.warm_up()
            self.assertEqual(warm.call_count, expected_calls, backend)


class PreviewControllerTests(unittest.TestCase):
    """선택 없음 안내, 다중 선택 시 가장 위 영상만 열기, 표 포커스에 따른 창 영상 교체를 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.close)

    def test_no_selection_reports_status_and_opens_nothing(self) -> None:
        messages: list[str] = []
        controller = _controller(self.parent, _FakeTable([], None), messages)
        controller.preview_selected()
        self.assertEqual(messages, ["영상을 먼저 선택해 주세요."])
        self.assertIsNone(controller.builtin_window)

    def test_space_opens_only_the_topmost_selected_video(self) -> None:
        paths = _make_videos(self, 3)
        table = _FakeTable(paths, selected_top=1)
        controller = _controller(self.parent, table, [])
        controller.preview_selected()
        window = controller.builtin_window
        assert window is not None
        self.assertEqual(window.current_path, paths[1])
        self.assertEqual(table.current, 1)
        controller.preview_selected()
        self.assertFalse(window.isVisible())

    def test_table_focus_change_swaps_video_while_window_is_visible(self) -> None:
        paths = _make_videos(self, 3)
        table = _FakeTable(paths, selected_top=0)
        controller = _controller(self.parent, table, [])
        controller.preview_selected()
        table.current = 2
        controller.on_table_current_changed()
        window = controller.builtin_window
        assert window is not None
        self.assertEqual(window.current_path, paths[2])

    def test_table_focus_change_is_ignored_when_window_is_hidden(self) -> None:
        paths = _make_videos(self, 2)
        table = _FakeTable(paths, selected_top=0)
        controller = _controller(self.parent, table, [])
        controller.on_table_current_changed()
        self.assertIsNone(controller.builtin_window)

    def test_step_request_moves_table_focus(self) -> None:
        paths = _make_videos(self, 3)
        table = _FakeTable(paths, selected_top=0)
        controller = _controller(self.parent, table, [])
        controller.on_step_requested(1)
        self.assertEqual(table.current, 1)


class VideoPreviewWindowTests(unittest.TestCase):
    """위아래 방향키의 목록 이동 요청, 좌우 방향키 구간 이동, 영상 교체, Space와 Esc 닫기를 검증"""

    def setUp(self) -> None:
        self.paths = _make_videos(self, 3)
        self.window = VideoPreviewWindow(
            None,
            lambda: DEFAULT_PREVIEW_SEEK_SECONDS,
            lambda: (DEFAULT_PREVIEW_VOLUME, DEFAULT_PREVIEW_MUTED),
            lambda _v, _m: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _t: False,
        )
        self.addCleanup(self.window.close)
        self.window.show_path(self.paths[0])

    def test_up_down_keys_request_steps_without_changing_video(self) -> None:
        steps: list[int] = []
        self.window.step_requested.connect(steps.append)
        _press(self.window, Qt.Key.Key_Down)
        _press(self.window, Qt.Key.Key_Up)
        self.assertEqual(steps, [1, -1])
        self.assertEqual(self.window.current_path, self.paths[0])

    def test_left_right_keys_seek_without_requesting_steps(self) -> None:
        steps: list[int] = []
        self.window.step_requested.connect(steps.append)
        _press(self.window, Qt.Key.Key_Left)
        _press(self.window, Qt.Key.Key_Right)
        self.assertEqual(steps, [])

    def test_load_path_replaces_video_and_title(self) -> None:
        self.window.load_path(self.paths[2])
        self.assertEqual(self.window.current_path, self.paths[2])
        self.assertEqual(self.window.windowTitle(), os.path.basename(self.paths[2]))

    def test_load_path_logs_start_line_with_filename(self) -> None:
        """영상 교체 때마다 재생 시작 로그 한 줄이 파일명과 함께 남는지 검증"""
        with self.assertLogs("src.gui.widgets.video_preview_window", level="INFO") as logs:
            self.window.load_path(self.paths[1])
        self.assertEqual(
            logs.output,
            [f"INFO:src.gui.widgets.video_preview_window:미리 보기 재생 시작: {os.path.basename(self.paths[1])}"],
        )

    def test_hide_releases_media_source_so_file_can_be_removed(self) -> None:
        player = self.window.findChild(QMediaPlayer)
        assert player is not None
        self.assertFalse(player.source().isEmpty())
        self.window.hide()
        self.assertTrue(player.source().isEmpty())
        self.assertEqual(self.window.current_path, "")

    def test_space_and_escape_hide_window(self) -> None:
        _press(self.window, Qt.Key.Key_Space)
        self.assertFalse(self.window.isVisible())
        self.window.show_path(self.paths[0])
        _press(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.isVisible())


class PreviewShortcutTests(unittest.TestCase):
    """미리 보기 창 명령이 단축키 설정 값으로 바뀌고, 바뀐 뒤에는 기본 키가 동작하지 않는지 검증"""

    def _window(self, shortcuts: dict[str, str]) -> VideoPreviewWindow:
        window = VideoPreviewWindow(
            None,
            lambda: DEFAULT_PREVIEW_SEEK_SECONDS,
            lambda: (DEFAULT_PREVIEW_VOLUME, DEFAULT_PREVIEW_MUTED),
            lambda _v, _m: None,
            lambda: {**DEFAULT_SHORTCUTS, **shortcuts},
            lambda _t: False,
        )
        self.addCleanup(window.close)
        return window

    def test_custom_key_triggers_previous_and_default_key_is_released(self) -> None:
        window = self._window({"preview.previous": "J", "preview.next": "L"})
        steps: list[int] = []
        window.step_requested.connect(steps.append)
        _press(window, Qt.Key.Key_J)
        _press(window, Qt.Key.Key_L)
        _press(window, Qt.Key.Key_Up)
        self.assertEqual(steps, [-1, 1])

    def test_f_toggles_fullscreen_and_escape_leaves_it_before_closing(self) -> None:
        window = self._window({})
        _press(window, Qt.Key.Key_F)
        self.assertTrue(window.isFullScreen())
        _press(window, Qt.Key.Key_Escape)
        self.assertFalse(window.isFullScreen())
        self.assertTrue(window.isVisible())
        _press(window, Qt.Key.Key_F)
        _press(window, Qt.Key.Key_F)
        self.assertFalse(window.isFullScreen())

    def test_empty_binding_disables_the_command(self) -> None:
        window = self._window({"preview.next": ""})
        steps: list[int] = []
        window.step_requested.connect(steps.append)
        _press(window, Qt.Key.Key_Down)
        self.assertEqual(steps, [])


class PreviewKeyFilterTests(unittest.TestCase):
    """창이 비활성이어도 미리 보기 명령이 동작하고, 텍스트 입력과 팝업, 닫힌 뒤에는 가로채지 않는지 검증"""

    def setUp(self) -> None:
        paths = _make_videos(self, 2)
        self.window = VideoPreviewWindow(
            None,
            lambda: DEFAULT_PREVIEW_SEEK_SECONDS,
            lambda: (DEFAULT_PREVIEW_VOLUME, DEFAULT_PREVIEW_MUTED),
            lambda _v, _m: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _t: False,
        )
        self.addCleanup(self.window.close)
        self.window.show_path(paths[0])
        self.other = QLabel()
        self.addCleanup(self.other.close)
        self.steps: list[int] = []
        self.window.step_requested.connect(self.steps.append)
        controls = self.window.findChild(PlaybackControls)
        assert controls is not None
        self.controls = controls

    def test_commands_work_while_another_widget_has_focus(self) -> None:
        _press(self.other, Qt.Key.Key_M)
        self.assertTrue(self.controls.audio_state()[1])
        _press(self.other, Qt.Key.Key_Down)
        self.assertEqual(self.steps, [1])

    def test_text_input_keeps_its_keys(self) -> None:
        line = QLineEdit()
        self.addCleanup(line.close)
        with mock.patch.object(QApplication, "focusWidget", return_value=line):
            _press(self.other, Qt.Key.Key_M)
        self.assertFalse(self.controls.audio_state()[1])

    def test_open_popup_keeps_arrow_keys(self) -> None:
        popup = QWidget()
        self.addCleanup(popup.close)
        with mock.patch.object(QApplication, "activePopupWidget", return_value=popup):
            _press(self.other, Qt.Key.Key_Down)
        self.assertEqual(self.steps, [])

    def test_unmatched_key_is_forwarded_only_when_window_is_active(self) -> None:
        forwarded: list[str] = []

        def forward(text: str) -> bool:
            forwarded.append(text)
            return True

        self.window._forward_key = forward
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_4.value, Qt.KeyboardModifier.ControlModifier
        )
        with mock.patch.object(self.window, "isActiveWindow", return_value=True):
            QApplication.sendEvent(self.window, event)
        self.assertEqual(forwarded, ["Ctrl+4"])
        forwarded.clear()
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_4.value, Qt.KeyboardModifier.ControlModifier
        )
        with mock.patch.object(self.window, "isActiveWindow", return_value=False):
            QApplication.sendEvent(self.other, event)
        self.assertEqual(forwarded, [])

    def test_filter_is_removed_when_window_is_hidden(self) -> None:
        self.window.hide()
        _press(self.other, Qt.Key.Key_M)
        self.assertFalse(self.controls.audio_state()[1])


class PlaybackControlsTests(unittest.TestCase):
    """음소거 전환이 번갈아 신호를 내보내고, 재생 위치 막대 트랙 클릭이 클릭한 지점으로 바로 이동하는지 검증"""

    def test_track_click_jumps_to_clicked_position(self) -> None:
        controls = PlaybackControls(None)
        self.addCleanup(controls.close)
        controls.resize(640, 40)
        controls.show()
        controls.set_duration(600_000)
        seeks: list[int] = []
        controls.seek_requested.connect(seeks.append)
        track = controls.findChild(QSlider, "PreviewPositionSlider")
        assert track is not None
        pos = QPointF(track.width() / 2, track.height() / 2)
        QApplication.sendEvent(
            track,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        QApplication.sendEvent(
            track,
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertTrue(seeks)
        self.assertAlmostEqual(seeks[-1], 300_000, delta=20_000)

    def test_track_drag_follows_pointer_and_seeks_on_release(self) -> None:
        controls = PlaybackControls(None)
        self.addCleanup(controls.close)
        controls.resize(640, 40)
        controls.show()
        controls.set_duration(600_000)
        seeks: list[int] = []
        controls.seek_requested.connect(seeks.append)
        track = controls.findChild(QSlider, "PreviewPositionSlider")
        assert track is not None
        y = QPointF(track.height() / 2, track.height() / 2)
        start = QPointF(track.width() * 0.25, y.y())
        end = QPointF(track.width() * 0.75, y.y())
        QApplication.sendEvent(
            track,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                start,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        QApplication.sendEvent(
            track,
            QMouseEvent(
                QEvent.Type.MouseMove,
                end,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        QApplication.sendEvent(
            track,
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                end,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertGreaterEqual(len(seeks), 2)
        self.assertAlmostEqual(seeks[0], 150_000, delta=30_000)
        self.assertAlmostEqual(seeks[-1], 450_000, delta=30_000)
        self.assertFalse(track.isSliderDown())

    def test_mute_toggle_alternates_and_keeps_volume_value(self) -> None:
        controls = PlaybackControls(None)
        self.addCleanup(controls.close)
        received: list[bool] = []
        controls.mute_toggled.connect(received.append)
        controls.toggle_mute()
        controls.toggle_mute()
        self.assertEqual(received, [True, False])


if __name__ == "__main__":
    unittest.main()
