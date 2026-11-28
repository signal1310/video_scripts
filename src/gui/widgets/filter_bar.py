"""검색 필터, 열 필터 해제 및 예외 규칙 목록 진입 컨트롤 바 위젯 모듈"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, format_tooltip_with_shortcut
from src.gui.constants import (
    COLUMN_PRESETS,
    PRESET_CUSTOM,
    PRESET_DEFAULT,
    clear_radio_checks,
    preset_label,
)
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    get_icon,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.filter_bar_chips import (
    CHIP_REVIEW,
    CHIP_SPECS,
    CHIP_VARY,
    COUNT_BADGE_OFFSET_PX,
    REVIEW_CHIP_LABEL,
    REVIEW_CHIP_TOOLTIP,
    VARY_CHIP_LABEL,
    VARY_CHIP_TOOLTIP,
    FilterBarCountBadge,
    count_badge_style,
)

__all__ = ["FilterTuningBar"]

COLUMN_PRESET_PREFIX: str = "열: "
COMPACT_BUTTON_TEXT: str = "좁게 보기"
EXCEPTION_BUTTON_TEXT: str = "예외 규칙"
GROUP_BUTTON_PREFIX: str = "그룹: "
GROUP_NONE_LABEL: str = "없음"
RESET_BUTTON_TEXT: str = "필터 초기화"
BAR_CONTROL_SIZE_PX: int = 28
SHRINKABLE_BUTTON_MIN_WIDTH_PX: int = 44
AUTO_FIT_LABEL: str = "모든 열 너비 맞춤"
AUTO_FIT_TOOLTIP: str = "모든 열 너비를 셀 값에 맞춥니다."
SEARCH_DEBOUNCE_MS: int = 150
COMPACT_TOOLTIP: str = "셀 여백과 행 높이를 줄여 한 화면에 더 많은 영상을 표시합니다."
ROW_GROUP_TOOLTIP: str = "영상을 폴더나 범주형 열 값으로 묶어 접을 수 있는 그룹 머리글을 표시합니다."
COLUMN_PRESET_TOOLTIP_TEMPLATE: str = "표에 표시할 열 묶음을 고릅니다. {labels} 중에서 선택합니다."
# - 좁은 창 단계별 접힘: 1단계 필터 초기화/칩, 2단계 열/그룹 드롭다운. 좁게 보기와 예외 규칙은 늘 아이콘만 씀
COLLAPSE_LEVEL_FULL: int = 0
COLLAPSE_LEVEL_CHIPS: int = 1
COLLAPSE_LEVEL_DROPDOWNS: int = 2
# - 1단계에서 검색창은 기본 최소 폭을 지키고, 2단계에서 모두 접힌 뒤에야 더 줄어듦
SEARCH_MIN_WIDTH_PX: int = 220
SEARCH_COMPACT_MIN_WIDTH_PX: int = 140
# - 글자 폭 합계가 가용 폭과 같아지는 경계에서 잘리지 않도록 남기는 여유
COLLAPSE_SAFETY_MARGIN_PX: int = 6
BAR_ICON_SIZE_PX: int = 14
# - 예외 규칙 배지는 아이콘 오른쪽에 붙이므로 아이콘 그림에 배지 폭과 사이 여백을 더함
EXCEPTION_BADGE_GAP_PX: int = 4
CURRENT_VALUE_TOOLTIP_TEMPLATE: str = "{base}\n현재 값: {value}"


class FilterTuningBar(QFrame, ThemeAwareMixin):
    """검색어 입력, 활성 필터 일괄 해제 및 예외 규칙 진입 컨트롤 바 위젯"""

    filter_changed = pyqtSignal()
    reset_filters_requested = pyqtSignal()
    exceptions_changed = pyqtSignal()
    exception_editor_requested = pyqtSignal()
    clear_column_filters_requested = pyqtSignal()
    # - 열 프리셋 키, 1회 크기 맞춤 요청, 좁게 보기 켜짐 여부
    column_preset_selected = pyqtSignal(str)
    auto_fit_requested = pyqtSignal()
    compact_toggled = pyqtSignal(bool)
    # - 행 묶기 키(빈 문자열은 없음), 모든 그룹 펼치기/접기 요청
    row_group_selected = pyqtSignal(str)
    # - 행 묶기 메뉴를 열기 직전 알림. 그룹 수를 처음 열 때 한 번만 다시 세도록 조율자가 받음
    row_group_menu_opening = pyqtSignal()
    expand_all_groups_requested = pyqtSignal()
    collapse_all_groups_requested = pyqtSignal()
    # - 두 필터 칩(여백 확인 필요, 구간별 여백 차이)의 켜짐 상태를 한 번에 알림
    chip_filters_changed = pyqtSignal(bool, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False
        self._active_tokens: list[str] = []
        self._disabled_count: int = 0
        self._exception_shortcut: str = DEFAULT_SHORTCUTS["edit.exception_editor"]
        self._row_group_options: list[tuple[str, str]] = []
        self._row_group_counts: dict[str, int] = {}
        self._group_by: str = ""
        self._row_group_action_group: QActionGroup | None = None
        self._expand_groups_action: QAction | None = None
        self._collapse_groups_action: QAction | None = None
        self._row_group_emitting: bool = False
        self._review_flagged_count: int = 0
        self._vary_flagged_count: int = 0
        self._column_filter_count: int = 0
        # - 접힘 단계(0..3)와 재진입 방지, 프리셋 키와 묶기 값은 접힌 뒤 툴팁에 다시 쓰기 위해 기억
        self._collapse_level: int = COLLAPSE_LEVEL_FULL
        self._evaluating_collapse: bool = False
        self._preset_key: str = PRESET_DEFAULT
        self._group_label: str = GROUP_NONE_LABEL
        self._group_count: int = 0
        self._palette: ThemePalette = get_active_palette()
        # - 버튼별 전체 문구를 기억해 두었다가 접힘 단계에 따라 빈 글자로 되돌림
        self._full_labels: dict[QPushButton, str] = {}
        # - 접힌 칩 버튼에서 배지 몫만큼 늘릴 때 기준이 되는 아이콘 기본 폭
        self._chip_base_widths: dict[QPushButton, int] = {}
        # - 가장 깊이 접었을 때 필요한 폭. minimumSizeHint가 이 값을 최소 폭으로 알림
        self._minimal_width: int = 0
        self._measuring_minimum: bool = False
        # - 위젯을 만드는 중에 레이아웃이 최소 크기를 물을 수 있으므로 준비 전에는 기본 힌트를 씀
        self._collapse_ready: bool = False
        self._accessible_names: dict[QPushButton, str] = {}
        self._icon_specs: dict[QPushButton, tuple[str, int]] = {}
        self._icon_pads: dict[QPushButton, int] = {}
        self._preset_base_tooltip: str = ""

        row = QHBoxLayout(self)
        self._row_layout = row
        # - 레이아웃이 위젯 최소 폭을 모든 글자 폭으로 고정하면 접힐 폭까지 줄어들 수 없으므로 해제
        row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(8)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱, 권장 처리, 판정 사유 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(SEARCH_MIN_WIDTH_PX)
        # - 글자를 칠 때마다 전체 표를 다시 거르면 묶기에서 멈추므로 입력이 잠깐 멈춘 뒤 한 번만 알림
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._emit_filter_changed)
        self.search_edit.textChanged.connect(self._on_search_text_changed)

        self.btn_clear_col_filters = QPushButton("", self)
        self.btn_clear_col_filters.setObjectName("ClearColumnFiltersButton")
        self.btn_clear_col_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_col_filters.setToolTip("적용된 모든 열 헤더 필터를 해제합니다.")
        self.btn_clear_col_filters.setAccessibleName("열 필터 해제")
        self.btn_clear_col_filters.setVisible(False)
        self.btn_clear_col_filters.clicked.connect(self.clear_column_filters_requested.emit)
        self._clear_col_filters_badge = FilterBarCountBadge(self.btn_clear_col_filters)

        # - 여백 확인 필요, 구간별 여백 차이 영상이 있을 때만 보이는 누름 상태 칩. 검색창 바로 오른쪽에 둠
        self.btn_review_filter, self._review_filter_badge = self._create_chip(CHIP_SPECS[0])
        self.btn_vary_filter, self._vary_filter_badge = self._create_chip(CHIP_SPECS[1])

        self.btn_reset_filters = QPushButton(RESET_BUTTON_TEXT, self)
        self.btn_reset_filters.setObjectName("ClearAllFiltersButton")
        self.btn_reset_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_filters.setToolTip("검색어와 열 헤더 필터를 모두 초기화합니다.")
        self.btn_reset_filters.setAccessibleName(RESET_BUTTON_TEXT)
        self.btn_reset_filters.setVisible(False)
        self.btn_reset_filters.clicked.connect(self.reset_filters_requested.emit)

        divider = QFrame(self)
        divider.setObjectName("ToolbarDivider")
        divider.setFixedSize(1, 20)
        self._row_divider = divider

        # - 예외 규칙은 필터 상태 진입점이라 뷰 컨트롤과 구분선으로 나눔
        exceptions_divider = QFrame(self)
        exceptions_divider.setObjectName("ToolbarDivider")
        exceptions_divider.setFixedSize(1, 20)
        self._exceptions_divider = exceptions_divider

        self.btn_edit_exceptions = QPushButton("", self)
        self.btn_edit_exceptions.setObjectName("ExceptionsButton")
        self.btn_edit_exceptions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_exceptions.setAccessibleName(EXCEPTION_BUTTON_TEXT)
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=get_active_palette(),
            size=14,
            pad_right=0,
        )
        self.btn_edit_exceptions.clicked.connect(self.exception_editor_requested.emit)
        # - 아이콘만 보이므로 폭은 아이콘과 배지가 정하고, 창이 좁아져도 글자가 잘릴 일이 없음
        self.btn_edit_exceptions.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._exception_badge = FilterBarCountBadge(self.btn_edit_exceptions)
        self._exception_icon_pad: int = 0

        self.btn_column_preset = QPushButton(f"{COLUMN_PRESET_PREFIX}{preset_label(PRESET_DEFAULT)}", self)
        self.btn_column_preset.setObjectName("ColumnPresetButton")
        self.btn_column_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        preset_menu = QMenu(self.btn_column_preset)
        self._preset_group = QActionGroup(preset_menu)
        self._preset_group.setExclusive(True)
        self._preset_actions: dict[str, QAction] = {}
        for preset_key, label in COLUMN_PRESETS.items():
            act = QAction(label, preset_menu)
            act.setCheckable(True)
            act.setChecked(preset_key == PRESET_DEFAULT)
            act.triggered.connect(lambda _checked=False, key=preset_key: self._on_preset_action(key))
            self._preset_group.addAction(act)
            preset_menu.addAction(act)
            self._preset_actions[preset_key] = act
        self.btn_column_preset.setMenu(preset_menu)
        # - 선호 폭은 글자 폭을 따르되, 좁은 창에서는 44px까지만 줄어 창 최소 폭을 밀어내지 않게 함
        self.btn_column_preset.setMinimumWidth(SHRINKABLE_BUTTON_MIN_WIDTH_PX)
        self.btn_column_preset.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_column_preset.setToolTip(
            COLUMN_PRESET_TOOLTIP_TEMPLATE.format(labels=", ".join(f"'{label}'" for label in COLUMN_PRESETS.values()))
        )
        set_button_icon(
            self.btn_column_preset,
            "eye",
            palette=get_active_palette(),
            size=14,
            pad_right=6,
        )

        self.btn_row_group = QPushButton(f"{GROUP_BUTTON_PREFIX}{GROUP_NONE_LABEL}", self)
        self.btn_row_group.setObjectName("RowGroupButton")
        self.btn_row_group.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_row_group.setToolTip(ROW_GROUP_TOOLTIP)
        # - 범주형 열 이름이 길어도 좁은 창에서 최소 폭까지만 줄어들게 함
        self.btn_row_group.setMinimumWidth(SHRINKABLE_BUTTON_MIN_WIDTH_PX)
        self.btn_row_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._row_group_menu = QMenu(self.btn_row_group)
        self._row_group_menu.setToolTipsVisible(True)
        self._row_group_actions: dict[str, QAction] = {}
        self._row_group_menu.aboutToShow.connect(self._on_row_group_menu_opening)
        self.btn_row_group.setMenu(self._row_group_menu)
        set_button_icon(self.btn_row_group, "folder-tree", palette=get_active_palette(), size=14, pad_right=6)

        self.btn_auto_fit = create_icon_button(
            self,
            AUTO_FIT_TOOLTIP,
            self.auto_fit_requested.emit,
            accessible_name=AUTO_FIT_LABEL,
            fixed_size=BAR_CONTROL_SIZE_PX,
        )
        set_button_icon(self.btn_auto_fit, "fit-columns-width", palette=get_active_palette(), size=14, pad_right=0)

        self.btn_compact = QPushButton("", self)
        self.btn_compact.setObjectName("CompactTableButton")
        self.btn_compact.setCheckable(True)
        self.btn_compact.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_compact.setToolTip(COMPACT_TOOLTIP)
        self.btn_compact.setAccessibleName(COMPACT_BUTTON_TEXT)
        # - 기능 이름은 접근성 이름과 툴팁으로만 알리고 버튼에는 아이콘만 그림
        self.btn_compact.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_compact.toggled.connect(self._on_compact_toggled)
        set_button_icon(self.btn_compact, "minimize-2", palette=get_active_palette(), size=14, pad_right=0)

        row.addWidget(self.search_edit, stretch=1)
        row.addWidget(self.btn_review_filter)
        row.addWidget(self.btn_vary_filter)
        row.addWidget(self.btn_clear_col_filters)
        row.addWidget(self.btn_reset_filters)
        row.addWidget(divider)
        row.addWidget(self.btn_edit_exceptions)
        row.addWidget(exceptions_divider)
        row.addWidget(self.btn_column_preset)
        row.addWidget(self.btn_row_group)
        row.addWidget(self.btn_auto_fit)
        row.addWidget(self.btn_compact)

        # - 칩은 글자에 개수가 붙어도 접근성 이름은 이름 그대로 유지
        self._accessible_names = {
            self.btn_clear_col_filters: "열 필터 해제",
            self.btn_review_filter: REVIEW_CHIP_LABEL,
            self.btn_vary_filter: VARY_CHIP_LABEL,
        }
        # - 좁게 보기와 예외 규칙은 늘 아이콘만 쓰므로 접힐 문구가 없음
        self._full_labels = {
            self.btn_reset_filters: RESET_BUTTON_TEXT,
            self.btn_column_preset: self.btn_column_preset.text(),
            self.btn_row_group: self.btn_row_group.text(),
            self.btn_clear_col_filters: "",
        }
        for button, text in self._full_labels.items():
            button.setAccessibleName(text)
        self.btn_clear_col_filters.setAccessibleName("열 필터 해제")
        self._icon_specs = {
            self.btn_column_preset: ("eye", 6),
            self.btn_row_group: ("folder-tree", 6),
            self.btn_auto_fit: ("fit-columns-width", 0),
            self.btn_compact: ("minimize-2", 0),
            self.btn_reset_filters: ("refresh-cw", 0),
            self.btn_clear_col_filters: ("filter", 0),
            self.btn_review_filter: ("alert-triangle", 0),
            self.btn_vary_filter: ("margins-vary", 0),
        }
        self._icon_pads = {button: pad for button, (_name, pad) in self._icon_specs.items()}
        self._preset_base_tooltip = self.btn_column_preset.toolTip()
        # - 예외 규칙 툴팁과 배지는 규칙 수에 따라 바뀌므로 아이콘 준비 뒤에 계산
        self._refresh_exception_button()
        for chip in (self.btn_clear_col_filters, self.btn_review_filter, self.btn_vary_filter):
            chip.installEventFilter(self)
        self.btn_edit_exceptions.installEventFilter(self)
        # - 여기서부터 접힘 판단이 유효해져 최소 크기 질의가 접힌 폭 기준으로 답해짐
        self._collapse_ready = True
        self.init_theme_listener()

    def _create_chip(
        self, spec: tuple[str, str, str, str, str, str]
    ) -> tuple[QPushButton, FilterBarCountBadge]:
        """칩 명세로 누름 상태 칩과 그 개수 배지를 만듦

        - 아이콘은 테마 반영 때 `_icon_specs`로 함께 다시 그림
        """
        _key, object_name, label, tooltip, _icon, _badge_tone = spec
        button = QPushButton("", self)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(label)
        button.setVisible(False)
        button.toggled.connect(self._on_chip_toggled)
        return button, FilterBarCountBadge(button)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """개수 배지를 겹쳐 둔 버튼의 폭이 바뀌면 배지 위치를 다시 잡음"""
        if a1 is not None and a1.type() == QEvent.Type.Resize:
            if a0 is self.btn_clear_col_filters:
                self._place_badge(self.btn_clear_col_filters, self._clear_col_filters_badge)
            elif a0 is self.btn_review_filter:
                self._place_badge(self.btn_review_filter, self._review_filter_badge)
            elif a0 is self.btn_vary_filter:
                self._place_badge(self.btn_vary_filter, self._vary_filter_badge)
            elif a0 is self.btn_edit_exceptions:
                self._place_exception_badge()
        return super().eventFilter(a0, a1)

    def minimumSizeHint(self) -> QSize:
        """가장 깊은 단계까지 접혔을 때 필요한 폭을 최소 크기로 알려 창이 그만큼 좁아질 수 있게 함

        - 기본 최소 힌트는 모든 글자를 펼친 폭이라, 그대로 두면 창이 좁아지기 전에 막혀 접힘이 일어나지 않음
        """
        if not self._collapse_ready:
            return super().minimumSizeHint()
        if self._minimal_width <= 0 and not self._measuring_minimum:
            self._measuring_minimum = True
            try:
                self._update_minimal_width()
            finally:
                self._measuring_minimum = False
        hint = super().minimumSizeHint()
        width = self._minimal_width if self._minimal_width > 0 else hint.width()
        return QSize(width, hint.height())

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        """폭이 바뀔 때마다 단계별 접힘을 다시 판단"""
        super().resizeEvent(a0)
        self._evaluate_collapse()

    def showEvent(self, a0: QShowEvent | None) -> None:
        """처음 보이거나 다시 보일 때 지금 폭 기준으로 접힘 단계를 다시 판단"""
        super().showEvent(a0)
        self._evaluate_collapse()

    @property
    def collapse_level(self) -> int:
        """지금 적용된 접힘 단계(0이면 모든 글자 표시)"""
        return self._collapse_level

    def _collapse_step_for(self, button: QPushButton) -> int:
        """그 버튼이 아이콘만 남는 접힘 단계. 접힘 대상이 아니면 단계 밖 값"""
        steps: dict[QPushButton, int] = {
            self.btn_reset_filters: COLLAPSE_LEVEL_CHIPS,
            self.btn_clear_col_filters: COLLAPSE_LEVEL_CHIPS,
            self.btn_review_filter: COLLAPSE_LEVEL_CHIPS,
            self.btn_vary_filter: COLLAPSE_LEVEL_CHIPS,
            self.btn_column_preset: COLLAPSE_LEVEL_DROPDOWNS,
            self.btn_row_group: COLLAPSE_LEVEL_DROPDOWNS,
        }
        return steps.get(button, COLLAPSE_LEVEL_DROPDOWNS + 1)

    def _set_button_label(self, button: QPushButton, text: str) -> None:
        """전체 문구를 기억하고, 지금 단계가 이 버튼을 접었다면 빈 글자로 그림"""
        self._full_labels[button] = text
        button.setAccessibleName(self._accessible_names.get(button, text))
        self._sync_button_label(button)

    def _sync_button_label(self, button: QPushButton) -> None:
        """접힘 단계에 맞춰 이 버튼의 글자를 전체 문구 또는 빈 값으로 맞춤"""
        full = self._full_labels.get(button, "")
        collapsed = self._collapse_level >= self._collapse_step_for(button)
        button.setText("" if collapsed else full)

    def _row_widgets(self) -> list[QWidget]:
        """한 줄에 놓인 위젯을 줄 순서대로 반환"""
        return [
            self.search_edit,
            self.btn_review_filter,
            self.btn_vary_filter,
            self.btn_clear_col_filters,
            self.btn_reset_filters,
            self._row_divider,
            self.btn_edit_exceptions,
            self._exceptions_divider,
            self.btn_column_preset,
            self.btn_row_group,
            self.btn_auto_fit,
            self.btn_compact,
        ]

    def _required_width(self) -> int:
        """지금 표시 중인 위젯을 글자 잘림 없이 한 줄에 놓는 데 필요한 폭

        - 검색창은 늘어나는 칸이라 최소 폭을 기준으로 함
        - 나머지는 전체 글자 폭과 최소 폭 중 큰 값으로 계산
        """
        widgets = [widget for widget in self._row_widgets() if not widget.isHidden()]
        if not widgets:
            return 0
        total = 0
        for widget in widgets:
            if widget is self.search_edit:
                total += self.search_edit.minimumWidth()
                continue
            total += max(widget.sizeHint().width(), widget.minimumWidth())
        margins = self._row_layout.contentsMargins()
        total += self._row_layout.spacing() * (len(widgets) - 1)
        total += margins.left() + margins.right()
        return total

    def _evaluate_collapse(self) -> None:
        """폭에 맞는 가장 얕은 접힘 단계를 골라 적용

        - 화면에 보이기 전에는 폭이 실제 창 폭이 아니므로 모든 글자를 그대로 둠
        - 단계마다 필요한 폭을 다시 계산해, 그 단계로도 넘치면 다음 단계로 넘어감
        """
        if self._evaluating_collapse:
            return
        if not self.isVisible():
            self._apply_collapse_parts(COLLAPSE_LEVEL_FULL)
            return
        self._evaluating_collapse = True
        try:
            # - 창이 좁아질 수 있는 하한은 가장 깊이 접었을 때 필요한 폭이어야 함
            self._update_minimal_width()
            available = self.width() - COLLAPSE_SAFETY_MARGIN_PX
            level = COLLAPSE_LEVEL_DROPDOWNS
            for candidate in range(COLLAPSE_LEVEL_FULL, COLLAPSE_LEVEL_DROPDOWNS + 1):
                self._apply_collapse_parts(candidate)
                level = candidate
                if self._required_width() <= available:
                    break
            self._apply_collapse_parts(level)
        finally:
            self._evaluating_collapse = False

    def _update_minimal_width(self) -> None:
        """가장 깊이 접었을 때 필요한 폭을 다시 계산하고 원래 표시 단계로 되돌림"""
        previous = self._collapse_level
        self._apply_collapse_parts(COLLAPSE_LEVEL_DROPDOWNS)
        minimal = self._required_width()
        self._apply_collapse_parts(previous)
        if minimal == self._minimal_width:
            return
        self._minimal_width = minimal
        self.updateGeometry()

    def _apply_collapse_parts(self, level: int) -> None:
        """단계에 맞춰 버튼 글자, 검색창 최소 폭, 개수 배지, 아이콘 여백을 맞춤"""
        self._collapse_level = level
        for button in self._full_labels:
            self._sync_button_label(button)
        self.search_edit.setMinimumWidth(
            SEARCH_COMPACT_MIN_WIDTH_PX if level >= COLLAPSE_LEVEL_DROPDOWNS else SEARCH_MIN_WIDTH_PX
        )
        chips_collapsed = level >= COLLAPSE_LEVEL_CHIPS
        self._update_badge(
            self.btn_clear_col_filters,
            self._clear_col_filters_badge,
            self._column_filter_count,
            chips_collapsed,
        )
        self._update_badge(
            self.btn_review_filter,
            self._review_filter_badge,
            self._review_flagged_count,
            chips_collapsed,
        )
        self._update_badge(
            self.btn_vary_filter,
            self._vary_filter_badge,
            self._vary_flagged_count,
            chips_collapsed,
        )
        self._sync_icon_padding()
        self._refresh_collapsed_tooltips()

    def _sync_icon_padding(self) -> None:
        """아이콘 여백이 필요한 버튼만 다시 그려 잦은 폭 변경에서 SVG 렌더링을 반복하지 않음"""
        for button, (name, pad_right) in self._icon_specs.items():
            target = 0 if self._is_collapsed(button) else pad_right
            if self._icon_pads.get(button) == target:
                continue
            self._icon_pads[button] = target
            set_button_icon(button, name, palette=self._palette, size=14, pad_right=target)

    def _is_collapsed(self, button: QPushButton) -> bool:
        """이 버튼이 지금 단계에서 글자 없이 아이콘만 보이는지"""
        return self._collapse_level >= self._collapse_step_for(button)

    def _update_badge(
        self,
        button: QPushButton,
        badge: FilterBarCountBadge,
        count: int,
        collapsed: bool,
    ) -> None:
        """개수가 있고 칩이 접힌 동안에만 버튼 오른쪽 위 배지를 보임

        - 배지가 아이콘을 덮지 않도록 그만큼 버튼 최소 폭을 늘리고, 펼치면 되돌림
        """
        badge.set_count(count)
        show_badge = collapsed and count > 0 and not button.isHidden()
        badge.setVisible(show_badge)
        if show_badge and button not in self._chip_base_widths:
            # - 글자를 비운 상태의 폭은 아이콘과 좌우 여백만 담으므로 배지 폭을 더할 기준으로 씀
            self._chip_base_widths[button] = button.sizeHint().width()
        base = self._chip_base_widths.get(button, 0)
        button.setMinimumWidth(base + badge.width() if show_badge else 0)
        self._place_badge(button, badge)

    def _place_badge(self, button: QPushButton, badge: FilterBarCountBadge) -> None:
        """배지를 버튼 오른쪽 위 모서리에 붙임"""
        if badge.isHidden():
            return
        x = max(0, button.width() - badge.width() - COUNT_BADGE_OFFSET_PX)
        badge.move(x, COUNT_BADGE_OFFSET_PX)

    def _refresh_collapsed_tooltips(self) -> None:
        """접힌 버튼의 툴팁에 지금 값을 덧붙여 글자 없이도 현재 상태를 알 수 있게 함"""
        self.btn_column_preset.setToolTip(
            self._tooltip_with_value(
                self._preset_base_tooltip,
                f"{COLUMN_PRESET_PREFIX}{preset_label(self._preset_key)}",
                COLLAPSE_LEVEL_DROPDOWNS,
            )
        )
        group_value = (
            f"{GROUP_BUTTON_PREFIX}{self._group_label} ({self._group_count})"
            if self._group_by
            else f"{GROUP_BUTTON_PREFIX}{GROUP_NONE_LABEL}"
        )
        self.btn_row_group.setToolTip(
            self._tooltip_with_value(ROW_GROUP_TOOLTIP, group_value, COLLAPSE_LEVEL_DROPDOWNS)
        )
        self.btn_clear_col_filters.setToolTip(
            self._tooltip_with_value(
                "적용된 모든 열 헤더 필터를 해제합니다.",
                f"열 필터 {self._column_filter_count}개 ×",
                COLLAPSE_LEVEL_CHIPS,
            )
        )
        self.btn_review_filter.setToolTip(
            self._tooltip_with_value(
                REVIEW_CHIP_TOOLTIP,
                f"{REVIEW_CHIP_LABEL} {self._review_flagged_count}",
                COLLAPSE_LEVEL_CHIPS,
            )
        )
        self.btn_vary_filter.setToolTip(
            self._tooltip_with_value(
                VARY_CHIP_TOOLTIP,
                f"{VARY_CHIP_LABEL} {self._vary_flagged_count}",
                COLLAPSE_LEVEL_CHIPS,
            )
        )

    def _tooltip_with_value(self, base: str, value: str, step: int) -> str:
        """이 버튼이 접힌 단계면 기본 툴팁 아래에 현재 값을 덧붙임"""
        if self._collapse_level < step:
            return base
        return CURRENT_VALUE_TOOLTIP_TEMPLATE.format(base=base, value=value)

    def set_exception_shortcut(self, key: str) -> None:
        """예외 규칙 편집기 단축키 변경을 예외 규칙 버튼 툴팁에 반영"""
        self._exception_shortcut = key
        self._refresh_exception_button()

    def set_any_filter_active(self, active: bool) -> None:
        """활성화된 필터 존재 여부에 따라 전체 필터 초기화 버튼 노출 갱신"""
        self.btn_reset_filters.setVisible(active)
        self._evaluate_collapse()

    def set_review_flagged_count(self, count: int) -> None:
        """여백 확인 필요 영상 수를 칩 라벨에 반영하고, 0이면 칩을 숨김

        - 개수가 0이 되면 켜져 있던 필터도 함께 풀어 빈 표가 남지 않게 함
        - 0이 아닌 동안에는 칩이 필터 해제 뒤에도 남아 다시 켤 수 있음
        """
        self._review_flagged_count = max(0, count)
        self._apply_chip_visibility(
            self.btn_review_filter, REVIEW_CHIP_LABEL, self._review_flagged_count
        )

    def set_vary_flagged_count(self, count: int) -> None:
        """구간별 여백 차이 영상 수를 칩 라벨에 반영하고, 0이면 칩을 숨김"""
        self._vary_flagged_count = max(0, count)
        self._apply_chip_visibility(
            self.btn_vary_filter, VARY_CHIP_LABEL, self._vary_flagged_count
        )

    def _apply_chip_visibility(self, button: QPushButton, label: str, count: int) -> None:
        """칩 개수 문구와 노출을 맞추고, 0이면 켜짐을 먼저 풀어 프록시 필터도 함께 풀리게 함"""
        if count <= 0:
            button.setChecked(False)
            button.setVisible(False)
            self._evaluate_collapse()
            return
        self._set_button_label(button, f"{label} {count}")
        button.setVisible(True)
        self._evaluate_collapse()

    @property
    def review_filter_enabled(self) -> bool:
        """여백 확인 필요 칩이 켜져 있는지 반환"""
        return self.btn_review_filter.isChecked()

    @property
    def vary_filter_enabled(self) -> bool:
        """구간별 여백 차이 칩이 켜져 있는지 반환"""
        return self.btn_vary_filter.isChecked()

    def clear_chip_filters(self) -> None:
        """필터 초기화에서 두 칩을 시그널 없이 끔(칩은 개수가 있는 동안 남음)"""
        self._updating = True
        try:
            self.btn_review_filter.setChecked(False)
            self.btn_vary_filter.setChecked(False)
        finally:
            self._updating = False

    def _on_chip_toggled(self, _checked: bool) -> None:
        """칩 변화를 복원 중이 아닐 때만 두 칩 상태를 한 번에 알림"""
        if not self._updating:
            self.chip_filters_changed.emit(
                self.btn_review_filter.isChecked(), self.btn_vary_filter.isChecked()
            )

    def _on_preset_action(self, preset_key: str) -> None:
        """프리셋 메뉴 항목 선택을 라벨에 반영하고 시그널로 알림"""
        self.set_column_preset(preset_key)
        self.column_preset_selected.emit(preset_key)

    def set_column_preset(self, preset_key: str) -> None:
        """현재 열 프리셋을 드롭다운 라벨과 체크 표시에 시그널 없이 반영

        - 사용자 지정처럼 메뉴에 없는 키는 어느 항목도 선택하지 않고 라벨만 바꿈
        """
        self._preset_key = preset_key
        self._set_button_label(self.btn_column_preset, f"{COLUMN_PRESET_PREFIX}{preset_label(preset_key)}")
        selected = self._preset_actions.get(preset_key)
        if selected is not None:
            selected.setChecked(True)
            self._evaluate_collapse()
            return
        if preset_key == PRESET_CUSTOM:
            clear_radio_checks(self._preset_group, self._preset_actions.values())
        self._evaluate_collapse()

    def set_row_group_options(self, options: list[tuple[str, str]]) -> None:
        """행 묶기 메뉴에 넣을 (키, 이름) 목록을 기억하고 다시 만들 준비를 함"""
        self._row_group_options = list(options)
        self._rebuild_row_group_actions()

    def set_row_group_counts(self, counts: dict[str, int]) -> None:
        """행 묶기 메뉴 항목별 그룹 수를 기억하고, 항목이 이미 있으면 글자만 고침

        - 0인 항목은 숫자를 붙이지 않아 아직 그룹이 없다는 사실을 이름만으로 보여 줌
        """
        self._row_group_counts = dict(counts)
        labels = dict(self._row_group_options)
        for key, act in self._row_group_actions.items():
            if not key:
                continue
            label = labels.get(key, "")
            if not label:
                continue
            count = self._row_group_counts.get(key, 0)
            act.setText(f"{label} {count}" if count else label)

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        """지금 적용 중인 묶기 키를 버튼 글자와 메뉴 체크에 시그널 없이 반영

        - 메뉴 항목은 지우지 않고 체크 상태만 바꿔 실행 중인 triggered 처리 중 동작이 사라지지 않게 함
        """
        self._group_by = group_by
        self._group_label = label if group_by else GROUP_NONE_LABEL
        self._group_count = count if group_by else 0
        if group_by:
            self._set_button_label(self.btn_row_group, f"{GROUP_BUTTON_PREFIX}{label} ({count})")
        else:
            self._set_button_label(self.btn_row_group, f"{GROUP_BUTTON_PREFIX}{GROUP_NONE_LABEL}")
        self._sync_row_group_state()
        self._evaluate_collapse()

    def _sync_row_group_state(self) -> None:
        """지금 묶기 키에 맞춰 메뉴 체크와 그룹 전체 명령 활성 상태만 바꿈"""
        for key, act in self._row_group_actions.items():
            act.setChecked(key == self._group_by)
        enabled = bool(self._group_by)
        for act in (self._expand_groups_action, self._collapse_groups_action):
            if act is not None:
                act.setEnabled(enabled)

    def _on_row_group_menu_opening(self) -> None:
        """메뉴를 열기 전에 그룹 수 요청을 알리고 기억해 둔 이름과 수로 항목을 채움"""
        self.row_group_menu_opening.emit()
        self._rebuild_row_group_actions()

    def _rebuild_row_group_actions(self) -> None:
        """없음, 폴더, 범주형 열과 펼치기, 접기 항목으로 메뉴를 다시 채움

        - 계획: 항목 이름 뒤에 마지막으로 계산한 그룹 수를 붙이고, 0이면 숫자를 생략
        - 메뉴를 여는 중에는 지우지 않고 체크만 바꿔 실행 중인 동작이 사라지지 않게 함
        """
        menu = self._row_group_menu
        # - 열린 메뉴나 실행 중인 동작을 지우면 triggered 처리 중 객체가 사라지므로 다음에 열 때 다시 만듦
        if menu.isVisible() or self._row_group_emitting:
            return
        menu.clear()
        self._row_group_actions = {}
        persistent = QActionGroup(menu)
        persistent.setExclusive(True)
        entries = [("", GROUP_NONE_LABEL), *self._row_group_options]
        for key, label in entries:
            count = self._row_group_counts.get(key, 0) if key else 0
            text = f"{label} {count}" if count else label
            act = QAction(text, menu)
            act.setCheckable(True)
            act.setChecked(key == self._group_by)
            act.triggered.connect(lambda _checked=False, k=key: self._on_row_group_action(k))
            persistent.addAction(act)
            menu.addAction(act)
            self._row_group_actions[key] = act
        menu.addSeparator()
        expand = QAction("모든 그룹 펼치기", menu)
        expand.setEnabled(bool(self._group_by))
        expand.triggered.connect(lambda _checked=False: self.expand_all_groups_requested.emit())
        menu.addAction(expand)
        collapse = QAction("모든 그룹 접기", menu)
        collapse.setEnabled(bool(self._group_by))
        collapse.triggered.connect(lambda _checked=False: self.collapse_all_groups_requested.emit())
        menu.addAction(collapse)
        # - 메뉴를 여는 중 체크 상태가 바뀌어도 선택 시그널이 나가지 않도록 그룹을 따로 보관
        self._row_group_action_group = persistent
        self._expand_groups_action = expand
        self._collapse_groups_action = collapse

    def _on_row_group_action(self, group_by: str) -> None:
        """메뉴에서 고른 묶기 키를 알림, 알림 처리 중에는 메뉴를 다시 만들지 않음"""
        self._row_group_emitting = True
        try:
            self.row_group_selected.emit(group_by)
        finally:
            self._row_group_emitting = False

    def set_compact(self, enabled: bool) -> None:
        """좁게 보기 버튼 상태를 시그널 없이 저장값에 맞춤"""
        self._updating = True
        try:
            self.btn_compact.setChecked(enabled)
        finally:
            self._updating = False

    def _on_compact_toggled(self, enabled: bool) -> None:
        """좁게 보기 버튼 변화를 저장 복원 중이 아닐 때만 알림"""
        if not self._updating:
            self.compact_toggled.emit(enabled)

    def is_compact(self) -> bool:
        """좁게 보기 버튼이 켜져 있는지 반환"""
        return self.btn_compact.isChecked()

    def _on_search_text_changed(self, text: str) -> None:
        """검색어 입력 변화를 묶어 알림, 입력이 비면 기다리지 않고 바로 알림"""
        if not text:
            self._emit_filter_changed()
            return
        self._search_timer.start()

    def _emit_filter_changed(self) -> None:
        """대기 중인 검색어 타이머를 끝내고 필터 변경을 한 번 알림"""
        self._search_timer.stop()
        self.filter_changed.emit()

    def flush_pending(self) -> None:
        """대기 중인 검색어 반영이 있으면 기다리지 않고 지금 알림"""
        if not self._search_timer.isActive():
            return
        self._emit_filter_changed()

    def _refresh_exception_button(self) -> None:
        """예외 규칙 버튼의 툴팁과 활성 규칙 수 배지를 지금 규칙 목록에 맞춤"""
        active = len(self._active_tokens)
        disabled = self._disabled_count
        tip_lines = [
            format_tooltip_with_shortcut(
                "제외할 파일명 키워드, 조건식 목록을 편집합니다.",
                self._exception_shortcut,
                bracket="round",
            )
        ]
        if self._active_tokens:
            preview = ", ".join(self._active_tokens[:5])
            if len(self._active_tokens) > 5:
                preview += f" 외 {len(self._active_tokens) - 5}개"
            tip_lines.append(f"• 활성 규칙 ({active}개): {preview}")
        if disabled > 0:
            tip_lines.append(f"• 비활성 규칙: {disabled}개")
        self.btn_edit_exceptions.setToolTip("\n".join(tip_lines))
        self._update_exception_badge(active)

    def _update_exception_badge(self, active: int) -> None:
        """활성 규칙이 있을 때만 아이콘 오른쪽 숫자 배지를 보이고, 배지 몫만큼 아이콘 오른쪽을 비움

        - 버튼에 글자가 없으므로 개수는 배지로만 알리고, 배지가 아이콘을 덮지 않게 아이콘 폭에 더함
        """
        self._exception_badge.set_count(active)
        show_badge = active > 0
        self._exception_badge.setVisible(show_badge)
        pad = self._exception_badge.width() + EXCEPTION_BADGE_GAP_PX if show_badge else 0
        if pad == self._exception_icon_pad:
            self._place_exception_badge()
            return
        self._exception_icon_pad = pad
        # - 아이콘 그림에 오른쪽 여백을 넣으면 버튼이 그만큼 넓어져 배지 자리가 비워짐
        set_button_icon(
            self.btn_edit_exceptions, "ban", palette=self._palette, size=BAR_ICON_SIZE_PX, pad_right=pad
        )
        self._place_exception_badge()

    def _place_exception_badge(self) -> None:
        """예외 규칙 배지를 버튼 안 아이콘 바로 오른쪽에 붙임"""
        badge = self._exception_badge
        if badge.isHidden():
            return
        button = self.btn_edit_exceptions
        # - 아이콘 폭이 바뀐 직후에는 아직 레이아웃 전이라 예상 폭으로 자리를 잡음
        content_width = BAR_ICON_SIZE_PX + self._exception_icon_pad
        width = max(button.width(), content_width)
        left = (width - content_width) // 2
        badge.move(
            left + BAR_ICON_SIZE_PX + EXCEPTION_BADGE_GAP_PX,
            (button.height() - badge.height()) // 2,
        )

    def set_active_column_filter_count(self, count: int) -> None:
        """활성화된 열 헤더 동적 필터 개수에 따라 일괄 해제 배지 버튼을 갱신"""
        self._column_filter_count = max(0, count)
        if self._column_filter_count > 0:
            self._set_button_label(self.btn_clear_col_filters, f"열 필터 {self._column_filter_count}개 ×")
            self.btn_clear_col_filters.setVisible(True)
        else:
            self._full_labels[self.btn_clear_col_filters] = ""
            self.btn_clear_col_filters.setText("")
            self.btn_clear_col_filters.setVisible(False)
        self._evaluate_collapse()

    def set_disabled_exception_count(self, count: int) -> None:
        """비활성화된 예외 규칙 건수를 예외 규칙 버튼 라벨, 툴팁에 반영"""
        self._disabled_count = max(0, count)
        self._refresh_exception_button()
        self._evaluate_collapse()

    def set_busy(self, busy: bool) -> None:
        """백그라운드 작업 중 예외 규칙 편집 버튼을 잠가 스레드 레이스 컨디션을 방지"""
        self.btn_edit_exceptions.setEnabled(not busy)

    def get_exception_tokens(self) -> list[str]:
        """현재 등록된 활성 예외 규칙 토큰 리스트 복사본을 순서대로 반환"""
        return list(self._active_tokens)

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """활성 예외 규칙 토큰 목록을 갱신하고 변경 시그널을 발행"""
        self._active_tokens = [tok.strip() for tok in tokens if tok.strip()]
        self._refresh_exception_button()
        self._evaluate_collapse()
        if not self._updating:
            self.exceptions_changed.emit()

    def sync_filter_state(
        self,
        *,
        search: str,
        exception_tokens: list[str],
    ) -> None:
        """저장된 검색, 예외 규칙 상태를 툴바 컨트롤에 시그널 없이 반영"""
        self._search_timer.stop()
        self._updating = True
        try:
            self.search_edit.blockSignals(True)
            try:
                self.search_edit.setText(search)
            finally:
                self.search_edit.blockSignals(False)
            self._active_tokens = [tok.strip() for tok in exception_tokens if tok.strip()]
            self._refresh_exception_button()
        finally:
            self._updating = False

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 검색창 및 툴바 버튼 아이콘 색상을 갱신"""
        self._palette = palette
        if self._action_search is not None:
            self._action_search.setIcon(get_icon("search", palette=palette, size=14, pad_right=7))
        for button, (name, pad_right) in self._icon_specs.items():
            set_button_icon(
                button,
                name,
                palette=palette,
                size=14,
                pad_right=0 if self._is_collapsed(button) else pad_right,
            )
        # - 예외 규칙 아이콘은 배지 자리만큼의 여백을 스스로 관리하므로 따로 다시 그림
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=palette,
            size=BAR_ICON_SIZE_PX,
            pad_right=self._exception_icon_pad,
        )
        self._style_count_badges(palette)

    def _style_count_badges(self, palette: ThemePalette) -> None:
        """개수 배지를 현재 테마 팔레트 색으로 다시 칠함"""
        self._clear_col_filters_badge.setStyleSheet(count_badge_style(palette, "clear"))
        self._review_filter_badge.setStyleSheet(count_badge_style(palette, CHIP_REVIEW))
        self._vary_filter_badge.setStyleSheet(count_badge_style(palette, CHIP_VARY))
        self._exception_badge.setStyleSheet(count_badge_style(palette, "clear"))
