"""화질 비교 창 검증: 세션 적재, 상단 접기, 사용자 지정 단축키, 재생, 전체화면, 오류, 정리

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
- 재생은 가짜 재생 제어기로 바꿔 시점과 상태 전이만 확인
"""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPoint, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel, QSlider, QToolButton, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    NO_TARGET_REASON,
    ComparePlan,
    FrameSource,
    resolve_compare_plan,
    result_target,
)
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_player import ComparePlaybackControls
from src.gui.dialogs.frame_compare_window import FrameCompareWindow
from src.gui.dialogs.frame_compare_worker import ExtractOutcome
from src.gui.preview.video_preview_controls import PlaybackControls
from src.gui.styles import stylesheet as app_stylesheet
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_decoder import DecodedFrame
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
)
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


def _bmp(color: str) -> bytes:
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "BMP")
    return bytes(buffer.data())


_ORIGINAL_BYTES: bytes = _bmp("#FF0000")
_COMPARED_BYTES: bytes = _bmp("#0000FF")


class _Extractor:
    """호출을 기록하고 정해진 프레임 바이트나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int, int]] = []
        self.failure: Exception | None = None
        self.released: int = 0

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        self.calls.append((original.path, compared.path, time_sec, frame_width, frame_height))
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_bytes=_ORIGINAL_BYTES, compared_bytes=_COMPARED_BYTES)

    def release_decoders(self) -> None:
        self.released += 1


class _FakePlayer(ComparePlaybackControls):
    """재생 엔진 없이 창의 재생 분기만 확인하는 가짜 제어기"""

    def __init__(self, timeline: PlaybackControls) -> None:
        self.calls: list[str] = []
        self.playing: bool = False
        self.position: int = 0
        self.rate_value: float = 1.0
        self.muted_value: bool = False
        self.volume: int = 100
        self.loaded: dict[str, object] = {}
        super().__init__(timeline, None)

    def load(
        self,
        *,
        original_path: str,
        compared_path: str,
        tier_key: str | None,
        frame_width: int,
        frame_height: int,
        position_ms: int,
    ) -> None:
        self.loaded = {
            "original_path": original_path,
            "compared_path": compared_path,
            "tier_key": tier_key,
            "position_ms": position_ms,
        }
        self.calls.append("load")

    def start(self) -> None:
        self.playing = True
        self.calls.append("start")

    def pause(self) -> None:
        self.playing = False
        self.calls.append("pause")

    def seek(self, position_ms: int) -> None:
        self.position = position_ms
        self.calls.append("seek")

    def stop(self) -> None:
        self.playing = False
        self.calls.append("stop")

    def is_playing(self) -> bool:
        return self.playing

    def position_ms(self) -> int:
        return self.position

    def rate(self) -> float:
        return self.rate_value

    def muted(self) -> bool:
        return self.muted_value

    def volume_percent(self) -> int:
        return self.volume

    def select_rate(self, rate: float) -> None:
        self.rate_value = rate
        self.calls.append("rate")

    def step_rate(self, step: int) -> None:
        self.calls.append(f"step_rate:{step}")

    def reset_rate(self) -> None:
        self.calls.append("reset_rate")

    def toggle_mute(self) -> None:
        self.muted_value = not self.muted_value
        self.calls.append("mute")

    def set_volume(self, value: int) -> None:
        self.volume = value
        self.calls.append("volume")

    def save_audio(self) -> None:
        self.calls.append("save_audio")

    def sync_controls(self) -> None:
        self.calls.append("sync")

    def reload_settings(self) -> None:
        self.calls.append("reload")



def _wait(window: FrameCompareWindow) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while window._extraction.busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    _APP.processEvents()


def _press(
    widget: QWidget,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    *,
    repeat: bool = False,
) -> None:
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifier, "", repeat))
    if modifier != Qt.KeyboardModifier.NoModifier:
        release_modifier_keys()


class _WindowCase(GuiTestCase):
    """임시 미디어 파일과 비교 계획, 세션을 준비하는 기반"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "video.mp4")
        self.result = os.path.join(self.tmp.name, "video_out.mp4")
        for path in (self.source, self.result):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.extractor = _Extractor()
        self.shortcuts: dict[str, str] = dict(DEFAULT_SHORTCUTS)
        self.window = FrameCompareWindow(self.extractor, lambda: 5, lambda: self.shortcuts)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        _APP.processEvents()

    def session(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> CompareSession:
        result = (
            result_target(self.result, width=1920, height=1080, size_bytes=580 * 1024 * 1024)
            if with_result
            else None
        )
        plan = resolve_compare_plan(
            source_path=self.source,
            width=3840,
            height=2160,
            size_bytes=1024**3,
            result=result,
            candidate_tier="1080p",
        )
        return CompareSession(
            plan=plan,
            duration_sec=100.0,
            fps=30.0,
            filename="video.mp4",
            position_sec=10.0,
            scores=scores,
            width=3840,
            height=2160,
            source_path=self.source,
        )

    def load(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> None:
        self.window.load_session(self.session(with_result=with_result, scores=scores))
        _wait(self.window)


class LoadSessionTests(_WindowCase):
    """세션 적재 뒤 상단 바, 메타, 대상, 프레임 상태"""

    def test_default_target_is_result_and_frames_load(self) -> None:
        self.load()
        self.assertEqual(self.window._top.filename.text(), "video.mp4")
        self.assertIn("video.mp4", self.window.windowTitle())
        self.assertEqual(self.window._top.target_button.text(), "결과")
        self.assertIn("원본 3840x2160", self.window._top.meta.original.text())
        self.assertIn("결과 1920x1080", self.window._top.meta.target.text())
        self.assertEqual(self.window._top.meta.time.text(), "시점 00:10.000")
        labels = [action.text() for action in self.window._top.target_menu.actions()]
        self.assertEqual(labels[0], "결과")
        self.assertIn("1080p 축소 비교", labels)
        self.assertIsNotNone(self.window._canvas._original)
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.assertEqual(self.extractor.calls[0][2], 10.0)
        self.assertEqual(self.extractor.calls[0][3:], (3840, 2160))

    def test_cached_score_is_shown_inline_only_for_its_tier(self) -> None:
        scores = (TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),)
        self.load(scores=scores)
        self.assertTrue(self.window._top.score.isHidden())
        self.window.set_target("1080p")
        _wait(self.window)
        self.assertFalse(self.window._top.score.isHidden())
        self.assertEqual(self.window._top.score.text(), "화질 분석 1080p: VMAF 98.7, SSIM 0.9983")
        self.assertIn("VMAF는 100에 가까울수록", self.window._top.score.toolTip())
        self.assertEqual(self.window._top.target_button.text(), "1080p 축소 비교")

    def test_plan_without_target_shows_error_overlay(self) -> None:
        plan = ComparePlan(
            original=FrameSource(key="원본", path=self.source, label="원본"),
            targets=(),
            default_key=None,
            reason=NO_TARGET_REASON,
        )
        session = CompareSession(plan=plan, duration_sec=100.0, fps=30.0, filename="video.mp4")
        self.window.load_session(session)
        self.assertFalse(self.window._stage.overlay.isHidden())
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), NO_TARGET_REASON)

    def test_missing_original_uses_the_approved_reason(self) -> None:
        os.remove(self.source)
        session = self.session()
        self.window.load_session(session)
        self.assertEqual(self.window._stage.body.text(), "원본 파일이 없어 비교할 수 없습니다.")

    def test_meta_tooltips_show_the_full_paths_of_both_files(self) -> None:
        self.load()
        self.assertEqual(self.window._top.meta.original.toolTip(), f"원본 파일: {self.source}")
        self.assertEqual(self.window._top.meta.target.toolTip(), f"결과 파일: {self.result}")

    def test_downscale_target_keeps_its_roundtrip_tooltip(self) -> None:
        self.load()
        self.window.set_target("1080p")
        _wait(self.window)
        self.assertIn("축소", self.window._top.meta.target.toolTip())
        self.assertEqual(self.window._top.target_button.text(), "1080p 축소 비교")


class InteractionTests(_WindowCase):
    """모드, 배율, 시점, 사용자 지정 단축키"""

    def test_mode_keys_switch_three_modes(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_2)
        self.assertEqual(self.window._canvas.mode(), MODE_TOGGLE)
        self.assertTrue(self.window._top.mode_buttons[MODE_TOGGLE].isChecked())
        _press(self.window, Qt.Key.Key_3)
        self.assertEqual(self.window._canvas.mode(), MODE_SIDE)
        _press(self.window, Qt.Key.Key_1)
        self.assertEqual(self.window._canvas.mode(), MODE_SPLIT)

    def test_c_key_toggles_original_only_in_toggle_mode(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_2)
        _press(self.window, Qt.Key.Key_C)
        self.assertTrue(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())

    def test_auto_repeat_does_not_flip_the_pinned_original(self) -> None:
        self.load()
        self.window.set_mode(MODE_TOGGLE)
        _press(self.window, Qt.Key.Key_C, repeat=True)
        self.assertFalse(self.window._canvas.original_pinned())

    def test_zero_and_plus_minus_change_zoom(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        _press(self.window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        self.assertFalse(any(button.isChecked() for button in self.window._top.zoom_buttons.values()))
        _press(self.window, Qt.Key.Key_Equal)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25 * 1.25)
        _press(self.window, Qt.Key.Key_Minus)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        _press(self.window, Qt.Key.Key_0)
        self.assertTrue(self.window._top.zoom_buttons["fit"].isChecked())

    def test_custom_mode_key_replaces_the_default(self) -> None:
        """사용자가 모드 키를 바꾸면 기본 숫자 키 대신 새 키로 동작하는지 검증"""
        self.load()
        self.window.set_mode(MODE_TOGGLE)
        self.shortcuts["compare.mode_split"] = "F2"
        _press(self.window, Qt.Key.Key_1)
        self.assertEqual(self.window._canvas.mode(), MODE_TOGGLE)
        _press(self.window, Qt.Key.Key_F2)
        self.assertEqual(self.window._canvas.mode(), MODE_SPLIT)

    def test_arrow_keys_seek_by_preview_interval_and_shift_is_six_times(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Right)
        self.assertAlmostEqual(self.window._position_sec, 15.0)
        _press(self.window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 45.0)
        _press(self.window, Qt.Key.Key_Left)
        self.assertAlmostEqual(self.window._position_sec, 40.0)
        _press(self.window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 10.0)

    def test_comma_and_period_step_one_frame(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.window._position_sec, 10.0 + 1 / 30)
        _press(self.window, Qt.Key.Key_Comma)
        self.assertAlmostEqual(self.window._position_sec, 10.0)

    def test_custom_frame_keys_are_used(self) -> None:
        self.load()
        self.shortcuts["preview.frame_forward"] = "X"
        _press(self.window, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.window._position_sec, 10.0)
        _press(self.window, Qt.Key.Key_X)
        self.assertAlmostEqual(self.window._position_sec, 10.0 + 1 / 30)

    def test_typed_zoom_applies_and_clears_preset_checks(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        spin = self.window._top.zoom_input
        spin.setValue(250)
        self.window._top.commit_zoom_input()
        self.assertAlmostEqual(self.window._canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in self.window._top.zoom_buttons.values()))

    def test_manual_zoom_and_sync_keep_the_input_in_step(self) -> None:
        self.load()
        self.window._canvas.zoom_step(1)
        self.window._top.on_manual_zoom()
        self.assertEqual(
            self.window._top.zoom_input.value(), round(self.window._canvas.scale() * 100)
        )

    def test_shortcut_keys_show_the_result_on_the_canvas_notice(self) -> None:
        """키보드로 실행한 명령의 결과가 캔버스 위쪽 알림에 뜨는지 검증"""
        self.load()
        notice = self.window._status._notice
        _press(self.window, Qt.Key.Key_2)
        self.assertEqual(notice.text(), "즉시 전환 모드")
        _press(self.window, Qt.Key.Key_C)
        self.assertEqual(notice.text(), "원본 고정")
        _press(self.window, Qt.Key.Key_Right)
        self.assertEqual(notice.text(), "구간 이동 +5초")
        _press(self.window, Qt.Key.Key_Period)
        self.assertEqual(notice.text(), "다음 프레임")
        _press(self.window, Qt.Key.Key_H)
        self.assertEqual(notice.text(), "조작 바 접기")
        _press(self.window, Qt.Key.Key_H)
        self.assertEqual(notice.text(), "조작 바 펼치기")
        _press(self.window, Qt.Key.Key_M)
        self.assertEqual(notice.text(), "음소거")


class CloseAndFullscreenTests(_WindowCase):
    """Space, Esc 닫기 순서와 전체화면"""

    def test_space_closes_the_window(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Space)
        self.assertTrue(self.window.isHidden())

    def test_escape_closes_the_window(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Escape)
        self.assertTrue(self.window.isHidden())

    def test_space_closes_the_open_target_menu_first(self) -> None:
        self.load()
        self.window._top.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        _press(self.window, Qt.Key.Key_Space)
        _APP.processEvents()
        self.assertFalse(self.window._top.target_menu.isVisible())
        self.assertFalse(self.window.isHidden())

    def test_escape_leaves_fullscreen_before_closing(self) -> None:
        self.load()
        self.window.showFullScreen()
        _APP.processEvents()
        self.assertTrue(self.window.isFullScreen())
        _press(self.window, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertFalse(self.window.isFullScreen())
        self.assertFalse(self.window.isHidden())

    def test_space_closes_the_open_shortcut_guide_first(self) -> None:
        self.load()
        self.window._timeline.guide_button.open_guide()
        _APP.processEvents()
        self.assertTrue(self.window._timeline.guide_button.is_guide_open())
        _press(self.window, Qt.Key.Key_Space)
        _APP.processEvents()
        self.assertFalse(self.window._timeline.guide_button.is_guide_open())
        self.assertFalse(self.window.isHidden())

    def test_shift_space_requests_preview_with_time_and_path(self) -> None:
        self.load()
        self.window.set_time(30.0)
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        _press(self.window, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(requests, [(self.source, 30.0)])

    def test_fullscreen_button_toggles_and_updates_the_icon(self) -> None:
        self.load()
        button = self.window._timeline.findChild(QToolButton, "PreviewFullscreenButton")
        assert button is not None
        with mock.patch.object(self.window, "showFullScreen") as show_fullscreen:
            button.click()
        show_fullscreen.assert_called_once()
        self.assertEqual(button.accessibleName(), "전체화면 전환")
        with (
            mock.patch.object(self.window, "isFullScreen", return_value=True),
            mock.patch.object(self.window, "showNormal") as show_normal,
        ):
            self.window._toggle_fullscreen()
        show_normal.assert_called_once()

    def test_auto_hide_is_blocked_while_menu_or_editor_is_open(self) -> None:
        self.load()
        self.assertTrue(self.window._can_auto_hide())
        self.window._top.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        self.assertFalse(self.window._can_auto_hide())
        self.window._top.target_menu.close()

    def test_h_key_collapses_and_reveals_the_top_bar(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_H)
        self.assertTrue(self.window._top.is_collapsed())
        self.window._top.note_mouse_activity(QPoint(10, 10))
        self.assertTrue(self.window._top.is_overlay_visible())
        _press(self.window, Qt.Key.Key_H)
        self.assertFalse(self.window._top.is_collapsed())

    def test_hide_closes_the_open_shortcut_guide(self) -> None:
        """호버 대기나 열린 안내가 남은 채 창을 숨기면 팝오버와 타이머를 정리하는지 검증"""
        self.load()
        guide = self.window._timeline.guide_button
        guide.open_guide()
        _APP.processEvents()
        self.assertTrue(guide.is_guide_open())
        self.window.hide()
        _APP.processEvents()
        self.assertFalse(guide.is_guide_open())
        self.assertFalse(guide._hide_timer.isActive())
        self.assertFalse(guide._open_timer.isActive())


class FocusEntryTests(_WindowCase):
    """창을 열거나 다시 활성화할 때 초기 포커스와 배율 입력 Esc"""

    def test_initial_focus_does_not_swallow_shift_space(self) -> None:
        """창을 연 직후 포커스가 대상 버튼이나 배율 입력이 아니고 미리 보기 전환이 동작하는지 검증"""
        self.load()
        top = self.window._top
        self.assertNotEqual(QApplication.focusWidget(), top.target_button)
        self.assertNotEqual(QApplication.focusWidget(), top.zoom_input)
        self.assertNotEqual(QApplication.focusWidget(), top.zoom_input.lineEdit())
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        _press(
            QApplication.focusWidget() or self.window,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(requests, [(self.source, 10.0)])

    def test_app_key_filter_handles_shift_space_while_the_window_is_active(self) -> None:
        """활성 상태면 자식 위젯 포커스와 무관하게 앱 키 필터가 전환 단축키를 처리하는지 검증"""
        self.load()
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier, ""
        )
        with mock.patch.object(self.window, "isActiveWindow", return_value=True):
            handled = self.window._key_filter.eventFilter(self.window, event)
        self.assertTrue(handled)
        self.assertEqual(requests, [(self.source, 10.0)])

    def test_app_key_filter_ignores_keys_while_the_window_is_inactive(self) -> None:
        """비활성 상태에서는 다른 창의 키를 가로채지 않는지 검증"""
        self.load()
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier, ""
        )
        with mock.patch.object(self.window, "isActiveWindow", return_value=False):
            self.assertFalse(self.window._key_filter.eventFilter(self.window, event))
        self.assertEqual(requests, [])

    def test_app_key_filter_yields_while_a_popup_is_open(self) -> None:
        """팝업이 키를 직접 받아야 하면 필터가 처리하지 않는지 검증"""
        self.load()
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier, ""
        )
        with (
            mock.patch.object(self.window, "isActiveWindow", return_value=True),
            mock.patch(
                "src.gui.dialogs.frame_compare_shortcuts.blocks_preview_keys", return_value=True
            ),
        ):
            self.assertFalse(self.window._key_filter.eventFilter(self.window, event))

    def test_key_filter_is_removed_while_the_window_is_hidden(self) -> None:
        """창을 숨기면 앱 키 필터를 해제해 다른 창의 키를 가로채지 않는지 검증"""
        self.load()
        self.window.hide()
        other = QLabel()
        self.addCleanup(other.deleteLater)
        other.show()
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        with mock.patch.object(self.window, "isActiveWindow", return_value=True):
            _press(other, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(requests, [])

    def test_key_filter_is_registered_once_for_repeated_shows(self) -> None:
        """창을 다시 표시해도 앱 키 필터를 한 번만 등록하는지 검증"""
        self.load()
        self.window.hide()
        self.window.show()
        _APP.processEvents()
        self.assertTrue(self.window._key_filter._installed)

    def test_escape_in_the_zoom_input_restores_the_value_and_the_focus(self) -> None:
        """배율 입력에서 Esc를 누르면 값이 되돌아가고 창이 다시 단축키를 받는지 검증"""
        self.load()
        top = self.window._top
        self.window.set_zoom_preset("1")
        top.zoom_input.setValue(250)
        top.zoom_input.setFocus(Qt.FocusReason.MouseFocusReason)
        _APP.processEvents()
        self.assertTrue(top.zoom_input.hasFocus())
        _press(QApplication.focusWidget() or top.zoom_input, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertFalse(top.zoom_input.hasFocus())
        self.assertEqual(top.zoom_input.value(), 100)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.0)
        self.assertTrue(top.zoom_buttons["1"].isChecked())
        self.assertFalse(self.window.isHidden())
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        _press(
            QApplication.focusWidget() or self.window,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(requests, [(self.source, 10.0)])


class ThemeAndLayoutTests(_WindowCase):
    """다크 시네마 스타일과 상단 바 구성"""

    def test_theme_applies_the_dark_cinema_stylesheet(self) -> None:
        self.load()
        self.window.apply_theme(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.media_bg, self.window.styleSheet())
        self.assertIn("QWidget#CompareTopBar", self.window.styleSheet())
        fullscreen = self.window._timeline.findChild(QToolButton, "PreviewFullscreenButton")
        assert fullscreen is not None
        self.assertFalse(fullscreen.icon().isNull())

    def test_top_bar_has_two_rows_and_no_extra_score_row(self) -> None:
        self.load()
        self.assertTrue(self.window._top.score.parent() is not None)
        self.assertIsNone(self.window.findChild(QWidget, "CompareScore"))
        self.assertIsNone(self.window.findChild(QWidget, "CompareShortcuts"))
        self.assertEqual(self.window._top.collapse_button.accessibleName(), "조작 바 접기")

    def test_zoom_input_has_a_visible_inner_editor(self) -> None:
        """전역 입력 규칙이 겹쳐도 배율 숫자 칸이 최소 높이를 갖는지 검증"""
        self.load()
        spin = self.window._top.zoom_input
        editor = spin.lineEdit()
        self.assertIsNotNone(editor)
        assert editor is not None
        self.assertGreaterEqual(editor.sizeHint().height(), editor.fontMetrics().height())
        self.assertGreaterEqual(spin.sizeHint().height(), spin.fontMetrics().height())

    def test_zoom_input_keeps_a_readable_editor_with_the_app_sheet(self) -> None:
        """전역 스핀박스 화살표 규칙이 겹쳐도 배율 입력 글씨 영역이 남는지 검증"""
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        app.setStyleSheet(app_stylesheet.build_stylesheet(LIGHT_PALETTE))
        self.addCleanup(app.setStyleSheet, "")
        self.load()
        self.window.apply_theme(LIGHT_PALETTE)
        _APP.processEvents()
        editor = self.window._top.zoom_input.lineEdit()
        self.assertIsNotNone(editor)
        assert editor is not None
        self.assertGreaterEqual(editor.width(), 40)
        self.assertGreaterEqual(editor.height(), 14)

    def test_minimum_width_fits_the_default_font(self) -> None:
        self.load()
        self.window.resize(720, 520)
        _APP.processEvents()
        self.assertLessEqual(self.window.minimumSizeHint().width(), 720)


class PlaybackTests(_WindowCase):
    """재생 단축키, 일시중지 정밀 프레임, 재생 중 이동, 끝 도달, 창 정리"""

    def setUp(self) -> None:
        super().setUp()
        self.player = _FakePlayer(self.window._timeline)
        self.addCleanup(self.player.deleteLater)
        self.window._player = self.player

    def test_load_session_loads_the_player_at_the_session_time(self) -> None:
        self.load()
        self.assertEqual(self.player.loaded["original_path"], self.source)
        self.assertEqual(self.player.loaded["compared_path"], self.result)
        self.assertIsNone(self.player.loaded["tier_key"])
        self.assertEqual(self.player.loaded["position_ms"], 10_000)

    def test_k_starts_and_pauses_keeping_the_current_position(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_K)
        self.assertIn("start", self.player.calls)
        self.player.playing = True
        self.window.set_time(50.0)
        self.assertIn("seek", self.player.calls)
        self.player.position = 50_000
        _press(self.window, Qt.Key.Key_K)
        self.assertIn("pause", self.player.calls)
        self.assertAlmostEqual(self.window.position_sec, 50.0)

    def test_pause_requests_a_precise_frame(self) -> None:
        self.load()
        self.player.playing = True
        self.player.position = 12_000
        with mock.patch.object(self.window, "_request_precise_frame") as request:
            self.window.toggle_playback()
        request.assert_called_once()

    def test_step_keys_pause_before_moving(self) -> None:
        self.load()
        self.player.playing = True
        _press(self.window, Qt.Key.Key_Period)
        self.assertIn("pause", self.player.calls)
        self.assertAlmostEqual(self.window.position_sec, 10.0 + 1 / 30)

    def test_rate_and_mute_keys_use_the_shared_player(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_BracketLeft)
        _press(self.window, Qt.Key.Key_BracketRight)
        _press(self.window, Qt.Key.Key_Backslash)
        _press(self.window, Qt.Key.Key_M)
        self.assertIn("step_rate:-1", self.player.calls)
        self.assertIn("step_rate:1", self.player.calls)
        self.assertIn("reset_rate", self.player.calls)
        self.assertIn("mute", self.player.calls)

    def test_target_change_while_playing_keeps_playing(self) -> None:
        self.load()
        self.player.playing = True
        self.window.set_target("1080p")
        self.assertEqual(self.player.loaded["tier_key"], "1080p")
        self.assertIn("start", self.player.calls)

    def test_playback_frames_and_position_update_the_view(self) -> None:
        self.load()
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#00FF00"))
        self.player.playing = True
        self.window._on_playing_changed(True)
        self.window._on_playback_frames(image, image)
        self.assertIsNotNone(self.window._canvas._compared)
        self.window._on_playback_position(20_000)
        self.assertAlmostEqual(self.window.position_sec, 20.0)
        self.window._on_playback_frames("잘못된 값", image)
        self.player.playing = False
        self.window._on_playback_position(30_000)
        self.assertAlmostEqual(self.window.position_sec, 20.0)

    def test_end_of_media_keeps_the_last_position(self) -> None:
        self.load()
        self.window._on_playback_end()
        self.assertAlmostEqual(self.window.position_sec, 100.0)
        self.assertEqual(self.window._top.meta.time.text(), "시점 01:40.000")

    def test_hide_stops_playback_and_releases_the_extraction(self) -> None:
        self.load()
        self.player.playing = True
        self.window.hide()
        self.assertIn("stop", self.player.calls)
        self.assertFalse(self.window._extraction.busy)
        self.assertGreaterEqual(self.extractor.released, 1)

    def test_request_preview_keeps_the_time_and_playing_state(self) -> None:
        """미리 보기 전환 요청이 재생을 먼저 멈춰 시점과 재생 상태를 잃지 않는지 검증"""
        self.load()
        self.player.playing = True
        self.player.position = 50_000
        self.window._position_sec = 50.0
        requests: list[tuple[str, float]] = []
        self.window.preview_requested.connect(lambda path, pos: requests.append((path, pos)))
        self.player.calls.clear()
        self.window._request_preview()
        self.assertEqual(requests, [(self.source, 50.0)])
        self.assertTrue(self.window.is_playing)
        self.assertNotIn("stop", self.player.calls)

    def test_load_session_reloads_shared_playback_settings(self) -> None:
        self.load()
        self.assertIn("reload", self.player.calls)

    def test_start_playback_only_starts_when_paused(self) -> None:
        self.load()
        self.window.start_playback()
        starts = self.player.calls.count("start")
        self.window.start_playback()
        self.assertEqual(self.player.calls.count("start"), starts)
        self.assertTrue(self.window.is_playing)

    def test_volume_slider_changes_are_forwarded_to_the_player(self) -> None:
        self.load()
        volume = self.window._timeline.findChild(QSlider, "PreviewVolumeSlider")
        assert volume is not None
        volume.setValue(35)
        self.assertIn("volume", self.player.calls)



class ErrorAndThreadTests(_WindowCase):
    """원인별 오류 표시, 지연 결과 폐기와 종료 시 취소, 로딩 배지"""

    def test_file_missing_and_execution_errors_use_different_bodies(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.load()
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), "파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.extractor.failure = FrameCompareExecutionError("FFmpeg로 프레임을 추출하지 못했습니다: boom")
        self.window.set_time(20.0)
        self.window._request_precise_frame()
        _wait(self.window)
        self.assertIn("boom", self.window._stage.body.text())
        self.extractor.failure = None
        self.window.set_time(30.0)
        self.window._request_precise_frame()
        _wait(self.window)
        self.assertTrue(self.window._stage.overlay.isHidden())

    def test_loading_overlay_uses_the_approved_text_when_no_frame(self) -> None:
        window = FrameCompareWindow(_Extractor(), lambda: 5, lambda: self.shortcuts)
        self.addCleanup(window.deleteLater)
        window._show_loading()
        self.assertFalse(window._stage.overlay.isHidden())
        self.assertEqual(window._stage.title.text(), "프레임을 불러오는 중...")
        self.assertEqual(window._stage.body.text(), "")

    def test_loading_after_a_frame_shows_only_the_pill(self) -> None:
        """프레임이 있는 상태에서는 전체 오버레이 대신 알약 배지만 쓰는지 검증"""
        self.load()
        self.window._show_loading()
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.assertTrue(self.window._stage.pill.isHidden())
        self.window._status.reveal()
        self.assertFalse(self.window._stage.pill.isHidden())
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.window._hide_overlay()
        self.assertTrue(self.window._stage.pill.isHidden())

    def test_raw_rgb_frames_from_the_memory_decoder_are_shown(self) -> None:
        """BMP 바이트 없이 RGB24 프레임만 와도 캔버스에 픽셀 그대로 표시하는지 검증"""
        frame = DecodedFrame(
            width=2, height=1, bytes_per_line=6, data=bytes([255, 0, 0, 0, 255, 0])
        )

        class _Raw(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height, cancel_event
                return ExtractedFrames(
                    original_bytes=b"",
                    compared_bytes=b"",
                    original_frame=frame,
                    compared_frame=frame,
                )

        window = FrameCompareWindow(_Raw(), lambda: 5, lambda: self.shortcuts)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        _wait(window)
        image = window._canvas._original
        self.assertIsNotNone(image)
        assert image is not None
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")
        self.assertEqual(image.pixelColor(1, 0).name(), "#00ff00")

    def test_unreadable_frame_bytes_show_a_decode_error(self) -> None:
        class _Broken(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height, cancel_event
                return ExtractedFrames(original_bytes=b"not an image", compared_bytes=b"not an image")

        window = FrameCompareWindow(_Broken(), lambda: 5, lambda: self.shortcuts)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        _wait(window)
        self.assertEqual(window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(window._stage.body.text(), "추출한 프레임 이미지를 읽지 못했습니다.")

    def test_malformed_outcomes_are_ignored(self) -> None:
        self.load()
        before = self.window._canvas._original
        self.window._on_frames_extracted("잘못된 값")
        self.window._on_extraction_failed("잘못된 값")
        self.window._on_frames_extracted(ExtractOutcome(1, None))
        self.assertIs(self.window._canvas._original, before)

    def test_close_cancels_a_running_extraction(self) -> None:
        started = threading.Event()
        cancelled: list[bool] = []

        class _Blocking(_Extractor):
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                del original, compared, time_sec, frame_width, frame_height
                started.set()
                cancel_event.wait(_WAIT_SECONDS)
                cancelled.append(cancel_event.is_set())
                raise InterruptedError("취소")

        window = FrameCompareWindow(_Blocking(), lambda: 5, lambda: self.shortcuts)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        deadline = time.monotonic() + _WAIT_SECONDS
        while not started.is_set() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(started.is_set())
        window.close()
        self.assertFalse(window._extraction.busy)
        self.assertTrue(cancelled)


class ShortcutSyncTests(_WindowCase):
    """단축키 변경이 상단, 하단 툴팁과 열린 안내에 반영되는지"""

    def test_sync_shortcuts_updates_tooltips_and_open_guide(self) -> None:
        self.load()
        guide = self.window._timeline.guide_button
        guide.open_guide()
        _APP.processEvents()
        effective = dict(self.shortcuts)
        effective["compare.mode_split"] = "F2"
        effective["preview.toggle_playback"] = "X"
        self.shortcuts.update(effective)
        self.window.sync_shortcuts(effective)
        _APP.processEvents()
        self.assertIn("[F2]", self.window._top.mode_buttons[MODE_SPLIT].toolTip())
        toggle = self.window._timeline.findChild(QToolButton, "PreviewToggleButton")
        assert toggle is not None
        self.assertIn("[X]", toggle.toolTip())
        self.assertTrue(guide.is_guide_open())
        popover = guide._ensure_popover()
        keys = [
            label.text()
            for label in popover.findChildren(QLabel, "ShortcutGuideKey")
        ]
        self.assertIn("F2", keys)
        guide.close_guide()


if __name__ == "__main__":
    unittest.main(verbosity=2)
