from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class TwistieHeader(QFrame):
    """
    - 마우스 클릭 및 키보드(Space, Enter) 포커스 선택을 지원하는 헤더 프레임.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TwistieHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
        super().keyPressEvent(event)


class CollapsibleSection(QWidget, ThemeAwareMixin):
    """
    - 클릭하여 본문 영역을 접거나 펼칠 수 있는 섹션 위젯.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        is_expanded: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._is_expanded = is_expanded

        self._init_ui()
        self.init_theme_listener()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        # 1. 헤더 (화살표 아이콘 + 제목 + 설명 문구)
        self.header = TwistieHeader(self)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(6, 6, 8, 6)
        h_layout.setSpacing(8)

        self.lbl_chevron = QLabel()
        self.lbl_chevron.setFixedSize(16, 16)
        self.lbl_chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        h_layout.addWidget(self.lbl_chevron)

        self.lbl_title = QLabel(self._title)
        self.lbl_title.setObjectName("TwistieTitle")
        self.lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        h_layout.addWidget(self.lbl_title)

        if self._subtitle:
            self.lbl_sub = QLabel(self._subtitle)
            self.lbl_sub.setObjectName("TwistieSubtitle")
            self.lbl_sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            h_layout.addWidget(self.lbl_sub)

        h_layout.addStretch(1)

        self.header.clicked.connect(self.toggle)
        root_layout.addWidget(self.header)

        # 2. 본문 컨테이너
        self.content_widget = QWidget(self)
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 6, 0, 4)
        self.content_layout.setSpacing(14)
        root_layout.addWidget(self.content_widget)

        self.set_expanded(self._is_expanded)
        self._update_icons(get_active_palette())

    def toggle(self) -> None:
        self.set_expanded(not self._is_expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._is_expanded = expanded
        self.content_widget.setVisible(expanded)
        self._update_icons(get_active_palette())
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self._is_expanded

    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def _update_icons(self, palette: ThemePalette) -> None:
        icon_name = "chevron-down" if self._is_expanded else "chevron-right"
        color = palette.text_primary if self._is_expanded else palette.text_secondary
        self.lbl_chevron.setPixmap(
            get_icon(icon_name, color=color, size=15).pixmap(16, 16)
        )

    def apply_theme(self, palette: ThemePalette) -> None:
        self._update_icons(palette)
