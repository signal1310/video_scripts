"""화질 비교 창의 위젯 배치와 문구, 팔레트 스타일 시트

- 창 동작과 상태는 `frame_compare_window.py`가 맡고, 이 모듈은 배치와 문구만 만듦
- 화면 문구와 배치는 승인된 프로토타입과 `TERMINOLOGY.md` 6.16절을 따름
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.frame_compare import ZOOM_FIT, ZOOM_PRESETS, ComparePlan
from src.core.settings.ui_state import PREVIEW_VOLUME_MAX, VALID_PREVIEW_PLAYBACK_RATES
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE, FrameCompareCanvas
from src.gui.preview.video_preview_controls import TrackSlider

WINDOW_TITLE: str = "화질 비교"
WINDOW_WIDTH_PX: int = 1180
WINDOW_HEIGHT_PX: int = 820
WINDOW_MIN_WIDTH_PX: int = 720
WINDOW_MIN_HEIGHT_PX: int = 520
CANVAS_MIN_HEIGHT_PX: int = 300
ICON_SIZE: int = 16
BUTTON_SIZE: int = 28
TARGET_MENU_TIP: str = "비교할 대상을 고릅니다."
MODE_TIPS: dict[str, str] = {
    MODE_SPLIT: "세로 분할선을 드래그해 원본과 비교 대상을 나눠 봅니다.",
    MODE_TOGGLE: (
        "같은 자리에서 원본과 비교 대상을 바꿔 봅니다. C 키를 누를 때마다 바뀌고, "
        "마우스를 누르고 있는 동안에는 원본이 보입니다."
    ),
    MODE_SIDE: "두 화면을 나란히 두고 같은 위치와 배율로 봅니다.",
}
MODE_LABELS: tuple[tuple[str, str], ...] = (
    (MODE_SPLIT, "분할선"),
    (MODE_TOGGLE, "즉시 전환"),
    (MODE_SIDE, "좌우 비교"),
)
ZOOM_TIPS: dict[str, str] = {
    ZOOM_FIT: "영상 전체가 보이도록 맞춥니다.",
    "1": "원본 픽셀 100% 크기로 표시합니다.",
    "200": "원본 픽셀을 2배로 확대합니다.",
    "400": "원본 픽셀을 4배로 확대합니다.",
}
ZOOM_BADGE_TIP: str = "현재 화면 배율입니다. 마우스 휠로 바꾸거나 10%~800% 값을 직접 입력할 수 있습니다."
ZOOM_INPUT_MIN_PERCENT: int = 10
ZOOM_INPUT_MAX_PERCENT: int = 800
ZOOM_INPUT_WIDTH_PX: int = 76
PREV_TIP: str = "한 프레임 뒤로 이동합니다."
NEXT_TIP: str = "한 프레임 앞으로 이동합니다."
MARKS_TIP: str = "영상의 10%부터 90%까지 9개 지점으로 바로 이동합니다."
MARK_TIP_TEMPLATE: str = "영상의 {percent}% 지점으로 이동합니다."
ORIGINAL_PATH_TIP: str = "원본 파일: {path}"
RESULT_PATH_TIP: str = "결과 파일: {path}"
LOADING_TEXT: str = "프레임을 불러오는 중..."
ERROR_TITLE: str = "프레임을 불러오지 못했습니다."
SHORTCUT_GUIDE: str = (
    "1/2/3 모드   C 또는 누르고 있기 원본/비교   좌/우 방향키 구간 이동   "
    "Shift+좌/우 크게 이동   ,/. 1프레임   K 재생/일시중지   [/]/\\ 배속   M 음소거   "
    "0 맞춤   +/- 확대/축소   휠 확대   Shift+Space 미리 보기 전환   Space/Esc 닫기"
)
PLAY_TEXT: str = "재생"
PAUSE_TEXT: str = "일시중지"
RATE_TEXT: str = "재생 배속"
MUTE_TEXT: str = "음소거"
UNMUTE_TEXT: str = "음소거 해제"
PLAY_TOGGLE_TIP: str = "재생하거나 일시중지합니다. (K)"
RATE_TIP_TEMPLATE: str = "재생 배속을 바꿉니다. 현재 {rate:g}배입니다."
MUTE_TIP: str = "영상 소리를 끕니다. (M)"
UNMUTE_TIP: str = "영상 소리를 다시 켭니다. (M)"
VOLUME_TIP: str = "영상 소리 크기를 조절합니다."
RATE_LABEL_TEMPLATE: str = "{rate:g}배"
PLAY_ICON: str = "play"
PAUSE_ICON: str = "pause"
RATE_ICON: str = "gauge"
VOLUME_ICON: str = "volume-2"
VOLUME_MUTED_ICON: str = "volume-x"
VOLUME_SLIDER_WIDTH_PX: int = 88
PILL_MARGIN_PX: int = 10


@dataclass(frozen=True, slots=True)
class CompareToolbar:
    """상단 툴바 위젯 묶음"""

    filename: QLabel
    target_button: QToolButton
    target_menu: QMenu
    mode_buttons: dict[str, QToolButton]
    zoom_buttons: dict[str, QToolButton]
    zoom_input: QSpinBox


@dataclass(frozen=True, slots=True)
class CompareStage:
    """캔버스와 로딩 알약, 로딩과 오류 오버레이 위젯 묶음"""

    canvas: FrameCompareCanvas
    overlay: QWidget
    title: QLabel
    body: QLabel
    pill: QLabel


@dataclass(frozen=True, slots=True)
class CompareTimeline:
    """하단 타임라인 위젯 묶음"""

    slider: TrackSlider
    prev_button: QToolButton
    next_button: QToolButton
    time_label: QLabel
    mark_buttons: list[QToolButton]
    shortcuts: QLabel
    toggle_button: QToolButton
    rate_button: QToolButton
    rate_actions: dict[float, QAction]
    mute_button: QToolButton
    volume_slider: TrackSlider


def build_toolbar(
    parent: QWidget,
    on_mode: Callable[[str], None],
    on_zoom: Callable[[str], None],
) -> CompareToolbar:
    """파일명과 비교 대상, 비교 모드, 확대 배율 세그먼트를 세 줄로 배치

    - 큰 글꼴에서도 최소 창 폭을 넘지 않도록 파일명은 줄어들 수 있게 둠
    """
    bar = QWidget(parent)
    bar.setObjectName("CompareToolbar")
    outer = QVBoxLayout(bar)
    outer.setContentsMargins(14, 8, 14, 8)
    outer.setSpacing(6)
    first = QHBoxLayout()
    first.setSpacing(12)
    filename = QLabel("", bar)
    filename.setObjectName("CompareFileName")
    filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    first.addWidget(filename, stretch=1)
    target_button = QToolButton(bar)
    target_button.setObjectName("CompareTargetButton")
    target_button.setToolTip(TARGET_MENU_TIP)
    target_button.setAccessibleName("비교 대상")
    target_button.setCursor(Qt.CursorShape.PointingHandCursor)
    target_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    target_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    target_menu = QMenu(target_button)
    target_button.setMenu(target_menu)
    first.addWidget(target_button)
    outer.addLayout(first)
    second = QHBoxLayout()
    second.setSpacing(8)
    second.addWidget(_caption("비교 모드", bar))
    mode_host, mode_buttons = _segmented(MODE_LABELS, MODE_TIPS, on_mode, bar)
    second.addWidget(mode_host)
    second.addStretch(1)
    outer.addLayout(second)
    third = QHBoxLayout()
    third.setSpacing(8)
    third.addWidget(_caption("확대 배율", bar))
    zoom_host, zoom_buttons = _segmented(ZOOM_PRESETS, ZOOM_TIPS, on_zoom, bar)
    third.addWidget(zoom_host)
    third.addStretch(1)
    zoom_input = _zoom_input(bar)
    third.addWidget(zoom_input)
    outer.addLayout(third)
    return CompareToolbar(filename, target_button, target_menu, mode_buttons, zoom_buttons, zoom_input)


def _zoom_input(parent: QWidget) -> QSpinBox:
    """배율을 직접 입력하는 칸. 화살표 버튼 없이 숫자와 접미사 %만 보여 줌"""
    spin = QSpinBox(parent)
    spin.setObjectName("CompareZoomInput")
    spin.setRange(ZOOM_INPUT_MIN_PERCENT, ZOOM_INPUT_MAX_PERCENT)
    spin.setSuffix("%")
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    spin.setKeyboardTracking(False)
    spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    spin.setFixedWidth(ZOOM_INPUT_WIDTH_PX)
    spin.setValue(100)
    spin.setToolTip(ZOOM_BADGE_TIP)
    spin.setAccessibleName("화면 배율")
    return spin


def _caption(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("CompareCaption")
    return label


def fill_target_menu(menu: QMenu, plan: ComparePlan, on_select: Callable[[str], None]) -> None:
    """비교 대상 드롭다운 항목을 다시 채우고 기본 대상에 체크를 둠"""
    menu.clear()
    for target in plan.targets:
        action = QAction(target.label, menu)
        action.setCheckable(True)
        action.setChecked(target.key == plan.default_key)
        if target.tooltip:
            action.setToolTip(target.tooltip)
        action.triggered.connect(lambda _checked=False, key=target.key: on_select(key))
        menu.addAction(action)


def _segmented(
    items: Sequence[tuple[str, str]],
    tips: dict[str, str],
    on_click: Callable[[str], None],
    parent: QWidget,
) -> tuple[QWidget, dict[str, QToolButton]]:
    host = QWidget(parent)
    host.setObjectName("CompareSegmented")
    layout = QHBoxLayout(host)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(2)
    group = QButtonGroup(host)
    group.setExclusive(True)
    buttons: dict[str, QToolButton] = {}
    for key, label in items:
        button = QToolButton(host)
        button.setText(label)
        button.setCheckable(True)
        button.setToolTip(tips.get(key, ""))
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, value=key: on_click(value))
        group.addButton(button)
        layout.addWidget(button)
        buttons[key] = button
    return host, buttons


class CompareLoadingPill(QLabel):
    """캔버스를 가리지 않고 상단 가운데에 잠깐 뜨는 로딩 알약 배지

    - 마우스 사건을 통과시켜 아래 캔버스 조작을 막지 않음
    - 부모 캔버스 크기가 바뀌면 가운데 위치를 다시 잡음
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(LOADING_TEXT, parent)
        self.setObjectName("CompareLoadingPill")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.adjustSize()
        parent.installEventFilter(self)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """부모 캔버스 크기가 바뀌면 가운데 위치를 다시 잡음"""
        if a1 is not None and a1.type() == QEvent.Type.Resize:
            self.reposition()
        return super().eventFilter(a0, a1)

    def reposition(self) -> None:
        """캔버스 상단 가운데로 옮김"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(max(0, (parent.width() - self.width()) // 2), PILL_MARGIN_PX)

    def show(self) -> None:
        """표시할 때마다 가운데 위치를 다시 계산"""
        self.reposition()
        super().show()


def build_stage(
    parent: QWidget,
    on_scale: Callable[[float], None],
    on_side: Callable[[bool], None],
) -> CompareStage:
    """캔버스와 그 위에 겹치는 로딩, 오류 오버레이"""
    wrap = QWidget(parent)
    wrap.setObjectName("CompareStageWrap")
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(14, 12, 14, 0)
    canvas = FrameCompareCanvas(wrap)
    canvas.setMinimumHeight(CANVAS_MIN_HEIGHT_PX)
    canvas.scale_changed.connect(on_scale)
    canvas.side_changed.connect(on_side)
    layout.addWidget(canvas)
    overlay = QWidget(canvas)
    overlay.setObjectName("CompareOverlay")
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    overlay_layout = QVBoxLayout(overlay)
    overlay_layout.setContentsMargins(24, 24, 24, 24)
    overlay_layout.addStretch(1)
    title = QLabel("", overlay)
    title.setObjectName("CompareOverlayTitle")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setWordWrap(True)
    body = QLabel("", overlay)
    body.setObjectName("CompareOverlayBody")
    body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body.setWordWrap(True)
    overlay_layout.addWidget(title)
    overlay_layout.addWidget(body)
    overlay_layout.addStretch(1)
    canvas_layout = QVBoxLayout(canvas)
    canvas_layout.setContentsMargins(0, 0, 0, 0)
    canvas_layout.addWidget(overlay)
    overlay.hide()
    pill = CompareLoadingPill(canvas)
    pill.hide()
    return CompareStage(canvas, overlay, title, body, pill)


def _rate_button(parent: QWidget, on_rate: Callable[[float], None]) -> tuple[QToolButton, dict[float, QAction]]:
    """재생 배속 메뉴를 단 아이콘 버튼과 배속별 메뉴 항목"""
    button = _icon_button(RATE_ICON, RATE_TIP_TEMPLATE.format(rate=1.0), RATE_TEXT, parent)
    button.setObjectName("CompareRateButton")
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    group = QActionGroup(menu)
    group.setExclusive(True)
    actions: dict[float, QAction] = {}
    for rate in VALID_PREVIEW_PLAYBACK_RATES:
        action = QAction(RATE_LABEL_TEMPLATE.format(rate=rate), menu)
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, value=rate: on_rate(value))
        group.addAction(action)
        menu.addAction(action)
        actions[rate] = action
    button.setMenu(menu)
    return button, actions


def _volume_slider(parent: QWidget, on_volume: Callable[[int], None]) -> TrackSlider:
    """컴팩트 볼륨 막대. 방향키는 창이 받도록 포커스를 두지 않음"""
    slider = TrackSlider(Qt.Orientation.Horizontal, parent)
    slider.setObjectName("CompareVolumeSlider")
    slider.setToolTip(VOLUME_TIP)
    slider.setRange(0, PREVIEW_VOLUME_MAX)
    slider.setValue(PREVIEW_VOLUME_MAX)
    slider.setFixedWidth(VOLUME_SLIDER_WIDTH_PX)
    slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    slider.valueChanged.connect(on_volume)
    return slider


def build_timeline(
    parent: QWidget,
    on_step: Callable[[int], None],
    on_mark: Callable[[int], None],
    on_slider: Callable[[int], None],
    on_toggle: Callable[[], None],
    on_rate: Callable[[float], None],
    on_mute: Callable[[], None],
    on_volume: Callable[[int], None],
) -> CompareTimeline:
    """슬라이더, 시각, 재생과 프레임 이동, 배속과 음소거, 대표 시점 칩, 단축키 안내"""
    bar = QWidget(parent)
    bar.setObjectName("CompareTimeline")
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(14, 10, 14, 11)
    layout.setSpacing(7)
    row = QHBoxLayout()
    row.setSpacing(9)
    toggle_button = _icon_button(PLAY_ICON, PLAY_TOGGLE_TIP, PLAY_TEXT, bar)
    prev_button = _icon_button("chevron-left", PREV_TIP, "이전 프레임", bar)
    slider = TrackSlider(Qt.Orientation.Horizontal, bar)
    slider.setObjectName("CompareTimelineSlider")
    slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    slider.setRange(0, 0)
    slider.valueChanged.connect(on_slider)
    time_label = QLabel("00:00.000 / 00:00.000", bar)
    time_label.setObjectName("CompareTimeLabel")
    next_button = _icon_button("chevron-right", NEXT_TIP, "다음 프레임", bar)
    rate_button, rate_actions = _rate_button(bar, on_rate)
    mute_button = _icon_button(VOLUME_ICON, MUTE_TIP, MUTE_TEXT, bar)
    volume_slider = _volume_slider(bar, on_volume)
    toggle_button.clicked.connect(lambda _checked=False: on_toggle())
    mute_button.clicked.connect(lambda _checked=False: on_mute())
    prev_button.clicked.connect(lambda _checked=False: on_step(-1))
    next_button.clicked.connect(lambda _checked=False: on_step(1))
    row.addWidget(toggle_button)
    row.addWidget(prev_button)
    row.addWidget(slider, stretch=1)
    row.addWidget(time_label)
    row.addWidget(next_button)
    row.addWidget(rate_button)
    row.addWidget(mute_button)
    row.addWidget(volume_slider)
    layout.addLayout(row)
    marks_host = QWidget(bar)
    marks = FlowLayout(marks_host, spacing=6)
    marks.setContentsMargins(0, 0, 0, 0)
    caption = _caption("대표 시점", marks_host)
    caption.setToolTip(MARKS_TIP)
    marks.addWidget(caption)
    buttons: list[QToolButton] = []
    for percent in range(10, 100, 10):
        chip = QToolButton(marks_host)
        chip.setObjectName("CompareMarkChip")
        chip.setText(f"{percent}%")
        chip.setToolTip(MARK_TIP_TEMPLATE.format(percent=percent))
        chip.setCheckable(True)
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda _checked=False, value=percent: on_mark(value))
        marks.addWidget(chip)
        buttons.append(chip)
    layout.addWidget(marks_host)
    shortcuts = QLabel(SHORTCUT_GUIDE, bar)
    shortcuts.setObjectName("CompareShortcuts")
    shortcuts.setWordWrap(True)
    shortcuts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout.addWidget(shortcuts)
    return CompareTimeline(
        slider,
        prev_button,
        next_button,
        time_label,
        buttons,
        shortcuts,
        toggle_button,
        rate_button,
        rate_actions,
        mute_button,
        volume_slider,
    )


def _icon_button(icon_name: str, tip: str, accessible_name: str, parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("CompareIconButton")
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setToolTip(tip)
    button.setAccessibleName(accessible_name)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def sync_zoom_input(canvas: FrameCompareCanvas, toolbar: CompareToolbar) -> None:
    """캔버스 배율을 입력 칸에 반영. 입력 확정 신호가 되돌아오지 않게 막음"""
    spin = toolbar.zoom_input
    spin.blockSignals(True)
    spin.setValue(round(canvas.scale() * 100))
    spin.blockSignals(False)


def apply_zoom_input(canvas: FrameCompareCanvas, toolbar: CompareToolbar) -> bool:
    """입력한 배율을 적용하고 프리셋 체크를 풀었으면 참"""
    if not canvas.apply_zoom_scale(toolbar.zoom_input.value() / 100.0):
        return False
    clear_button_checks(toolbar.zoom_buttons)
    sync_zoom_input(canvas, toolbar)
    return True


def manual_zoom(canvas: FrameCompareCanvas, toolbar: CompareToolbar, direction: int) -> None:
    """키보드 단계 확대, 축소. 배율 버튼 체크는 풀고 입력 칸에 현재 배율을 남김"""
    canvas.zoom_step(direction)
    clear_button_checks(toolbar.zoom_buttons)
    sync_zoom_input(canvas, toolbar)


def clear_button_checks(buttons: dict[str, QToolButton]) -> None:
    """배타 그룹에서도 버튼 체크를 모두 풀어 현재 배율만 배지에 남길 수 있게 함"""
    groups: list[QButtonGroup] = []
    for button in buttons.values():
        group = button.group()
        if group is not None:
            groups.append(group)
    for group in groups:
        group.setExclusive(False)
    for button in buttons.values():
        button.setChecked(False)
    for group in groups:
        group.setExclusive(True)
