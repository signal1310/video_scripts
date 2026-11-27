"""미리 보기 모드 전환 검증: 비교 페이지 전환, 공용 조작 바와 정보 표시 동기화, 핀과 전체화면 전환"""

import os
import tempfile
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget

from src.core.analysis.frame_compare import ComparePlan, FrameSource
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.preview import preview_modes
from src.gui.preview.preview_info_presenter import PreviewInfoPresenter
from src.gui.preview.preview_modes import PreviewModeSwitcher
from src.gui.preview.video_preview_controls import PlaybackControls
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_service import ExtractedFrames
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class _Extractor:
    """비교 뷰가 쓰는 프레임 추출기 대역"""

    def __init__(self) -> None:
        self.released: int = 0

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: object,
    ) -> ExtractedFrames:
        del original, compared, time_sec, frame_width, frame_height, cancel_event
        return ExtractedFrames(original_bytes=b"", compared_bytes=b"")

    def release_decoders(self) -> None:
        self.released += 1


def _session(path: str) -> CompareSession:
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
        filename="clip.mp4",
        position_sec=5.0,
        source_path=path,
    )


class ModeSwitcherTests(GuiTestCase):
    """페이지, 조작 바, 정보 표시, 안내 자리를 비교 모드에 맞춰 바꾸는지"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "clip.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"not a real video")
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        self.stack = QStackedWidget(self.host)
        self.normal_page = QWidget(self.host)
        self.compare_page = QWidget(self.host)
        self.stack.addWidget(self.normal_page)
        self.stack.addWidget(self.compare_page)
        self.controls = PlaybackControls(self.host, lambda: DEFAULT_SHORTCUTS)
        self.addCleanup(self.controls.deleteLater)
        self.info_mock = mock.Mock()
        self.info = cast("PreviewInfoPresenter", self.info_mock)
        self.collapsed_value: list[bool] = [False]
        self.collapsed_changes: list[bool] = []
        self.switcher = PreviewModeSwitcher(
            self.stack,
            self.normal_page,
            self.compare_page,
            self.controls,
            self.info,
            get_seek_seconds=lambda: 5,
            get_shortcuts=lambda: DEFAULT_SHORTCUTS,
            get_extractor=_Extractor,
            seek_normal=lambda _ms: None,
            step_normal=lambda _direction: None,
            toggle_normal=lambda: None,
            get_compare_top_bar_collapsed=lambda: self.collapsed_value[0],
            on_compare_top_bar_collapsed_changed=self.collapsed_changes.append,
        )
        self.host.show()
        _APP.processEvents()

    def test_show_session_switches_to_the_compare_page(self) -> None:
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=2.0, volume=40, muted=True
        )
        _APP.processEvents()
        self.assertTrue(self.switcher.compare_mode)
        self.assertIs(self.stack.currentWidget(), self.compare_page)
        self.assertTrue(self.controls.compare_button.isChecked())
        self.info_mock.set_compare_mode.assert_called_with(True)
        view = self.switcher.view
        assert view is not None
        self.assertEqual(view.source_path, self.path)
        self.assertAlmostEqual(self.switcher.position_sec, 5.0)
        self.assertFalse(self.switcher.is_playing)
        self.assertIsNotNone(self.switcher.notice_area_global())
        self.switcher.set_rate(1.5)
        self.switcher.set_audio(60, False)
        self.switcher.sync_shortcuts(DEFAULT_SHORTCUTS)
        self.switcher.apply_theme(LIGHT_PALETTE)
        self.switcher.seek(3000)
        self.switcher.step_frame(1)
        self.switcher.toggle()
        self.assertTrue(
            self.switcher.handle_key_event(
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier)
            )
        )

    def test_stage_style_reaches_the_open_compare_view(self) -> None:
        """배경 색과 영상 테두리가 열린 비교 뷰의 캔버스와 무대 틀에 바로 적용되는지 검증"""
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        view = self.switcher.view
        assert view is not None
        self.switcher.set_stage_style("white", True)
        self.assertEqual(view._stage_background, "white")
        self.assertTrue(view._stage_outline)
        self.assertEqual(view._canvas._background, "white")
        self.assertTrue(view._canvas._frame_outline)
        wrap = view._canvas.parentWidget()
        assert wrap is not None
        self.assertEqual(wrap.property("stagebg"), "white")

    def test_first_compare_view_applies_the_active_palette_to_canvas_labels(self) -> None:
        """비교 뷰를 처음 만들 때 세 라벨에 현재 팔레트의 HUD 색이 적용되는지 검증"""
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        view = self.switcher.view
        assert view is not None
        palette = get_active_palette()
        for label in (
            view._canvas._left_label,
            view._canvas._right_label,
            view._canvas._center_badge,
        ):
            sheet = label.styleSheet()
            self.assertIn(palette.media_hud_bg, sheet)
            self.assertIn(palette.media_track, sheet)
            self.assertIn(palette.media_fg, sheet)

    def test_compare_view_restores_the_saved_top_bar_collapsed_state(self) -> None:
        """저장된 접힘 상태로 비교 뷰를 만들고 H 토글을 저장 쪽에 알리는지 검증"""
        self.collapsed_value[0] = True
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        view = self.switcher.view
        assert view is not None
        self.assertTrue(view._top.is_collapsed())
        self.assertEqual(self.collapsed_changes, [])
        self.assertTrue(
            self.switcher.handle_key_event(
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_H, Qt.KeyboardModifier.NoModifier)
            )
        )
        self.assertFalse(view._top.is_collapsed())
        self.assertEqual(self.collapsed_changes, [False])

    def test_pause_only_stops_the_active_compare_view(self) -> None:
        """창을 잠시 가릴 때 비교 모드가 켜져 있으면 비교 재생만 일시중지하는지 검증"""
        self.switcher.pause()
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        view = self.switcher.view
        assert view is not None
        with mock.patch.object(view, "pause_playback") as pause:
            self.switcher.pause()
        pause.assert_called_once()

    def test_reset_restores_the_normal_page(self) -> None:
        self.switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        _APP.processEvents()
        self.switcher.reset()
        _APP.processEvents()
        self.assertFalse(self.switcher.compare_mode)
        self.assertIs(self.stack.currentWidget(), self.normal_page)
        self.assertFalse(self.controls.compare_button.isChecked())
        self.info_mock.set_compare_mode.assert_called_with(False)
        self.assertIsNone(self.switcher.notice_area_global())
        self.switcher.reset()

    def test_without_an_extractor_the_page_does_not_change(self) -> None:
        switcher = PreviewModeSwitcher(
            self.stack,
            self.normal_page,
            self.compare_page,
            self.controls,
            self.info,
            get_seek_seconds=lambda: 5,
            get_shortcuts=lambda: DEFAULT_SHORTCUTS,
            get_extractor=lambda: None,
            seek_normal=lambda _ms: None,
            step_normal=lambda _direction: None,
            toggle_normal=lambda: None,
        )
        switcher.show_session(
            _session(self.path), auto_play=False, rate=1.0, volume=100, muted=False
        )
        self.assertFalse(switcher.compare_mode)
        self.assertIsNone(switcher.view)
        self.assertIsNone(switcher.notice_area_global())
        self.assertEqual(switcher.source_path, "")
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(switcher.handle_key_event(event))
        self.assertIsNone(switcher.seek(1000))
        self.assertIsNone(switcher.step_frame(1))
        self.assertIsNone(switcher.set_rate(1.0))
        self.assertIsNone(switcher.set_audio(50, False))
        self.assertIsNone(switcher.sync_shortcuts(DEFAULT_SHORTCUTS))
        self.assertIsNone(switcher.apply_theme(LIGHT_PALETTE))
        switcher.stop()



class WindowStateTests(GuiTestCase):
    """핀과 전체화면이 창 플래그, 조작 바, 저장값을 함께 바꾸는지"""

    def setUp(self) -> None:
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.controls = PlaybackControls()
        self.addCleanup(self.controls.deleteLater)
        _APP.processEvents()

    def test_apply_stays_on_top_updates_the_controls_and_saves(self) -> None:
        changed: list[bool] = []
        preview_modes.apply_stays_on_top(
            False,
            controls=self.controls,
            on_changed=changed.append,
        )
        self.assertEqual(changed, [False])
        self.assertFalse(self.controls._always_on_top)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_toggle_fullscreen_updates_the_control_bar(self) -> None:
        changed: list[bool] = []
        preview_modes.toggle_fullscreen(
            self.window,
            controls=self.controls,
            stored=False,
            on_changed=changed.append,
        )
        _APP.processEvents()
        self.assertEqual(self.window.isFullScreen(), True)
        self.assertEqual(changed, [True])
        preview_modes.toggle_fullscreen(
            self.window,
            controls=self.controls,
            stored=True,
            on_changed=changed.append,
        )
        _APP.processEvents()
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(changed, [True, False])


if __name__ == "__main__":
    unittest.main(verbosity=2)


