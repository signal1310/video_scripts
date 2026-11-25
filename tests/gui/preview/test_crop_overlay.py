"""미리 보기 여백 겹침의 배치 계산과 그림, 창 안 버튼과 겹침 상태를 검증"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication, QToolButton

from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.preview.crop_overlay import (
    CropOverlay,
    CropOverlayRects,
    crop_overlay_rects,
)
from src.gui.preview.preview_controller import PreviewPositionMemory
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.video_preview_controls import PlaybackControls
from src.gui.preview.video_preview_window import VideoPreviewWindow
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _total_area(rects: CropOverlayRects) -> int:
    """유지 영역과 잘라낼 여백 면적의 합"""
    return rects.kept.width() * rects.kept.height() + sum(
        rect.width() * rect.height() for rect in rects.cuts
    )


class CropOverlayRectsTests(unittest.TestCase):
    """영상 위 내용 영역, 유지 영역, 잘라낼 여백 계산"""

    def test_full_frame_has_no_cut_margins(self) -> None:
        rects = crop_overlay_rects((1920, 1080), (1920, 1080), ActiveFrame(1920, 1080, 0, 0))
        self.assertEqual(rects.content, QRect(0, 0, 1920, 1080))
        self.assertEqual(rects.kept, QRect(0, 0, 1920, 1080))
        self.assertEqual(rects.cuts, ())

    def test_top_and_bottom_bars_are_cut(self) -> None:
        rects = crop_overlay_rects((1920, 1080), (1920, 1080), ActiveFrame(1920, 800, 0, 140))
        self.assertEqual(rects.kept, QRect(0, 140, 1920, 800))
        self.assertEqual(rects.cuts, (QRect(0, 0, 1920, 140), QRect(0, 940, 1920, 140)))
        self.assertEqual(_total_area(rects), 1920 * 1080)

    def test_left_and_right_bars_are_cut(self) -> None:
        rects = crop_overlay_rects((1920, 1080), (1920, 1080), ActiveFrame(1600, 1080, 160, 0))
        self.assertEqual(rects.kept, QRect(160, 0, 1600, 1080))
        self.assertEqual(rects.cuts, (QRect(0, 0, 160, 1080), QRect(1760, 0, 160, 1080)))
        self.assertEqual(_total_area(rects), 1920 * 1080)

    def test_view_aspect_difference_letterboxes_the_content(self) -> None:
        rects = crop_overlay_rects((1000, 1000), (1920, 1080), ActiveFrame(1920, 1080, 0, 0))
        self.assertEqual(rects.content, QRect(0, 219, 1000, 562))
        # - 내용 영역은 위아래가 같은 여백으로 가운데 놓임
        self.assertEqual(rects.content.top(), 1000 - rects.content.top() - rects.content.height())

    def test_non_integer_scale_rounds_margins(self) -> None:
        rects = crop_overlay_rects((800, 450), (1920, 1080), ActiveFrame(1800, 1000, 60, 40))
        self.assertEqual(rects.content, QRect(0, 0, 800, 450))
        self.assertEqual(rects.kept, QRect(25, 17, 750, 416))
        self.assertEqual(_total_area(rects), 800 * 450)

    def test_margins_plus_kept_fill_the_content_rect(self) -> None:
        cases: tuple[tuple[tuple[int, int], tuple[int, int], ActiveFrame], ...] = (
            ((640, 480), (1920, 1080), ActiveFrame(1920, 800, 0, 140)),
            ((1280, 720), (1440, 1080), ActiveFrame(1200, 1000, 120, 40)),
            ((333, 777), (1080, 1920), ActiveFrame(1000, 1800, 40, 60)),
        )
        for view, video, active in cases:
            with self.subTest(view=view, video=video):
                rects = crop_overlay_rects(view, video, active)
                self.assertEqual(
                    _total_area(rects),
                    rects.content.width() * rects.content.height(),
                )
                for cut in rects.cuts:
                    self.assertTrue(rects.content.contains(cut))
                self.assertTrue(rects.content.contains(rects.kept))

    def test_active_frame_outside_the_video_is_clamped(self) -> None:
        rects = crop_overlay_rects((1920, 1080), (1920, 1080), ActiveFrame(1920, 1080, 200, 200))
        self.assertEqual(rects.kept.right(), 1919)
        self.assertEqual(rects.kept.bottom(), 1079)
        self.assertEqual(_total_area(rects), 1920 * 1080)

class CropOverlayWidgetTests(GuiTestCase):
    """겹침 창의 표시와 그림"""

    def setUp(self) -> None:
        self.overlay = CropOverlay(None, lambda: QRect(10, 20, 400, 300))
        self.addCleanup(self.overlay.deleteLater)

    def test_setting_crop_shows_the_overlay_and_none_hides_it(self) -> None:
        self.assertFalse(self.overlay.isVisible())
        rects = crop_overlay_rects((400, 300), (400, 300), ActiveFrame(400, 200, 0, 50))
        self.overlay.set_crop(rects)
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.geometry(), QRect(10, 20, 400, 300))
        self.overlay.set_crop(None)
        self.assertFalse(self.overlay.isVisible())

    def test_reposition_follows_the_video_rect(self) -> None:
        areas: list[QRect] = [QRect(0, 0, 100, 100)]
        overlay = CropOverlay(None, lambda: areas[0])
        self.addCleanup(overlay.deleteLater)
        overlay.set_crop(crop_overlay_rects((100, 100), (100, 100), ActiveFrame(100, 80, 0, 10)))
        self.assertEqual(overlay.geometry(), QRect(0, 0, 100, 100))
        areas[0] = QRect(30, 40, 200, 150)
        overlay.reposition()
        self.assertEqual(overlay.geometry(), QRect(30, 40, 200, 150))

    def test_painting_margins_border_and_label_does_not_fail(self) -> None:
        cases: tuple[ActiveFrame, ...] = (
            ActiveFrame(1920, 800, 0, 140),
            ActiveFrame(1000, 1080, 0, 0),
            ActiveFrame(1920, 1080, 0, 0),
        )
        self.overlay.resize(400, 300)
        for active in cases:
            with self.subTest(active=active):
                self.overlay.set_crop(crop_overlay_rects((400, 300), (1920, 1080), active))
                self.assertFalse(self.overlay.grab().isNull())

    def test_overlay_ignores_mouse_input(self) -> None:
        self.assertTrue(self.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(self.overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))


class CropButtonTests(GuiTestCase):
    """하단 바의 여백 미리 보기 버튼"""

    def setUp(self) -> None:
        self.controls = PlaybackControls()
        self.addCleanup(self.controls.deleteLater)
        self.controls.show()
        _APP.processEvents()

    def test_button_has_accessible_name_and_approved_tooltip(self) -> None:
        button = self.controls.crop_button
        self.assertEqual(button.accessibleName(), "여백 잘라내기 미리 보기")
        self.assertEqual(button.toolTip(), "인코딩할 때 잘라낼 검은 여백을 영상 위에 표시합니다.")
        self.assertEqual(button.objectName(), "PreviewCropButton")
        self.assertTrue(button.isCheckable())
        self.assertFalse(button.icon().isNull())

    def test_button_is_hidden_until_a_crop_target_appears(self) -> None:
        button = self.controls.crop_button
        self.assertTrue(button.isHidden())
        self.controls.set_crop_available(True)
        self.assertFalse(button.isHidden())
        button.setChecked(True)
        self.controls.set_crop_available(False)
        self.assertTrue(button.isHidden())
        self.assertFalse(button.isChecked())

    def test_toggle_emits_the_new_state(self) -> None:
        states: list[bool] = []
        self.controls.crop_preview_toggled.connect(states.append)
        self.controls.crop_button.setChecked(True)
        self.controls.crop_button.setChecked(False)
        self.assertEqual(states, [True, False])


def _window() -> VideoPreviewWindow:
    state = UiState()
    return VideoPreviewWindow(
        lambda: state.preview_seek_seconds,
        lambda: (state.preview_volume, state.preview_muted),
        lambda _volume, _muted: None,
        lambda: DEFAULT_SHORTCUTS,
        lambda _text: False,
        lambda: state.preview_info,
        lambda: None,
        lambda: state.preview_playback_rate,
        lambda _rate: None,
        lambda: state.preview_fullscreen,
        lambda _fullscreen: None,
        lambda: state.preview_always_on_top,
        lambda _pinned: None,
        PreviewPositionMemory(),
    )


def _crop_info() -> PreviewVideoInfo:
    ev = make_eval(make_video(), active_frame=ActiveFrame(1920, 800, 0, 140))
    return PreviewVideoInfo(ev, ClassifierConfig(), {}, 1)


def _plain_info() -> PreviewVideoInfo:
    return PreviewVideoInfo(make_eval(), ClassifierConfig(), {}, 1)


def _crop_button(window: VideoPreviewWindow) -> QToolButton:
    """창의 여백 미리 보기 버튼"""
    button = window.findChild(QToolButton, "PreviewCropButton")
    assert button is not None
    return button


def _overlay(window: VideoPreviewWindow) -> CropOverlay:
    """창의 여백 겹침 위젯"""
    overlay = window.findChild(CropOverlay)
    assert overlay is not None
    return overlay


class CropPreviewWindowTests(GuiTestCase):
    """창의 버튼 표시와 겹침 상태"""

    def setUp(self) -> None:
        self.window = _window()
        self.addCleanup(self.window.close)
        self.window.show()
        _APP.processEvents()

    def test_button_hidden_when_the_video_has_no_crop(self) -> None:
        self.window.set_video_info(_plain_info())
        self.assertTrue(_crop_button(self.window).isHidden())

    def test_button_visible_and_checkable_for_a_crop_target(self) -> None:
        self.window.set_video_info(_crop_info())
        button = _crop_button(self.window)
        self.assertFalse(button.isHidden())
        self.assertTrue(button.isCheckable())

    def test_toggling_shows_and_hides_the_overlay(self) -> None:
        self.window.set_video_info(_crop_info())
        overlay = _overlay(self.window)
        button = _crop_button(self.window)
        self.assertFalse(overlay.isVisible())
        button.setChecked(True)
        _APP.processEvents()
        self.assertTrue(overlay.isVisible())
        button.setChecked(False)
        _APP.processEvents()
        self.assertFalse(overlay.isVisible())

    def test_switching_to_a_non_target_hides_button_and_overlay(self) -> None:
        self.window.set_video_info(_crop_info())
        button = _crop_button(self.window)
        overlay = _overlay(self.window)
        button.setChecked(True)
        _APP.processEvents()
        self.assertTrue(overlay.isVisible())
        self.window.set_video_info(_plain_info())
        _APP.processEvents()
        self.assertTrue(button.isHidden())
        self.assertFalse(overlay.isVisible())

    def test_state_survives_switching_between_crop_targets(self) -> None:
        self.window.set_video_info(_crop_info())
        button = _crop_button(self.window)
        overlay = _overlay(self.window)
        button.setChecked(True)
        _APP.processEvents()
        self.window.set_video_info(_crop_info())
        _APP.processEvents()
        self.assertTrue(button.isChecked())
        self.assertTrue(overlay.isVisible())

    def test_compare_mode_hides_the_button_and_the_overlay(self) -> None:
        self.window.set_video_info(_crop_info())
        button = _crop_button(self.window)
        overlay = _overlay(self.window)
        button.setChecked(True)
        _APP.processEvents()
        with mock.patch.object(
            VideoPreviewWindow, "compare_mode", new_callable=mock.PropertyMock, return_value=True
        ):
            self.window.set_video_info(_crop_info())
            _APP.processEvents()
            self.assertTrue(button.isHidden())
            self.assertFalse(overlay.isVisible())
        self.window.set_video_info(_crop_info())
        _APP.processEvents()
        self.assertFalse(button.isHidden())
        self.assertFalse(overlay.isVisible())

    def test_hiding_the_window_hides_the_overlay(self) -> None:
        self.window.set_video_info(_crop_info())
        overlay = _overlay(self.window)
        button = _crop_button(self.window)
        button.setChecked(True)
        _APP.processEvents()
        self.assertTrue(overlay.isVisible())
        self.window.hide()
        _APP.processEvents()
        self.assertFalse(overlay.isVisible())
        self.assertFalse(button.isChecked())

    def test_overlay_follows_window_resize_and_move(self) -> None:
        self.window.set_video_info(_crop_info())
        button = _crop_button(self.window)
        overlay = _overlay(self.window)
        button.setChecked(True)
        _APP.processEvents()
        self.window.resize(1000, 700)
        _APP.processEvents()
        video_rect = QRect(self.window._video.mapToGlobal(QPoint(0, 0)), self.window._video.size())
        self.assertEqual(overlay.geometry(), video_rect)
        self.window.move(self.window.pos() + QPoint(40, 30))
        _APP.processEvents()
        moved_rect = QRect(self.window._video.mapToGlobal(QPoint(0, 0)), self.window._video.size())
        self.assertEqual(overlay.geometry(), moved_rect)


if __name__ == "__main__":
    unittest.main()

