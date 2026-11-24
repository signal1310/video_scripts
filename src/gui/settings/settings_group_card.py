"""설정 그룹 헤더와 설정 행 목록을 감싸는 카드 컨테이너 위젯 모듈"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.settings.setting_controls import HELP_ICON_SIZE, create_help_icon_button
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class SettingsGroupCard(QWidget, ThemeAwareMixin):
    """설정 그룹 위젯"""

    help_requested = pyqtSignal(str)

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        help_anchor: str = "",
        help_tooltip: str = "",
        help_accessible_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsGroupCard")
        self.icon_name = icon_name
        self.title_text = title
        self.subtitle_text = subtitle
        self.help_anchor = help_anchor
        self.help_tooltip = help_tooltip or (
            f"{title} 항목의 계산 원리를 사용 가이드에서 엽니다."
        )
        self.help_accessible_name = help_accessible_name or f"{title} 설명 보기"

        self._rows: list[QWidget] = []
        self.btn_help: QPushButton | None = None
        self._init_ui()
        self.init_theme_listener()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(4)

        # 1. 상단 헤더 (아이콘 + 제목 + 설명 문구 + 도움말 버튼)
        header_widget = QWidget(self)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(2, 0, 2, 0)
        h_layout.setSpacing(6)

        self.lbl_icon = QLabel(header_widget)
        self.lbl_icon.setFixedSize(16, 16)
        h_layout.addWidget(self.lbl_icon)

        self.lbl_title = QLabel(self.title_text, header_widget)
        self.lbl_title.setObjectName("GroupHeaderTitle")
        h_layout.addWidget(self.lbl_title)

        if self.subtitle_text:
            self.lbl_subtitle = QLabel(self.subtitle_text, header_widget)
            self.lbl_subtitle.setObjectName("GroupHeaderSubtitle")
            h_layout.addWidget(self.lbl_subtitle)

        h_layout.addStretch(1)
        if self.help_anchor:
            self.btn_help = create_help_icon_button(
                header_widget,
                self.help_tooltip,
                self._emit_help,
                accessible_name=self.help_accessible_name,
            )
            h_layout.addWidget(self.btn_help)
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
        """설정 행 위젯을 추가하고 첫/마지막 행의 스타일 속성을 갱신"""
        self._rows.append(row_widget)
        self.body_layout.addWidget(row_widget)
        self._update_row_styling()

    def _emit_help(self) -> None:
        """카드 도움말 버튼 요청을 사용 가이드 앵커와 함께 알림"""
        self.help_requested.emit(self.help_anchor)

    def _update_row_styling(self) -> None:
        total = len(self._rows)
        visible_card = self.isVisible()
        for i, r in enumerate(self._rows):
            is_first = i == 0
            is_last = i == (total - 1)
            r.setProperty("is_first_row", "true" if is_first else "false")
            r.setProperty("is_last_row", "true" if is_last else "false")
            if visible_card:
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
        """활성 테마 팔레트 기반 그룹 헤더 아이콘 색상 갱신"""
        self._update_icon(palette)
        if self.btn_help is not None:
            self.btn_help.setIcon(
                get_icon("help-circle", palette=palette, size=HELP_ICON_SIZE)
            )
