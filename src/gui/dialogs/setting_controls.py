"""설정/판별 기준 통합 대화상자 공용 UI 컨트롤 및 셸 빌더 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette

TAB_INNER_MARGINS: tuple[int, int, int, int] = (0, 6, 0, 0)
TAB_INNER_SPACING: int = 6
SCROLL_BODY_MARGINS: tuple[int, int, int, int] = (0, 2, 4, 4)
SCROLL_BODY_SPACING: int = 9
RESET_BUTTON_SIZE: int = 26
RESET_ICON_SIZE: int = 13
INLINE_RESET_ICON_SIZE: int = 12


class NoWheelSlider(QSlider):
    """스크롤 영역 탐색 중 값 변경을 막기 위해 휠 이벤트를 상위로 넘기는 슬라이더"""

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """마우스 휠 이벤트를 무시해 부모 스크롤 영역으로 전달"""
        if event is not None:
            event.ignore()


class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """비포커스 상태에서 마우스 휠로 값이 바뀌지 않고 스크롤 영역으로 전달되는 실수 입력 박스"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """포커스된 상태에서만 휠 증감을 허용하고 비포커스 시 상위로 전달"""
        if not self.hasFocus():
            if event is not None:
                event.ignore()
            return
        super().wheelEvent(event)


class ScrollSafeComboBox(QComboBox):
    """비포커스 상태에서 마우스 휠로 선택 항목이 바뀌지 않는 콤보박스"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """포커스된 상태에서만 휠 전환을 허용하고 비포커스 시 상위로 전달"""
        if not self.hasFocus():
            if event is not None:
                event.ignore()
            return
        super().wheelEvent(event)


def build_scroll_tab_shell(
    tab_widget: QWidget,
    desc_text: str,
    reset_button_text: str,
    reset_tooltip: str,
    on_reset: Callable[[], None],
    scroll_area: QScrollArea | None = None,
) -> tuple[QScrollArea, QWidget, QVBoxLayout, QLabel, QPushButton]:
    """상단 인라인 복원 바, 단일 스크롤 본문 영역으로 구성된 콤팩트 탭 셸 생성"""
    root = QVBoxLayout(tab_widget)
    root.setContentsMargins(*TAB_INNER_MARGINS)
    root.setSpacing(TAB_INNER_SPACING)

    header_row = QHBoxLayout()
    header_row.setContentsMargins(2, 0, 2, 0)
    header_row.setSpacing(8)

    lbl_desc = QLabel(desc_text)
    lbl_desc.setObjectName("SettingDesc")
    lbl_desc.setWordWrap(True)
    header_row.addWidget(lbl_desc, stretch=1)

    btn_reset = QPushButton(reset_button_text)
    btn_reset.setObjectName("InlineResetButton")
    btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_reset.setToolTip(reset_tooltip)
    apply_inline_reset_button_icon(btn_reset, get_active_palette())
    btn_reset.clicked.connect(on_reset)
    header_row.addWidget(btn_reset, alignment=Qt.AlignmentFlag.AlignVCenter)
    root.addLayout(header_row)

    scroll = scroll_area if scroll_area is not None else QScrollArea(tab_widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    body = QWidget(scroll)
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(*SCROLL_BODY_MARGINS)
    body_layout.setSpacing(SCROLL_BODY_SPACING)

    scroll.setWidget(body)
    root.addWidget(scroll, stretch=1)
    return scroll, body, body_layout, lbl_desc, btn_reset


def build_row_reset_button(
    tooltip: str,
    on_clicked: Callable[[], None],
    parent: QWidget | None = None,
) -> QPushButton:
    """숨김 상태에서도 우측 슬롯 너비를 유지하는 행 단위 기본값 복원 버튼 생성"""
    btn = QPushButton(parent)
    sp = btn.sizePolicy()
    sp.setRetainSizeWhenHidden(True)
    btn.setSizePolicy(sp)
    btn.setObjectName("ResetButton")
    btn.setFixedSize(RESET_BUTTON_SIZE, RESET_BUTTON_SIZE)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tooltip)
    apply_row_reset_icon(btn, get_active_palette())
    btn.clicked.connect(on_clicked)
    return btn


def apply_row_reset_icon(button: QPushButton, palette: ThemePalette) -> None:
    """행 단위 기본값 복원 버튼의 아이콘 색상을 현재 팔레트에 맞춰 갱신"""
    button.setIcon(get_icon("rotate-ccw", palette=palette, size=RESET_ICON_SIZE))


def apply_inline_reset_button_icon(
    button: QPushButton, palette: ThemePalette
) -> None:
    """상단 인라인 기본값 복원 버튼 아이콘 색상을 현재 팔레트에 맞춰 갱신"""
    set_button_icon(
        button,
        "rotate-ccw",
        palette=palette,
        size=INLINE_RESET_ICON_SIZE,
        pad_right=4,
    )


def format_setting_tooltip(
    desc: str,
    default_text: str = "",
    *,
    bounds_text: str = "",
    setting_key: str = "",
) -> str:
    """설명, 기본값, 허용 범위, 설정 키를 통일된 다중 줄 툴팁 문자열로 포맷"""
    lines: list[str] = []
    if desc:
        lines.append(desc)
    if default_text:
        lines.append(f"• 기본값: {default_text}")
    if bounds_text:
        lines.append(f"• 허용 범위: {bounds_text}")
    if setting_key:
        lines.append(f"• 설정 키: {setting_key}")
    return "\n".join(lines)
