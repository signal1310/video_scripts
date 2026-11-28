"""행 묶기 드롭다운 항목의 체크 표시, 이름, 오른쪽 개수 알약 배지를 그리는 모듈

- 메뉴 항목을 `QWidgetAction` 기본 위젯으로 만들어 오른쪽 개수를 알약으로 정렬함
- 행 위젯은 마우스를 통과시켜 클릭, 키보드 이동, 비활성 건너뛰기를 `QMenu`가 그대로 처리하게 함
- 항목 글자에는 이름만 넣어 메뉴 hovered 시그널과 접근성 이름에 개수가 섞이지 않게 함
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget, QWidgetAction

from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.filter_bar_chips import FilterBarCountBadge, muted_count_badge_style

NONE_LABEL: str = "없음"
EXPAND_ALL_LABEL: str = "모든 그룹 펼치기"
COLLAPSE_ALL_LABEL: str = "모든 그룹 접기"

# - 체크 아이콘 자리를 늘 비워 두어 선택 여부와 관계없이 이름 시작 자리가 같음
ROW_GROUP_CHECK_COLUMN_PX: int = 16
ROW_GROUP_CHECK_ICON_PX: int = 12
ROW_GROUP_ROW_PAD_X_PX: int = 10
ROW_GROUP_ROW_PAD_Y_PX: int = 4
ROW_GROUP_ROW_SPACING_PX: int = 6
# - 알약을 숨긴 행도 같은 높이를 유지해 메뉴 항목 높이가 들쭉날쭉하지 않게 함
ROW_GROUP_ROW_HEIGHT_PX: int = 22
# - 그룹 버튼 오른쪽 캐럿이 차지하는 폭과, 이름/알약/캐럿 사이에 남기는 여백
ROW_GROUP_CARET_RESERVE_PX: int = 18
ROW_GROUP_BADGE_GAP_PX: int = 4
# - 글자 잉크가 알약에 닿지 않도록 오른쪽 여백에 더 두는 폭
ROW_GROUP_TEXT_GAP_PX: int = 6


@dataclass(frozen=True)
class RowGroupMenuEntry:
    """묶기 메뉴 한 항목의 이름, 키, 그룹 수, 선택, 사용 가능 여부"""

    label: str
    key: str = ""
    count: int = 0
    checkable: bool = True
    checked: bool = False
    enabled: bool = True


class RowGroupMenuRow(QWidget):
    """체크 표시, 이름, 오른쪽 개수 알약을 한 줄로 그리는 메뉴 항목 행

    - 선택 배경은 `highlighted` 속성으로 칠해 메뉴 hovered 시그널을 따름
    - 꺼진 명령은 글자색만 흐리게 해 클릭할 수 없음을 알림
    """

    def __init__(self, entry: RowGroupMenuEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RowGroupMenuRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # - 행이 마우스를 받으면 메뉴의 hovered 처리와 클릭 실행이 끊기므로 통과시킴
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        margins = (
            ROW_GROUP_ROW_PAD_X_PX,
            ROW_GROUP_ROW_PAD_Y_PX,
            ROW_GROUP_ROW_PAD_X_PX,
            ROW_GROUP_ROW_PAD_Y_PX,
        )
        layout.setContentsMargins(*margins)
        layout.setSpacing(ROW_GROUP_ROW_SPACING_PX)
        self._check = QLabel(self)
        self._check.setObjectName("RowGroupMenuCheck")
        self._check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._check.setFixedWidth(ROW_GROUP_CHECK_COLUMN_PX)
        self._name = QLabel(entry.label, self)
        self._name.setObjectName("RowGroupMenuName")
        self._badge = FilterBarCountBadge(self)
        layout.addWidget(self._check)
        layout.addWidget(self._name, 1)
        layout.addWidget(self._badge)
        self.setMinimumHeight(ROW_GROUP_ROW_HEIGHT_PX)
        self._checked: bool = False
        self._highlighted: bool = False
        self._palette: ThemePalette = get_active_palette()
        self.setEnabled(entry.enabled)
        self.set_checked(entry.checked)
        self.set_count(entry.count)
        self.apply_theme(self._palette)

    @property
    def name_text(self) -> str:
        """행에 그려진 항목 이름"""
        return self._name.text()

    @property
    def badge(self) -> FilterBarCountBadge:
        """오른쪽 개수 알약 배지"""
        return self._badge

    @property
    def checked(self) -> bool:
        """선택 체크 표시가 그려져 있는지"""
        return self._checked

    @property
    def highlighted(self) -> bool:
        """메뉴에서 강조 중인 행인지"""
        return self._highlighted

    def set_checked(self, checked: bool) -> None:
        """선택 항목에만 체크 아이콘을 그림"""
        self._checked = checked
        self._refresh_check()

    def set_count(self, count: int) -> None:
        """그룹 수 알약을 0보다 클 때만 보임"""
        value = max(0, count)
        self._badge.set_count(value)
        self._badge.setVisible(value > 0)

    def set_highlighted(self, highlighted: bool) -> None:
        """메뉴에서 강조 중인 행만 선택 배경으로 칠함"""
        if highlighted == self._highlighted:
            return
        self._highlighted = highlighted
        self.setProperty("highlighted", highlighted)
        self._repolish()
        self.update()

    def apply_theme(self, palette: ThemePalette) -> None:
        """체크 아이콘과 알약 배지 색을 팔레트 토큰으로 다시 칠함

        - 이름과 선택 배경 색은 앱 스타일시트의 `RowGroupMenuRow` 규칙이 맡음
        """
        self._palette = palette
        self._badge.setStyleSheet(muted_count_badge_style(palette))
        self._refresh_check()

    def _refresh_check(self) -> None:
        """체크 상태에 맞춰 체크 아이콘을 다시 그림"""
        if not self._checked:
            self._check.clear()
            return
        icon = get_icon("check", color=self._palette.accent_blue, size=ROW_GROUP_CHECK_ICON_PX)
        self._check.setPixmap(icon.pixmap(ROW_GROUP_CHECK_ICON_PX, ROW_GROUP_CHECK_ICON_PX))

    def _repolish(self) -> None:
        """바뀐 속성으로 스타일시트를 다시 적용해 선택 배경을 칠함"""
        style = self.style()
        if style is None:
            return
        style.unpolish(self)
        style.polish(self)


def build_row_group_action(
    menu: QMenu, entry: RowGroupMenuEntry, palette: ThemePalette
) -> QWidgetAction:
    """묶기 메뉴 항목 하나를 체크, 이름, 개수 알약 행으로 만들어 반환

    - 항목 글자는 이름만 두어 hovered 시그널과 접근성 이름이 이름 그대로 유지됨
    - 체크와 사용 가능 상태가 나중에 바뀌어도 행 표시가 함께 따라감
    """
    action = QWidgetAction(menu)
    action.setText(entry.label)
    action.setCheckable(entry.checkable)
    action.setChecked(entry.checked)
    action.setEnabled(entry.enabled)
    row = RowGroupMenuRow(entry, menu)
    action.setDefaultWidget(row)

    def _on_toggled(checked: bool) -> None:
        row.set_checked(checked)

    def _on_changed() -> None:
        row.setEnabled(action.isEnabled())

    action.toggled.connect(_on_toggled)
    action.changed.connect(_on_changed)
    return action


def row_group_action_row(action: QAction) -> RowGroupMenuRow | None:
    """메뉴 항목의 기본 위젯이 묶기 행이면 그 행을 반환"""
    if not isinstance(action, QWidgetAction):
        return None
    widget = action.defaultWidget()
    return widget if isinstance(widget, RowGroupMenuRow) else None


def apply_row_group_menu_theme(menu: QMenu, palette: ThemePalette) -> None:
    """메뉴에 담긴 모든 묶기 행의 체크 아이콘과 알약 색을 새 팔레트로 다시 칠함"""
    for action in menu.actions():
        row = row_group_action_row(action)
        if row is not None:
            row.apply_theme(palette)


def set_highlighted_action(menu: QMenu, action: QAction | None) -> None:
    """메뉴에서 강조 중인 항목의 행만 선택 배경으로 바꿈"""
    for candidate in menu.actions():
        row = row_group_action_row(candidate)
        if row is not None:
            row.set_highlighted(candidate is action)


@dataclass(frozen=True)
class RowGroupMenuActions:
    """묶기 메뉴를 다시 만들 때 만든 항목 묶음"""

    options: dict[str, QAction]
    expand: QAction
    collapse: QAction
    exclusive: QActionGroup


def build_row_group_menu(
    menu: QMenu,
    *,
    options: list[tuple[str, str]],
    counts: dict[str, int],
    group_by: str,
    palette: ThemePalette,
    on_select: Callable[[str], None],
    on_expand: Callable[[], None],
    on_collapse: Callable[[], None],
) -> RowGroupMenuActions:
    """없음, 폴더, 범주형 열과 펼치기, 접기 항목으로 묶기 메뉴를 다시 채움

    - 항목 오른쪽에는 그룹 수를 알약으로 붙이고 0이면 알약을 숨김
    - 그룹을 고르지 않았으면 펼치기, 접기 명령은 꺼 둠
    """
    exclusive = QActionGroup(menu)
    exclusive.setExclusive(True)
    entries = [RowGroupMenuEntry(NONE_LABEL, "", checked=not group_by)]
    for key, label in options:
        entries.append(
            RowGroupMenuEntry(label, key, count=counts.get(key, 0), checked=key == group_by)
        )
    created: dict[str, QAction] = {}
    for entry in entries:
        action = build_row_group_action(menu, entry, palette)
        action.triggered.connect(lambda _checked=False, k=entry.key: on_select(k))
        exclusive.addAction(action)
        menu.addAction(action)
        created[entry.key] = action
    menu.addSeparator()
    commands_enabled = bool(group_by)
    expand = build_row_group_action(
        menu,
        RowGroupMenuEntry(EXPAND_ALL_LABEL, checkable=False, enabled=commands_enabled),
        palette,
    )
    expand.triggered.connect(lambda _checked=False: on_expand())
    menu.addAction(expand)
    collapse = build_row_group_action(
        menu,
        RowGroupMenuEntry(COLLAPSE_ALL_LABEL, checkable=False, enabled=commands_enabled),
        palette,
    )
    collapse.triggered.connect(lambda _checked=False: on_collapse())
    menu.addAction(collapse)
    return RowGroupMenuActions(created, expand, collapse, exclusive)


def row_group_button_padding_style(pad_px: int) -> str:
    """그룹 버튼 글자 뒤에 알약 자리를 비우는 오른쪽 여백 QSS. 0이면 빈 문자열

    - 여백은 알약 폭에 양쪽 간격을 더해 잡고, 글자는 남은 칸 가운데에 그려져 알약과 겹치지 않음
    """
    if pad_px <= 0:
        return ""
    return f"QPushButton#RowGroupButton {{ padding-right: {pad_px}px; }}"


def apply_row_group_button_badge(
    button: QPushButton, badge: FilterBarCountBadge, count: int
) -> int:
    """그룹 버튼 알약에 그룹 수를 넣고, 알약 자리만큼 오른쪽 여백을 확보한 뒤 캐럿 앞에 붙임

    - 반환값은 지금 적용한 오른쪽 여백으로, 같은 값을 다시 쓰지 않게 호출자가 기억함
    - `padding-right`를 늘리면 캐럿이 왼쪽으로 밀리므로 알약을 그 앞에 놓음
    """
    badge.set_count(count)
    show = count > 0
    badge.setVisible(show)
    pad = badge.width() + ROW_GROUP_BADGE_GAP_PX + ROW_GROUP_TEXT_GAP_PX if show else 0
    button.setStyleSheet(row_group_button_padding_style(pad))
    place_row_group_button_badge(button, badge)
    return pad


def place_row_group_button_badge(button: QPushButton, badge: FilterBarCountBadge) -> None:
    """알약 배지를 버튼 오른쪽 캐럿 바로 앞에 붙임"""
    if badge.isHidden():
        return
    x = button.width() - ROW_GROUP_CARET_RESERVE_PX - ROW_GROUP_BADGE_GAP_PX - badge.width()
    badge.move(max(0, x), (button.height() - badge.height()) // 2)


__all__ = [
    "COLLAPSE_ALL_LABEL",
    "EXPAND_ALL_LABEL",
    "NONE_LABEL",
    "ROW_GROUP_BADGE_GAP_PX",
    "ROW_GROUP_CARET_RESERVE_PX",
    "ROW_GROUP_TEXT_GAP_PX",
    "RowGroupMenuActions",
    "RowGroupMenuEntry",
    "RowGroupMenuRow",
    "apply_row_group_button_badge",
    "apply_row_group_menu_theme",
    "build_row_group_action",
    "build_row_group_menu",
    "place_row_group_button_badge",
    "row_group_action_row",
    "row_group_button_padding_style",
    "set_highlighted_action",
]
