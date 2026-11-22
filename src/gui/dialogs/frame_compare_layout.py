"""화질 비교 창의 위젯 배치와 문구, 팔레트 스타일 시트

- 창 동작과 상태는 `frame_compare_window.py`가 맡고, 이 모듈은 배치와 문구만 만듦
- 화면 문구와 배치는 승인된 프로토타입과 `TERMINOLOGY.md` 6.16절을 따름
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.frame_compare import ZOOM_FIT, ZOOM_PRESETS, ComparePlan
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE, FrameCompareCanvas
from src.gui.preview.video_preview_controls import TrackSlider
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette

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
ZOOM_BADGE_TIP: str = "현재 화면 배율입니다. 마우스 휠로 바꿀 수 있습니다."
PREV_TIP: str = "한 프레임 뒤로 이동합니다."
NEXT_TIP: str = "한 프레임 앞으로 이동합니다."
MARKS_TIP: str = "영상의 10%부터 90%까지 9개 지점으로 바로 이동합니다."
MARK_TIP_TEMPLATE: str = "영상의 {percent}% 지점으로 이동합니다."
LOADING_TEXT: str = "프레임을 불러오는 중..."
ERROR_TITLE: str = "프레임을 불러오지 못했습니다."
SHORTCUT_GUIDE: str = (
    "1/2/3 모드   C 또는 누르고 있기 원본/비교   좌/우 방향키 구간 이동   "
    "Shift+좌/우 크게 이동   ,/. 1프레임   0 맞춤   +/- 확대/축소   휠 확대   Space/Esc 닫기"
)
_RADII = "6px"
_ROW_SPACING_PX = 8


@dataclass(frozen=True, slots=True)
class CompareToolbar:
    """상단 툴바 위젯 묶음"""

    filename: QLabel
    target_button: QToolButton
    target_menu: QMenu
    mode_buttons: dict[str, QToolButton]
    zoom_buttons: dict[str, QToolButton]
    zoom_badge: QLabel


@dataclass(frozen=True, slots=True)
class CompareMeta:
    """메타 줄 위젯 묶음"""

    original: QLabel
    target: QLabel
    time: QLabel


@dataclass(frozen=True, slots=True)
class CompareStage:
    """캔버스와 로딩, 오류 오버레이 위젯 묶음"""

    canvas: FrameCompareCanvas
    overlay: QWidget
    title: QLabel
    body: QLabel


@dataclass(frozen=True, slots=True)
class CompareTimeline:
    """하단 타임라인 위젯 묶음"""

    slider: TrackSlider
    prev_button: QToolButton
    next_button: QToolButton
    time_label: QLabel
    mark_buttons: list[QToolButton]
    shortcuts: QLabel


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
    zoom_badge = QLabel("100%", bar)
    zoom_badge.setObjectName("CompareZoomBadge")
    zoom_badge.setToolTip(ZOOM_BADGE_TIP)
    third.addWidget(zoom_badge)
    outer.addLayout(third)
    return CompareToolbar(filename, target_button, target_menu, mode_buttons, zoom_buttons, zoom_badge)


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


def build_meta_row(parent: QWidget) -> CompareMeta:
    """원본, 비교 대상, 시점을 한 줄로 보여 주는 메타 줄"""
    row = QWidget(parent)
    row.setObjectName("CompareMeta")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(14, 7, 14, 7)
    layout.setSpacing(_ROW_SPACING_PX)
    labels = (QLabel("", row), QLabel("", row), QLabel("", row))
    for index, label in enumerate(labels):
        label.setObjectName("CompareMetaText")
        if index:
            separator = QLabel("/", row)
            separator.setObjectName("CompareMetaSeparator")
            layout.addWidget(separator)
        layout.addWidget(label)
    layout.addStretch(1)
    return CompareMeta(*labels)


def build_score_label(parent: QWidget) -> QLabel:
    """선택 규격의 화질 분석 점수 줄"""
    label = QLabel("", parent)
    label.setObjectName("CompareScore")
    label.setWordWrap(True)
    label.hide()
    return label


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
    return CompareStage(canvas, overlay, title, body)


def build_timeline(
    parent: QWidget,
    on_step: Callable[[int], None],
    on_mark: Callable[[int], None],
    on_slider: Callable[[int], None],
) -> CompareTimeline:
    """슬라이더, 시각, 이전/다음 프레임 버튼, 대표 시점 칩, 단축키 안내"""
    bar = QWidget(parent)
    bar.setObjectName("CompareTimeline")
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(14, 10, 14, 11)
    layout.setSpacing(7)
    row = QHBoxLayout()
    row.setSpacing(9)
    prev_button = _icon_button("chevron-left", PREV_TIP, "이전 프레임", bar)
    slider = TrackSlider(Qt.Orientation.Horizontal, bar)
    slider.setObjectName("CompareTimelineSlider")
    slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    slider.setRange(0, 0)
    slider.valueChanged.connect(on_slider)
    time_label = QLabel("00:00.000 / 00:00.000", bar)
    time_label.setObjectName("CompareTimeLabel")
    next_button = _icon_button("chevron-right", NEXT_TIP, "다음 프레임", bar)
    prev_button.clicked.connect(lambda _checked=False: on_step(-1))
    next_button.clicked.connect(lambda _checked=False: on_step(1))
    row.addWidget(prev_button)
    row.addWidget(slider, stretch=1)
    row.addWidget(time_label)
    row.addWidget(next_button)
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
    hint = QLabel("i", marks_host)
    hint.setObjectName("CompareMarksHint")
    hint.setToolTip(MARKS_TIP)
    marks.addWidget(hint)
    layout.addWidget(marks_host)
    shortcuts = QLabel(SHORTCUT_GUIDE, bar)
    shortcuts.setObjectName("CompareShortcuts")
    shortcuts.setWordWrap(True)
    shortcuts.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout.addWidget(shortcuts)
    return CompareTimeline(slider, prev_button, next_button, time_label, buttons, shortcuts)


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


def refresh_icons(toolbar: CompareToolbar, timeline: CompareTimeline, palette: ThemePalette) -> None:
    """테마가 바뀌면 아이콘 색을 다시 입힘"""
    toolbar.target_button.setIcon(get_icon("chevron-down", color=palette.text_secondary, size=ICON_SIZE))
    timeline.prev_button.setIcon(get_icon("chevron-left", color=palette.text_primary, size=ICON_SIZE))
    timeline.next_button.setIcon(get_icon("chevron-right", color=palette.text_primary, size=ICON_SIZE))


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


def build_stylesheet(palette: ThemePalette) -> str:
    """창 전체에 적용하는 팔레트 기반 스타일 시트"""
    return f"""
QWidget#CompareToolbar, QWidget#CompareTimeline, QWidget#CompareStageWrap {{
    background: {palette.bg_card}; border-bottom: 1px solid {palette.border_subtle};
}}
QWidget#CompareTimeline {{ border-top: 1px solid {palette.border_subtle}; border-bottom: 0; }}
QLabel#CompareFileName {{ color: {palette.text_primary}; font-weight: 600; }}
QLabel#CompareCaption {{ color: {palette.text_muted}; }}
QWidget#CompareMeta {{ background: {palette.bg_card}; border-bottom: 1px solid {palette.border_subtle}; }}
QLabel#CompareMetaText {{ color: {palette.text_secondary}; }}
QLabel#CompareMetaSeparator {{ color: {palette.text_muted}; }}
QLabel#CompareScore {{
    background: {palette.subtitle_bg}; color: {palette.subtitle_text};
    border-bottom: 1px solid {palette.subtitle_border}; padding: 6px 14px;
}}
QLabel#CompareZoomBadge {{
    color: {palette.text_secondary}; border: 1px solid {palette.border_subtle};
    border-radius: 999px; padding: 2px 8px; background: {palette.bg_input};
}}
QToolButton#CompareTargetButton {{
    color: {palette.text_primary}; background: {palette.bg_input};
    border: 1px solid {palette.border_subtle}; border-radius: {_RADII}; padding: 4px 8px;
}}
QToolButton#CompareTargetButton:hover {{ border-color: {palette.border_focus}; background: {palette.bg_hover}; }}
QWidget#CompareSegmented {{
    background: {palette.bg_input}; border: 1px solid {palette.border_subtle}; border-radius: {_RADII};
}}
QWidget#CompareSegmented QToolButton {{
    border: 0; background: transparent; color: {palette.text_secondary};
    border-radius: 4px; padding: 3px 10px;
}}
QWidget#CompareSegmented QToolButton:hover {{ background: {palette.bg_hover}; color: {palette.text_primary}; }}
QWidget#CompareSegmented QToolButton:checked {{
    background: {palette.bg_selected}; color: {palette.text_primary}; font-weight: 600;
}}
QToolButton#CompareIconButton {{ border: 0; background: transparent; border-radius: 4px; }}
QToolButton#CompareIconButton:hover {{ background: {palette.bg_hover}; }}
QToolButton#CompareMarkChip {{
    color: {palette.text_secondary}; border: 1px solid {palette.border_subtle};
    border-radius: 999px; padding: 1px 8px; background: {palette.bg_input};
}}
QToolButton#CompareMarkChip:checked {{
    background: {palette.bg_selected}; border-color: {palette.action_primary_border};
    color: {palette.text_primary}; font-weight: 600;
}}
QLabel#CompareMarksHint {{ color: {palette.text_muted}; }}
QLabel#CompareTimeLabel {{ color: {palette.text_secondary}; }}
QLabel#CompareShortcuts {{ color: {palette.text_muted}; }}
QWidget#CompareOverlay {{ background: {palette.bg_base}; border: 1px solid {palette.border_subtle}; }}
QLabel#CompareOverlayTitle {{ color: {palette.text_primary}; font-weight: 700; }}
QLabel#CompareOverlayBody {{ color: {palette.text_secondary}; }}
QSlider#CompareTimelineSlider::groove:horizontal {{
    height: 3px; background: {palette.progress_track_bg}; border-radius: 2px;
}}
QSlider#CompareTimelineSlider::sub-page:horizontal {{ background: {palette.action_primary_bg}; border-radius: 2px; }}
QSlider#CompareTimelineSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: {palette.action_primary_bg};
}}
"""
