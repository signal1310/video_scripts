"""미리 보기 창의 활성 화면 직접 편집 배선, 되돌리기 단축키, 되돌리기 버튼 검증"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.core.settings.ui_state import UiState
from src.gui.preview.preview_controller import PreviewPositionMemory
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.video_preview_window import VideoPreviewWindow
from src.gui.styles.icons import SVG_TEMPLATES, get_icon
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_DEFAULT_FRAME = ActiveFrame(1920, 800, 0, 140)


def _window(
    on_manual: list[tuple[VideoEvaluation, ActiveFrame | None]] | None = None,
    on_exclusion: list[tuple[VideoEvaluation, bool]] | None = None,
) -> VideoPreviewWindow:
    """직접 지정 저장과 잘라내기 제외 요청을 기록하는 미리 보기 창"""
    state = UiState()
    manual = on_manual if on_manual is not None else []
    exclusion = on_exclusion if on_exclusion is not None else []
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
        apply_manual_active_frame=lambda ev, frame: manual.append((ev, frame)),
        apply_crop_exclusion=lambda ev, excluded: exclusion.append((ev, excluded)),
    )


def _info(active_frame: ActiveFrame | None = _DEFAULT_FRAME) -> PreviewVideoInfo:
    ev = make_eval(make_video(), active_frame=active_frame)
    return PreviewVideoInfo(ev, ClassifierConfig(), {}, 1, has_frame_analysis=True)


def _overlay_info() -> PreviewVideoInfo:
    """검은 띠가 없어 활성 화면이 비어 있는 영상"""
    return _info(active_frame=None)


def _mouse(event_type: QEvent.Type, point: QPoint) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    return QMouseEvent(
        event_type,
        QPointF(point),
        QPointF(point),
        button,
        button if event_type != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drag(video: QWidget, start: QPoint, end: QPoint) -> None:
    """영상 위젯에 누름, 이동, 뗌을 순서대로 보냄"""
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonPress, start))
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseMove, end))
    QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonRelease, end))



class CropEditWindowTests(GuiTestCase):
    """창이 버튼과 마우스 요청을 컨트롤러 경로로 넘기는지 검증"""

    def setUp(self) -> None:
        self.manual: list[tuple[VideoEvaluation, ActiveFrame | None]] = []
        self.exclusion: list[tuple[VideoEvaluation, bool]] = []
        self.window = _window(self.manual, self.exclusion)
        self.addCleanup(self.window.close)
        self.window.set_video_info(_info())
        self.window.show()
        _APP.processEvents()
        self.window._controls.crop_button.setChecked(True)
        _APP.processEvents()

    def test_dragging_inside_the_overlay_saves_the_new_frame(self) -> None:
        rects = self.window._crop_edit.rects()
        assert rects is not None
        center = rects.kept.center()
        _drag(self.window._video, center, QPoint(center.x(), center.y() + 20))
        self.assertEqual(len(self.manual), 1)
        ev, frame = self.manual[0]
        self.assertEqual(ev.video.filename, "clip.mp4")
        assert frame is not None
        self.assertGreater(frame.y, 140)

    def test_dragging_a_handle_resizes_the_frame(self) -> None:
        rects = self.window._crop_edit.rects()
        assert rects is not None
        edge = QPoint(rects.kept.left() + rects.kept.width(), rects.kept.center().y())
        _drag(self.window._video, edge, QPoint(edge.x() - 40, edge.y()))
        _, frame = self.manual[0]
        assert frame is not None
        self.assertLess(frame.width, 1920)

    def test_full_frame_target_shows_the_whole_frame_and_saves_the_drag(self) -> None:
        self.manual.clear()
        self.window.set_video_info(_overlay_info())
        _APP.processEvents()
        rects = self.window._crop_edit.rects()
        assert rects is not None
        self.assertEqual(rects.kept, rects.content)
        edge = QPoint(rects.content.left() + rects.content.width(), rects.content.center().y())
        _drag(self.window._video, edge, QPoint(edge.x() - 100, edge.y()))
        self.assertEqual(len(self.manual), 1)
        saved = self.manual[0][1]
        assert saved is not None
        self.assertLess(saved.width, 1920)

    def test_escape_cancels_a_drag_without_saving(self) -> None:
        rects = self.window._crop_edit.rects()
        assert rects is not None
        center = rects.kept.center()
        video = self.window._video
        QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonPress, center))
        QApplication.sendEvent(
            video, _mouse(QEvent.Type.MouseMove, QPoint(center.x() + 30, center.y()))
        )
        QApplication.sendEvent(
            self.window,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, ""),
        )
        QApplication.sendEvent(video, _mouse(QEvent.Type.MouseButtonRelease, center))
        self.assertEqual(self.manual, [])
        self.assertTrue(self.window.isVisible())

    def test_ctrl_z_undoes_the_last_change(self) -> None:
        rects = self.window._crop_edit.rects()
        assert rects is not None
        center = rects.kept.center()
        _drag(self.window._video, center, QPoint(center.x(), center.y() + 20))
        self.assertEqual(len(self.manual), 1)
        self.addCleanup(release_modifier_keys)
        QApplication.sendEvent(
            self.window,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, ""),
        )
        self.assertEqual(len(self.manual), 2)
        self.assertEqual(self.manual[1][1], _DEFAULT_FRAME)

    def test_reset_button_clears_manual_and_exclusion(self) -> None:
        button = self.window.findChild(QToolButton, "PreviewCropResetButton")
        assert button is not None
        button.click()
        self.assertIsNone(self.manual[-1][1])
        self.assertFalse(self.exclusion[-1][1])

    def test_keep_full_button_clears_manual_and_sets_exclusion(self) -> None:
        button = self.window.findChild(QToolButton, "PreviewCropFullButton")
        assert button is not None
        button.click()
        self.assertIsNone(self.manual[-1][1])
        self.assertTrue(self.exclusion[-1][1])

    def test_reset_buttons_are_hidden_until_crop_preview(self) -> None:
        self.window._controls.crop_button.setChecked(False)
        _APP.processEvents()
        button = self.window.findChild(QToolButton, "PreviewCropResetButton")
        assert button is not None
        self.assertFalse(button.isVisible())

    def test_reset_buttons_use_icon_only_accessible_names(self) -> None:
        reset = self.window.findChild(QToolButton, "PreviewCropResetButton")
        full = self.window.findChild(QToolButton, "PreviewCropFullButton")
        assert reset is not None and full is not None
        self.assertEqual(reset.text(), "")
        self.assertEqual(full.text(), "")
        self.assertEqual(reset.accessibleName(), "자동 감지로 되돌리기")
        self.assertEqual(full.accessibleName(), "전체 화면 유지")
        self.assertEqual(reset.toolTip(), "자동으로 감지한 활성 화면으로 되돌립니다.")
        self.assertEqual(full.toolTip(), "이 영상은 잘라내지 않고 전체 화면을 유지합니다.")

    def test_new_crop_icons_exist_and_render(self) -> None:
        for name in ("crop-reset", "crop-full"):
            with self.subTest(name=name):
                self.assertIn(name, SVG_TEMPLATES)
                self.assertFalse(get_icon(name).pixmap(16, 16).isNull())

    def test_no_crop_preview_means_no_mouse_edit(self) -> None:
        self.window._controls.crop_button.setChecked(False)
        _APP.processEvents()
        rects = self.window._crop_edit.rects()
        assert rects is not None
        center = rects.kept.center()
        _drag(self.window._video, center, QPoint(center.x() + 20, center.y()))
        self.assertEqual(self.manual, [])

    def test_targets_set_after_creation_receive_the_drag(self) -> None:
        """창을 만든 뒤 연결한 대상도 끌기 저장과 제외 요청을 받는지 검증"""
        manual: list[tuple[VideoEvaluation, ActiveFrame | None]] = []
        exclusion: list[tuple[VideoEvaluation, bool]] = []
        window = VideoPreviewWindow(
            lambda: 0,
            lambda: (80, False),
            lambda _volume, _muted: None,
            lambda: DEFAULT_SHORTCUTS,
            lambda _text: False,
            lambda: UiState().preview_info,
            lambda: None,
            lambda: 1.0,
            lambda _rate: None,
            lambda: False,
            lambda _fullscreen: None,
            lambda: True,
            lambda _pinned: None,
            PreviewPositionMemory(),
        )
        self.addCleanup(window.close)
        window.set_crop_edit_targets(
            lambda ev, frame: manual.append((ev, frame)),
            lambda ev, excluded: exclusion.append((ev, excluded)),
        )
        window.set_video_info(_info())
        window.show()
        _APP.processEvents()
        window._controls.crop_button.setChecked(True)
        _APP.processEvents()
        rects = window._crop_edit.rects()
        assert rects is not None
        center = rects.kept.center()
        _drag(window._video, center, QPoint(center.x(), center.y() + 20))
        self.assertEqual(len(manual), 1)
        window._crop_edit.request_keep_full()
        self.assertTrue(exclusion[-1][1])
