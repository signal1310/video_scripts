"""환경 설정 좌측 범주 내비게이션(자동 아이콘 레일) 모듈

- 기본 폭에서는 아이콘과 범주 이름을, 좁은 폭에서는 아이콘만 남긴 52px 레일로 자동 축소
- 검색 중에만 일치 개수 배지를 표시하고, 결과 항목을 목록 맨 위에 둠
"""

from collections.abc import Mapping
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QLabel, QToolButton, QVBoxLayout, QWidget

from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

WIDTH_EXPANDED: int = 184
WIDTH_RAIL: int = 52
AUTO_RAIL_WIDTH: int = 620
NAV_ICON_SIZE: int = 17
BADGE_HEIGHT: int = 16
BADGE_MIN_WIDTH: int = 16
BADGE_RIGHT_MARGIN: int = 8
BADGE_TOP_MARGIN: int = 4


@dataclass(frozen=True, slots=True)
class NavCategory:
    """사이드바 범주 항목(페이지 인덱스, 표시 이름, 아이콘 이름)"""

    index: int
    label: str
    icon: str


NAV_CATEGORIES: tuple[NavCategory, ...] = (
    NavCategory(0, "일반", "settings"),
    NavCategory(1, "해상도 및 분석", "analyze"),
    NavCategory(2, "판별 기준", "sliders"),
    NavCategory(3, "재인코딩", "play"),
    NavCategory(4, "단축키", "keyboard"),
)

RESULTS_INDEX: int = 5
RESULTS_LABEL: str = "전체 결과"
RESULTS_ICON: str = "search"


class NavButton(QToolButton):
    """범주 하나를 나타내는 아이콘, 이름, 일치 개수 배지 버튼"""

    def __init__(
        self,
        label: str,
        icon_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.label_text = label
        self.icon_name = icon_name
        self.setObjectName("SettingsNavButton")
        self.setCheckable(True)
        self.setAutoRaise(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setText(label)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self.badge = QLabel(self)
        self.badge.setObjectName("SettingsNavBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedHeight(BADGE_HEIGHT)
        self.badge.setMinimumWidth(BADGE_MIN_WIDTH)
        self.badge.hide()
        self.apply_palette(get_active_palette())

    def set_badge(self, count: int | None) -> None:
        """일치 개수 배지를 표시하거나 숨김"""
        if count is None or count <= 0:
            self.badge.hide()
            return
        self.set_badge_value(str(count))

    def set_badge_value(self, text: str) -> None:
        """배지에 표시할 문자열을 직접 지정(0건 안내처럼 0도 표시해야 할 때)"""
        self.badge.setText(text)
        self.badge.adjustSize()
        self.badge.setFixedHeight(BADGE_HEIGHT)
        self.badge.show()
        self._place_badge()

    def set_compact(self, compact: bool) -> None:
        """레일 모드에서는 이름을 숨기고 툴팁으로 알림"""
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if compact
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._place_badge()

    def apply_palette(self, palette: ThemePalette) -> None:
        """현재 팔레트 색으로 아이콘을 다시 그림"""
        self.setIcon(get_icon(self.icon_name, palette=palette, size=NAV_ICON_SIZE))

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """버튼 폭이 바뀌면 배지 위치를 다시 잡음"""
        super().resizeEvent(event)
        self._place_badge()

    def _place_badge(self) -> None:
        if not self.badge.isVisible():
            return
        if self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            x = self.width() - self.badge.width() - BADGE_TOP_MARGIN
            y = BADGE_TOP_MARGIN
        else:
            x = self.width() - self.badge.width() - BADGE_RIGHT_MARGIN
            y = (self.height() - self.badge.height()) // 2
        self.badge.move(max(0, x), max(0, y))

class SettingsSidebar(QWidget, ThemeAwareMixin):
    """범주 목록, 자동 아이콘 레일, 수동 접기 버튼을 담는 좌측 내비게이션"""

    category_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsSidebar")
        self._auto_compact: bool = False
        self._manual_collapsed: bool = False
        self._buttons: dict[int, NavButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(2)

        self.btn_results = self._add_button(layout, RESULTS_INDEX, RESULTS_LABEL, RESULTS_ICON)
        self.btn_results.setObjectName("SettingsNavResults")
        self.btn_results.hide()

        for category in NAV_CATEGORIES:
            self._add_button(layout, category.index, category.label, category.icon)

        layout.addStretch(1)
        self.btn_toggle = QToolButton(self)
        self.btn_toggle.setObjectName("SettingsNavToggle")
        self.btn_toggle.setAutoRaise(True)
        self.btn_toggle.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
        self.btn_toggle.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.btn_toggle)

        self.set_compact(False)
        self.init_theme_listener()

    def _add_button(
        self,
        layout: QVBoxLayout,
        index: int,
        label: str,
        icon_name: str,
    ) -> NavButton:
        button = NavButton(label, icon_name, self)
        button.clicked.connect(lambda _checked=False, i=index: self.category_selected.emit(i))
        self._buttons[index] = button
        layout.addWidget(button)
        return button

    def button(self, index: int) -> NavButton:
        """페이지 인덱스에 해당하는 범주 버튼 반환"""
        return self._buttons[index]

    def set_current(self, index: int) -> None:
        """선택한 범주의 버튼만 눌린 상태로 표시"""
        for page, button in self._buttons.items():
            button.setChecked(page == index)

    def set_badges(self, counts: Mapping[int, int]) -> None:
        """검색 중 일치 개수를 범주별 배지로 표시, 없는 범주는 숨김"""
        for page, button in self._buttons.items():
            button.set_badge(counts.get(page))

    def set_visible_categories(self, counts: Mapping[int, int]) -> None:
        """일치 항목이 있는 범주만 남기고, 값이 없으면 범주를 모두 숨김"""
        for page, button in self._buttons.items():
            if page == RESULTS_INDEX:
                continue
            button.setVisible(counts.get(page, 0) > 0)

    def show_all_categories(self) -> None:
        """모든 범주 버튼을 다시 보임(전체 결과는 검색 중에만 보이므로 제외)"""
        for page, button in self._buttons.items():
            if page == RESULTS_INDEX:
                continue
            button.setVisible(True)

    def set_results_visible(self, visible: bool) -> None:
        """검색 중에만 전체 결과 항목 표시"""
        self.btn_results.setVisible(visible)

    def set_results_badge(self, count: int) -> None:
        """전체 결과 배지는 0건일 때도 표시해 검색 결과가 없음을 알림"""
        self.btn_results.set_badge_value(str(max(0, count)))

    def set_auto_compact(self, compact: bool) -> None:
        """창 폭에 따른 자동 레일 전환"""
        if compact == self._auto_compact:
            return
        self._auto_compact = compact
        self.set_compact(self._effective_compact())

    def toggle_collapsed(self) -> None:
        """수동 아이콘 레일 접기, 펼치기 전환"""
        if self._auto_compact:
            return
        self._manual_collapsed = not self._manual_collapsed
        self.set_compact(self._effective_compact())

    def is_compact(self) -> bool:
        """현재 아이콘 전용 레일 상태 여부"""
        return self._effective_compact()

    def set_compact(self, compact: bool) -> None:
        """레일 폭과 이름 표시, 접기 버튼 아이콘을 한 번에 전환"""
        self.setFixedWidth(WIDTH_RAIL if compact else WIDTH_EXPANDED)
        for button in self._buttons.values():
            button.set_compact(compact)
        self.btn_toggle.setEnabled(not self._auto_compact)
        icon_name = "chevron-right" if compact else "chevron-left"
        self.btn_toggle.setIcon(
            get_icon(icon_name, palette=get_active_palette(), size=NAV_ICON_SIZE)
        )
        if self._auto_compact:
            tip = "창 폭이 좁아 아이콘 레일로 표시합니다."
        elif compact:
            tip = "범주 이름 펼치기"
        else:
            tip = "범주 이름 접기"
        self.btn_toggle.setToolTip(tip)
        self.btn_toggle.setAccessibleName(tip)

    def _effective_compact(self) -> bool:
        return self._auto_compact or self._manual_collapsed

    def apply_theme(self, palette: ThemePalette) -> None:
        """모든 범주 버튼과 접기 버튼 아이콘을 현재 팔레트 색으로 갱신"""
        for button in self._buttons.values():
            button.apply_palette(palette)
        icon_name = "chevron-right" if self.is_compact() else "chevron-left"
        self.btn_toggle.setIcon(get_icon(icon_name, palette=palette, size=NAV_ICON_SIZE))

