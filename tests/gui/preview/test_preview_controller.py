"""미리 보기 컨트롤러의 설정 저장과 인코딩 확인 창 연결(고른 영상 고정, 창 부모 전환)을 검증

- 내장 미리 보기 창은 가짜로 바꿔 실제 미디어를 열지 않음
"""

import os
import tempfile
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from src.core.analysis.frame_compare import ComparePlan, FrameSource
from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.preview import preview_controller as module
from src.gui.preview.preview_controller import PreviewController
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase

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

    def test_compare_top_bar_collapse_is_saved_for_the_next_compare(self) -> None:
        """비교 상단 바 접힘 변경이 UI 상태에 저장되는지 검증"""
        self.controller._on_compare_top_bar_collapsed_changed(True)
        self.assertTrue(self.harness.ui.compare_top_bar_collapsed)
        self.assertEqual(self.harness.persisted, [True])

    def test_attach_compare_applies_the_extractor_to_an_open_window(self) -> None:
        """비교 연결이 이미 열린 창에도 프레임 추출기를 넘기는지 검증"""
        window = mock.Mock()
        self.controller._window = window
        builder = mock.Mock(return_value=mock.Mock())
        extractor = mock.Mock()
        self.controller.attach_compare(builder, extractor)
        window.set_compare_extractor.assert_called_once_with(extractor)

    def test_attach_crop_edit_wraps_the_callbacks_for_the_open_window(self) -> None:
        """활성 화면 직접 편집 연결이 지금 창에 단일 영상 콜백으로 넘기는지 검증"""
        window = mock.Mock()
        self.controller._window = window
        manual: list[tuple[list[Any], Any]] = []
        excluded: list[tuple[list[Any], bool]] = []
        self.controller.attach_crop_edit(
            lambda evals, frame: manual.append((evals, frame)),
            lambda evals, flag: excluded.append((evals, flag)),
        )
        on_manual, on_exclusion = window.set_crop_edit_targets.call_args.args
        ev = make_eval()
        on_manual(ev, None)
        on_exclusion(ev, True)
        self.assertEqual(manual, [([ev], None)])
        self.assertEqual(excluded, [([ev], True)])

    def test_attach_crop_edit_before_the_window_opens_keeps_the_callbacks(self) -> None:
        """창을 만들기 전에 연결해도 콜백이 남아 있는지 검증"""
        manual: list[tuple[list[Any], Any]] = []
        self.controller.attach_crop_edit(
            lambda evals, frame: manual.append((evals, frame)),
            lambda _evals, _flag: None,
        )
        self.assertIsNotNone(self.controller._apply_manual_active_frame)
        self.assertIsNotNone(self.controller._apply_crop_exclusion)

    def test_compare_manual_target_returns_false_without_a_callback(self) -> None:
        self.assertFalse(self.controller._compare_manual_target(make_eval(), "1080p"))

    def test_compare_manual_target_applies_the_value_and_returns_the_result(self) -> None:
        """비교 창의 1클릭 지정이 표의 직접 지정으로 넘어가고 결과를 그대로 돌려주는지 검증"""
        calls: list[tuple[object, str | None]] = []
        self.controller.attach_compare(
            mock.Mock(),
            mock.Mock(),
            apply_manual_target=lambda ev, value: calls.append((ev, value)) or True,
        )
        ev = make_eval()
        self.assertTrue(self.controller._compare_manual_target(ev, "1080p"))
        self.assertEqual(calls, [(ev, "1080p")])

    def test_sync_compare_report_forwards_to_the_open_window(self) -> None:
        """표 판정 결과 변경이 열린 미리 보기 창의 비교 뷰 동기화로 넘어가는지 검증"""
        window = mock.Mock()
        self.controller._window = window
        report = mock.Mock()
        self.controller.sync_compare_report(report)
        window.sync_compare_report.assert_called_once_with(report)
        self.controller._window = None
        self.controller.sync_compare_report(report)

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
        open_compare.assert_called_once_with("C:/a.mp4", 7.5, auto_play=True, owner=None)

    def test_toggle_compare_without_a_window_opens_the_selected_video(self) -> None:
        """창이 없으면 표의 가장 위 선택 영상으로 비교 모드를 여는지 검증"""
        self.harness.table.topmost_selected_video_row.return_value = 0
        self.harness.table.video_path_at_row.return_value = "C:/pick.mp4"
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller.toggle_compare()
        open_compare.assert_called_once_with("C:/pick.mp4")

    def test_sync_shortcuts_forwards_to_the_builtin_window(self) -> None:
        """단축키 변경이 열린 내장 미리 보기 창으로 전달되는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.controller._preview_builtin(path)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(window.close)
            effective = dict(DEFAULT_SHORTCUTS)
            effective["preview.toggle_playback"] = "X"
            self.controller.sync_shortcuts(effective)
            self.assertEqual(window._controls._btn_toggle.toolTip(), "재생 [X]")

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
        open_compare.assert_called_once_with("C:/a.mp4", 3.0, auto_play=True, owner=None)

    def test_preview_path_uses_the_requesting_window_as_owner_when_pinned(self) -> None:
        """진행 창에서 요청한 미리 보기는 그 창을 소유자로 두고, 표에서 다시 열면 메인 창으로 되돌리는지 검증"""
        progress = QWidget()
        self.addCleanup(progress.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = False
            self.controller.preview_path("C:/a.mp4", owner=progress)
            window.set_owner.assert_called_with(progress)
            window.set_owner.reset_mock()
            self.controller.preview_path("C:/a.mp4")
            window.set_owner.assert_called_with(self.parent)
            window.set_owner.reset_mock()
            self.harness.table.topmost_selected_video_row.return_value = 0
            self.harness.table.video_path_at_row.return_value = "C:/b.mp4"
            self.controller.preview_selected()
            window.set_owner.assert_called_with(self.parent)

    def test_preview_path_without_the_pin_has_no_owner_even_from_the_progress_window(self) -> None:
        """핀을 끈 미리 보기는 진행 창에서 요청해도 소유자를 두지 않는지 검증"""
        self.harness.ui.preview_always_on_top = False
        progress = QWidget()
        self.addCleanup(progress.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.preview_path("C:/a.mp4", owner=progress)
        window_class.return_value.set_owner.assert_called_with(None)

    def test_toggle_compare_from_the_window_keeps_the_requesting_owner(self) -> None:
        """진행 창에서 연 미리 보기의 비교 전환이 그 창을 소유자로 유지하는지 검증"""
        progress = QWidget()
        self.addCleanup(progress.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = True
            window.compare_mode = False
            window.current_path = "C:/a.mp4"
            window.position_sec = 7.5
            window.is_playing = True
            self.controller.preview_path("C:/a.mp4", owner=progress)
            with mock.patch.object(self.controller, "open_compare") as open_compare:
                self.controller.toggle_compare()
        open_compare.assert_called_once_with(
            "C:/a.mp4", 7.5, auto_play=True, owner=progress
        )

    def test_compare_request_from_the_window_keeps_the_requesting_owner(self) -> None:
        """진행 창에서 연 미리 보기의 비교 버튼 요청도 그 창을 소유자로 유지하는지 검증"""
        progress = QWidget()
        self.addCleanup(progress.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = False
            window.is_playing = False
            self.controller.preview_path("C:/a.mp4", owner=progress)
            with mock.patch.object(self.controller, "open_compare") as open_compare:
                self.controller._on_compare_requested("C:/a.mp4", 3.0)
        open_compare.assert_called_once_with("C:/a.mp4", 3.0, auto_play=False, owner=progress)

    def test_open_compare_keeps_the_requested_owner(self) -> None:
        """비교 모드를 직접 열 때도 넘긴 창을 소유자로 쓰는지 검증"""
        progress = QWidget()
        self.addCleanup(progress.deleteLater)
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.attach_compare(mock.Mock(return_value=mock.Mock()), mock.Mock())
            self.controller.open_compare("C:/a.mp4", 1.0, owner=progress)
        window_class.return_value.set_owner.assert_called_with(progress)

    def test_compare_request_without_a_path_does_nothing(self) -> None:
        """경로 없는 비교 요청은 창을 열지 않는지 검증"""
        with mock.patch.object(self.controller, "open_compare") as open_compare:
            self.controller._on_compare_requested("", 0.0)
        open_compare.assert_not_called()

    def test_refresh_video_info_uses_the_file_the_window_plays(self) -> None:
        """표 포커스 행이 아니라 창이 재생하는 파일의 평가 결과를 넘기는지 검증"""
        played = "C:/videos/b.mp4"
        focused = make_eval(video=make_video(filename="a.mp4"))
        playing = make_eval(video=make_video(filename="b.mp4"))
        window = mock.Mock()
        window.isVisible.return_value = True
        window.current_path = played
        self.controller._window = window
        self.harness.table.get_evaluation_at.return_value = focused
        self.harness.table.evaluation_at_path.return_value = playing
        self.controller.refresh_video_info()
        self.harness.table.evaluation_at_path.assert_called_once_with(played)
        self.assertIs(window.set_video_info.call_args.args[0].ev, playing)

    def test_refresh_video_info_disables_the_overlay_for_a_file_outside_the_table(self) -> None:
        """표에 없는 파일을 재생하면 정보 표시를 끄는지 검증"""
        window = mock.Mock()
        window.isVisible.return_value = True
        window.current_path = "C:/other/clip.mp4"
        self.controller._window = window
        self.harness.table.evaluation_at_path.return_value = None
        self.controller.refresh_video_info()
        window.set_video_info.assert_called_once_with(None)

    def test_table_focus_change_in_compare_mode_opens_at_the_remembered_position(self) -> None:
        """비교 모드에서 표 포커스가 바뀌면 구간 이동 간격이 아니라 기억한 시점으로 여는지 검증"""
        paths = ["C:/a.mp4", "C:/b.mp4"]
        window = mock.Mock()
        window.isVisible.return_value = True
        window.compare_mode = True
        window.current_path = paths[0]
        self.controller._window = window
        self.harness.table.current_video_path.return_value = paths[1]
        self.harness.ui.preview_seek_seconds = 15
        self.controller._positions.remember(paths[1], 21.0)
        builder = mock.Mock(return_value=mock.Mock())
        self.controller.attach_compare(builder, mock.Mock())
        self.controller.on_table_current_changed()
        builder.assert_called_once_with(paths[1], 21.0)

    def test_table_focus_change_in_compare_mode_without_a_memory_starts_at_zero(self) -> None:
        """기억한 시점이 없으면 구간 이동 간격이 아니라 처음부터 여는지 검증"""
        paths = ["C:/a.mp4", "C:/b.mp4"]
        window = mock.Mock()
        window.isVisible.return_value = True
        window.compare_mode = True
        window.current_path = paths[0]
        self.controller._window = window
        self.harness.table.current_video_path.return_value = paths[1]
        self.harness.ui.preview_seek_seconds = 15
        builder = mock.Mock(return_value=mock.Mock())
        self.controller.attach_compare(builder, mock.Mock())
        self.controller.on_table_current_changed()
        builder.assert_called_once_with(paths[1], 0.0)

    def test_builtin_preview_switches_to_compare_mode(self) -> None:
        """내장 미리 보기에서도 비교 모드로 바꾸는지 검증"""
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
            self.controller._preview_builtin(path)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(window.close)
            self.controller.open_compare(path, 1.5)
            _APP.processEvents()
            self.assertTrue(window.compare_mode)

    def test_builtin_window_uses_the_main_window_as_owner_when_pinned(self) -> None:
        """핀 기본값에서 내장 창을 만들면 메인 창을 소유자로 두고, 위치는 인자로 넘기지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller._preview_builtin("C:/a.mp4")
        self.assertNotIn(self.parent, window_class.call_args.args)
        self.assertEqual(len(window_class.call_args.args), 14)
        window_class.return_value.show_path.assert_called_once_with("C:/a.mp4")
        window_class.return_value.set_owner.assert_called_with(self.parent)

    def test_close_windows_closes_the_builtin_window_and_popups(self) -> None:
        """종료 정리에서 내장 창과 설정 팝업을 모두 닫는지 검증"""
        window = mock.Mock()
        popup = mock.Mock()
        self.controller._window = window
        self.controller._window_popup = popup
        self.controller._main_popup = mock.Mock()
        self.controller.close_windows()
        window.close.assert_called_once()
        popup.close.assert_called_once()
        self.controller._main_popup.close.assert_called_once()


    def test_open_compare_when_closed_installs_the_key_filter_and_syncs_the_window(self) -> None:
        """닫힌 상태에서 비교 모드를 직접 열면 경로, 제목, 오디오, 배속, 키 필터가 맞는지 검증"""
        self.harness.ui.preview_volume = 25
        self.harness.ui.preview_muted = True
        self.harness.ui.preview_playback_rate = 1.25
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.controller.attach_compare(lambda target, time: _session(target, time), mock.Mock())
            self.controller.open_compare(path)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            _APP.processEvents()
            self.assertTrue(window.isVisible())
            self.assertEqual(window.current_path, path)
            self.assertEqual(window.windowTitle(), "화질 비교 - clip.mp4")
            self.assertEqual(window._controls.audio_state(), (25, True))
            self.assertEqual(window._controls._playback_rate, 1.25)
            self.assertTrue(window._key_filter_installed)

    def test_open_compare_recalls_the_session_position(self) -> None:
        """시점을 넘기지 않은 비교 진입은 이번 실행 기억 시점으로 세션을 만드는지 검증"""
        self.controller._positions.remember("C:/a.mp4", 42.0)
        builder = mock.Mock(return_value=mock.Mock())
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            self.controller.attach_compare(builder, mock.Mock())
            self.controller.open_compare("C:/a.mp4")
        window_class.assert_called_once()
        builder.assert_called_once_with("C:/a.mp4", 42.0)

    def test_closed_toggle_compare_opens_at_the_remembered_position(self) -> None:
        """닫힌 상태의 비교 전환도 기억한 시점으로 여는지 검증"""
        self.harness.table.topmost_selected_video_row.return_value = 0
        self.harness.table.video_path_at_row.return_value = "C:/pick.mp4"
        self.controller._positions.remember("C:/pick.mp4", 33.0)
        builder = mock.Mock(return_value=mock.Mock())
        with mock.patch.object(module, "VideoPreviewWindow"):
            self.controller.attach_compare(builder, mock.Mock())
            self.controller.toggle_compare()
        builder.assert_called_once_with("C:/pick.mp4", 33.0)

    def test_preview_selected_restores_the_remembered_position(self) -> None:
        """표에서 Space로 열 때도 기억한 시점을 넘기는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.harness.table.topmost_selected_video_row.return_value = 0
            self.harness.table.video_path_at_row.return_value = path
            self.controller._positions.remember(path, 42.0)
            self.controller.preview_selected()
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            self.assertEqual(window._playback.pending_position_ms, 42000)

    def test_table_change_in_normal_mode_restores_the_remembered_position(self) -> None:
        """일반 모드에서 표 행을 바꾸면 새 영상의 기억 시점부터 여는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "clip0.mp4")
            second = os.path.join(root, "clip1.mp4")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"not a real video")
            self.harness.table.topmost_selected_video_row.return_value = 0
            self.harness.table.video_path_at_row.return_value = first
            self.controller.preview_selected()
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            self.controller._positions.remember(second, 9.0)
            self.harness.table.current_video_path.return_value = second
            self.controller.on_table_current_changed()
            self.assertEqual(window.current_path, second)
            self.assertEqual(window._playback.pending_position_ms, 9000)

    def test_hiding_compare_then_previewing_restores_the_compare_position(self) -> None:
        """비교 모드에서 닫은 뒤 일반 미리 보기로 다시 열면 비교 시점부터 여는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"not a real video")
            self.harness.table.topmost_selected_video_row.return_value = 0
            self.harness.table.video_path_at_row.return_value = path
            self.controller.attach_compare(
                lambda target, time: _session(target, 12.5), mock.Mock()
            )
            self.controller.toggle_compare()
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            _APP.processEvents()
            self.assertTrue(window.compare_mode)
            window.hide()
            self.controller.preview_selected()
            self.assertEqual(window._playback.pending_position_ms, 12500)


class PreviewOwnerPolicyTests(GuiTestCase):
    """핀 상태별 소유자 창, 메인 창 최소화 연동, 앱 내부 창 위 유지"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.parent.resize(640, 480)
        self.parent.show()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")
        self.harness = _Harness(self.parent)
        self.harness.table.get_evaluation_at.return_value = None
        self.harness.table.topmost_selected_video_row.return_value = 0
        self.harness.table.video_path_at_row.return_value = self.path
        self.controller: PreviewController = self.harness.controller
        _APP.processEvents()

    def _open(self) -> Any:
        self.controller.preview_selected()
        window = self.controller.builtin_window
        assert window is not None
        self.addCleanup(window.close)
        return window

    def _pin_button(self, window: Any) -> QToolButton:
        button = window.findChild(QToolButton, "PreviewPinButton")
        assert button is not None
        return button

    def test_pinned_window_is_owned_by_the_main_window(self) -> None:
        """핀 기본값에서 내장 창이 메인 창의 소유 창이 되어 함께 최소화될 수 있는지 검증"""
        window = self._open()
        self.assertIs(window.parentWidget(), self.parent)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_unpinned_window_has_no_owner(self) -> None:
        """핀을 끈 상태로 열면 독립 창이 되어 메인 창 뒤로 갈 수 있는지 검증"""
        self.harness.ui.preview_always_on_top = False
        window = self._open()
        self.assertIsNone(window.parentWidget())
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_pin_toggle_moves_the_owner_and_keeps_the_session(self) -> None:
        """핀 전환으로 소유자를 바꿔도 보던 영상과 창 위치가 그대로인지 검증"""
        window = self._open()
        window.move(30, 40)
        window.resize(640, 360)
        _APP.processEvents()
        geometry = window.geometry()
        button = self._pin_button(window)
        button.click()
        _APP.processEvents()
        self.assertIsNone(window.parentWidget())
        self.assertFalse(self.harness.ui.preview_always_on_top)
        self.assertEqual(window.current_path, self.path)
        self.assertEqual(window.geometry(), geometry)
        button.click()
        _APP.processEvents()
        self.assertIs(window.parentWidget(), self.parent)
        self.assertTrue(self.harness.ui.preview_always_on_top)
        self.assertEqual(window.current_path, self.path)
        self.assertEqual(window.geometry(), geometry)

    def test_unpinned_window_restores_fullscreen_with_the_main_window(self) -> None:
        """핀을 끈 전체화면 창도 메인 창 복원 때 전체화면으로 되살아나는지 검증"""
        self.harness.ui.preview_always_on_top = False
        self.harness.ui.preview_fullscreen = True
        window = self._open()
        self.assertTrue(window.isFullScreen())
        with (
            mock.patch.object(window, "showMinimized"),
            mock.patch.object(self.parent, "isMinimized", return_value=True),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        with (
            mock.patch.object(window, "isMinimized", return_value=True),
            mock.patch.object(window, "showFullScreen") as show_fullscreen,
            mock.patch.object(self.parent, "isMinimized", return_value=False),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        show_fullscreen.assert_called_once()
        self.assertFalse(self.controller._minimized_with_parent)

    def test_unpinned_window_minimizes_and_restores_with_the_main_window(self) -> None:
        """핀을 끈 창도 메인 창을 최소화하면 함께 최소화되고 복원되면 되살아나는지 검증"""
        self.harness.ui.preview_always_on_top = False
        window = self._open()
        with (
            mock.patch.object(window, "showMinimized") as minimize,
            mock.patch.object(self.parent, "isMinimized", return_value=True),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        minimize.assert_called_once()
        self.assertTrue(self.controller._minimized_with_parent)
        with (
            mock.patch.object(window, "isMinimized", return_value=True),
            mock.patch.object(window, "showNormal") as show_normal,
            mock.patch.object(self.parent, "isMinimized", return_value=False),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        show_normal.assert_called_once()
        self.assertFalse(self.controller._minimized_with_parent)
        self.assertEqual(window.current_path, self.path)

    def test_pinned_window_leaves_minimizing_to_the_owner(self) -> None:
        """핀을 켠 소유 창은 운영 체제가 함께 최소화하므로 직접 최소화하지 않는지 검증"""
        window = self._open()
        with (
            mock.patch.object(window, "showMinimized") as minimize,
            mock.patch.object(self.parent, "isMinimized", return_value=True),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        minimize.assert_not_called()
        self.assertFalse(self.controller._minimized_with_parent)

    def test_closed_preview_stays_hidden_when_the_main_window_restores(self) -> None:
        """핀을 끈 창을 메인 창과 함께 최소화한 뒤 닫으면, 메인 창 복원 때 다시 열지 않는지 검증"""
        self.harness.ui.preview_always_on_top = False
        window = self._open()
        with mock.patch.object(self.parent, "isMinimized", return_value=True):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        self.assertTrue(window.isMinimized())
        self.assertTrue(self.controller._minimized_with_parent)
        window.hide()
        _APP.processEvents()
        self.assertFalse(window.isVisible())
        self.assertTrue(window.isMinimized())
        with (
            mock.patch.object(window, "showNormal") as show_normal,
            mock.patch.object(window, "showFullScreen") as show_fullscreen,
            mock.patch.object(self.parent, "isMinimized", return_value=False),
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowStateChange))
        show_normal.assert_not_called()
        show_fullscreen.assert_not_called()
        self.assertFalse(window.isVisible())
        self.assertFalse(self.controller._minimized_with_parent)

    def test_sibling_window_activation_raises_without_taking_focus(self) -> None:
        """앱 내부 다른 창이 활성화되면 포커스는 뺏지 않고 미리 보기만 위로 올리는지 검증"""
        window = self._open()
        with (
            mock.patch.object(window, "raise_") as raise_window,
            mock.patch.object(window, "activateWindow") as activate_window,
        ):
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowActivate))
        raise_window.assert_called_once()
        activate_window.assert_not_called()

    def test_table_focus_change_in_normal_mode_keeps_the_progress_window_owner(self) -> None:
        """일반 모드에서 표 행을 바꿔도 진행 창에서 연 미리 보기의 소유자가 그대로인지 검증"""
        progress = QWidget(None, Qt.WindowType.Window)
        self.addCleanup(self._release_widget, progress)
        progress.show()
        _APP.processEvents()
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "clip0.mp4")
            second = os.path.join(root, "clip1.mp4")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"not a real video")
            self.controller.preview_path(first, owner=progress)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            self.assertIs(window.parentWidget(), progress)
            self.harness.table.current_video_path.return_value = second
            self.controller.on_table_current_changed()
        self.assertEqual(window.current_path, second)
        self.assertIs(window.parentWidget(), progress)

    def test_table_focus_change_in_compare_mode_keeps_the_progress_window_owner(self) -> None:
        """비교 모드에서 표 행을 바꿔도 진행 창에서 연 비교 뷰의 소유자가 그대로인지 검증"""
        progress = QWidget(None, Qt.WindowType.Window)
        self.addCleanup(self._release_widget, progress)
        progress.show()
        _APP.processEvents()
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "clip0.mp4")
            second = os.path.join(root, "clip1.mp4")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"not a real video")
            self.controller.attach_compare(
                lambda target, time: _session(target, time), mock.Mock()
            )
            self.controller.preview_path(first, owner=progress)
            self.controller.open_compare(first, owner=progress)
            window = self.controller.builtin_window
            assert window is not None
            self.addCleanup(_release_window, window)
            _APP.processEvents()
            self.assertIs(window.parentWidget(), progress)
            self.harness.table.current_video_path.return_value = second
            self.controller.on_table_current_changed()
        self.assertEqual(window.current_path, second)
        self.assertIs(window.parentWidget(), progress)

    @staticmethod
    def _release_widget(widget: QWidget) -> None:
        """아직 지워지지 않은 창만 정리"""
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()

    def test_unpinned_and_external_deactivation_do_not_raise(self) -> None:
        """핀을 껐거나 외부 앱이 활성화될 때는 미리 보기 창을 올리지 않는지 검증"""
        window = self._open()
        self.harness.ui.preview_always_on_top = False
        with mock.patch.object(window, "raise_") as raise_window:
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.WindowActivate))
            self.controller.eventFilter(self.parent, QEvent(QEvent.Type.ApplicationDeactivate))
        raise_window.assert_not_called()

    def _progress_window(self) -> QWidget:
        """진행 창 자리에 쓸 독립 창 하나를 띄움"""
        progress = QWidget(None, Qt.WindowType.Window)
        self.addCleanup(self._release_progress, progress)
        progress.show()
        _APP.processEvents()
        return progress

    @staticmethod
    def _release_progress(progress: QWidget) -> None:
        """아직 지워지지 않은 진행 창만 정리"""
        if not sip.isdeleted(progress):
            progress.close()
            progress.deleteLater()

    def _open_with_owner(self, owner: QWidget) -> Any:
        """진행 창에서 요청한 것으로 미리 보기를 열고 그 창을 돌려줌"""
        self.controller.preview_path(self.path, owner=owner)
        window = self.controller.builtin_window
        assert window is not None
        self.addCleanup(window.close)
        _APP.processEvents()
        return window

    def test_hiding_the_progress_window_moves_the_preview_to_the_main_window(self) -> None:
        """진행 창을 숨기면 미리 보기가 메인 창 소유로 돌아가 숨은 창 아래에 매인 채 남지 않는지 검증"""
        progress = self._progress_window()
        window = self._open_with_owner(progress)
        self.assertIs(window.parentWidget(), progress)
        progress.hide()
        _APP.processEvents()
        self.assertIsNone(self.controller._owner_window)
        self.assertIs(window.parentWidget(), self.parent)
        self.assertTrue(window.isVisible())

    def test_deleting_the_progress_window_keeps_the_preview_open(self) -> None:
        """진행 창을 지워도 미리 보기 창이 함께 지워지지 않고 화면에 남는지 검증"""
        progress = self._progress_window()
        window = self._open_with_owner(progress)
        self.assertIs(window.parentWidget(), progress)
        progress.close()
        progress.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()
        self.assertTrue(sip.isdeleted(progress))
        self.assertFalse(sip.isdeleted(window))
        self.assertTrue(window.isVisible())
        self.assertIs(window.parentWidget(), self.parent)
        self.assertIsNone(self.controller._live_owner())

    def test_close_windows_does_not_raise_the_progress_window(self) -> None:
        """앱 종료 정리가 미리 보기를 닫을 때 진행 창을 다시 앞으로 올리지 않는지 검증"""
        progress = self._progress_window()
        window = self._open_with_owner(progress)
        with mock.patch.object(progress, "raise_") as raise_owner:
            self.controller.close_windows()
            _APP.processEvents()
        raise_owner.assert_not_called()
        self.assertFalse(window.isVisible())

    def test_closing_the_preview_from_the_table_leaves_a_hidden_progress_window(self) -> None:
        """메인 창이 활성일 때 표에서 미리 보기를 닫으면 진행 창이 메인 창 위로 떠오르지 않는지 검증"""
        progress = self._progress_window()
        window = self._open_with_owner(progress)
        with (
            mock.patch.object(self.parent, "isActiveWindow", return_value=True),
            mock.patch.object(progress, "raise_") as raise_owner,
        ):
            self.controller.preview_selected()
            _APP.processEvents()
        raise_owner.assert_not_called()
        self.assertFalse(window.isVisible())

    def test_closing_the_preview_from_the_table_raises_an_inactive_main_window_owner(self) -> None:
        """메인 창이 활성이 아니면 표에서 닫을 때 소유 창을 다시 앞으로 올리는지 검증"""
        progress = self._progress_window()
        window = self._open_with_owner(progress)
        with (
            mock.patch.object(self.parent, "isActiveWindow", return_value=False),
            mock.patch.object(progress, "raise_") as raise_owner,
        ):
            self.controller.preview_selected()
            _APP.processEvents()
        raise_owner.assert_called_once()
        self.assertFalse(window.isVisible())

    def test_preview_request_ignores_an_already_deleted_owner(self) -> None:
        """이미 지워진 창에서 온 미리 보기 요청은 소유자로 삼지 않고 메인 창 소유로 여는지 검증"""
        progress = self._progress_window()
        progress.close()
        progress.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(progress))
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = False
            self.controller.preview_path("C:/a.mp4", owner=progress)
        self.assertIsNone(self.controller._owner_window)
        window.set_owner.assert_called_with(self.parent)

    def test_live_owner_forgets_a_window_deleted_while_it_was_remembered(self) -> None:
        """소유자로 기억한 창이 뒤에서 지워지면 다음 조회에서 연결을 끊고 없음으로 보는지 검증"""
        progress = self._progress_window()
        self.controller._set_owner_window(progress)
        progress.close()
        progress.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(progress))
        self.assertIsNone(self.controller._live_owner())
        self.assertIsNone(self.controller._owner_window)

    def test_owner_window_clearing_without_an_owner_does_nothing(self) -> None:
        """소유자 없이 연결을 지우거나 숨김 사건을 받아도 창 정책을 건드리지 않는지 검증"""
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = False
            self.controller.preview_path("C:/a.mp4")
            window.set_owner.reset_mock()
            self.controller._drop_owner_window()
            self.controller._clear_owner_window()
        self.assertIsNone(self.controller._owner_window)
        window.set_owner.assert_not_called()

    def test_owner_window_hide_moves_the_preview_back_to_the_main_window(self) -> None:
        """소유 창의 숨김 사건을 사건 거르개로 받아 메인 창 소유로 되돌리는지 검증"""
        progress = self._progress_window()
        with mock.patch.object(module, "VideoPreviewWindow") as window_class:
            window = window_class.return_value
            window.isVisible.return_value = False
            self.controller.preview_path("C:/a.mp4", owner=progress)
            window.set_owner.reset_mock()
            self.controller.eventFilter(progress, QEvent(QEvent.Type.Hide))
        self.assertIsNone(self.controller._owner_window)
        window.set_owner.assert_called_once_with(self.parent)


def _session(path: str, position_sec: float) -> CompareSession:
    """비교 모드 테스트가 쓰는 세션"""
    plan = ComparePlan(
        original=FrameSource(key="원본", path=path, label="원본"),
        targets=(),
        default_key=None,
        reason="비교할 대상이 없습니다.",
    )
    return CompareSession(
        plan=plan,
        duration_sec=100.0,
        fps=30.0,
        filename=os.path.basename(path),
        position_sec=position_sec,
        source_path=path,
    )


def _release_window(window: QWidget) -> None:
    """앱 키 필터를 해제하고 미리 보기 창을 닫음"""
    window.hide()
    window.close()


class PreviewPositionMemoryTests(unittest.TestCase):
    """세션 재생 위치 저장소의 LRU 상한과 경로 키 정규화"""

    def test_capacity_drops_the_least_recently_used_video(self) -> None:
        memory = module.PreviewPositionMemory()
        for index in range(module.PREVIEW_POSITION_LIMIT + 1):
            memory.remember(f"C:/videos/{index}.mp4", float(index))
        self.assertIsNone(memory.recall("C:/videos/0.mp4"))
        self.assertEqual(memory.recall(f"C:/videos/{module.PREVIEW_POSITION_LIMIT}.mp4"), 500.0)

    def test_recall_moves_the_video_to_the_recent_side(self) -> None:
        memory = module.PreviewPositionMemory(limit=2)
        memory.remember("C:/videos/a.mp4", 1.0)
        memory.remember("C:/videos/b.mp4", 2.0)
        self.assertEqual(memory.recall("C:/videos/a.mp4"), 1.0)
        memory.remember("C:/videos/c.mp4", 3.0)
        self.assertIsNone(memory.recall("C:/videos/b.mp4"))
        self.assertEqual(memory.recall("C:/videos/a.mp4"), 1.0)

    def test_path_key_ignores_case_and_separators(self) -> None:
        memory = module.PreviewPositionMemory()
        memory.remember("C:/Videos/Clip.mp4", 4.0)
        self.assertEqual(memory.recall("c:\\videos\\clip.mp4"), 4.0)

    def test_forget_removes_only_the_requested_path(self) -> None:
        memory = module.PreviewPositionMemory()
        memory.remember("C:/videos/a.mp4", 1.0)
        memory.remember("C:/videos/b.mp4", 2.0)
        memory.forget("C:/videos/a.mp4")
        self.assertIsNone(memory.recall("C:/videos/a.mp4"))
        self.assertEqual(memory.recall("C:/videos/b.mp4"), 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
