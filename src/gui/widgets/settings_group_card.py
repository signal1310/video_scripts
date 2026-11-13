
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


class SettingsGroupCard(QWidget, ThemeAwareMixin):
    """
    - 설정 그룹 위젯.
    """

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsGroupCard")
        self.icon_name = icon_name
        self.title_text = title
        self.subtitle_text = subtitle

        self._rows: list[QWidget] = []
        self._init_ui()
        self.init_theme_listener()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        # 1. 상단 헤더 (아이콘 + 제목 + 설명 문구)
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(4, 2, 4, 2)
        h_layout.setSpacing(8)

        self.lbl_icon = QLabel()
        self.lbl_icon.setFixedSize(16, 16)
        h_layout.addWidget(self.lbl_icon)

        self.lbl_title = QLabel(self.title_text)
        self.lbl_title.setObjectName("GroupHeaderTitle")
        h_layout.addWidget(self.lbl_title)

        if self.subtitle_text:
            self.lbl_subtitle = QLabel(self.subtitle_text)
            self.lbl_subtitle.setObjectName("GroupHeaderSubtitle")
            h_layout.addWidget(self.lbl_subtitle)

        h_layout.addStretch(1)
        root_layout.addWidget(header_widget)

        # 2. 설정 행 컨테이너
        self.card_box = QFrame(self)
        self.card_box.setObjectName("SettingsCardBox")
        self.body_layout = QVBoxLayout(self.card_box)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        root_layout.addWidget(self.card_box)

        self._update_icon(get_active_palette())

    def add_row(self, row_widget: QWidget) -> None:
        """
        - 설정 행 위젯을 추가하고 첫/마지막 행의 스타일 속성을 갱신.
        """
        self._rows.append(row_widget)
        self.body_layout.addWidget(row_widget)
        self._update_row_styling()

    def _update_row_styling(self) -> None:
        total = len(self._rows)
        for i, r in enumerate(self._rows):
            is_first = i == 0
            is_last = i == (total - 1)
            r.setProperty("is_first_row", "true" if is_first else "false")
            r.setProperty("is_last_row", "true" if is_last else "false")
            style = r.style()
            if style is not None:
                style.unpolish(r)
                style.polish(r)

    def _update_icon(self, palette: ThemePalette) -> None:
        if self.icon_name:
            self.lbl_icon.setPixmap(
                get_icon(self.icon_name, color=palette.accent_blue, size=15).pixmap(16, 16)
            )

    def apply_theme(self, palette: ThemePalette) -> None:
        self._update_icon(palette)
