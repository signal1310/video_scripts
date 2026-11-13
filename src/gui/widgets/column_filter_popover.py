"""테이블 열 데이터 타입별 동적 필터 조건 입력 팝오버 위젯 모듈"""

from PyQt6.QtCore import QEvent, QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QHideEvent, QKeyEvent, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.column_filters import (
    TEXT_FILTER_MODES,
    TEXT_MODE_CONTAINS,
    ColumnDataType,
    ColumnFilterRule,
    ColumnSpec,
)
from src.gui.constants import FILTER_DEBOUNCE_MS
from src.gui.widgets.custom_spinbox import OptionalNumberSpinBox

_POPOVER_MIN_WIDTH: int = 280
_POPOVER_MAX_WIDTH: int = 440
_VALUE_LIST_MAX_HEIGHT: int = 230
_NUMBER_LIST_MAX_HEIGHT: int = 196
_VALUE_LIST_MIN_HEIGHT: int = 88
_VALUE_ITEM_HEIGHT_HINT: int = 28
_POPOVER_TEXT_EXTRA_WIDTH: int = 96
_POPOVER_SHADOW_MARGIN: int = 6
_POPOVER_SHADOW_BLUR: float = 14.0
_POPOVER_SHADOW_OFFSET_Y: float = 3.0
_POPOVER_SHADOW_ALPHA: int = 55
_NUMBER_LABEL_WIDTH: int = 64


class ColumnFilterPopover(QFrame):
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
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("ColumnFilterPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(_POPOVER_MIN_WIDTH)
        self.setMaximumWidth(_POPOVER_MAX_WIDTH + _POPOVER_SHADOW_MARGIN * 2)

        self._col = col
        self._spec = spec
        self._value_counts = list(value_counts or [])
        self._numeric_bounds = numeric_bounds
        self._number_bucket_counts = list(number_bucket_counts or [])
        self._toggle_rect_global = (
            QRect(toggle_rect_global) if toggle_rect_global is not None else QRect()
        )
        self._app_filter_installed = False
        self._checklist_total: int = (
            len(self._value_counts)
            if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY)
            else len(self._number_bucket_counts)
        )
        self._syncing = False

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            _POPOVER_SHADOW_MARGIN,
            _POPOVER_SHADOW_MARGIN,
            _POPOVER_SHADOW_MARGIN,
            _POPOVER_SHADOW_MARGIN,
        )
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

        # 상단 헤더 (열 이름 + 데이터 타입 배지)
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

        # 데이터 타입별 본문
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            self._build_category_body(root, current_rule)
        elif spec.data_type is ColumnDataType.NUMBER:
            self._build_number_body(root, current_rule)
        else:
            self._build_text_body(root, current_rule)

        # 하단 푸터 (필터 해제 / 닫기)
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

    def _build_value_checklist(
        self,
        layout: QVBoxLayout,
        items: list[tuple[str, str, int]],
        current_rule: ColumnFilterRule | None,
        *,
        include_search: bool,
        max_list_height: int,
    ) -> None:
        if include_search:
            search = QLineEdit(self)
            search.setPlaceholderText("항목 검색...")
            search.setClearButtonEnabled(True)
            search.textChanged.connect(self._filter_option_rows)
            self.edit_option_search = search
            layout.addWidget(search)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        btn_all = QPushButton("모두 선택", self)
        btn_all.setObjectName("PopoverLinkButton")
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.clicked.connect(lambda: self._set_all_checked(True))

        btn_none = QPushButton("모두 해제", self)
        btn_none.setObjectName("PopoverLinkButton")
        btn_none.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_none.clicked.connect(lambda: self._set_all_checked(False))

        lbl_count = QLabel("", self)
        lbl_count.setObjectName("SectionLabel")
        lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_select_all = btn_all
        self.btn_deselect_all = btn_none
        self.lbl_selected_count = lbl_count
        actions.addWidget(btn_all)
        actions.addWidget(btn_none)
        actions.addStretch(1)
        actions.addWidget(lbl_count)
        layout.addLayout(actions)

        lst = QListWidget(self)
        lst.setObjectName("ColumnFilterValueList")
        lst.setAlternatingRowColors(True)
        lst.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_values = lst

        selected = (
            current_rule.selected_values
            if current_rule is not None and current_rule.selected_values is not None
            else None
        )
        max_text_px = 0
        fm = lst.fontMetrics()
        self._syncing = True
        try:
            if not items:
                empty_item = QListWidgetItem("(표시할 항목 없음)")
                empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
                lst.addItem(empty_item)
                btn_all.setEnabled(False)
                btn_none.setEnabled(False)
            else:
                for raw_key, display_label, count in items:
                    display_text = f"{display_label} ({count})"
                    max_text_px = max(max_text_px, fm.horizontalAdvance(display_text))
                    item = QListWidgetItem(display_text)
                    item.setToolTip(display_text)
                    item.setData(Qt.ItemDataRole.UserRole, raw_key)
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    is_checked = selected is None or raw_key in selected
                    item.setCheckState(
                        Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked
                    )
                    lst.addItem(item)
        finally:
            self._syncing = False

        if max_text_px > 0:
            max_outer_w = _POPOVER_MAX_WIDTH + _POPOVER_SHADOW_MARGIN * 2
            preferred_w = max(
                self.minimumWidth(),
                min(
                    max_outer_w,
                    max_text_px + _POPOVER_TEXT_EXTRA_WIDTH + _POPOVER_SHADOW_MARGIN * 2,
                ),
            )
            self.setMinimumWidth(preferred_w)

        est_height = max(
            _VALUE_LIST_MIN_HEIGHT,
            min(max_list_height, len(items) * _VALUE_ITEM_HEIGHT_HINT + 12),
        )
        lst.setFixedHeight(est_height)
        lst.itemChanged.connect(self._on_value_item_changed)
        layout.addWidget(lst)
        self._update_selected_count_label()

    def _build_category_body(
        self, layout: QVBoxLayout, current_rule: ColumnFilterRule | None
    ) -> None:
        items = [
            (raw_val, raw_val if raw_val else "(비어 있음)", count)
            for raw_val, count in self._value_counts
        ]
        self._build_value_checklist(
            layout,
            items,
            current_rule,
            include_search=True,
            max_list_height=_VALUE_LIST_MAX_HEIGHT,
        )

    def _create_bound_spinbox(
        self,
        *,
        initial_value: float | None,
        default_anchor: float | None,
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

    def _build_number_body(
        self, layout: QVBoxLayout, current_rule: ColumnFilterRule | None
    ) -> None:
        min_bound, max_bound, null_count = self._numeric_bounds
        unit = f" {self._spec.unit}" if self._spec.unit else ""
        if min_bound is not None and max_bound is not None:
            fmt = f".{self._spec.decimals}f"
            bounds_text = (
                f"데이터 범위: {format(min_bound, fmt)} ~ {format(max_bound, fmt)}{unit}"
            )
            if null_count > 0 and self._spec.nullable_label:
                bounds_text += f" · {self._spec.nullable_label} {null_count}개"
        elif null_count > 0 and self._spec.nullable_label:
            bounds_text = f"{self._spec.nullable_label} {null_count}개"
        else:
            bounds_text = "수치 데이터 없음"

        lbl_bounds = QLabel(bounds_text, self)
        lbl_bounds.setObjectName("SectionLabel")
        lbl_bounds.setWordWrap(True)
        layout.addWidget(lbl_bounds)

        row_min = QHBoxLayout()
        row_min.setSpacing(6)
        lbl_min = QLabel("최소 (>=):", self)
        lbl_min.setFixedWidth(_NUMBER_LABEL_WIDTH)
        spin_min = self._create_bound_spinbox(
            initial_value=current_rule.min_value if current_rule is not None else None,
            default_anchor=min_bound,
        )
        row_min.addWidget(lbl_min)
        row_min.addWidget(spin_min, stretch=1)
        layout.addLayout(row_min)
        self.spin_min = spin_min

        row_max = QHBoxLayout()
        row_max.setSpacing(6)
        lbl_max = QLabel("최대 (<=):", self)
        lbl_max.setFixedWidth(_NUMBER_LABEL_WIDTH)
        spin_max = self._create_bound_spinbox(
            initial_value=current_rule.max_value if current_rule is not None else None,
            default_anchor=max_bound,
        )
        row_max.addWidget(lbl_max)
        row_max.addWidget(spin_max, stretch=1)
        layout.addLayout(row_max)
        self.spin_max = spin_max

        if self._spec.nullable_label:
            nl = self._spec.nullable_label
            row_null = QHBoxLayout()
            row_null.setSpacing(6)
            lbl_null = QLabel(f"{nl}:", self)
            lbl_null.setFixedWidth(_NUMBER_LABEL_WIDTH)
            combo_null = QComboBox(self)
            combo_null.addItem("기본 (조건 입력 시 제외)", "default")
            combo_null.addItem(f"{nl} 포함", "include_null")
            combo_null.addItem("측정된 항목만", "measured_only")
            combo_null.addItem(f"{nl} 항목만 보기", "null_only")
            if current_rule is not None:
                if current_rule.null_only:
                    mode_key = "null_only"
                elif current_rule.include_null:
                    mode_key = "include_null"
                elif (
                    current_rule.min_value is None
                    and current_rule.max_value is None
                    and current_rule.selected_values is None
                ):
                    mode_key = "measured_only"
                else:
                    mode_key = "default"
                idx = combo_null.findData(mode_key)
                combo_null.setCurrentIndex(idx if idx >= 0 else 0)
            combo_null.currentIndexChanged.connect(self._on_null_mode_changed)
            self.combo_null_mode = combo_null
            row_null.addWidget(lbl_null)
            row_null.addWidget(combo_null, stretch=1)
            layout.addLayout(row_null)

        if self._number_bucket_counts:
            lbl_buckets = QLabel(
                f"구간별 분포 ({len(self._number_bucket_counts)}개)", self
            )
            lbl_buckets.setObjectName("SectionLabel")
            layout.addWidget(lbl_buckets)
            self._build_value_checklist(
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
            idx = combo.findData(current_rule.text_mode)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
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

    def _filter_option_rows(self, text: str) -> None:
        if self.list_values is None:
            return
        query = text.strip().lower()
        for row in range(self.list_values.count()):
            item = self.list_values.item(row)
            if item is None:
                continue
            raw_val = item.data(Qt.ItemDataRole.UserRole)
            if raw_val is None:
                continue
            matched = query in str(raw_val).lower() or query in item.text().lower()
            item.setHidden(bool(query) and not matched)

    def _set_all_checked(self, checked: bool) -> None:
        if self.list_values is None or self._checklist_total == 0:
            return
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._syncing = True
        try:
            for row in range(self.list_values.count()):
                item = self.list_values.item(row)
                if item is not None and not item.isHidden():
                    item.setCheckState(state)
        finally:
            self._syncing = False
        self._update_selected_count_label()
        self.apply_current()

    def _reset_checklist_checked(self) -> None:
        if self.list_values is None or self._checklist_total == 0:
            return
        self._syncing = True
        try:
            for row in range(self.list_values.count()):
                item = self.list_values.item(row)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                    item.setCheckState(Qt.CheckState.Checked)
        finally:
            self._syncing = False
        self._update_selected_count_label()

    def _on_value_item_changed(self, _item: QListWidgetItem) -> None:
        if self._syncing:
            return
        self._update_selected_count_label()
        self.apply_current()

    def _update_selected_count_label(self) -> None:
        if self.lbl_selected_count is None:
            return
        total = self._checklist_total
        checked = len(self.selected_values())
        self.lbl_selected_count.setText(f"{checked} / {total} 선택")

    def _sync_null_only_inputs(self) -> None:
        is_null_only = (
            self.combo_null_mode is not None
            and self.combo_null_mode.currentData() == "null_only"
        )
        enabled = not is_null_only
        if self.spin_min is not None:
            self.spin_min.setEnabled(enabled)
        if self.spin_max is not None:
            self.spin_max.setEnabled(enabled)
        if self.list_values is not None and self._checklist_total > 0:
            self.list_values.setEnabled(enabled)
        if self.btn_select_all is not None and self._checklist_total > 0:
            self.btn_select_all.setEnabled(enabled)
        if self.btn_deselect_all is not None and self._checklist_total > 0:
            self.btn_deselect_all.setEnabled(enabled)

    def _on_null_mode_changed(self) -> None:
        self._sync_null_only_inputs()
        self.apply_current()

    def selected_values(self) -> set[str]:
        """체크리스트에서 현재 체크된 고유 값 집합 반환"""
        if self.list_values is None or self._checklist_total == 0:
            return set()
        selected: set[str] = set()
        for row in range(self.list_values.count()):
            item = self.list_values.item(row)
            if item is None:
                continue
            raw_val = item.data(Qt.ItemDataRole.UserRole)
            if raw_val is not None and item.checkState() == Qt.CheckState.Checked:
                selected.add(str(raw_val))
        return selected

    def build_rule(self) -> ColumnFilterRule | None:
        """현재 UI 입력 상태를 바탕으로 ColumnFilterRule(미필터 시 None) 생성"""
        dtype = self._spec.data_type
        if dtype in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self._checklist_total == 0:
                return None
            checked = self.selected_values()
            if len(checked) == self._checklist_total:
                return None
            return ColumnFilterRule(selected_values=frozenset(checked))

        if dtype is ColumnDataType.NUMBER:
            min_v = self.spin_min.value() if self.spin_min is not None else None
            max_v = self.spin_max.value() if self.spin_max is not None else None
            selected_nums: frozenset[str] | None = None
            if self._number_bucket_counts:
                checked = self.selected_values()
                if len(checked) < len(self._number_bucket_counts):
                    selected_nums = frozenset(checked)

            null_mode = (
                str(self.combo_null_mode.currentData() or "default")
                if self.combo_null_mode is not None
                else "include_null"
            )
            if null_mode == "null_only":
                return ColumnFilterRule(null_only=True, include_null=True)

            has_value_constraint = (
                min_v is not None or max_v is not None or selected_nums is not None
            )
            if null_mode == "default":
                if not has_value_constraint:
                    return None
                return ColumnFilterRule(
                    min_value=min_v,
                    max_value=max_v,
                    selected_values=selected_nums,
                    include_null=False,
                    null_only=False,
                )
            if null_mode == "measured_only":
                return ColumnFilterRule(
                    min_value=min_v,
                    max_value=max_v,
                    selected_values=selected_nums,
                    include_null=False,
                    null_only=False,
                )
            if not has_value_constraint:
                return None
            return ColumnFilterRule(
                min_value=min_v,
                max_value=max_v,
                selected_values=selected_nums,
                include_null=True,
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
        is_active = rule is not None and rule.is_active(self._spec.data_type)
        self.btn_clear.setEnabled(is_active)

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
            self._reset_checklist_checked()
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
            self._reset_checklist_checked()
            self._sync_null_only_inputs()
        else:
            if self.edit_text_query is not None:
                self.edit_text_query.clear()
            if self.combo_text_mode is not None:
                self.combo_text_mode.setCurrentIndex(0)

        self._debounce_timer.stop()
        self._update_clear_button_state(None)
        self.filter_changed.emit(self._col, None)

    def _on_close_clicked(self) -> None:
        if self._debounce_timer.isActive():
            self.apply_current()
        self.close()

    def _remove_app_event_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            app.removeEventFilter(self)
            self._app_filter_installed = False

    def showEvent(self, a0: QShowEvent | None) -> None:
        """팝오버 표시 시 마우스 그랩 없이 외부 클릭·ESC 닫기를 감지하는 필터 등록"""
        super().showEvent(a0)
        app = QApplication.instance()
        if app is not None and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def hideEvent(self, a0: QHideEvent | None) -> None:
        """팝오버가 닫힐 때 이벤트 필터를 해제하고 대기 중인 디바운스 입력을 반영"""
        self._remove_app_event_filter()
        if self._debounce_timer.isActive():
            self.apply_current()
        super().hideEvent(a0)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """외부 클릭·ESC·앱 비활성화 시 팝오버를 닫고 부모 창의 호버·원클릭 전환은 차단하지 않음"""
        if a1 is None or not self.isVisible():
            return False

        etype = a1.type()
        if etype == QEvent.Type.ApplicationDeactivate:
            self.close()
            return False

        if etype == QEvent.Type.KeyPress and isinstance(a1, QKeyEvent):
            if a1.key() == Qt.Key.Key_Escape:
                active_popup = QApplication.activePopupWidget()
                if active_popup is not None and self.isAncestorOf(active_popup):
                    return False
                self.close()
                return True
            return False

        if etype == QEvent.Type.MouseButtonPress and isinstance(a1, QMouseEvent):
            global_pos = a1.globalPosition().toPoint()
            if self.rect().contains(self.mapFromGlobal(global_pos)):
                return False

            active_popup = QApplication.activePopupWidget()
            if active_popup is not None and self.isAncestorOf(active_popup):
                return False

            widget_at = QApplication.widgetAt(global_pos)
            if widget_at is not None and (
                widget_at is self or self.isAncestorOf(widget_at)
            ):
                return False

            self.close()
            return bool(
                self._toggle_rect_global.isValid()
                and self._toggle_rect_global.contains(global_pos)
            )

        return False
