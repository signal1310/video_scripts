"""여백 미리 보기에서 유지 영역을 자동 감지나 전체 화면으로 되돌리는 아이콘 버튼 모듈

- 영상 위에 뜨는 편집 HUD(`crop_hud`)에 놓여 여백 미리 보기를 켠 동안에만 보임
- 접근성 이름은 명사구, 툴팁은 존댓말 한 문장
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from src.gui.styles.icons import get_active_palette, get_icon

CROP_RESET_TEXT: str = "자동 감지로 되돌리기"
CROP_RESET_TIP: str = "자동으로 감지한 활성 화면으로 되돌립니다."
CROP_FULL_TEXT: str = "전체 화면 유지"
CROP_FULL_TIP: str = "이 영상은 잘라내지 않고 전체 화면을 유지합니다."
CROP_RESET_ICON: str = "crop-reset"
CROP_FULL_ICON: str = "crop-full"
_BUTTON_SIZE: int = 26
_ICON_SIZE: int = 16
_BUTTON_SPACING: int = 2


class CropActionButtons(QWidget):
    """유지 영역 되돌리기 두 아이콘 버튼을 담는 조각"""

    reset_requested = pyqtSignal()
    keep_full_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewCropActions")
        self._btn_reset = self._icon_button("PreviewCropResetButton", CROP_RESET_TEXT)
        self._btn_full = self._icon_button("PreviewCropFullButton", CROP_FULL_TEXT)
        self._btn_reset.clicked.connect(lambda _checked=False: self.reset_requested.emit())
        self._btn_full.clicked.connect(lambda _checked=False: self.keep_full_requested.emit())
        self._btn_reset.setToolTip(CROP_RESET_TIP)
        self._btn_full.setToolTip(CROP_FULL_TIP)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_BUTTON_SPACING)
        layout.addWidget(self._btn_reset)
        layout.addWidget(self._btn_full)
        self.refresh_icons()
        self.hide()

    def _icon_button(self, name: str, accessible_name: str) -> QToolButton:
        """아이콘만 있는 조작 버튼, 재생 바 기준 크기와 커서를 맞춤"""
        button = QToolButton(self)
        button.setObjectName(name)
        button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(accessible_name)
        return button

    @property
    def reset_button(self) -> QToolButton:
        """자동 감지로 되돌리는 아이콘 버튼"""
        return self._btn_reset

    @property
    def keep_full_button(self) -> QToolButton:
        """전체 화면 유지 아이콘 버튼"""
        return self._btn_full

    def refresh_icons(self) -> None:
        """현재 테마 팔레트로 두 아이콘을 다시 그림"""
        palette = get_active_palette()
        self._btn_reset.setIcon(
            get_icon(CROP_RESET_ICON, color=palette.media_fg, size=_ICON_SIZE)
        )
        self._btn_full.setIcon(get_icon(CROP_FULL_ICON, color=palette.media_fg, size=_ICON_SIZE))
