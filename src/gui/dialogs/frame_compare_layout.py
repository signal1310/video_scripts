"""화질 비교 창의 캔버스, 하단 재생 바 배치와 문구

- 상단 2줄 조작 바는 `frame_compare_top_bar.py`, 창 동작은 `frame_compare_window.py`가 맡음
- 화면 문구와 배치는 승인된 프로토타입과 `TERMINOLOGY.md` 6.16절, 6.17절을 따름
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.frame_compare import ZOOM_FIT
from src.core.settings.ui_state import PREVIEW_VOLUME_MAX, VALID_PREVIEW_PLAYBACK_RATES
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE, FrameCompareCanvas
from src.gui.preview.video_preview_controls import TrackSlider
from src.gui.widgets.shortcut_guide_popover import ShortcutGuideButton

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
    MODE_TOGGLE: "같은 자리에서 원본과 비교 대상을 바꿔 봅니다. 마우스를 누르고 있는 동안에는 원본이 보입니다.",
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
COLLAPSE_TIP: str = "상단 조작 바를 접어 영상을 넓게 봅니다."
EXPAND_TIP: str = "상단 조작 바를 다시 펼칩니다."
PREV_TIP: str = "한 프레임 뒤로 이동합니다."
NEXT_TIP: str = "한 프레임 앞으로 이동합니다."
FULLSCREEN_TIP: str = "화질 비교 창을 전체화면과 이전 크기 사이에서 전환합니다."
GUIDE_TITLE: str = "화질 비교 창 단축키"
MODE_ACTION_IDS: dict[str, str] = {
    MODE_SPLIT: "compare.mode_split",
    MODE_TOGGLE: "compare.mode_toggle",
    MODE_SIDE: "compare.mode_side",
}
ZOOM_ACTION_IDS: dict[str, str] = {
    ZOOM_FIT: "compare.zoom_fit",
    "1": "",
    "200": "",
    "400": "",
}
# - 화질 비교 창이 받는 명령 순서: 화질 비교 전용이 미리 보기 공용 키보다 우선함
COMPARE_ACTION_ORDER: tuple[str, ...] = (
    "compare.mode_split",
    "compare.mode_toggle",
    "compare.mode_side",
    "compare.toggle_original",
    "compare.zoom_fit",
    "compare.zoom_in",
    "compare.zoom_out",
    "compare.toggle_controls",
    "classify.compare_selected",
    "preview.seek_backward",
    "preview.seek_forward",
    "preview.seek_backward_large",
    "preview.seek_forward_large",
    "preview.frame_backward",
    "preview.frame_forward",
    "preview.rate_down",
    "preview.rate_up",
    "preview.rate_reset",
    "preview.toggle_playback",
    "preview.toggle_mute",
    "preview.toggle_fullscreen",
)
COMPARE_GUIDE_ACTIONS: tuple[str, ...] = COMPARE_ACTION_ORDER
ORIGINAL_PATH_TIP: str = "원본 파일: {path}"
RESULT_PATH_TIP: str = "결과 파일: {path}"
LOADING_TEXT: str = "프레임을 불러오는 중..."
ERROR_TITLE: str = "프레임을 불러오지 못했습니다."
PLAY_TEXT: str = "재생"
PAUSE_TEXT: str = "일시중지"
RATE_TEXT: str = "재생 배속"
MUTE_TEXT: str = "음소거"
UNMUTE_TEXT: str = "음소거 해제"
PLAY_TOGGLE_TIP: str = "재생하거나 일시중지합니다."
RATE_TIP_TEMPLATE: str = "재생 배속을 바꿉니다. 현재 {rate:g}배입니다."
MUTE_TIP: str = "영상 소리를 끕니다."
UNMUTE_TIP: str = "영상 소리를 다시 켭니다."
VOLUME_TIP: str = "영상 소리 크기를 조절합니다."
RATE_LABEL_TEMPLATE: str = "{rate:g}배"
PLAY_ICON: str = "play"
PAUSE_ICON: str = "pause"
RATE_ICON: str = "gauge"
VOLUME_ICON: str = "volume-2"
VOLUME_MUTED_ICON: str = "volume-x"
VOLUME_SLIDER_WIDTH_PX: int = 88
PILL_MARGIN_PX: int = 10
BAR_DIVIDER_WIDTH_PX: int = 1
BAR_DIVIDER_HEIGHT_PX: int = 18


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
    """하단 재생 바 위젯 묶음"""

    slider: TrackSlider
    prev_button: QToolButton
    next_button: QToolButton
    time_label: QLabel
    toggle_button: QToolButton
    rate_button: QToolButton
    rate_actions: dict[float, QAction]
    mute_button: QToolButton
    volume_slider: TrackSlider
    fullscreen_button: QToolButton
    guide_button: ShortcutGuideButton


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
    on_slider: Callable[[int], None],
    on_toggle: Callable[[], None],
    on_rate: Callable[[float], None],
    on_mute: Callable[[], None],
    on_volume: Callable[[int], None],
    on_fullscreen: Callable[[], None],
    get_shortcuts: Callable[[], Mapping[str, str]],
) -> CompareTimeline:
    """재생, 프레임 이동, 위치 막대, 시각, 배속, 음소거, 음량, 전체화면, 단축키 안내를 한 줄로 배치"""
    bar = QWidget(parent)
    bar.setObjectName("CompareTimeline")
    row = QHBoxLayout(bar)
    row.setContentsMargins(14, 8, 12, 8)
    row.setSpacing(8)
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
    fullscreen_button = _icon_button("maximize-2", FULLSCREEN_TIP, "전체화면 전환", bar)
    guide_button = ShortcutGuideButton(
        bar, get_shortcuts, COMPARE_GUIDE_ACTIONS, title=GUIDE_TITLE
    )
    toggle_button.clicked.connect(lambda _checked=False: on_toggle())
    mute_button.clicked.connect(lambda _checked=False: on_mute())
    fullscreen_button.clicked.connect(lambda _checked=False: on_fullscreen())
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
    row.addWidget(_bar_divider(bar))
    row.addWidget(fullscreen_button)
    row.addWidget(guide_button)
    return CompareTimeline(
        slider,
        prev_button,
        next_button,
        time_label,
        toggle_button,
        rate_button,
        rate_actions,
        mute_button,
        volume_slider,
        fullscreen_button,
        guide_button,
    )


def _bar_divider(parent: QWidget) -> QFrame:
    """하단 재생 조작과 전체화면, 단축키 안내를 나누는 세로 구분선"""
    divider = QFrame(parent)
    divider.setObjectName("CompareBarDivider")
    divider.setFixedSize(BAR_DIVIDER_WIDTH_PX, BAR_DIVIDER_HEIGHT_PX)
    return divider



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

