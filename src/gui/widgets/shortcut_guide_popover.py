"""미리 보기 창과 화질 비교 창이 함께 쓰는 호버 단축키 안내 버튼과 팝오버

- 버튼에 마우스를 올리면 짧게 기다렸다가 현재 단축키 설정을 2열 키캡 표로 보여 줌
- 라이트와 다크 테마 모두에서 영상 위에 겹치는 어두운 반투명 HUD로 그려 뒤 영상이 은은하게 비침
- 읽기 전용 안내이므로 버튼과 팝오버 밖으로 마우스가 벗어나면 닫히고, 클릭으로는 아무 동작도 하지 않음
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QCursor, QEnterEvent, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import QGridLayout, QLabel, QToolButton, QWidget

from src.core.settings.shortcuts import SHORTCUT_SPEC_BY_ID, normalize_shortcut
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.floating_popup import FloatingPopupFrame

GUIDE_TEXT: str = "단축키 안내"
GUIDE_ICON: str = "keyboard"
OPEN_DELAY_MS: int = 120
HIDE_DELAY_MS: int = 260
POPOVER_GAP_PX: int = 8
KEY_COLUMNS: int = 2
_CARD_PADDING: int = 10
_ROW_SPACING: int = 4
_COLUMN_SPACING: int = 12
_KEY_RADII: str = "4px"


@dataclass(frozen=True, slots=True)
class ShortcutEntry:
    """단축키 안내에 표시할 명령 이름, 현재 키, 사용자 변경 여부"""

    label: str
    key: str
    customized: bool


def build_shortcut_entries(
    shortcuts: Mapping[str, str], action_ids: Sequence[str]
) -> list[ShortcutEntry]:
    """지정한 명령의 현재 유효 단축키를 표시 순서대로 만듦, 키가 없는 명령은 뺌"""
    entries: list[ShortcutEntry] = []
    for action_id in action_ids:
        spec = SHORTCUT_SPEC_BY_ID.get(action_id)
        key = normalize_shortcut(shortcuts.get(action_id, ""))
        if spec is None or not key:
            continue
        entries.append(ShortcutEntry(spec.label, key, key != spec.default_key))
    return entries


def build_guide_qss(palette: ThemePalette) -> str:
    """영상 위에 겹치는 다크 반투명 HUD 카드와 키캡의 팔레트 기반 스타일"""
    return f"""
QFrame#ShortcutGuideCard {{
    background-color: {palette.media_hud_bg};
    border: 1px solid {palette.media_track};
    border-radius: 8px;
}}
QLabel#ShortcutGuideTitle {{ color: {palette.media_fg}; font-weight: 700; font-size: 9.5pt; }}
QLabel#ShortcutGuideLabel {{ color: {palette.media_overlay_label}; font-size: 8.5pt; }}
QLabel#ShortcutGuideKey {{
    color: {palette.media_fg}; background-color: {palette.media_keycap_bg};
    border: 1px solid {palette.media_keycap_border}; border-radius: {_KEY_RADII};
    padding: 1px 6px; font-weight: 600; font-size: 8.5pt;
}}
"""



class ShortcutGuidePopover(FloatingPopupFrame):
    """현재 단축키를 2열 키캡 표로 보여 주는 팝오버"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # - 호버 표시가 부모 영상 창의 활성 포커스를 빼앗지 않게 함
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        card, body = self._build_card("ShortcutGuideCard", _CARD_PADDING)
        self._title = QLabel("", card)
        self._title.setObjectName("ShortcutGuideTitle")
        body.addWidget(self._title)
        host = QWidget(card)
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_COLUMN_SPACING)
        self._grid.setVerticalSpacing(_ROW_SPACING)
        body.addWidget(host)
        self._entries: list[ShortcutEntry] = []
        self._anchor: QToolButton | None = None
        self.setStyleSheet(build_guide_qss(get_active_palette()))

    def is_open(self) -> bool:
        """팝오버가 지금 표시 중인지"""
        return self.isVisible()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """앵커 클릭은 고정 없이 안내를 유지하고, 그 밖의 바깥 클릭은 공통 기반이 닫게 함"""
        if (
            a1 is not None
            and self.isVisible()
            and self._anchor is not None
            and a1.type() == QEvent.Type.MouseButtonPress
            and isinstance(a1, QMouseEvent)
        ):
            anchor_rect = QRect(self._anchor.mapToGlobal(QPoint(0, 0)), self._anchor.size())
            if anchor_rect.contains(a1.globalPosition().toPoint()):
                return False
        return super().eventFilter(a0, a1)

    def set_entries(self, title: str, entries: Sequence[ShortcutEntry]) -> None:
        """제목과 단축키 행을 다시 채움"""
        self._title.setText(title)
        self._entries = list(entries)
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                # - 지연 삭제만 하면 다음 레이아웃 계산에 이전 위젯이 남으므로 바로 떼어 냄
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for index, entry in enumerate(self._entries):
            row, column = divmod(index, KEY_COLUMNS)
            self._grid.addWidget(self._label(entry.label), row, column * 2)
            self._grid.addWidget(self._key(entry.key), row, column * 2 + 1)

    def show_for(self, button: QToolButton) -> None:
        """앵커 버튼 위쪽에 화면 안으로 보정해 표시"""
        self._anchor = button
        rect = QRect(button.mapToGlobal(QPoint(0, 0)), button.size())
        self.set_toggle_rect(rect)
        self.apply_theme(get_active_palette())
        # - 그리드를 다시 채운 직후에는 레이아웃을 확정한 크기로 좌표를 계산해야 바닥이 앵커를 덮지 않음
        layout = self.layout()
        if layout is not None:
            layout.activate()
        size = self.sizeHint()
        self.show_clamped_at(
            QPoint(rect.right() - size.width(), rect.top() - size.height() - POPOVER_GAP_PX)
        )

    def enterEvent(self, event: QEnterEvent | None) -> None:
        """마우스가 팝오버 안으로 오면 앵커의 자동 닫힘을 멈춤"""
        super().enterEvent(event)
        hook = getattr(self._anchor, "_on_popover_enter", None)
        if callable(hook):
            hook()

    def leaveEvent(self, event: QEvent | None) -> None:
        """마우스가 팝오버 밖으로 나가면 앵커의 자동 닫힘 타이머를 다시 시작"""
        super().leaveEvent(event)
        hook = getattr(self._anchor, "_on_popover_leave", None)
        if callable(hook):
            hook()

    def apply_theme(self, palette: ThemePalette) -> None:
        """현재 테마 팔레트로 팝오버 스타일을 다시 입힘"""
        self.setStyleSheet(build_guide_qss(palette))

    def contains_global(self, pos: QPoint) -> bool:
        """전역 좌표가 팝오버 안인지"""
        return self.is_open() and self.frameGeometry().contains(pos)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("ShortcutGuideLabel")
        return label

    def _key(self, text: str) -> QLabel:
        key = QLabel(text, self)
        key.setObjectName("ShortcutGuideKey")
        key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return key



class ShortcutGuideButton(QToolButton):
    """호버로 단축키 안내 팝오버를 여는 아이콘 버튼"""

    def __init__(
        self,
        parent: QWidget,
        get_shortcuts: Callable[[], Mapping[str, str]],
        action_ids: Sequence[str],
        *,
        title: str,
        button_size: int = 26,
        icon_size: int = 16,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShortcutGuideButton")
        self.setFixedSize(button_size, button_size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(GUIDE_TEXT)
        self.setAccessibleName(GUIDE_TEXT)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._get_shortcuts = get_shortcuts
        self._action_ids = tuple(action_ids)
        self._title = title
        self._icon_size = icon_size
        self._popover: ShortcutGuidePopover | None = None
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(OPEN_DELAY_MS)
        self._open_timer.timeout.connect(self.open_guide)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self.hide_if_outside)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        """현재 팔레트의 흐린 영상 글자색으로 키보드 아이콘을 다시 입힘"""
        self._set_icon(get_active_palette().media_muted)

    def showEvent(self, event: QShowEvent | None) -> None:
        """표시될 때 아이콘 색을 현재 팔레트로 맞춤"""
        self.refresh_icon()
        super().showEvent(event)

    def enterEvent(self, event: QEnterEvent | None) -> None:
        """호버가 시작되면 밝게 강조하고 지연 뒤 팝오버를 엶"""
        super().enterEvent(event)
        self._set_icon(get_active_palette().media_fg)
        self._hide_timer.stop()
        self._open_timer.start()

    def leaveEvent(self, event: QEvent | None) -> None:
        """마우스가 버튼을 떠나면 흐린 색으로 되돌리고 잠시 뒤 닫음"""
        super().leaveEvent(event)
        self._set_icon(get_active_palette().media_muted)
        self._open_timer.stop()
        self._hide_timer.start()

    def open_guide(self) -> None:
        """지금 단축키 설정으로 팝오버를 엶"""
        self._open_timer.stop()
        popover = self._ensure_popover()
        popover.set_entries(
            self._title, build_shortcut_entries(self._get_shortcuts(), self._action_ids)
        )
        popover.show_for(self)

    def close_guide(self) -> None:
        """열린 팝오버와 대기 타이머를 닫음"""
        self._open_timer.stop()
        self._hide_timer.stop()
        if self._popover is not None:
            self._popover.close()

    def close_guide_if_open(self) -> bool:
        """안내가 열려 있으면 닫고 참, 아니면 거짓"""
        if not self.is_guide_open():
            return False
        self.close_guide()
        return True

    def is_guide_open(self) -> bool:
        """팝오버가 표시 중인지"""
        return self._popover is not None and self._popover.is_open()

    def refresh_entries(self) -> None:
        """팝오버가 열려 있으면 최신 단축키로 다시 채움"""
        if self._popover is not None and self._popover.is_open():
            self.open_guide()

    def set_guide_mode(self, title: str, action_ids: Sequence[str]) -> None:
        """안내 제목과 표시할 명령 목록을 바꾸고 열려 있으면 새 목록으로 다시 채움"""
        self._title = title
        self._action_ids = tuple(action_ids)
        self.refresh_entries()

    def hide_if_outside(self) -> None:
        """버튼과 팝오버 밖에 마우스가 있으면 닫음"""
        popover = self._popover
        if popover is None or not popover.is_open():
            return
        cursor = QCursor.pos()
        if self.underMouse() or popover.contains_global(cursor):
            return
        popover.close()

    def _on_popover_enter(self) -> None:
        """팝오버 안으로 마우스가 들어오면 닫힘 대기를 멈춤"""
        self._hide_timer.stop()

    def _on_popover_leave(self) -> None:
        """팝오버 밖으로 마우스가 나가면 잠시 뒤 닫도록 다시 셈"""
        popover = self._popover
        if popover is not None and popover.is_open():
            self._hide_timer.start()

    def _set_icon(self, color: str) -> None:
        self.setIcon(get_icon(GUIDE_ICON, color=color, size=self._icon_size))

    def _ensure_popover(self) -> ShortcutGuidePopover:
        if self._popover is None:
            self._popover = ShortcutGuidePopover(self.window())
        return self._popover
