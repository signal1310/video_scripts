"""미리 보기 영상 위쪽 가운데에 잠깐 떠 있는 활성 화면 편집 도구 모음

- 여백 미리 보기를 켠 동안에만 보이고, 끄면 함께 사라짐
- 영상 표면이 네이티브 창이라 반투명 도구 창을 영상 위에 올리고, 배경은 안쪽 틀이 그림
- 창을 활성화하지 않아 편집 중에도 미리 보기 창이 키 입력을 계속 받음
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.core.models import ActiveFrame
from src.gui.preview.crop_actions import CropActionButtons
from src.gui.preview.crop_edit import HANDLE_EDGE_PX
from src.gui.preview.video_preview_overlay import EDGE_MARGIN_PX

CROP_HUD_FRAME_OBJECT: str = "PreviewCropHudFrame"
CROP_HUD_SIZE_OBJECT: str = "PreviewCropHudSize"
_HUD_PADDING_PX: int = 6
_HUD_SPACING_PX: int = 6
# - 영상 위쪽 가운데 손잡이를 덮지 않게 손잡이 판정 폭만큼 더 내려 놓음
HUD_TOP_GAP_PX: int = EDGE_MARGIN_PX + HANDLE_EDGE_PX


def hud_position(area: QRect, size: QSize, *, margin: int = HUD_TOP_GAP_PX) -> QPoint:
    """영역 위쪽 가운데에 도구 모음을 놓을 때의 화면 전역 왼쪽 위 좌표

    - 폭이 모자라면 영역 왼쪽에 붙여 잘라내지 않음
    """
    left = area.left() + max((area.width() - size.width()) // 2, 0)
    return QPoint(left, area.top() + margin)


class CropEditHud(QWidget):
    """유지 영역 크기 표기와 자동 감지 복귀, 전체 화면 유지 아이콘 버튼을 담는 HUD"""

    def __init__(self, parent: QWidget | None, get_area: Callable[[], QRect]) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self._get_area = get_area
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._actions = CropActionButtons(self)
        self._size_label = QLabel(self)
        self._size_label.setObjectName(CROP_HUD_SIZE_OBJECT)
        self._size_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        body = QFrame(self)
        body.setObjectName(CROP_HUD_FRAME_OBJECT)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(
            _HUD_PADDING_PX, _HUD_PADDING_PX, _HUD_PADDING_PX, _HUD_PADDING_PX
        )
        body_layout.setSpacing(_HUD_SPACING_PX)
        body_layout.addWidget(self._size_label)
        body_layout.addWidget(self._actions)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(body)
        self.hide()

    @property
    def action_buttons(self) -> CropActionButtons:
        """자동 감지 복귀와 전체 화면 유지 요청을 내보내는 버튼 묶음"""
        return self._actions

    @property
    def size_label(self) -> QLabel:
        """지금 유지 영역 크기를 `너비 x 높이`로 보여 주는 표기"""
        return self._size_label

    def set_kept(self, frame: ActiveFrame) -> None:
        """유지 영역 크기 표기를 바꿈"""
        self._size_label.setText(f"{frame.width} x {frame.height}")

    def refresh_icons(self) -> None:
        """현재 테마 팔레트로 두 아이콘을 다시 그림"""
        self._actions.refresh_icons()

    def show_hud(self) -> None:
        """도구 모음을 영상 위쪽 가운데에 놓고 표시"""
        self._actions.show()
        self.adjustSize()
        self._move_to_area()
        self.show()
        self.raise_()

    def reposition(self) -> None:
        """영상 영역이 바뀌면 도구 모음을 다시 맞춤"""
        if not self.isVisible():
            return
        self._move_to_area()

    def _move_to_area(self) -> None:
        self.move(hud_position(self._get_area(), self.sizeHint()))
