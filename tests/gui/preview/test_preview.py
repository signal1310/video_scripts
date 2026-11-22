"""미리 보기 검증: 단축키 기본값, 방식 설정 정규화, QuickLook 활성화, 내장 창 방향키와 닫기, 음소거 전환, 표와의 선택 동기화"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QModelIndex, QPoint, QPointF, Qt
from PyQt6.QtGui import QAction, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QSlider, QToolButton, QWidget

from src.core.config import ClassifierConfig
from src.core.settings.preview_info import (
    INFO_LAYOUT_SINGLE_LINE,
    INFO_MODE_DETAIL,
    INFO_MODE_OFF,
    INFO_MODE_SUMMARY,
)
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.state_codec import parse_state_mapping
from src.core.settings.ui_state import (
    DEFAULT_PREVIEW_BACKEND,
    DEFAULT_PREVIEW_MUTED,
    DEFAULT_PREVIEW_PLAYBACK_RATE,
    DEFAULT_PREVIEW_SEEK_SECONDS,
    DEFAULT_PREVIEW_VOLUME,
    PREVIEW_BACKEND_QUICKLOOK,
    VALID_PREVIEW_PLAYBACK_RATES,
    VALID_PREVIEW_SEEK_SECONDS,
    UiState,
    shift_playback_rate,
)
from src.gui.preview.preview_controller import PreviewController
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.preview_settings_panel import PreviewSettingsPopup
from src.gui.preview.quicklook_launcher import open_with_quicklook, warm_up_quicklook
from src.gui.preview.video_preview_controls import PlaybackControls, format_playback_time
from src.gui.preview.video_preview_window import (
    VideoPreviewWindow,
    _blocks_preview_keys,
    clamp_seek_position,
)
from src.gui.table.video_table_view import VideoTableView
from src.gui.widgets.floating_popup import FloatingPopupFrame
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase, release_key

_APP = QApplication.instance() or QApplication([])
_POLL_INTERVAL_SECONDS: float = 0.01
_END_TIMEOUT_SECONDS: float = 10.0


def _make_window(
    shortcuts: Mapping[str, str] | None = None,
    ui: UiState | None = None,
    on_info_changed: Callable[[], None] | None = None,
) -> VideoPreviewWindow:
    """미리 보기 창을 UI 상태 객체 하나에 연결해 생성, 정보 표시 설정 변경은 상태에 바로 쓰임"""
    state = ui if ui is not None else UiState()
    return VideoPreviewWindow(
        lambda: state.preview_seek_seconds,
        lambda: (state.preview_volume, state.preview_muted),
        lambda _v, _m: None,
        lambda: shortcuts if shortcuts is not None else DEFAULT_SHORTCUTS,
        lambda _t: False,
        lambda: state.preview_info,
        on_info_changed or (lambda: None),
        lambda: state.preview_playback_rate,
        _set_rate_into(state),
        lambda: state.preview_fullscreen,
        _set_fullscreen_into(state),
    )


def _set_rate_into(state: UiState) -> Callable[[float], None]:
    def set_rate(rate: float) -> None:
        state.preview_playback_rate = rate

    return set_rate


def _set_fullscreen_into(state: UiState) -> Callable[[bool], None]:
    def set_fullscreen(fullscreen: bool) -> None:
        state.preview_fullscreen = fullscreen

    return set_fullscreen


def _press(
    widget: QWidget,
    key: Qt.Key,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """위젯에 키 누름 이벤트를 직접 보내 창 수준 단축키를 거치지 않고 위젯의 keyPressEvent만 검증"""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key.value, modifiers))


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
_QUICKLOOK_LAUNCHER = "src.gui.preview.quicklook_launcher"


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

    def notify_missing_selected(self) -> bool:
        return False

    def notify_current_missing(self) -> None:
        return None

    def set_current_row(self, row: int) -> None:
        self.current = row

    def video_path_at_row(self, row: int) -> str | None:
        return self.paths[row] if 0 <= row < len(self.paths) else None

    def current_video_path(self) -> str | None:
        return None if self.current is None else self.video_path_at_row(self.current)

    def move_current_row(self, step: int) -> None:
        if self.current is not None and self.video_path_at_row(self.current + step) is not None:
            self.current += step

    def currentIndex(self) -> QModelIndex:
        return QModelIndex()

    def get_evaluation_at(self, _index: QModelIndex) -> None:
        return None

    def selectionModel(self) -> None:
        return None


def _controller(
    parent: QWidget,
    table: _FakeTable,
    messages: list[str],
    ui: UiState | None = None,
    persist: Callable[[], None] | None = None,
) -> PreviewController:
    state = ui if ui is not None else UiState()
    return PreviewController(
        parent,
        cast("VideoTableView", table),
        lambda: state,
        ClassifierConfig,
        persist or (lambda: None),
        lambda _v, _m: None,
        lambda _rate: None,
        lambda: DEFAULT_SHORTCUTS,
        lambda _t: False,
        messages.append,
    )


class QuickLookWarmUpTests(GuiTestCase):
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
            with mock.patch("src.gui.preview.preview_controller.warm_up_quicklook") as warm:
                controller = _controller(
                    parent, _FakeTable([], None), [], UiState(preview_backend=backend)
                )
                controller.warm_up()
            self.assertEqual(warm.call_count, expected_calls, backend)


class PreviewControllerTests(GuiTestCase):
    """선택 없음 안내, 다중 선택 시 가장 위 영상만 열기, 표 포커스에 따른 창 영상 교체를 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.close)
        self.addCleanup(self._close_preview_windows)

    def _close_preview_windows(self) -> None:
        """열린 채 남은 미리 보기 창은 앱 전역 키 필터를 유지해 다음 테스트의 키 입력을 가로채므로 닫음"""
        for window in self.parent.findChildren(VideoPreviewWindow):
            window.close()

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
        self.window = _make_window()
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
        with self.assertLogs("src.gui.preview.video_preview_window", level="INFO") as logs:
            self.window.load_path(self.paths[1])
        self.assertEqual(
            logs.output,
            [f"INFO:src.gui.preview.video_preview_window:미리 보기 재생 시작: {os.path.basename(self.paths[1])}"],
        )

    def test_hide_releases_media_source_so_file_can_be_removed(self) -> None:
        player = self.window.findChild(QMediaPlayer)
        assert player is not None
        self.assertFalse(player.source().isEmpty())
        self.window.hide()
        self.assertTrue(player.source().isEmpty())
        self.assertEqual(self.window.current_path, "")

    @unittest.skipUnless(sys.platform == "win32", "공유 모드 핸들은 Windows 전용")
    def test_playing_file_can_be_renamed_while_shown(self) -> None:
        """내장 창이 재생 중인 영상을 다른 프로그램이 이름 바꾸기할 수 있는지 검증(삭제 허용 공유 모드)"""
        moved = os.path.join(os.path.dirname(self.paths[0]), "moved_" + os.path.basename(self.paths[0]))
        os.rename(self.paths[0], moved)
        self.assertTrue(os.path.exists(moved))

    def test_deleted_playing_file_keeps_window_and_reopen_reports_unplayable(self) -> None:
        """재생 중 영상이 삭제돼도 창은 유지되고, 창을 닫을 때 오류가 없으며, 같은 경로를 다시 열면 재생 불가로 일관되게 처리되는지 검증"""
        path = self.paths[0]
        os.remove(path)
        self.assertTrue(self.window.isVisible())
        self.window.hide()
        self.window.show_path(path)
        player = self.window.findChild(QMediaPlayer)
        assert player is not None
        deadline = time.monotonic() + 5.0
        while player.error() == QMediaPlayer.Error.NoError and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertNotEqual(player.error(), QMediaPlayer.Error.NoError)

    def test_space_and_escape_hide_window(self) -> None:
        _press(self.window, Qt.Key.Key_Space)
        self.assertFalse(self.window.isVisible())
        self.window.show_path(self.paths[0])
        _press(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.isVisible())


def _make_clip(test: unittest.TestCase, duration_seconds: int) -> str:
    """ffmpeg로 짧은 실제 영상을 임시 폴더에 만들어 경로 반환, 재생 종료와 프레임 출력 검증용"""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    path = os.path.join(tmp.name, "clip.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration_seconds}:size=160x120:rate=25",
            "-pix_fmt",
            "yuv420p",
            path,
        ],
        check=True,
    )
    return path


def _wait_until(predicate: Callable[[], bool], timeout_seconds: float) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 제한 시간 안에 참이면 True"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return predicate()


class PreviewEndOfMediaTests(unittest.TestCase):
    """끝까지 재생된 뒤에도 프레임이 유지되고, 이후 탐색 때 프레임이 다시 출력되는지 실제 영상으로 검증"""

    def test_end_holds_last_frame_seek_back_works_and_play_restarts(self) -> None:
        clip = _make_clip(self, duration_seconds=1)
        window = _make_window()
        self.addCleanup(window.close)
        player = window.findChild(QMediaPlayer)
        assert player is not None
        sink = player.videoSink()
        assert sink is not None
        # - 종료 처리 중 일시중지로 바뀌어 상태 조회로는 종료 시점을 놓칠 수 있어 신호로 기록
        ended: list[bool] = []
        player.mediaStatusChanged.connect(
            lambda status: ended.append(True) if status == QMediaPlayer.MediaStatus.EndOfMedia else None
        )
        window.show_path(clip)

        self.assertTrue(_wait_until(lambda: bool(ended), _END_TIMEOUT_SECONDS))
        self.assertTrue(_wait_until(lambda: sink.videoFrame().isValid(), _END_TIMEOUT_SECONDS))
        self.assertEqual(player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(player.position(), player.duration())

        half = player.duration() // 2
        player.setPosition(half)
        self.assertTrue(_wait_until(lambda: sink.videoFrame().isValid(), _END_TIMEOUT_SECONDS))
        self.assertEqual(player.position(), half)

        player.setPosition(player.duration())
        self.assertTrue(_wait_until(lambda: sink.videoFrame().isValid(), _END_TIMEOUT_SECONDS))
        _press(window, Qt.Key.Key_Left)
        self.assertTrue(_wait_until(lambda: player.position() < player.duration(), _END_TIMEOUT_SECONDS))
        self.assertEqual(player.playbackState(), QMediaPlayer.PlaybackState.PausedState)

        player.setPosition(player.duration())
        self.assertTrue(_wait_until(lambda: sink.videoFrame().isValid(), _END_TIMEOUT_SECONDS))
        _press(window, Qt.Key.Key_K)
        self.assertTrue(
            _wait_until(
                lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                _END_TIMEOUT_SECONDS,
            )
        )
        self.assertTrue(_wait_until(lambda: player.position() < half, _END_TIMEOUT_SECONDS))


def _overlay_card(window: VideoPreviewWindow) -> QWidget:
    """정보 표시 오버레이의 최상위 카드 창"""
    label = window.findChild(QLabel, "PreviewInfoOverlay")
    assert label is not None
    card = label.window()
    assert card is not None
    return card


def _overlay_text(window: VideoPreviewWindow, name: str = "PreviewInfoOverlay") -> str:
    label = window.findChild(QLabel, name)
    assert label is not None
    return label.text()


def _video_info(selected_count: int = 1) -> PreviewVideoInfo:
    return PreviewVideoInfo(make_eval(), ClassifierConfig(), {}, selected_count)


class PreviewInfoWindowTests(GuiTestCase):
    """I 키 단계 전환, 정보 표시와 안내의 표시 조건, 설정 버튼 신호를 검증"""

    def setUp(self) -> None:
        self.paths = _make_videos(self, 1)
        self.ui = UiState()
        self.saved: list[int] = []
        self.window = _make_window(ui=self.ui, on_info_changed=lambda: self.saved.append(1))
        self.addCleanup(self.window.close)
        self.window.show_path(self.paths[0])

    def test_i_key_cycles_off_summary_detail_and_requests_save_each_time(self) -> None:
        modes: list[str] = []
        for _ in range(3):
            _press(self.window, Qt.Key.Key_I)
            modes.append(self.ui.preview_info.mode)
        self.assertEqual(modes, [INFO_MODE_SUMMARY, INFO_MODE_DETAIL, INFO_MODE_OFF])
        self.assertEqual(len(self.saved), 3)

    def test_mode_change_is_announced_over_the_video(self) -> None:
        _press(self.window, Qt.Key.Key_I)
        self.assertIn("정보 표시 요약", _overlay_text(self.window, "PreviewToast"))

    def test_overlay_is_hidden_while_mode_is_off(self) -> None:
        self.window.set_video_info(_video_info())
        self.assertFalse(_overlay_card(self.window).isVisible())

    def test_overlay_shows_table_values_when_a_mode_is_on(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info())
        self.assertTrue(_overlay_card(self.window).isVisible())
        text = _overlay_text(self.window)
        self.assertIn("1920x1080", text)
        self.assertIn("h264", text)
        self.assertNotIn("중 재생 중", text)

    def test_overlay_uses_the_abbreviated_table_headers(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_DETAIL
        self.window.set_video_info(_video_info())
        text = _overlay_text(self.window)
        self.assertIn(">BR<", text)
        self.assertIn(">KF 간격<", text)
        self.assertNotIn("비트레이트", text)

    def test_wheel_over_the_overlay_changes_scale_and_requests_save(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info())
        card = _overlay_card(self.window)
        QApplication.sendEvent(
            card,
            QWheelEvent(
                QPointF(5, 5),
                QPointF(5, 5),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            ),
        )
        self.assertEqual(self.ui.preview_info.scale, 110)
        self.assertEqual(len(self.saved), 1)

    def test_multi_selection_is_marked_in_the_overlay(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info(selected_count=3))
        self.assertIn("선택 3개 중 재생 중", _overlay_text(self.window))

    def test_layout_choice_switches_between_single_and_multi_line_markup(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info())
        self.assertIn("<table", _overlay_text(self.window))
        self.ui.preview_info.layout = INFO_LAYOUT_SINGLE_LINE
        self.window.refresh_info()
        self.assertNotIn("<table", _overlay_text(self.window))

    def test_overlay_without_selected_items_stays_hidden(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.ui.preview_info.summary_items = []
        self.window.set_video_info(_video_info())
        self.assertFalse(_overlay_card(self.window).isVisible())

    def test_overlay_follows_the_info_values_of_the_new_video(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info())
        other = make_eval(make_video(codec="hevc"))
        self.window.set_video_info(PreviewVideoInfo(other, ClassifierConfig(), {}, 1))
        self.assertIn("hevc", _overlay_text(self.window))

    def test_hiding_the_window_hides_the_overlay_and_the_notice(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.window.set_video_info(_video_info())
        self.window.show_notice("안내")
        toast = self.window.findChild(QLabel, "PreviewToast")
        assert toast is not None
        toast_window = toast.window()
        assert toast_window is not None
        self.assertTrue(toast_window.isVisible())
        self.window.hide()
        self.assertFalse(_overlay_card(self.window).isVisible())
        self.assertFalse(toast_window.isVisible())

    def test_settings_button_requests_the_settings_popup(self) -> None:
        requested: list[int] = []
        self.window.settings_requested.connect(lambda: requested.append(1))
        controls = self.window.findChild(PlaybackControls)
        assert controls is not None
        controls.settings_button.click()
        self.assertEqual(requested, [1])
        self.assertEqual(controls.settings_button.accessibleName(), "미리 보기 설정")
        self.assertFalse(controls.settings_button.text())

    def test_preview_keys_yield_while_a_settings_popup_is_the_active_window(self) -> None:
        popup = FloatingPopupFrame(self.window)
        self.addCleanup(popup.close)
        popup.show()
        with mock.patch.object(QApplication, "activeWindow", return_value=popup):
            self.assertTrue(_blocks_preview_keys())


class PreviewControllerInfoTests(GuiTestCase):
    """실행 결과 안내, 설정 팝업, 설정 변경 저장을 컨트롤러 수준에서 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.close)
        self.paths = _make_videos(self, 1)
        self.ui = UiState()
        self.saved: list[int] = []
        self.controller = _controller(
            self.parent,
            _FakeTable(self.paths, selected_top=0),
            [],
            self.ui,
            lambda: self.saved.append(1),
        )

    def _open_window(self) -> VideoPreviewWindow:
        self.controller.preview_selected()
        window = self.controller.builtin_window
        assert window is not None
        self.addCleanup(window.close)
        return window

    def test_notice_is_shown_over_the_video_only_while_the_window_is_open(self) -> None:
        self.controller.notify("무시됨")
        window = self._open_window()
        self.controller.notify("선택 3개 영상 출력 해상도 지정: 720p")
        self.assertIn("선택 3개 영상 출력 해상도 지정: 720p", _overlay_text(window, "PreviewToast"))

    def test_settings_popup_opens_on_the_preview_window_when_it_is_open(self) -> None:
        window = self._open_window()
        self.controller.show_settings()
        popups = window.findChildren(PreviewSettingsPopup)
        self.assertEqual(len(popups), 1)
        self.assertTrue(popups[0].isVisible())
        popups[0].close()

    def test_settings_popup_opens_from_the_main_window_without_a_preview_window(self) -> None:
        self.controller.show_settings()
        popups = self.parent.findChildren(PreviewSettingsPopup)
        self.assertEqual(len(popups), 1)
        self.assertTrue(popups[0].isVisible())
        popups[0].close()

    def test_panel_changes_are_saved_and_kept_in_the_shared_state(self) -> None:
        self.controller.show_settings()
        popup = self.parent.findChildren(PreviewSettingsPopup)[0]
        self.addCleanup(popup.close)
        combos = popup.findChildren(QComboBox)
        combos[1].setCurrentIndex(combos[1].findData(30))
        self.assertEqual(self.ui.preview_seek_seconds, 30)
        self.assertEqual(self.saved, [1])

    def test_switching_to_quicklook_in_the_panel_warms_it_up_once(self) -> None:
        self.controller.show_settings()
        popup = self.parent.findChildren(PreviewSettingsPopup)[0]
        self.addCleanup(popup.close)
        combos = popup.findChildren(QComboBox)
        with mock.patch("src.gui.preview.preview_controller.warm_up_quicklook") as warm:
            combos[0].setCurrentIndex(combos[0].findData(PREVIEW_BACKEND_QUICKLOOK))
            combos[1].setCurrentIndex(combos[1].findData(30))
        self.assertEqual(self.ui.preview_backend, PREVIEW_BACKEND_QUICKLOOK)
        warm.assert_called_once()


class PlaybackRateAndLargeSeekTests(unittest.TestCase):
    """배속 메뉴와 저장, 플레이어 반영, Shift 좌우키의 큰 구간 이동을 검증"""

    def setUp(self) -> None:
        self.state = UiState()
        self.window = _make_window(ui=self.state)
        self.addCleanup(self.window.close)
        button = self.window.findChild(QToolButton, "PreviewRateButton")
        player = self.window.findChild(QMediaPlayer)
        assert button is not None and button.menu() is not None and player is not None
        self.button: QToolButton = button
        self.player: QMediaPlayer = player

    def _rate_actions(self) -> list[QAction]:
        menu = self.button.menu()
        assert menu is not None
        return menu.actions()

    def _choose(self, rate: float) -> None:
        label = f"{rate:g}배"
        action = next(action for action in self._rate_actions() if action.text() == label)
        action.trigger()

    def test_rates_follow_the_requested_steps_up_to_eight_times(self) -> None:
        expected = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
        expected += [3.0 + 0.5 * step for step in range(1, 11)]
        self.assertEqual(list(VALID_PREVIEW_PLAYBACK_RATES), expected)
        self.assertEqual(VALID_PREVIEW_PLAYBACK_RATES[-1], 8.0)
        labels = [action.text() for action in self._rate_actions()]
        self.assertEqual(labels[0], "0.25배")
        self.assertEqual(labels[3], "1배")
        self.assertEqual(labels[-1], "8배")

    def test_choosing_a_rate_applies_to_the_player_and_is_saved(self) -> None:
        self._choose(2.5)
        self.assertEqual(self.player.playbackRate(), 2.5)
        self.assertEqual(self.state.preview_playback_rate, 2.5)
        self.assertIn("2.5배", self.button.toolTip())

    def test_saved_rate_is_restored_when_the_window_is_created(self) -> None:
        self.state.preview_playback_rate = 1.75
        window = _make_window(ui=self.state)
        self.addCleanup(window.close)
        button = window.findChild(QToolButton, "PreviewRateButton")
        player = window.findChild(QMediaPlayer)
        assert button is not None and button.menu() is not None and player is not None
        menu = button.menu()
        assert menu is not None
        checked = [action.text() for action in menu.actions() if action.isChecked()]
        self.assertEqual(checked, ["1.75배"])
        self.assertEqual(player.playbackRate(), 1.75)

    def test_rate_survives_loading_another_video(self) -> None:
        paths = _make_videos(self, 2)
        self._choose(2.0)
        self.window.load_path(paths[1])
        self.assertEqual(self.player.playbackRate(), 2.0)

    def test_shift_arrows_seek_by_the_large_multiple_of_the_interval(self) -> None:
        with mock.patch.object(VideoPreviewWindow, "_seek_by") as seek:
            _press(self.window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
            _press(self.window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
            _press(self.window, Qt.Key.Key_Right)
        large_ms = self.state.preview_seek_seconds * 1000 * 6
        self.assertEqual(
            [call.args[0] for call in seek.call_args_list],
            [large_ms, -large_ms, self.state.preview_seek_seconds * 1000],
        )

    def test_rate_keys_step_through_the_list_and_reset_to_normal(self) -> None:
        self._choose(1.0)
        _press(self.window, Qt.Key.Key_BracketRight)
        self.assertEqual(self.player.playbackRate(), 1.25)
        _press(self.window, Qt.Key.Key_BracketLeft)
        _press(self.window, Qt.Key.Key_BracketLeft)
        self.assertEqual(self.player.playbackRate(), 0.75)
        self.assertEqual(self.state.preview_playback_rate, 0.75)
        _press(self.window, Qt.Key.Key_Backslash)
        self.assertEqual(self.player.playbackRate(), 1.0)
        self.assertEqual(self.state.preview_playback_rate, 1.0)

    def test_shift_playback_rate_stops_at_the_ends_of_the_list(self) -> None:
        self.assertEqual(shift_playback_rate(1.1, 1), 1.25)
        self.assertEqual(shift_playback_rate(8.0, 1), 8.0)
        self.assertEqual(shift_playback_rate(0.25, -1), 0.25)

    def test_invalid_saved_rate_falls_back_to_normal_speed(self) -> None:
        info = parse_state_mapping({"ui": {"preview_playback_rate": 1.7}}).ui
        self.assertEqual(info.preview_playback_rate, DEFAULT_PREVIEW_PLAYBACK_RATE)
        valid = parse_state_mapping({"ui": {"preview_playback_rate": 2.5}}).ui
        self.assertEqual(valid.preview_playback_rate, 2.5)


class PreviewShortcutTests(unittest.TestCase):
    """미리 보기 창 명령이 단축키 설정 값으로 바뀌고, 바뀐 뒤에는 기본 키가 동작하지 않는지 검증"""

    def _window(self, shortcuts: dict[str, str]) -> VideoPreviewWindow:
        window = _make_window({**DEFAULT_SHORTCUTS, **shortcuts})
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

    def test_f_toggles_fullscreen_and_remembers_the_state(self) -> None:
        state = UiState()
        window = _make_window(ui=state)
        self.addCleanup(window.close)
        window.show_path(_make_videos(self, 1)[0])
        _press(window, Qt.Key.Key_F)
        self.assertTrue(window.isFullScreen())
        self.assertTrue(state.preview_fullscreen)
        _press(window, Qt.Key.Key_F)
        self.assertFalse(window.isFullScreen())
        self.assertFalse(state.preview_fullscreen)
        self.assertTrue(window.isVisible())

    def test_escape_closes_the_preview_while_keeping_fullscreen(self) -> None:
        """전체 화면에서 Esc를 누르면 전체 화면을 풀지 않고 닫으며, 그 상태가 저장됨을 검증"""
        state = UiState()
        window = _make_window(ui=state)
        self.addCleanup(window.close)
        window.show_path(_make_videos(self, 1)[0])
        _press(window, Qt.Key.Key_F)
        self.assertTrue(window.isFullScreen())
        _press(window, Qt.Key.Key_Escape)
        self.assertFalse(window.isVisible())
        self.assertTrue(window.isFullScreen())
        self.assertTrue(state.preview_fullscreen)

    def test_saved_fullscreen_state_is_restored_when_the_preview_opens(self) -> None:
        """재시작 뒤 저장된 전체 화면 상태로 미리 보기가 열리는지 검증"""
        state = UiState()
        state.preview_fullscreen = True
        window = _make_window(ui=state)
        self.addCleanup(window.close)
        window.show_path(_make_videos(self, 1)[0])
        self.assertTrue(window.isFullScreen())

    def test_empty_binding_disables_the_command(self) -> None:
        window = self._window({"preview.next": ""})
        steps: list[int] = []
        window.step_requested.connect(steps.append)
        _press(window, Qt.Key.Key_Down)
        self.assertEqual(steps, [])


class PreviewKeyFilterTests(GuiTestCase):
    """창이 비활성이어도 미리 보기 명령이 동작하고, 텍스트 입력과 팝업, 닫힌 뒤에는 가로채지 않는지 검증"""

    def setUp(self) -> None:
        paths = _make_videos(self, 2)
        self.window = _make_window()
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
        release_key(self.other, Qt.Key.Key_Control)

    def test_filter_is_removed_when_window_is_hidden(self) -> None:
        self.window.hide()
        _press(self.other, Qt.Key.Key_M)
        self.assertFalse(self.controls.audio_state()[1])


class PlaybackControlsTests(GuiTestCase):
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
