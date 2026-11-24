"""화질 비교 창 상단 2줄 조작 바: 도킹, 접기, 상단 핫존 반투명 오버레이

- 펼침은 캔버스 위쪽에 도킹해 영상을 가리지 않고, 접힘은 캔버스를 위까지 넓힌 뒤 마우스가 위쪽 가장자리에 올 때만 오버레이로 띄움
- 메뉴, 배율 입력, 단축키 팝오버가 열려 있으면 자동 숨김을 멈춰 조작 중인 화면이 사라지지 않게 함
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QEnterEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
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
from src.core.settings.shortcuts import format_tooltip_with_shortcut
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import FrameCompareCanvas
from src.gui.dialogs.frame_compare_meta import CompareMeta, build_meta_row, build_score_label
from src.gui.menus.manual_target_menu import FRAME_CANDIDATE_SUFFIX
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette

TOP_BAR_OBJECT: str = "CompareTopBar"
OVERLAY_PROPERTY: str = "overlay"
AUTO_HIDE_MS: int = 600
TOP_HOTZONE_PX: int = 44
DOCK_TOP_GAP_PX: int = 6
LABEL_CLEARANCE_PX: int = 8
BAR_MARGIN_H: int = 12
BAR_MARGIN_V: int = 6
ROW_SPACING: int = 8


class CompareTopBar(QObject):
    """파일명, 모드, 배율, 비교 대상과 메타, 점수 칩을 담은 2줄 조작 바

    - 캔버스 위에 겹쳐 두고, 펼침 상태에서는 캔버스 레이아웃 위 여백을 늘려 도킹함
    - 접힘 상태에서는 캔버스 위쪽 가장자리에 마우스가 올 때만 반투명 오버레이로 나타남
    """

    def __init__(
        self,
        window: QWidget,
        canvas: FrameCompareCanvas,
        *,
        on_mode: Callable[[str], None],
        on_zoom: Callable[[str], None],
        on_target: Callable[[str], None],
        collapsed: bool = False,
        on_collapsed_changed: Callable[[bool], None] | None = None,
        on_apply_output: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._canvas = canvas
        self._wrap = canvas.parentWidget()
        self._original_top_margin = self._read_top_margin()
        self._on_target = on_target
        self._on_apply_output = on_apply_output
        self._on_collapsed_changed = on_collapsed_changed
        self._collapsed: bool = False
        self._overlay_visible: bool = False
        self._safety_check: Callable[[], bool] | None = None
        self._shortcuts: Mapping[str, str] = {}

        self.container = QWidget(self._wrap)
        self.container.setObjectName(TOP_BAR_OBJECT)
        self.container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rows = QVBoxLayout(self.container)
        rows.setContentsMargins(BAR_MARGIN_H, BAR_MARGIN_V, BAR_MARGIN_H, BAR_MARGIN_V)
        rows.setSpacing(4)

        first = QHBoxLayout()
        first.setSpacing(ROW_SPACING)
        self.filename = QLabel("", self.container)
        self.filename.setObjectName("CompareFileName")
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        first.addWidget(self.filename, stretch=1)
        self.mode_buttons = self._segmented(layout.MODE_LABELS, layout.MODE_TIPS, on_mode)
        first.addWidget(self.mode_buttons["split"].parentWidget())
        self.zoom_buttons = self._segmented(ZOOM_PRESETS, layout.ZOOM_TIPS, on_zoom)
        first.addWidget(self.zoom_buttons[ZOOM_FIT].parentWidget())
        self.zoom_input = self._zoom_input()
        first.addWidget(self.zoom_input)
        self.target_button, self.target_menu = self._menu_button(
            "CompareTargetButton", "비교 대상", layout.TARGET_MENU_TIP
        )
        first.addWidget(self.target_button)
        self.collapse_button = QToolButton(self.container)
        self.collapse_button.setObjectName("CompareCollapseButton")
        self.collapse_button.setFixedSize(layout.BUTTON_SIZE, layout.BUTTON_SIZE)
        self.collapse_button.setIconSize(QSize(layout.ICON_SIZE, layout.ICON_SIZE))
        self.collapse_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setAccessibleName("조작 바 접기")
        first.addWidget(self.collapse_button)
        rows.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(ROW_SPACING)
        self.meta: CompareMeta = build_meta_row(self.container)
        meta_row = self.meta.original.parentWidget()
        self.score = build_score_label(self.container)
        if meta_row is not None:
            second.addWidget(meta_row, stretch=1)
            meta_layout = meta_row.layout()
            if meta_layout is not None:
                meta_layout.addWidget(self.score)
        self.output_button, self.output_menu = self._menu_button(
            "CompareOutputButton", "출력 해상도", layout.OUTPUT_MENU_TIP
        )
        second.addWidget(self.output_button)
        self.apply_output_button = QToolButton(self.container)
        self.apply_output_button.setObjectName("CompareApplyOutputButton")
        self.apply_output_button.setFixedSize(layout.BUTTON_SIZE, layout.BUTTON_SIZE)
        self.apply_output_button.setIconSize(QSize(layout.ICON_SIZE, layout.ICON_SIZE))
        self.apply_output_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_output_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_output_button.setAccessibleName(layout.APPLY_OUTPUT_NAME)
        self.apply_output_button.clicked.connect(self._apply_output_clicked)
        second.addWidget(self.apply_output_button)
        rows.addLayout(second)
        self.set_output_state(
            "-",
            enabled=False,
            action_enabled=False,
            tip=layout.OUTPUT_UNAVAILABLE_TIP,
            action_tip=layout.APPLY_OUTPUT_NO_EVAL_TIP,
        )

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self.hide_overlay_if_allowed)
        self.collapse_button.clicked.connect(lambda _checked=False: self.toggle_collapsed())
        for watched in (canvas, window, self.container, self.zoom_input):
            watched.installEventFilter(self)
        zoom_editor = self.zoom_input.lineEdit()
        if zoom_editor is not None:
            zoom_editor.installEventFilter(self)
        self.set_collapsed(collapsed)

    def set_safety_check(self, check: Callable[[], bool]) -> None:
        """자동 숨김을 막아야 하는 조건(메뉴, 입력 포커스, 팝오버)을 알려 줌"""
        self._safety_check = check

    def set_collapsed(self, collapsed: bool) -> None:
        """접힘과 펼침을 바꿔 도킹 여백과 버튼 아이콘을 맞춤"""
        self._collapsed = collapsed
        self._hide_timer.stop()
        self._overlay_visible = False
        self.container.setProperty(OVERLAY_PROPERTY, False)
        self._repolish()
        self._apply_top_margin(docked=not collapsed)
        self.container.setVisible(not collapsed)
        self._canvas.set_top_inset(0)
        self._update_collapse_button()
        self._place()

    def toggle_collapsed(self) -> None:
        """접기와 펼치기를 번갈아 전환하고 바뀐 상태를 저장하도록 알림"""
        self.set_collapsed(not self._collapsed)
        if self._on_collapsed_changed is not None:
            self._on_collapsed_changed(self._collapsed)

    def is_collapsed(self) -> bool:
        """지금 접힌 상태인지"""
        return self._collapsed

    def is_overlay_visible(self) -> bool:
        """접힘 상태에서 상단 오버레이가 보이는지"""
        return self._overlay_visible

    def note_mouse_activity(self, canvas_pos: QPoint) -> None:
        """캔버스 위 마우스 위치로 오버레이를 띄우거나 자동 숨김 시간을 다시 셈"""
        if not self._collapsed:
            return
        if not self._overlay_visible:
            if self._in_top_hotzone(canvas_pos):
                self.reveal()
            return
        if self._in_top_hotzone(canvas_pos):
            self._hide_timer.stop()
            return
        self._hide_timer.start()

    def _hold_overlay(self) -> None:
        """오버레이 위에 마우스가 있는 동안 자동 숨김을 멈춤"""
        if self._overlay_visible:
            self._hide_timer.stop()

    def reveal(self) -> None:
        """접힘 상태에서 상단 2줄을 반투명 오버레이로 띄움"""
        if not self._collapsed or self._overlay_visible:
            return
        self._overlay_visible = True
        self.container.setProperty(OVERLAY_PROPERTY, True)
        self._repolish()
        self.container.show()
        self.container.raise_()
        self._place()
        self._hide_timer.start()

    def hide_overlay_if_allowed(self) -> None:
        """안전 조건이 걸려 있지 않을 때만 오버레이를 숨김"""
        if not self._collapsed or not self._overlay_visible:
            return
        if self._safety_check is not None and not self._safety_check():
            self._hide_timer.start()
            return
        self._overlay_visible = False
        self.container.setProperty(OVERLAY_PROPERTY, False)
        self._repolish()
        self.container.hide()
        self._canvas.set_top_inset(0)

    def close_open_menu(self) -> bool:
        """비교 대상이나 출력 해상도 메뉴가 열려 있으면 닫고 참, 아니면 거짓"""
        for menu in (self.output_menu, self.target_menu):
            if menu.isVisible():
                menu.close()
                return True
        return False

    def _apply_output_clicked(self) -> None:
        """이 규격으로 지정 버튼을 누르면 뷰에 지정을 요청"""
        if self._on_apply_output is not None:
            self._on_apply_output()

    def set_output_state(
        self,
        value_text: str,
        *,
        enabled: bool,
        action_enabled: bool,
        tip: str,
        action_tip: str,
    ) -> None:
        """출력 해상도 드롭다운 문구와 이 규격으로 지정 버튼의 활성, 툴팁을 맞춤"""
        self.output_button.setText(f"{layout.OUTPUT_LABEL_PREFIX}{value_text}")
        self.output_button.setEnabled(enabled)
        self.output_button.setToolTip(tip)
        self.apply_output_button.setEnabled(action_enabled)
        self.apply_output_button.setToolTip(action_tip)
        self._theme_apply_output(action_enabled)

    def _theme_apply_output(self, active: bool) -> None:
        """지정 가능한 동안은 강조색, 아니면 흐린 색 아이콘으로 구분"""
        palette = get_active_palette()
        color = palette.accent_blue if active else palette.media_muted
        self.apply_output_button.setIcon(
            get_icon("check", color=color, size=layout.ICON_SIZE)
        )

    def rebuild_target_menu(self, plan: ComparePlan) -> None:
        """비교 대상 드롭다운 항목을 다시 채우고 기본 대상에 체크를 둠

        - 화질 분석 축소 후보 규격은 메뉴에서만 (축소 후보)를 덧붙이고 캔버스 라벨은 그대로 둠
        """
        self.target_menu.clear()
        for target in plan.targets:
            label = target.label
            if plan.candidate_tier is not None and target.key == plan.candidate_tier:
                label += FRAME_CANDIDATE_SUFFIX
            action = QAction(label, self.target_menu)
            action.setData(target.key)
            action.setCheckable(True)
            action.setChecked(target.key == plan.default_key)
            if target.tooltip:
                action.setToolTip(target.tooltip)
            action.triggered.connect(lambda _checked=False, key=target.key: self._on_target(key))
            self.target_menu.addAction(action)
        self.target_button.setEnabled(bool(plan.targets))

    def sync_zoom_input(self) -> None:
        """캔버스 배율을 입력 칸에 반영, 입력 확정 신호가 되돌아오지 않게 막음"""
        self.zoom_input.blockSignals(True)
        self.zoom_input.setValue(round(self._canvas.scale() * 100))
        self.zoom_input.blockSignals(False)

    def apply_zoom_input(self) -> bool:
        """입력한 배율을 적용하고 프리셋 체크를 풀었으면 참"""
        if not self._canvas.apply_zoom_scale(self.zoom_input.value() / 100.0):
            return False
        self.clear_zoom_checks()
        self.sync_zoom_input()
        return True

    def _cancel_zoom_input(self) -> None:
        """입력 중인 배율을 현재 캔버스 배율로 되돌리고 포커스를 창에 돌려줌

        - 포커스가 떠나며 편집 확정 신호가 되돌아오면 프리셋 체크와 맞춤 상태가 풀리므로 신호를 막고 옮김
        """
        spin = self.zoom_input
        spin.blockSignals(True)
        spin.setValue(round(self._canvas.scale() * 100))
        self._window.setFocus(Qt.FocusReason.OtherFocusReason)
        spin.blockSignals(False)

    def commit_zoom_input(self) -> None:
        """입력한 배율을 적용하고, 포커스를 창으로 돌려 단축키 조작이 바로 이어지게 함"""
        if not self.apply_zoom_input():
            return
        if self.zoom_input.hasFocus():
            self._window.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_manual_zoom(self) -> None:
        """휠이나 드래그로 배율이 바뀌면 프리셋 체크를 풀고 입력 칸을 맞춤"""
        self.clear_zoom_checks()
        self.sync_zoom_input()

    def manual_zoom(self, direction: int) -> None:
        """키보드 단계 확대, 축소"""
        self._canvas.zoom_step(direction)
        self.clear_zoom_checks()
        self.sync_zoom_input()

    def clear_zoom_checks(self) -> None:
        """배타 그룹에서도 배율 버튼 체크를 모두 풀어 현재 배율만 입력 칸에 남김"""
        groups: list[QButtonGroup] = []
        for button in self.zoom_buttons.values():
            group = button.group()
            if group is not None:
                groups.append(group)
        for group in groups:
            group.setExclusive(False)
        for button in self.zoom_buttons.values():
            button.setChecked(False)
        for group in groups:
            group.setExclusive(True)

    def set_mode_checks(self, mode: str) -> None:
        """모드 버튼 체크를 현재 모드에 맞춤"""
        for key, button in self.mode_buttons.items():
            button.setChecked(key == mode)

    def set_zoom_checks(self, preset: str) -> None:
        """배율 버튼 체크를 현재 프리셋에 맞춤, 직접 배율이면 모두 풂"""
        for key, button in self.zoom_buttons.items():
            button.setChecked(key == preset)

    def apply_theme(self, palette: ThemePalette) -> None:
        """아이콘과 오버레이 속성 스타일을 현재 팔레트로 맞춤"""
        self.target_button.setIcon(
            get_icon("chevron-down", color=palette.media_muted, size=layout.ICON_SIZE)
        )
        self.output_button.setIcon(
            get_icon("chevron-down", color=palette.media_muted, size=layout.ICON_SIZE)
        )
        self._theme_apply_output(self.apply_output_button.isEnabled())
        self._update_collapse_button()
        self._repolish()

    def sync_shortcut_tooltips(self, shortcuts: Mapping[str, str]) -> None:
        """버튼 툴팁에 현재 단축키를 덧붙임"""
        self._shortcuts = dict(shortcuts)
        for key, button in self.mode_buttons.items():
            button.setToolTip(
                format_tooltip_with_shortcut(
                    layout.MODE_TIPS[key], shortcuts.get(layout.MODE_ACTION_IDS[key], "")
                )
            )
        for key, button in self.zoom_buttons.items():
            button.setToolTip(
                format_tooltip_with_shortcut(
                    layout.ZOOM_TIPS[key], shortcuts.get(layout.ZOOM_ACTION_IDS[key], "")
                )
            )
        self.target_button.setToolTip(layout.TARGET_MENU_TIP)
        self._update_collapse_button()

    def _update_collapse_button(self) -> None:
        palette = get_active_palette()
        icon_name = "panel-top-open" if self._collapsed else "panel-top-close"
        self.collapse_button.setIcon(
            get_icon(icon_name, color=palette.media_muted, size=layout.ICON_SIZE)
        )
        tip = layout.EXPAND_TIP if self._collapsed else layout.COLLAPSE_TIP
        self.collapse_button.setToolTip(
            format_tooltip_with_shortcut(tip, self._shortcuts.get("compare.toggle_controls", ""))
        )
        self.collapse_button.setAccessibleName(
            "조작 바 펼치기" if self._collapsed else "조작 바 접기"
        )

    def _segmented(
        self,
        items: tuple[tuple[str, str], ...],
        tips: dict[str, str],
        on_click: Callable[[str], None],
    ) -> dict[str, QToolButton]:
        host = QWidget(self.container)
        host.setObjectName("CompareSegmented")
        row = QHBoxLayout(host)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
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
            row.addWidget(button)
            buttons[key] = button
        return buttons

    def _zoom_input(self) -> QSpinBox:
        spin = QSpinBox(self.container)
        spin.setObjectName("CompareZoomInput")
        spin.setRange(layout.ZOOM_INPUT_MIN_PERCENT, layout.ZOOM_INPUT_MAX_PERCENT)
        spin.setSuffix("%")
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setKeyboardTracking(False)
        spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        spin.setFixedWidth(layout.ZOOM_INPUT_WIDTH_PX)
        spin.setValue(100)
        spin.setToolTip(layout.ZOOM_BADGE_TIP)
        spin.setAccessibleName("화면 배율")
        editor = spin.lineEdit()
        if editor is not None:
            editor.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        return spin

    def _menu_button(
        self, object_name: str, accessible_name: str, tip: str
    ) -> tuple[QToolButton, QMenu]:
        """드롭다운 메뉴를 여는 텍스트 겸 아이콘 버튼과 그 메뉴"""
        button = QToolButton(self.container)
        button.setObjectName(object_name)
        button.setToolTip(tip)
        button.setAccessibleName(accessible_name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        menu = QMenu(button)
        button.setMenu(menu)
        return button, menu

    def _read_top_margin(self) -> int:
        wrapper_layout = None if self._wrap is None else self._wrap.layout()
        if wrapper_layout is None:
            return 0
        return wrapper_layout.contentsMargins().top()

    def _apply_top_margin(self, *, docked: bool) -> None:
        wrapper_layout = None if self._wrap is None else self._wrap.layout()
        if wrapper_layout is None:
            return
        margins = wrapper_layout.contentsMargins()
        height = self.container.sizeHint().height() + DOCK_TOP_GAP_PX if docked else 0
        wrapper_layout.setContentsMargins(
            margins.left(), self._original_top_margin + height, margins.right(), margins.bottom()
        )

    def _place(self) -> None:
        height = self.container.sizeHint().height()
        width = max(self._canvas.width(), 1)
        self.container.resize(width, height)
        x = self._canvas.x()
        if self._collapsed:
            y = self._canvas.y()
            self._canvas.set_top_inset(
                height + LABEL_CLEARANCE_PX if self._overlay_visible else 0
            )
        else:
            y = max(0, self._canvas.y() - height - DOCK_TOP_GAP_PX)
        self.container.move(QPoint(x, y))

    def _repolish(self) -> None:
        style = self.container.style()
        if style is None:
            return
        style.unpolish(self.container)
        style.polish(self.container)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """배율 입력의 Esc를 먼저 처리하고, 캔버스 위쪽 가장자리에서만 오버레이를 띄움"""
        if a1 is None:
            return super().eventFilter(a0, a1)
        etype = a1.type()
        if (
            etype == QEvent.Type.KeyPress
            and isinstance(a1, QKeyEvent)
            and a1.key() == Qt.Key.Key_Escape
            and (a0 is self.zoom_input or a0 is self.zoom_input.lineEdit())
        ):
            self._cancel_zoom_input()
            return True
        if etype == QEvent.Type.MouseMove:
            if a0 is self.container:
                self._hold_overlay()
            elif a0 is self._canvas and isinstance(a1, QMouseEvent):
                self.note_mouse_activity(a1.position().toPoint())
        elif etype == QEvent.Type.Enter:
            if a0 is self.container:
                self._hold_overlay()
            elif a0 is self._canvas and isinstance(a1, QEnterEvent):
                self.note_mouse_activity(a1.position().toPoint())
        elif etype == QEvent.Type.Leave:
            if a0 in (self.container, self._canvas) and self._overlay_visible:
                self._hide_timer.start()
        elif etype == QEvent.Type.Resize and a0 in (self._canvas, self._wrap):
            self._place()
        return super().eventFilter(a0, a1)

    def _in_top_hotzone(self, canvas_pos: QPoint) -> bool:
        """캔버스 위쪽 가장자리 트리거 영역 안인지"""
        return 0 <= canvas_pos.y() <= TOP_HOTZONE_PX
