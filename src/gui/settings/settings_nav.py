"""환경 설정 좌측 범주 내비게이션(자동 아이콘 레일) 모듈

- 기본 폭에서는 아이콘과 범주 이름을, 좁은 폭에서는 아이콘만 남긴 52px 레일로 자동 축소
- 검색 중에만 일치 개수 배지를 표시하고, 결과 항목을 목록 맨 위에 둠
- 창 왼쪽과 아래 모서리에 밀착하도록 카드 바탕과 오른쪽 1px 구분선만 씀
"""

from collections.abc import Mapping
from dataclasses import dataclass

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPaintEvent, QResizeEvent
from PyQt6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

WIDTH_EXPANDED: int = 158
WIDTH_RAIL: int = 52
AUTO_RAIL_WIDTH: int = 620
NAV_ICON_SIZE: int = 17
NAV_ICON_GAP_PX: int = 8
NAV_BORDER_PX: int = 1
NAV_PADDING_LEFT_PX: int = 8
NAV_TEXT_INSET_PX: int = NAV_BORDER_PX + NAV_PADDING_LEFT_PX
BADGE_HEIGHT: int = 18
BADGE_MIN_WIDTH: int = 22
BADGE_RADIUS: int = 9
BADGE_RIGHT_MARGIN: int = 8
BADGE_TOP_MARGIN: int = 4
BADGE_PADDING_X_PX: int = 6
BADGE_TEXT_GAP_PX: int = 4


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
    NavCategory(3, "인코딩", "play"),
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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.toggled.connect(self._on_toggled)
        self._palette = get_active_palette()
        self.apply_palette(self._palette)

    def set_badge(self, count: int | None) -> None:
        """일치 개수 배지를 표시하거나 숨김"""
        if count is None or count <= 0:
            self.badge.hide()
            # 배지가 사라지면 이름 영역이 넓어지므로 말줄임 전 전체 이름으로 즉시 다시 그림
            self.update()
            return
        self.set_badge_value(str(count))

    def set_badge_value(self, text: str) -> None:
        """배지에 표시할 문자열을 직접 지정(0건 안내처럼 0도 표시해야 할 때)"""
        self.badge.setText(text)
        # 스타일시트 패딩이 반영된 크기를 얻도록 색조 적용을 크기 계산보다 먼저 함
        self.apply_badge_tone(self._palette)
        self.badge.adjustSize()
        self.badge.setFixedHeight(BADGE_HEIGHT)
        self.badge.show()
        self._place_badge()
        self.update()

    def set_compact(self, compact: bool) -> None:
        """레일 모드에서는 이름을 숨기고 툴팁으로 알림"""
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if compact
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        # 명시 적용한 다크/라이트 팔레트를 접기 전환에서도 유지
        self.apply_palette(self._palette)
        self._place_badge()
        self.update()

    def apply_palette(self, palette: ThemePalette) -> None:
        """현재 팔레트 색으로 아이콘과 배지 색을 다시 그림"""
        self._palette = palette
        compact = self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        pad_right = 0 if compact else NAV_ICON_GAP_PX
        self.setIcon(
            get_icon(self.icon_name, palette=palette, size=NAV_ICON_SIZE, pad_right=pad_right)
        )
        self.setIconSize(QSize(NAV_ICON_SIZE + pad_right, NAV_ICON_SIZE))
        self.apply_badge_tone(palette)

    def badge_colors(self, palette: ThemePalette) -> tuple[str, str]:
        """선택 상태에 따른 배지 배경색, 글자색"""
        if self.isChecked():
            return palette.accent_blue, palette.text_on_accent
        return palette.bg_elevated, palette.text_secondary

    def apply_badge_tone(self, palette: ThemePalette) -> None:
        """선택 여부에 따라 배지를 액센트 또는 중립 두 색조로 표시"""
        background, foreground = self.badge_colors(palette)
        self.badge.setStyleSheet(
            f"QLabel#SettingsNavBadge {{ background-color: {background};"
            f" color: {foreground}; border-radius: {BADGE_RADIUS}px;"
            f" padding: 0px {BADGE_PADDING_X_PX}px; font-size: 8pt; font-weight: 700; }}"
        )

    def text_rect(self) -> QRect:
        """범주 이름을 그리는 영역, 배지가 보이면 배지 왼쪽까지만 사용"""
        left = NAV_TEXT_INSET_PX + NAV_ICON_SIZE + NAV_ICON_GAP_PX
        if self.badge.isHidden():
            right = self.width() - NAV_TEXT_INSET_PX
        else:
            right = self.width() - self.badge.width() - BADGE_RIGHT_MARGIN - BADGE_TEXT_GAP_PX
        height = self.fontMetrics().height()
        top = (self.height() - height) // 2
        return QRect(left, top, max(0, right - left), height)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """버튼 폭이 바뀌면 배지 위치를 다시 잡음"""
        super().resizeEvent(event)
        self._place_badge()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """배지와 겹치지 않게 이름을 직접 줄여 그리고, 배경은 스타일, 아이콘과 이름은 직접 그림"""
        if self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        # 배경, 테두리만 스타일로 그리고 아이콘과 이름은 겹침 계산을 위해 직접 그림
        option.text = ""
        option.icon = QIcon()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        self._draw_icon(painter)
        self._draw_label(painter, option)
        painter.end()

    def _on_toggled(self, checked: bool) -> None:
        """선택 상태가 바뀌면 배지 색조를 갱신"""
        self.apply_badge_tone(self._palette)

    def _place_badge(self) -> None:
        """배지를 오른쪽 끝에 붙임, 표시 전(숨김 상태)에도 위치를 계산해 두어 0, 0에 남지 않게 함"""
        if self.badge.isHidden():
            return
        if self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            x = self.width() - self.badge.width() - BADGE_TOP_MARGIN
            y = BADGE_TOP_MARGIN
        else:
            x = self.width() - self.badge.width() - BADGE_RIGHT_MARGIN
            y = (self.height() - self.badge.height()) // 2
        self.badge.move(max(0, x), max(0, y))

    def _draw_icon(self, painter: QStylePainter) -> None:
        """아이콘을 이름 왼쪽 고정 위치에 그림, 비활성 상태는 흐린 모드로 렌더링"""
        icon_size = self.iconSize()
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        pixmap = self.icon().pixmap(icon_size, mode, QIcon.State.Off)
        y = (self.height() - icon_size.height()) // 2
        painter.drawPixmap(NAV_TEXT_INSET_PX, max(0, y), pixmap)

    def _draw_label(self, painter: QStylePainter, option: QStyleOptionToolButton) -> None:
        """범주 이름을 배지 왼쪽 영역에 말줄임으로 그림"""
        rect = self.text_rect()
        if rect.width() <= 0:
            return
        if not self.isEnabled():
            color = self._palette.text_muted
        elif self.isChecked():
            color = self._palette.accent_blue
        elif option.state & QStyle.StateFlag.State_MouseOver:
            color = self._palette.text_primary
        else:
            color = self._palette.text_secondary
        text = self.fontMetrics().elidedText(
            self.label_text, Qt.TextElideMode.ElideRight, rect.width()
        )
        painter.save()
        painter.setPen(QColor(color))
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text
        )
        painter.restore()


class SettingsSidebar(QWidget, ThemeAwareMixin):
    """범주 목록, 자동 아이콘 레일, 수동 접기 버튼을 담는 좌측 내비게이션"""

    category_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsSidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

