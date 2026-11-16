"""단축키 설정 탭의 개별 명령 행과 키 입력 캡처 버튼 위젯 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QKeyCombination, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QIcon, QKeyEvent, QKeySequence, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.shortcuts import ShortcutSpec, normalize_shortcut
from src.gui.dialogs.setting_controls import (
    RESET_BUTTON_SIZE,
    RESET_ICON_SIZE,
    build_row_reset_button,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import set_state_property

_MODIFIER_KEYS: tuple[Qt.Key, ...] = (
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
)
_EMPTY_ICON_NAME: str = "keyboard"
_EMPTY_ICON_SIZE: int = 14
_CAPTURE_PROMPT_TEXT: str = "키 입력..."
_CAPTURE_TOOLTIP: str = "칸을 누른 뒤 키를 입력하면 단축키가 지정됩니다."
_CLEAR_ICON_NAME: str = "x"
_CLEAR_ACCESSIBLE_NAME: str = "단축키 해제"
_RESET_ROW_TOOLTIP: str = "기본 단축키로 되돌립니다."
_CLEAR_ROW_TOOLTIP: str = "단축키를 해제합니다."


def key_event_to_shortcut_str(event: QKeyEvent) -> str | None:
    """키 입력을 QKeySequence 표준 문자열로 변환하며, 수식키 단독 입력은 None 반환

    - 단독 Enter는 Return으로 바꿔 기본값과 같은 표기를 쓰도록 함
    """
    key = event.key()
    if key in _MODIFIER_KEYS:
        return None
    qt_key = Qt.Key.Key_Return if key == Qt.Key.Key_Enter else Qt.Key(key)
    combination = QKeyCombination(event.modifiers(), qt_key)
    text = QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)
    return normalize_shortcut(text) if text else None


class ShortcutCaptureButton(QPushButton):
    """클릭 후 키를 누르면 단축키를 받는 칸, 포커스 이탈이나 외부 클릭 시 캡처를 취소"""

    shortcut_captured = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        # - 생성 중에도 event()가 호출되므로 대기 상태는 부모 초기화보다 먼저 정의
        self._capturing: bool = False
        super().__init__(parent)
        self.setObjectName("ShortcutCaptureButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(_EMPTY_ICON_SIZE, _EMPTY_ICON_SIZE))
        self.setToolTip(_CAPTURE_TOOLTIP)
        self._key: str = ""
        self._palette: ThemePalette | None = None
        # 마우스는 mousePressEvent가 시작하므로, 스페이스 같은 키보드 활성화도 같은 입력 대기로 연결
        self.clicked.connect(self.begin_capture)
        self._refresh_display()

    def apply_palette(self, palette: ThemePalette) -> None:
        """테마 팔레트를 지정해 빈 칸 플레이스홀더 아이콘 색상을 갱신"""
        self._palette = palette
        self._refresh_display()

    def shortcut_text(self) -> str:
        """현재 할당된 단축키 문자열, 미지정이면 빈 문자열"""
        return self._key

    def is_capturing(self) -> bool:
        """키 입력 대기 상태인지 반환"""
        return self._capturing

    def set_key(self, key: str) -> None:
        """표시할 단축키를 바꾸며, 대기 중이었다면 캡처를 취소"""
        self._end_capture()
        self._key = key
        self._refresh_display()

    def begin_capture(self) -> None:
        """키 입력 대기 상태로 전환하고 외부 마우스 클릭 감시를 시작"""
        if self._capturing:
            return
        self._capturing = True
        self._refresh_display()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def cancel_capture(self) -> None:
        """대기 상태를 해제하고 기존 단축키 표시로 되돌림"""
        if self._capturing:
            self._end_capture()

    def event(self, e: QEvent | None) -> bool:
        """대기 중에는 창에 등록된 단축키(되돌리기 등)가 입력을 가로채지 않고 이 칸이 키를 받도록 함"""
        if self._capturing and e is not None and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        """칸을 누르면 키 입력 대기 상태로 전환"""
        self.begin_capture()
        super().mousePressEvent(e)

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        """대기 중에는 수식키 단독 입력을 무시하고, 그 밖의 키는 단축키로 받아 신호로 전달"""
        if e is None or not self._capturing:
            super().keyPressEvent(e)
            return
        e.accept()
        text = key_event_to_shortcut_str(e)
        if text is None:
            return
        self._end_capture()
        self.shortcut_captured.emit(text)

    def focusOutEvent(self, e: QFocusEvent | None) -> None:
        """포커스를 잃으면 키 입력 대기를 취소"""
        self.cancel_capture()
        super().focusOutEvent(e)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """대기 중 이 칸 밖을 누르면 캡처를 취소하고 이벤트는 그대로 통과"""
        if (
            self._capturing
            and a0 is not self
            and isinstance(a1, QMouseEvent)
            and a1.type() == QEvent.Type.MouseButtonPress
        ):
            self.cancel_capture()
        return super().eventFilter(a0, a1)

    def _end_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._refresh_display()

    def _refresh_display(self) -> None:
        is_empty = not self._key and not self._capturing
        if self._capturing:
            self.setText(_CAPTURE_PROMPT_TEXT)
        else:
            self.setText(self._key)
        self.setIcon(self._placeholder_icon() if is_empty else QIcon())
        set_state_property(self, "capturing", self._capturing)

    def _placeholder_icon(self) -> QIcon:
        """미지정 상태를 나타내는 흐린 키보드 아이콘, 팔레트 미지정 시 활성 팔레트 사용"""
        palette = self._palette or get_active_palette()
        return get_icon(_EMPTY_ICON_NAME, color=palette.text_muted, size=_EMPTY_ICON_SIZE)


def _retain_slot_when_hidden(widget: QWidget) -> None:
    """숨김 상태에서도 가로 자리를 유지해 버튼 표시 전환 시 행 안의 칸 위치가 밀리지 않도록 설정"""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


def apply_clear_icon(button: QPushButton, palette: ThemePalette) -> None:
    """해제 버튼의 아이콘을 현재 팔레트의 보조 글자색으로 갱신"""
    button.setIcon(get_icon(_CLEAR_ICON_NAME, color=palette.text_secondary, size=RESET_ICON_SIZE))


class ShortcutRow(QFrame):
    """명령 하나의 이름, 단축키 칸, 해제 버튼, 기본값 복원 버튼으로 구성된 행"""

    def __init__(
        self,
        spec: ShortcutSpec,
        *,
        on_captured: Callable[[str], None],
        on_clear: Callable[[], None],
        on_reset: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.spec = spec

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 8, 4)
        outer.setSpacing(2)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addLayout(layout)

        self.lbl_name = QLabel(spec.label, self)
        self.lbl_name.setToolTip(spec.description)
        # 줄바꿈을 허용해야 좁은 창에서 명령 이름이 행의 최소 폭을 밀어내지 않음
        self.lbl_name.setWordWrap(True)
        layout.addWidget(self.lbl_name, stretch=1)

        self.capture = ShortcutCaptureButton(self)
        self.capture.shortcut_captured.connect(on_captured)
        layout.addWidget(self.capture)

        self.btn_clear = QPushButton(self)
        # 기본값 복원 버튼과 같은 아이콘 버튼 서식을 공유
        self.btn_clear.setObjectName("ResetButton")
        self.btn_clear.setFixedSize(RESET_BUTTON_SIZE, RESET_BUTTON_SIZE)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip(_CLEAR_ROW_TOOLTIP)
        self.btn_clear.setAccessibleName(_CLEAR_ACCESSIBLE_NAME)
        apply_clear_icon(self.btn_clear, get_active_palette())
        self.btn_clear.clicked.connect(lambda _checked=False: on_clear())
        _retain_slot_when_hidden(self.btn_clear)
        layout.addWidget(self.btn_clear)

        self.btn_reset = build_row_reset_button(
            _RESET_ROW_TOOLTIP,
            on_reset,
            self,
        )
        layout.addWidget(self.btn_reset)

        self.lbl_hint = QLabel(spec.description, self)
        self.lbl_hint.setObjectName("SettingDesc")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setVisible(False)
        outer.addWidget(self.lbl_hint)

    def set_hint_visible(self, visible: bool) -> None:
        """명령 설명 라벨을 이름 아래에 표시하거나 숨김"""
        self.lbl_hint.setVisible(visible)

    def refresh(self, key: str, default_key: str) -> None:
        """현재 유효 단축키와 기본값을 칸에 반영하고, 해당 없는 해제, 복원 버튼은 숨김"""
        self.capture.set_key(key)
        self.btn_clear.setVisible(bool(key))
        self.btn_reset.setVisible(key != default_key)

    def search_text(self, group_title: str) -> str:
        """필터 검색 대상 문자열, 기능 그룹 이름과 명령 이름과 현재 단축키를 포함"""
        return f"{group_title} {self.spec.label} {self.capture.shortcut_text()}"
