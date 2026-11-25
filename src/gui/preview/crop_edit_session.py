"""미리 보기 영상 위에서 활성 화면을 직접 끄는 편집 세션 모듈

- 겹침 창은 입력을 받지 않으므로 영상 위젯의 마우스 사건을 대신 받아 손잡이 끌기를 처리
- 저장과 되돌리기는 신호로만 알리고, 실제 저장은 창이 컨트롤러에 요청
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QContextMenuEvent, QMouseEvent
from PyQt6.QtWidgets import QMenu, QWidget

from src.core.models import ActiveFrame
from src.gui.preview.crop_actions import (
    CROP_FULL_ICON,
    CROP_FULL_TEXT,
    CROP_FULL_TIP,
    CROP_RESET_ICON,
    CROP_RESET_TEXT,
    CROP_RESET_TIP,
)
from src.gui.preview.crop_edit import (
    CropHandle,
    FrameHistory,
    cursor_for,
    drag_frame,
    full_active_frame,
    hit_test,
)
from src.gui.preview.crop_overlay import CropOverlayRects, crop_overlay_rects
from src.gui.styles.icons import get_active_palette, get_icon

UNDO_DONE_NOTICE: str = "사용자 지정 활성 화면을 되돌렸습니다."
_ICON_SIZE: int = 16


class CropEditSession(QObject):
    """영상 위젯의 마우스로 유지 영역을 끄는 편집 상태, 되돌리기 스택, 우클릭 메뉴"""

    preview_changed = pyqtSignal()
    commit_requested = pyqtSignal(object)
    reset_auto_requested = pyqtSignal()
    keep_full_requested = pyqtSignal()
    undo_requested = pyqtSignal(object)

    def __init__(self, video: QWidget, menu_parent: QWidget) -> None:
        super().__init__(video)
        self._video = video
        self._menu_parent = menu_parent
        self._history = FrameHistory()
        self._enabled = False
        self._path = ""
        self._video_size: tuple[int, int] | None = None
        self._frame = ActiveFrame(0, 0, 0, 0)
        self._table_frame = self._frame
        self._dirty = False
        self._handle: CropHandle | None = None
        self._start_frame: ActiveFrame | None = None
        self._start_point = QPoint()
        self._dragging = False
        video.installEventFilter(self)

    @property
    def frame(self) -> ActiveFrame:
        """지금 겹침에 그리는 유지 영역, 영상이 없으면 0 크기"""
        return self._frame

    @property
    def is_dragging(self) -> bool:
        """끌기 중인지"""
        return self._dragging

    def has_undo(self) -> bool:
        """이 창에서 되돌릴 변경이 남아 있는지"""
        return self._history.can_undo()

    def set_enabled(self, enabled: bool) -> None:
        """여백 미리 보기 표시 동안만 마우스 편집을 받음"""
        self._enabled = enabled
        self._video.setMouseTracking(enabled)
        if not enabled:
            self._dragging = False
            self._handle = None
            self._video.unsetCursor()

    def set_video(
        self, path: str, video_size: tuple[int, int] | None, frame: ActiveFrame | None
    ) -> None:
        """지금 영상의 크기와 유지 영역을 반영

        - 영상이 바뀌면 되돌리기 스택을 비워 다른 영상의 값을 되살리지 않게 함
        - 표에서 온 값이 지난번과 같으면 끌기 중이거나 저장 직후의 화면을 덮어쓰지 않음
        - 표 값이 달라졌으면(재평가 뒤) 그 값을 받아들여 화면과 열이 어긋나지 않게 함
        """
        self._video_size = video_size
        if path != self._path:
            self._path = path
            self._history.clear()
            self._dirty = False
            self._table_frame = frame or full_active_frame(video_size or (0, 0))
            self._frame = self._table_frame
            return
        if video_size is None:
            return
        incoming = frame or full_active_frame(video_size)
        if incoming != self._table_frame:
            self._table_frame = incoming
            self._frame = incoming
            self._dirty = False
        elif not self._dirty:
            self._frame = incoming

    def cancel_drag(self) -> bool:
        """끌던 중이면 시작 값으로 되돌리고 참, 저장은 하지 않음"""
        if not self._dragging:
            return False
        self._dragging = False
        self._handle = None
        if self._start_frame is not None:
            self._frame = self._start_frame
            self._dirty = True
        self._start_frame = None
        self.preview_changed.emit()
        return True

    def undo(self) -> bool:
        """이 창에서 마지막으로 바꾼 값을 되돌리도록 요청, 되돌릴 값이 없으면 거짓"""
        if not self._history.can_undo():
            return False
        previous = self._history.pop()
        self._frame = previous or full_active_frame(self._video_size or (0, 0))
        self._table_frame = self._frame
        self._dirty = False
        self.preview_changed.emit()
        self.undo_requested.emit(previous)
        return True

    def rects(self) -> CropOverlayRects | None:
        """지금 유지 영역의 겹침 배치, 영상 정보가 없으면 None"""
        if self._video_size is None:
            return None
        return crop_overlay_rects(
            (self._video.width(), self._video.height()), self._video_size, self._frame
        )

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """영상 위젯의 마우스로 손잡이 끌기와 우클릭 메뉴를 처리"""
        if a0 is not self._video or a1 is None or not self._enabled or self._video_size is None:
            return super().eventFilter(a0, a1)
        event_type = a1.type()
        if event_type == QEvent.Type.MouseButtonPress and isinstance(a1, QMouseEvent):
            if a1.button() != Qt.MouseButton.LeftButton:
                return super().eventFilter(a0, a1)
            return self._on_press(a1.position().toPoint())
        if event_type == QEvent.Type.MouseMove and isinstance(a1, QMouseEvent):
            return self._on_move(a1.position().toPoint())
        if event_type == QEvent.Type.MouseButtonRelease and isinstance(a1, QMouseEvent):
            if a1.button() != Qt.MouseButton.LeftButton:
                return super().eventFilter(a0, a1)
            return self._on_release()
        if event_type == QEvent.Type.ContextMenu and isinstance(a1, QContextMenuEvent):
            self._show_menu(a1.globalPos())
            return True
        return super().eventFilter(a0, a1)

    def _kept_rect(self) -> QRect:
        """지금 유지 영역의 위젯 좌표 사각형"""
        rects = self.rects()
        assert rects is not None
        return rects.kept

    def _on_press(self, point: QPoint) -> bool:
        """손잡이를 잡으면 끌기를 시작하고, 빈 곳이면 기본 처리를 그대로 둠"""
        handle = hit_test(self._kept_rect(), point)
        if handle is None:
            return False
        self._handle = handle
        self._start_frame = self._frame
        self._start_point = point
        self._dragging = True
        self._dirty = True
        self._video.setCursor(cursor_for(handle))
        return True

    def _on_move(self, point: QPoint) -> bool:
        """끌기 중이면 유지 영역을 갱신하고, 아니면 손잡이 모양으로 커서만 바꿈"""
        if not self._dragging or self._handle is None or self._start_frame is None:
            self._video.setCursor(cursor_for(hit_test(self._kept_rect(), point)))
            return False
        rects = self.rects()
        assert rects is not None and self._video_size is not None
        self._frame = drag_frame(
            self._handle,
            self._start_frame,
            self._start_point,
            point,
            rects.content,
            self._video_size,
        )
        self.preview_changed.emit()
        return True

    def _on_release(self) -> bool:
        """끌기를 끝내고, 값이 달라졌으면 저장을 요청"""
        if not self._dragging:
            return False
        self._dragging = False
        previous = self._start_frame
        self._handle = None
        self._start_frame = None
        if previous is None or previous == self._frame:
            return True
        self._history.push(previous)
        self.commit_requested.emit(self._frame)
        return True

    def _show_menu(self, global_pos: QPoint) -> None:
        """영상 우클릭 메뉴로 자동 감지 복귀와 전체 화면 유지를 제공"""
        menu = QMenu(self._menu_parent)
        palette = get_active_palette()
        reset = QAction(CROP_RESET_TEXT, menu)
        reset.setIcon(get_icon(CROP_RESET_ICON, color=palette.text_primary, size=_ICON_SIZE))
        reset.setToolTip(CROP_RESET_TIP)
        reset.triggered.connect(self.request_reset_auto)
        full = QAction(CROP_FULL_TEXT, menu)
        full.setIcon(get_icon(CROP_FULL_ICON, color=palette.text_primary, size=_ICON_SIZE))
        full.setToolTip(CROP_FULL_TIP)
        full.triggered.connect(self.request_keep_full)
        menu.addAction(reset)
        menu.addAction(full)
        menu.exec(global_pos)

    def request_reset_auto(self) -> None:
        """자동 감지 복귀를 요청하고 지금 값을 되돌리기 스택에 쌓음"""
        self._history.push(self._frame)
        self._dirty = True
        self.reset_auto_requested.emit()

    def request_keep_full(self) -> None:
        """전체 화면 유지를 요청하고 겹침을 전체 프레임으로 바꿈"""
        self._history.push(self._frame)
        self._frame = full_active_frame(self._video_size or (0, 0))
        self._table_frame = self._frame
        self._dirty = True
        self.preview_changed.emit()
        self.keep_full_requested.emit()

