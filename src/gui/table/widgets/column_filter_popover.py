"""테이블 열 데이터 타입별 동적 필터 조건 입력 팝오버 위젯 모듈"""

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.constants import FILTER_DEBOUNCE_MS
from src.gui.table.column_filters import (
    TEXT_FILTER_MODES,
    TEXT_MODE_CONTAINS,
    ColumnDataType,
    ColumnFilterRule,
    ColumnSpec,
)
from src.gui.table.widgets.filter_checklist_widget import FilterChecklistWidget
from src.gui.widgets.custom_spinbox import OptionalNumberSpinBox
from src.gui.widgets.floating_popup import FloatingPopupFrame

_POPOVER_MIN_WIDTH: int = 280
_POPOVER_MAX_WIDTH: int = 440
_VALUE_LIST_MAX_HEIGHT: int = 230
_NUMBER_LIST_MAX_HEIGHT: int = 196
_POPOVER_TEXT_EXTRA_WIDTH: int = 96
_POPOVER_SHADOW_MARGIN: int = 6
_POPOVER_ANCHOR_OFFSET_Y: int = 2
_POPOVER_SHADOW_BLUR: float = 14.0
_POPOVER_SHADOW_OFFSET_Y: float = 3.0
_POPOVER_SHADOW_ALPHA: int = 55
_NUMBER_LABEL_WIDTH: int = 64


class ColumnFilterPopover(FloatingPopupFrame):
    """열의 데이터 타입에 맞춰 체크리스트, 수치 범위 또는 문자열 필터 UI를 제공하는 팝오버"""

    filter_changed = pyqtSignal(int, object)

    def __init__(
        self,
        col: int,
        title: str,
        spec: ColumnSpec,
        current_rule: ColumnFilterRule | None = None,
        value_counts: list[tuple[str, int]] | None = None,
        numeric_bounds: tuple[float | None, float | None, int] = (None, None, 0),
        number_bucket_counts: list[tuple[str, str, int]] | None = None,
        toggle_rect_global: QRect | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ColumnFilterPopover")
        self.setMinimumWidth(_POPOVER_MIN_WIDTH)
        self.setMaximumWidth(_POPOVER_MAX_WIDTH + _POPOVER_SHADOW_MARGIN * 2)

        self._col = col
        self._spec = spec
        self._value_counts = list(value_counts or [])
        self._numeric_bounds = numeric_bounds
        self._number_bucket_counts = list(number_bucket_counts or [])
        if toggle_rect_global is not None:
            self.set_toggle_rect(toggle_rect_global)
        self._checklist: FilterChecklistWidget | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self.apply_current)

        # 타입별 선택 위젯 참조 (타입 미해당 시 None)
        self.edit_option_search: QLineEdit | None = None
        self.list_values: QListWidget | None = None
        self.btn_select_all: QPushButton | None = None
        self.btn_deselect_all: QPushButton | None = None
        self.lbl_selected_count: QLabel | None = None
        self.spin_min: OptionalNumberSpinBox | None = None
        self.spin_max: OptionalNumberSpinBox | None = None
        self.combo_null_mode: QComboBox | None = None
        self.combo_text_mode: QComboBox | None = None
        self.edit_text_query: QLineEdit | None = None

        root = self._create_card_shell(title, spec)
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            items = [
                (val, val or "(비어 있음)", count) for val, count in self._value_counts
            ]
            self._attach_checklist(
                root,
                items,
                current_rule,
                include_search=True,
                max_list_height=_VALUE_LIST_MAX_HEIGHT,
            )
        elif spec.data_type is ColumnDataType.NUMBER:
            self._build_number_body(root, current_rule)
        else:
            self._build_text_body(root, current_rule)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.btn_clear = QPushButton("필터 해제", self)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear_filter)
        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setObjectName("PrimaryButton")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self._on_close_clicked)
        footer.addWidget(self.btn_clear)
        footer.addStretch(1)
        footer.addWidget(self.btn_close)
        root.addLayout(footer)
        self._update_clear_button_state(current_rule)

    def _create_card_shell(self, title: str, spec: ColumnSpec) -> QVBoxLayout:
        outer = QVBoxLayout(self)
        m = _POPOVER_SHADOW_MARGIN
        outer.setContentsMargins(m, m, m, m)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("ColumnFilterPopoverCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(_POPOVER_SHADOW_BLUR)
        shadow.setOffset(0.0, _POPOVER_SHADOW_OFFSET_Y)
        shadow.setColor(QColor(0, 0, 0, _POPOVER_SHADOW_ALPHA))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        lbl_title = QLabel(title, self)
        lbl_title.setObjectName("PopoverTitle")
        lbl_type = QLabel(spec.data_type.label, self)
        lbl_type.setObjectName("PopoverTypeBadge")
        hdr.addWidget(lbl_title)
        hdr.addWidget(lbl_type)
        hdr.addStretch(1)
        root.addLayout(hdr)
        return root

    def _attach_checklist(
        self,
        layout: QVBoxLayout,
        items: list[tuple[str, str, int]],
        current_rule: ColumnFilterRule | None,
        *,
        include_search: bool,
        max_list_height: int,
    ) -> None:
        selected = current_rule.selected_values if current_rule is not None else None
        checklist = FilterChecklistWidget(
            items,
            selected,
            include_search=include_search,
            max_list_height=max_list_height,
            parent=self,
        )
        checklist.selection_changed.connect(self.apply_current)
        self._checklist = checklist
        self.edit_option_search = checklist.edit_search
        self.list_values = checklist.list_values
        self.btn_select_all = checklist.btn_select_all
        self.btn_deselect_all = checklist.btn_deselect_all
        self.lbl_selected_count = checklist.lbl_selected_count

        if checklist.preferred_content_width > 0:
            margin_pad = _POPOVER_SHADOW_MARGIN * 2
            preferred_w = min(
                _POPOVER_MAX_WIDTH + margin_pad,
                checklist.preferred_content_width + _POPOVER_TEXT_EXTRA_WIDTH + margin_pad,
            )
            self.setMinimumWidth(max(self.minimumWidth(), preferred_w))
        layout.addWidget(checklist)

    def _create_bound_spinbox(
        self, initial_value: float | None, default_anchor: float | None
    ) -> OptionalNumberSpinBox:
        spin = OptionalNumberSpinBox(
            decimals=self._spec.decimals,
            step=self._spec.filter_step,
            default_anchor=default_anchor,
            placeholder="제한 없음",
            parent=self,
        )
        if self._spec.unit:
            spin.setSuffix(self._spec.unit)
        if initial_value is not None:
            spin.setValue(initial_value)
        spin.valueChanged.connect(lambda _v: self._debounce_timer.start())
        spin.returnPressed.connect(self.apply_current)
        return spin

    def _add_labeled_row(
        self, parent_layout: QVBoxLayout, label_text: str, widget: QWidget
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label_text, self)
        lbl.setFixedWidth(_NUMBER_LABEL_WIDTH)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        parent_layout.addLayout(row)

    @staticmethod
    def _resolve_initial_null_mode(rule: ColumnFilterRule) -> str:
        if rule.null_only:
            return "null_only"
        if rule.include_null:
            return "include_null"
        if rule.min_value is None and rule.max_value is None and rule.selected_values is None:
            return "measured_only"
        return "default"

    def _format_numeric_bounds_text(self) -> str:
        min_b, max_b, null_count = self._numeric_bounds
        unit = f" {self._spec.unit}" if self._spec.unit else ""
        nullable = self._spec.nullable_label
        if min_b is not None and max_b is not None:
            fmt = f".{self._spec.decimals}f"
            text = f"데이터 범위: {format(min_b, fmt)} ~ {format(max_b, fmt)}{unit}"
            return f"{text}, {nullable} {null_count}개" if null_count > 0 and nullable else text
        return f"{nullable} {null_count}개" if null_count > 0 and nullable else "수치 데이터 없음"

    def _build_number_body(
        self, layout: QVBoxLayout, current_rule: ColumnFilterRule | None
    ) -> None:
        min_bound, max_bound, _ = self._numeric_bounds
        lbl_bounds = QLabel(self._format_numeric_bounds_text(), self)
        lbl_bounds.setObjectName("SectionLabel")
        lbl_bounds.setWordWrap(True)
        layout.addWidget(lbl_bounds)

        self.spin_min = self._create_bound_spinbox(
            current_rule.min_value if current_rule is not None else None, min_bound
        )
        self._add_labeled_row(layout, "최소 (>=):", self.spin_min)
        self.spin_max = self._create_bound_spinbox(
            current_rule.max_value if current_rule is not None else None, max_bound
        )
        self._add_labeled_row(layout, "최대 (<=):", self.spin_max)

        if nl := self._spec.nullable_label:
            combo = QComboBox(self)
            for label, mode in (
                ("기본 (조건 입력 시 제외)", "default"),
                (f"{nl} 포함", "include_null"),
                ("측정된 항목만", "measured_only"),
                (f"{nl} 항목만 보기", "null_only"),
            ):
                combo.addItem(label, mode)
            if current_rule is not None:
                idx = combo.findData(self._resolve_initial_null_mode(current_rule))
                combo.setCurrentIndex(max(0, idx))
            combo.currentIndexChanged.connect(self._on_null_mode_changed)
            self.combo_null_mode = combo
            self._add_labeled_row(layout, f"{nl}:", combo)

        if self._number_bucket_counts:
            lbl_buckets = QLabel(f"구간별 분포 ({len(self._number_bucket_counts)}개)", self)
            lbl_buckets.setObjectName("SectionLabel")
            layout.addWidget(lbl_buckets)
            self._attach_checklist(
                layout,
                self._number_bucket_counts,
                current_rule,
                include_search=False,
                max_list_height=_NUMBER_LIST_MAX_HEIGHT,
            )

        if self._spec.nullable_label:
            self._sync_null_only_inputs()

    def _build_text_body(
        self, layout: QVBoxLayout, current_rule: ColumnFilterRule | None
    ) -> None:
        combo = QComboBox(self)
        for label, mode in TEXT_FILTER_MODES:
            combo.addItem(label, mode)
        if current_rule is not None:
            combo.setCurrentIndex(max(0, combo.findData(current_rule.text_mode)))
        combo.currentIndexChanged.connect(self.apply_current)
        self.combo_text_mode = combo
        layout.addWidget(combo)

        edit = QLineEdit(self)
        edit.setPlaceholderText("검색어 입력...")
        edit.setClearButtonEnabled(True)
        if current_rule is not None and current_rule.text_query:
            edit.setText(current_rule.text_query)
        edit.textChanged.connect(lambda _t: self._debounce_timer.start())
        edit.returnPressed.connect(self.apply_current)
        self.edit_text_query = edit
        layout.addWidget(edit)

    def _sync_null_only_inputs(self) -> None:
        enabled = not (
            self.combo_null_mode is not None
            and self.combo_null_mode.currentData() == "null_only"
        )
        for spin in (self.spin_min, self.spin_max):
            if spin is not None:
                spin.setEnabled(enabled)
        if self._checklist is not None:
            self._checklist.set_controls_enabled(enabled)

    def _on_null_mode_changed(self) -> None:
        self._sync_null_only_inputs()
        self.apply_current()

    def selected_values(self) -> set[str]:
        """체크리스트에서 현재 체크된 고유 값 집합 반환"""
        return self._checklist.selected_values() if self._checklist is not None else set()

    def build_rule(self) -> ColumnFilterRule | None:
        """현재 UI 입력 상태를 바탕으로 ColumnFilterRule(미필터 시 None) 생성"""
        dtype = self._spec.data_type
        if dtype in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self._checklist is None or self._checklist.total_count == 0:
                return None
            checked = self._checklist.selected_values()
            if len(checked) == self._checklist.total_count:
                return None
            return ColumnFilterRule(selected_values=frozenset(checked))

        if dtype is ColumnDataType.NUMBER:
            min_v = self.spin_min.value() if self.spin_min is not None else None
            max_v = self.spin_max.value() if self.spin_max is not None else None
            selected_nums: frozenset[str] | None = None
            if self._number_bucket_counts and self._checklist is not None:
                checked = self._checklist.selected_values()
                if len(checked) < len(self._number_bucket_counts):
                    selected_nums = frozenset(checked)

            null_mode = (
                str(self.combo_null_mode.currentData() or "default")
                if self.combo_null_mode is not None
                else "include_null"
            )
            if null_mode == "null_only":
                return ColumnFilterRule(null_only=True, include_null=True)
            has_val = min_v is not None or max_v is not None or selected_nums is not None
            if null_mode != "measured_only" and not has_val:
                return None
            return ColumnFilterRule(
                min_value=min_v,
                max_value=max_v,
                selected_values=selected_nums,
                include_null=(null_mode == "include_null"),
                null_only=False,
            )

        query = self.edit_text_query.text() if self.edit_text_query is not None else ""
        mode = (
            str(self.combo_text_mode.currentData() or TEXT_MODE_CONTAINS)
            if self.combo_text_mode is not None
            else TEXT_MODE_CONTAINS
        )
        rule = ColumnFilterRule(text_query=query, text_mode=mode)
        return rule if rule.is_active(dtype) else None

    def _update_clear_button_state(self, rule: ColumnFilterRule | None) -> None:
        self.btn_clear.setEnabled(rule is not None and rule.is_active(self._spec.data_type))

    def popup_at(self, anchor_global: QPoint) -> None:
        """그림자 여백 보정 후 화면 경계 클램핑을 거쳐 지정 좌표에 팝오버 표시"""
        self.show_clamped_at(
            QPoint(
                anchor_global.x() - _POPOVER_SHADOW_MARGIN,
                anchor_global.y() - _POPOVER_ANCHOR_OFFSET_Y,
            )
        )

    def apply_current(self) -> None:
        """현재 입력 조건을 즉시 반영하여 filter_changed 시그널을 방출"""
        self._debounce_timer.stop()
        rule = self.build_rule()
        self._update_clear_button_state(rule)
        self.filter_changed.emit(self._col, rule)

    def clear_filter(self) -> None:
        """현재 열의 모든 필터 입력을 초기화하고 필터를 해제"""
        self._debounce_timer.stop()
        dtype = self._spec.data_type
        if dtype in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self.edit_option_search is not None:
                self.edit_option_search.clear()
        elif dtype is ColumnDataType.NUMBER:
            for spin in (self.spin_min, self.spin_max):
                if spin is not None:
                    spin.blockSignals(True)
                    spin.setValue(None)
                    spin.blockSignals(False)
            if self.combo_null_mode is not None:
                self.combo_null_mode.blockSignals(True)
                self.combo_null_mode.setCurrentIndex(0)
                self.combo_null_mode.blockSignals(False)
            self._sync_null_only_inputs()
        else:
            if self.edit_text_query is not None:
                self.edit_text_query.clear()
            if self.combo_text_mode is not None:
                self.combo_text_mode.setCurrentIndex(0)

        if self._checklist is not None:
            self._checklist.apply_checked_state(True, visible_only=False, emit_signal=False)
        self._update_clear_button_state(None)
        self.filter_changed.emit(self._col, None)

    def _on_close_clicked(self) -> None:
        if self._debounce_timer.isActive():
            self.apply_current()
        self.close()

    def _on_hidden(self) -> None:
        """팝오버가 닫힐 때 대기 중인 디바운스 입력을 반영"""
        if self._debounce_timer.isActive():
            self.apply_current()
