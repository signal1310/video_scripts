"""검색 필터, 열 필터 해제 및 예외 규칙 목록 진입 컨트롤 바 위젯 모듈"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, format_tooltip_with_shortcut
from src.gui.constants import COLUMN_PRESETS, PRESET_CUSTOM, PRESET_DEFAULT, preset_label
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    get_icon,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

__all__ = ["FilterTuningBar"]

COLUMN_PRESET_PREFIX: str = "열: "
COMPACT_BUTTON_TEXT: str = "좁게 보기"
GROUP_BUTTON_PREFIX: str = "그룹: "
GROUP_NONE_LABEL: str = "없음"
BAR_CONTROL_SIZE_PX: int = 28
SHRINKABLE_BUTTON_MIN_WIDTH_PX: int = 44
AUTO_FIT_LABEL: str = "모든 열 크기 맞춤"
COMPACT_TOOLTIP: str = "셀 여백과 행 높이를 줄여 한 화면에 더 많은 영상을 표시합니다."
ROW_GROUP_TOOLTIP: str = "영상을 폴더나 범주형 열 값으로 묶어 접을 수 있는 그룹 머리글을 표시합니다."


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
    expand_all_groups_requested = pyqtSignal()
    collapse_all_groups_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False
        self._active_tokens: list[str] = []
        self._disabled_count: int = 0
        self._exception_shortcut: str = DEFAULT_SHORTCUTS["edit.exception_editor"]
        self._row_group_options: list[tuple[str, str]] = []
        self._group_by: str = ""
        self._row_group_action_group: QActionGroup | None = None
        self._expand_groups_action: QAction | None = None
        self._collapse_groups_action: QAction | None = None
        self._row_group_emitting: bool = False

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(8)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱, 권장 처리, 판정 사유 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.textChanged.connect(self.filter_changed.emit)

        self.btn_clear_col_filters = QPushButton("", self)
        self.btn_clear_col_filters.setObjectName("ClearColumnFiltersButton")
        self.btn_clear_col_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_col_filters.setToolTip("적용된 모든 열 헤더 필터를 해제합니다.")
        self.btn_clear_col_filters.setVisible(False)
        self.btn_clear_col_filters.clicked.connect(
            self.clear_column_filters_requested.emit
        )

        self.btn_reset_filters = QPushButton("필터 초기화", self)
        self.btn_reset_filters.setObjectName("ClearAllFiltersButton")
        self.btn_reset_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_filters.setToolTip("검색어와 열 헤더 필터를 모두 초기화합니다.")
        self.btn_reset_filters.setVisible(False)
        self.btn_reset_filters.clicked.connect(self.reset_filters_requested.emit)

        divider = QFrame(self)
        divider.setObjectName("ToolbarDivider")
        divider.setFixedSize(1, 20)

        self.btn_edit_exceptions = QPushButton("예외 규칙", self)
        self.btn_edit_exceptions.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=get_active_palette(),
            size=14,
            pad_right=6,
        )
        self.btn_edit_exceptions.clicked.connect(self.exception_editor_requested.emit)
        # - 규칙 개수를 붙인 글자가 길어져도 좁은 창에서 최소 폭까지만 줄어들게 함
        self.btn_edit_exceptions.setMinimumWidth(SHRINKABLE_BUTTON_MIN_WIDTH_PX)
        self.btn_edit_exceptions.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

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
            act.triggered.connect(
                lambda _checked=False, key=preset_key: self._on_preset_action(key)
            )
            self._preset_group.addAction(act)
            preset_menu.addAction(act)
            self._preset_actions[preset_key] = act
        self.btn_column_preset.setMenu(preset_menu)
        self._preset_menu = preset_menu
        # - 선호 폭은 글자 폭을 따르되, 좁은 창에서는 44px까지만 줄어 창 최소 폭을 밀어내지 않게 함
        self.btn_column_preset.setMinimumWidth(SHRINKABLE_BUTTON_MIN_WIDTH_PX)
        self.btn_column_preset.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_column_preset.setToolTip(
            "표에 표시할 열 묶음을 고릅니다. 기본, 화질, 해상도, 기술 사양, 전체 중에서 선택합니다."
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
        self._row_group_menu.aboutToShow.connect(self._rebuild_row_group_actions)
        self.btn_row_group.setMenu(self._row_group_menu)
        set_button_icon(
            self.btn_row_group, "folder-tree", palette=get_active_palette(), size=14, pad_right=6
        )

        self.btn_auto_fit = create_icon_button(
            self,
            AUTO_FIT_LABEL,
            self.auto_fit_requested.emit,
            accessible_name=AUTO_FIT_LABEL,
            fixed_size=BAR_CONTROL_SIZE_PX,
        )
        set_button_icon(
            self.btn_auto_fit, "move-horizontal", palette=get_active_palette(), size=14, pad_right=0
        )

        self.btn_compact = QPushButton(COMPACT_BUTTON_TEXT, self)
        self.btn_compact.setObjectName("CompactTableButton")
        self.btn_compact.setCheckable(True)
        self.btn_compact.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_compact.setToolTip(COMPACT_TOOLTIP)
        # - 선호 폭은 글자 폭을 따르되, 좁은 창에서는 44px까지만 줄어 창 최소 폭을 밀어내지 않게 함
        self.btn_compact.setMinimumWidth(SHRINKABLE_BUTTON_MIN_WIDTH_PX)
        self.btn_compact.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_compact.toggled.connect(self._on_compact_toggled)
        set_button_icon(
            self.btn_compact, "minimize-2", palette=get_active_palette(), size=14, pad_right=6
        )

        row.addWidget(self.search_edit, stretch=1)
        row.addWidget(self.btn_clear_col_filters)
        row.addWidget(self.btn_reset_filters)
        row.addWidget(divider)
        row.addWidget(self.btn_column_preset)
        row.addWidget(self.btn_row_group)
        row.addWidget(self.btn_auto_fit)
        row.addWidget(self.btn_compact)
        row.addWidget(self.btn_edit_exceptions)

        self._refresh_exception_button()
        self.init_theme_listener()

    def set_exception_shortcut(self, key: str) -> None:
        """예외 규칙 편집기 단축키 변경을 예외 규칙 버튼 툴팁에 반영"""
        self._exception_shortcut = key
        self._refresh_exception_button()

    def set_any_filter_active(self, active: bool) -> None:
        """활성화된 필터 존재 여부에 따라 전체 필터 초기화 버튼 노출 갱신"""
        self.btn_reset_filters.setVisible(active)

    def _on_preset_action(self, preset_key: str) -> None:
        """프리셋 메뉴 항목 선택을 라벨에 반영하고 시그널로 알림"""
        self.set_column_preset(preset_key)
        self.column_preset_selected.emit(preset_key)

    def set_column_preset(self, preset_key: str) -> None:
        """현재 열 프리셋을 드롭다운 라벨과 체크 표시에 시그널 없이 반영

        - 사용자 지정처럼 메뉴에 없는 키는 어느 항목도 선택하지 않고 라벨만 바꿈
        """
        self.btn_column_preset.setText(f"{COLUMN_PRESET_PREFIX}{preset_label(preset_key)}")
        selected = self._preset_actions.get(preset_key)
        if selected is not None:
            selected.setChecked(True)
            return
        if preset_key == PRESET_CUSTOM:
            self._preset_group.setExclusive(False)
            for act in self._preset_actions.values():
                act.setChecked(False)
            self._preset_group.setExclusive(True)

    def set_row_group_options(self, options: list[tuple[str, str]]) -> None:
        """행 묶기 메뉴에 넣을 (키, 이름) 목록을 기억하고 다시 만들 준비를 함"""
        self._row_group_options = list(options)
        self._rebuild_row_group_actions()

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        """지금 적용 중인 묶기 키를 버튼 글자와 메뉴 체크에 시그널 없이 반영

        - 메뉴 항목은 지우지 않고 체크 상태만 바꿔 실행 중인 triggered 처리 중 동작이 사라지지 않게 함
        """
        if group_by:
            self.btn_row_group.setText(f"{GROUP_BUTTON_PREFIX}{label} ({count})")
        else:
            self.btn_row_group.setText(f"{GROUP_BUTTON_PREFIX}{GROUP_NONE_LABEL}")
        self._group_by = group_by
        self._sync_row_group_state()

    def _sync_row_group_state(self) -> None:
        """지금 묶기 키에 맞춰 메뉴 체크와 그룹 전체 명령 활성 상태만 바꿈"""
        for key, act in self._row_group_actions.items():
            act.setChecked(key == self._group_by)
        enabled = bool(self._group_by)
        for act in (self._expand_groups_action, self._collapse_groups_action):
            if act is not None:
                act.setEnabled(enabled)

    def _rebuild_row_group_actions(self) -> None:
        """없음, 폴더, 범주형 열과 펼치기, 접기 항목으로 메뉴를 다시 채움"""
        menu = self._row_group_menu
        # - 열린 메뉴나 실행 중인 동작을 지우면 triggered 처리 중 객체가 사라지므로 다음에 열 때 다시 만듦
        if menu.isVisible() or self._row_group_emitting:
            return
        menu.clear()
        self._row_group_actions = {}
        persistent = QActionGroup(menu)
        persistent.setExclusive(True)
        for key, label in [("", GROUP_NONE_LABEL), *self._row_group_options]:
            act = QAction(label, menu)
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

    def _refresh_exception_button(self) -> None:
        active = len(self._active_tokens)
        disabled = self._disabled_count
        if active > 0 and disabled > 0:
            label = f"예외 규칙 ({active}, 비활성 {disabled})"
        elif active > 0:
            label = f"예외 규칙 ({active})"
        elif disabled > 0:
            label = f"예외 규칙 (비활성 {disabled})"
        else:
            label = "예외 규칙"
        self.btn_edit_exceptions.setText(label)

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

    def set_active_column_filter_count(self, count: int) -> None:
        """활성화된 열 헤더 동적 필터 개수에 따라 일괄 해제 배지 버튼을 갱신"""
        if count > 0:
            self.btn_clear_col_filters.setText(f"열 필터 {count}개 ×")
            self.btn_clear_col_filters.setVisible(True)
        else:
            self.btn_clear_col_filters.setText("")
            self.btn_clear_col_filters.setVisible(False)

    def set_disabled_exception_count(self, count: int) -> None:
        """비활성화된 예외 규칙 건수를 예외 규칙 버튼 라벨, 툴팁에 반영"""
        self._disabled_count = max(0, count)
        self._refresh_exception_button()

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
        if not self._updating:
            self.exceptions_changed.emit()

    def sync_filter_state(
        self,
        *,
        search: str,
        exception_tokens: list[str],
    ) -> None:
        """저장된 검색, 예외 규칙 상태를 툴바 컨트롤에 시그널 없이 반영"""
        self._updating = True
        try:
            self.search_edit.blockSignals(True)
            try:
                self.search_edit.setText(search)
            finally:
                self.search_edit.blockSignals(False)
            self._active_tokens = [
                tok.strip() for tok in exception_tokens if tok.strip()
            ]
            self._refresh_exception_button()
        finally:
            self._updating = False

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 검색창 및 툴바 버튼 아이콘 색상을 갱신"""
        if self._action_search is not None:
            self._action_search.setIcon(
                get_icon("search", palette=palette, size=14, pad_right=7)
            )
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=palette,
            size=14,
            pad_right=6,
        )
        set_button_icon(self.btn_column_preset, "eye", palette=palette, size=14, pad_right=6)
        set_button_icon(self.btn_row_group, "folder-tree", palette=palette, size=14, pad_right=6)
        set_button_icon(self.btn_auto_fit, "move-horizontal", palette=palette, size=14, pad_right=0)
        set_button_icon(self.btn_compact, "minimize-2", palette=palette, size=14, pad_right=6)
