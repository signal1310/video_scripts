"""화질 비교 창의 캔버스 배치와 문구

- 상단 2줄 조작 바는 `frame_compare_top_bar.py`, 하단 재생 바는 공용 `PlaybackControls`, 창 동작은 `frame_compare_window.py`가 맡음
- 화면 문구와 배치는 승인된 프로토타입과 `TERMINOLOGY.md` 6.16절, 6.17절을 따름
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.core.analysis.frame_compare import ZOOM_FIT
from src.gui.dialogs.frame_compare_canvas import (
    MODE_SIDE,
    MODE_SPLIT,
    MODE_TOGGLE,
    FrameCompareCanvas,
)

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
PILL_MARGIN_PX: int = 10


@dataclass(frozen=True, slots=True)
class CompareStage:
    """캔버스와 로딩 알약, 로딩과 오류 오버레이 위젯 묶음"""

    canvas: FrameCompareCanvas
    overlay: QWidget
    title: QLabel
    body: QLabel
    pill: QLabel


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

