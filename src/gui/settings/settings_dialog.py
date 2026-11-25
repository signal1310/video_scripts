"""환경 설정 대화상자: 1줄 상단 바, 5범주 좌측 사이드바, 실시간 통합 검색, 자동 저장 모듈"""

import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QResizeEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    ClassifierConfig,
    SavingsConfig,
    ThresholdsConfig,
    merge_tuner_criteria,
)
from src.core.settings.app_state import (
    AppState,
    load_app_state,
    record_recent_roots,
    reset_app_state,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.settings.ui_state import UiState
from src.core.workspace_paths import (
    are_all_valid_dirs,
    normalize_root_spec,
    primary_root_dir,
)
from src.gui.constants import browse_directory
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.criteria_tuner_panel import CriteriaTunerPanel
from src.gui.settings.execution_sections import ExecutionTabPanel
from src.gui.settings.setting_controls import (
    apply_inline_reset_button_icon,
    build_help_link_row,
    build_scroll_tab_shell,
)
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_field_bindings import (
    NON_CONFIG_SETTING_KEYS,
    apply_setting_values,
    read_setting_value,
    set_row_value,
    split_setting_key,
)
from src.gui.settings.settings_form_fields import SettingsPageForm
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.settings_nav import (
    AUTO_RAIL_WIDTH,
    NAV_CATEGORIES,
    RESULTS_INDEX,
    SettingsSidebar,
)
from src.gui.settings.settings_search import SearchEntry
from src.gui.settings.settings_search_controller import (
    SearchContext,
    SettingsSearchController,
)
from src.gui.settings.settings_search_view import SearchResultsView
from src.gui.settings.settings_sections import (
    GENERAL_PAGE_TITLE,
    RESOLUTION_PAGE_TITLE,
    build_general_page,
    build_resolution_page,
)
from src.gui.settings.shortcuts_panel import ShortcutsPanel
from src.gui.settings.tuner_slider_row import TunerSliderRow
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager

_HEADER_MARGINS: tuple[int, int, int, int] = (12, 10, 12, 8)
_CONTENT_MARGINS: tuple[int, int, int, int] = (10, 10, 12, 12)
_HEADER_DIVIDER_HEIGHT: int = 1
_DIALOG_DEFAULT_WIDTH: int = 720
_DIALOG_DEFAULT_HEIGHT: int = 620
_DIALOG_MIN_WIDTH: int = 460
_DIALOG_MIN_HEIGHT: int = 420
_SEARCH_PLACEHOLDER: str = "설정 이름, 설명 검색..."
_PIN_TOOLTIP: str = (
    "설정 창을 앱의 다른 창보다 위에 고정합니다. 다른 프로그램 창은 가리지 않습니다."
)
_UNPIN_TOOLTIP: str = "항상 위에 표시를 풀어 앱의 다른 창 뒤로 갈 수 있게 합니다."
_HELP_TOOLTIP: str = "사용 가이드를 엽니다. (F1)"
_INTERNAL_REFERENCE_TEXT: str = "내부 기준값 보기"
_INTERNAL_REFERENCE_TOOLTIP: str = (
    "사용 가이드 판정 원리 탭의 내부 고정 기준값 안내로 이동합니다."
)
_INTERNAL_REFERENCE_ANCHOR: str = "internal-thresholds"
_NO_RESULT_TEXT: str = "일치하는 설정이 없습니다."


def _ancestor_of_type(widget: QWidget, cls: type[QWidget]) -> QWidget | None:
    """위젯의 부모 사슬에서 지정한 클래스의 첫 조상을 찾음"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, cls):
            return parent
        parent = parent.parentWidget()
    return None


class SettingsDialog(QDialog, ThemeAwareMixin):
    """일반, 해상도 및 분석, 판별 기준, 재인코딩, 단축키를 범주 사이드바로 전환하는 설정 대화상자"""

    TAB_GENERAL: int = 0
    TAB_ENVIRONMENT: int = 0
    TAB_RESOLUTION: int = 1
    TAB_CRITERIA: int = 2
    TAB_EXECUTION: int = 3
    TAB_SHORTCUTS: int = 4
    TAB_RESULTS: int = RESULTS_INDEX
    CATEGORY_TABS: tuple[int, ...] = (0, 1, 2, 3, 4)

    settings_applied = pyqtSignal(ClassifierConfig, str)
    settings_reset_confirmed = pyqtSignal()
    settings_reset = pyqtSignal(object)
    criteria_changed = pyqtSignal(object, object)
    tab_changed = pyqtSignal(int)
    setting_hints_changed = pyqtSignal(bool)
    shortcuts_changed = pyqtSignal(dict)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    help_requested = pyqtSignal(object, str)

    def __init__(
        self,
        current_config: ClassifierConfig | None = None,
        current_root_dir: str | None = None,
        app_state: AppState | None = None,
        *,
        active_tab: int | None = None,
        busy: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("환경 설정")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)

        self._is_loading: bool = True
        self._owner: QWidget | None = parent
        self._pinned: bool = False
        base_state = app_state.copy() if app_state is not None else load_app_state()
        self._cfg: ClassifierConfig = (
            current_config.copy()
            if current_config is not None
            else base_state.to_classifier_config()
        )
        self._initial_root_dir = current_root_dir or resolve_effective_root_dir()
        self._app_state = base_state
        self._app_state.apply_classifier_config(self._cfg)
        self._last_saved_root_dir: str = self._initial_root_dir

        self._rows: dict[str, SettingRow | TunerSliderRow] = {}
        self._form_rows: dict[str, SettingRow | TunerSliderRow] = {}
        self._row_pages: dict[str, int] = {}
        self._row_cards: dict[str, SettingsGroupCard] = {}
        self._section_rows: dict[CollapsibleSection, list[str]] = {}
        self._advanced_sections: list[CollapsibleSection] = []
        self._page_scrolls: dict[int, QScrollArea] = {}
        self._entries: list[SearchEntry] = []
        self._last_category: int = 0
        self.results_view = SearchResultsView(self)

        main_layout = QVBoxLayout(self)
        # - 사이드바를 창 왼쪽과 아래에 밀착시키려면 최상위 여백 대신 머리글과 내용 칸에 여백을 나눠 줌
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        (
            top_bar,
            self.search_edit,
            self.chk_show_hints,
            self.btn_pin,
            self.btn_help,
        ) = self._build_top_bar()
        header = QWidget(self)
        header.setObjectName("SettingsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(*_HEADER_MARGINS)
        header_layout.setSpacing(0)
        header_layout.addWidget(top_bar)
        main_layout.addWidget(header)
        main_layout.addWidget(self._build_header_divider())

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sidebar = SettingsSidebar(self)
        self.sidebar.category_selected.connect(self._on_sidebar_selected)
        body_layout.addWidget(self.sidebar)
        self.tab_widget = self._build_tab_widget()
        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(*_CONTENT_MARGINS)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.tab_widget)
        body_layout.addWidget(content, stretch=1)
        main_layout.addLayout(body_layout, stretch=1)

        self._build_pages(busy)
        self._collect_search_entries()
        self._search_controller = self._build_search_controller()
        self._build_history_actions()
        self._connect_signals()
        initial_tab = (
            active_tab
            if active_tab is not None
            else self._app_state.ui.layout.settings_tab
        )
        self.set_active_tab(initial_tab)
        self._load_values()
        self._is_loading = False
        self._disable_button_auto_default()
        self.init_theme_listener()

    def set_owner(self, owner: QWidget | None) -> None:
        """앱 범위 항상 위 고정에 쓸 소유자 창을 기록"""
        self._owner = owner


    def _build_top_bar(
        self,
    ) -> tuple[QWidget, QLineEdit, QCheckBox, QPushButton, QPushButton]:
        bar = QWidget(self)
        bar.setObjectName("SettingsTopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        search_edit = QLineEdit(bar)
        search_edit.setObjectName("SettingsSearchEdit")
        search_edit.setPlaceholderText(_SEARCH_PLACEHOLDER)
        search_edit.setClearButtonEnabled(True)
        search_edit.setAccessibleName("설정 검색")
        layout.addWidget(search_edit, stretch=1)

        chk_show_hints = QCheckBox("쉬운 설명", bar)
        chk_show_hints.setToolTip(
            "각 설정의 의도, 조정 방향, 실시간 기준 예시, 단축키 명령의 설명을 항목 아래에 함께 표시합니다. "
            "(끄면 항목 위에 마우스를 올렸을 때 툴팁으로만 확인합니다.)"
        )
        chk_show_hints.toggled.connect(self._on_show_hints_toggled)
        layout.addWidget(chk_show_hints)

        btn_pin = QPushButton(bar)
        btn_pin.setObjectName("IconToggleButton")
        btn_pin.setCheckable(True)
        btn_pin.setIconSize(QSize(16, 16))
        btn_pin.setFixedSize(26, 26)
        btn_pin.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pin.setToolTip(_PIN_TOOLTIP)
        btn_pin.setAccessibleName("항상 위에 표시")
        btn_pin.toggled.connect(self._on_pin_toggled)
        layout.addWidget(btn_pin)

        btn_help = QPushButton(bar)
        btn_help.setObjectName("IconActionButton")
        btn_help.setIcon(get_icon("help-circle", size=16))
        btn_help.setIconSize(QSize(16, 16))
        btn_help.setFixedSize(26, 26)
        btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_help.setToolTip(_HELP_TOOLTIP)
        btn_help.setAccessibleName("사용 가이드")
        btn_help.clicked.connect(lambda: self._request_help(""))
        layout.addWidget(btn_help)
        return bar, search_edit, chk_show_hints, btn_pin, btn_help

    def _build_header_divider(self) -> QFrame:
        """상단 바와 본문 사이 1px 구분선. 사이드바가 이 선 아래에서 시작하게 함"""
        divider = QFrame(self)
        divider.setObjectName("SettingsHeaderDivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(_HEADER_DIVIDER_HEIGHT)
        return divider

    def _build_tab_widget(self) -> QTabWidget:
        tab_widget = QTabWidget(self)
        tab_widget.setObjectName("SettingsTabs")
        tab_widget.setDocumentMode(True)
        tab_bar = tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)
            tab_bar.setVisible(False)
        return tab_widget

    def _reset_tooltip(self) -> str:
        return (
            "모든 분석 설정, 필터, 예외 규칙, 재인코딩 설정 및 화면 테마를 기본값으로 되돌립니다. "
            "(작업 폴더 경로는 유지됩니다.)"
        )

    def _new_config_page(
        self,
        title: str,
        reset_text: str,
        reset_tooltip: str,
        on_reset: Any,
    ) -> tuple[QWidget, QVBoxLayout, QScrollArea, QPushButton]:
        page = QWidget(self.tab_widget)
        scroll, _body, body_layout, btn_reset = build_scroll_tab_shell(
            page,
            reset_button_text=reset_text,
            reset_tooltip=reset_tooltip,
            on_reset=on_reset,
        )
        self.tab_widget.addTab(page, title)
        return page, body_layout, scroll, btn_reset

    def _add_internal_reference(self, advanced: CollapsibleSection) -> None:
        advanced.add_widget(
            build_help_link_row(
                _INTERNAL_REFERENCE_TEXT,
                _INTERNAL_REFERENCE_TOOLTIP,
                lambda: self._request_help(_INTERNAL_REFERENCE_ANCHOR),
            )
        )


    def _build_pages(self, busy: bool) -> None:
        self._page_forms: dict[int, SettingsPageForm] = {}

        _page, body, scroll, self.btn_reset_defaults = self._new_config_page(
            GENERAL_PAGE_TITLE,
            "전체 초기화",
            self._reset_tooltip(),
            self._on_reset_defaults_clicked,
        )
        general_advanced = CollapsibleSection(
            title="고급 설정", subtitle="(표 서식, 콘솔 출력)", is_expanded=False
        )
        general_form = build_general_page(body, general_advanced, self._on_browse_root_dir)
        body.addWidget(general_advanced)
        body.addStretch(1)
        self._add_internal_reference(general_advanced)
        self._page_forms[0] = general_form
        self._page_scrolls[0] = scroll
        self.combo_theme = self._extra_widget(general_form, "combo_theme", QComboBox)
        self.edit_root_dir = self._extra_widget(general_form, "edit_root_dir", QLineEdit)
        self.btn_browse_root = self._extra_widget(
            general_form, "btn_browse_root", QPushButton
        )
        self.edit_root_dir.textChanged.connect(self._on_root_dir_text_changed)

        _page2, body2, scroll2, btn_reset_resolution = self._new_config_page(
            RESOLUTION_PAGE_TITLE, "", "", lambda: None
        )
        btn_reset_resolution.hide()
        resolution_advanced = CollapsibleSection(
            title="고급 설정", subtitle="(화질 분석 기준, 여백 기준)", is_expanded=False
        )
        resolution_form = build_resolution_page(body2, resolution_advanced)
        body2.addWidget(resolution_advanced)
        body2.addStretch(1)
        self._add_internal_reference(resolution_advanced)
        self._page_forms[1] = resolution_form
        self._page_scrolls[1] = scroll2

        self.criteria_panel = CriteriaTunerPanel(
            thresholds=self._cfg.thresholds,
            savings=self._cfg.savings,
            parent=self.tab_widget,
            config=self._cfg,
            show_hints=bool(self._app_state.ui.show_setting_hints),
        )
        self.criteria_panel.set_busy(busy)
        self.tab_widget.addTab(self.criteria_panel, "판별 기준")
        self._page_scrolls[2] = self.criteria_panel.scroll_area
        if self.criteria_panel.section_advanced is not None:
            self._add_internal_reference(self.criteria_panel.section_advanced)

        self.execution_panel = ExecutionTabPanel(
            self._cfg.execution, self._cfg.encode, self.tab_widget
        )
        self.tab_widget.addTab(self.execution_panel, "재인코딩")
        execution_scroll = self.execution_panel.findChild(QScrollArea)
        if execution_scroll is not None:
            self._page_scrolls[3] = execution_scroll
        if self.execution_panel.section_advanced is not None:
            self._add_internal_reference(self.execution_panel.section_advanced)

        self.shortcuts_panel = ShortcutsPanel(
            overrides=self._app_state.ui.shortcuts,
            show_hints=bool(self._app_state.ui.show_setting_hints),
            show_search=False,
            parent=self.tab_widget,
        )
        self.tab_widget.addTab(self.shortcuts_panel, "단축키")

        self._build_results_page()
        self._register_page_rows(0, general_form)
        self._register_page_rows(1, resolution_form)
        self._register_page_rows(2, self.criteria_panel)
        for key, row in self.execution_panel.rows.items():
            self._rows[key] = row
            self._row_pages[key] = 3
        cards: list[SettingsGroupCard] = [
            *general_form.cards,
            *resolution_form.cards,
            *self.criteria_panel.cards,
            *self.execution_panel.findChildren(SettingsGroupCard),
            *self.shortcuts_panel.findChildren(SettingsGroupCard),
        ]
        self._advanced_sections = [
            section
            for section in (
                general_advanced,
                resolution_advanced,
                self.criteria_panel.section_advanced,
                self.execution_panel.section_advanced,
            )
            if section is not None
        ]
        for card in cards:
            card.help_requested.connect(self._request_help)
        for key, row in self._rows.items():
            card = _ancestor_of_type(row, SettingsGroupCard)
            if isinstance(card, SettingsGroupCard):
                self._row_cards[key] = card
            section = _ancestor_of_type(row, CollapsibleSection)
            if isinstance(section, CollapsibleSection):
                self._section_rows.setdefault(section, []).append(key)

    @staticmethod
    def _extra_widget(form: SettingsPageForm, name: str, cls: type[Any]) -> Any:
        widget = form.extras.get(name)
        if not isinstance(widget, cls):
            raise TypeError(f"페이지 준비물 {name} 형식이 올바르지 않습니다")
        return widget

    def _register_page_rows(self, page_index: int, form: Any) -> None:
        """일반 페이지 폼이나 판별 기준 패널의 행을 범주 색인과 함께 등록"""
        if isinstance(form, SettingsPageForm):
            for key, row in form.rows.items():
                self._rows[key] = row
                self._form_rows[key] = row
                self._row_pages[key] = page_index
            return
        panel = form
        for key, row in panel.setting_rows.items():
            self._rows[key] = row
            self._row_pages[key] = page_index
        for key, row in panel.standard_rows.items():
            self._form_rows[key] = row


    def _build_results_page(self) -> None:
        self.results_view.row_activated.connect(self._jump_to_row)
        self.results_view.shortcut_activated.connect(self._jump_to_shortcut)
        self.tab_widget.addTab(self.results_view, "전체 결과")
        self._page_scrolls[self.TAB_RESULTS] = self.results_view.scroll_area

    def _collect_search_entries(self) -> None:
        entries: list[SearchEntry] = []
        for key, row in self._rows.items():
            page = self._row_pages.get(key, 0)
            title = self._row_title(row)
            card = self._row_cards.get(key)
            card_title = card.title_text if card is not None else ""
            text = " ".join(
                part
                for part in (title, self._row_desc(row), card_title, self._category_label(page))
                if part
            )
            entries.append(SearchEntry(key=key, category=page, title=title, text=text))
        self._entries = entries

    @staticmethod
    def _row_title(row: SettingRow | TunerSliderRow) -> str:
        if isinstance(row, TunerSliderRow):
            return row.spec.title
        return row.title

    @staticmethod
    def _row_desc(row: SettingRow | TunerSliderRow) -> str:
        if isinstance(row, TunerSliderRow):
            return f"{row.spec.desc} {row.spec.hint}".strip()
        return row.desc

    @staticmethod
    def _category_label(page: int) -> str:
        for category in NAV_CATEGORIES:
            if category.index == page:
                return category.label
        return ""

    def _build_history_actions(self) -> None:
        """설정 창이 활성일 때도 메인 창의 되돌리기, 다시 실행과 사용 가이드 단축키가 동작하도록 창 소유 동작 등록"""
        self._undo_action = QAction(self)
        self._undo_action.triggered.connect(lambda _checked=False: self.undo_requested.emit())
        self._redo_action = QAction(self)
        self._redo_action.triggered.connect(lambda _checked=False: self.redo_requested.emit())
        self._help_action = QAction(self)
        self._help_action.setShortcut(QKeySequence("F1"))
        self._help_action.triggered.connect(lambda _checked=False: self._request_help(""))
        self.addAction(self._undo_action)
        self.addAction(self._redo_action)
        self.addAction(self._help_action)

    def _connect_signals(self) -> None:
        self.search_edit.textChanged.connect(self._apply_search)
        self.tab_widget.currentChanged.connect(self._on_tab_widget_changed)
        self.execution_panel.changed.connect(self._on_setting_changed)
        self.execution_panel.result_policy_changed.connect(self._update_forced_hidden)
        self.execution_panel.criteria_requested.connect(
            lambda: self.set_active_tab(self.TAB_CRITERIA)
        )
        self.criteria_panel.criteria_changed.connect(self._on_criteria_panel_changed)
        self.criteria_panel.changed.connect(self._on_setting_changed)
        self.shortcuts_panel.shortcuts_changed.connect(self._on_shortcuts_panel_changed)
        for form in self._page_forms.values():
            for key, row in form.rows.items():
                if not isinstance(row, SettingRow) or key in NON_CONFIG_SETTING_KEYS:
                    continue
                row.value_changed.connect(self._on_setting_changed)

    def set_history_shortcuts(self, undo_keys: list[str], redo_keys: list[str]) -> None:
        """되돌리기, 다시 실행에 바인딩할 유효 단축키 목록을 반영"""
        self._undo_action.setShortcuts([QKeySequence(key) for key in undo_keys])
        self._redo_action.setShortcuts([QKeySequence(key) for key in redo_keys])

    def _on_sidebar_selected(self, page: int) -> None:
        self.set_active_tab(page)


    def _disable_button_auto_default(self) -> None:
        """입력칸의 Enter가 다이얼로그 기본 버튼(전체 초기화 등)을 누르지 않도록 버튼의 자동 기본 설정 해제"""
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    @classmethod
    def normalize_tab_index(cls, tab_index: int) -> int:
        """범주 페이지 인덱스를 허용 범위로 정규화"""
        if tab_index in cls.CATEGORY_TABS or tab_index == cls.TAB_RESULTS:
            return tab_index
        return cls.TAB_GENERAL

    def _on_tab_widget_changed(self, tab_index: int) -> None:
        target = self.normalize_tab_index(tab_index)
        self.sidebar.set_current(target)
        if target != self.TAB_RESULTS:
            self._last_category = target
            if self._app_state.ui.layout.settings_tab != target:
                self._app_state.ui.layout.settings_tab = target
                if not self._is_loading:
                    self.tab_changed.emit(target)

    def set_active_tab(self, tab_index: int) -> None:
        """범주 페이지 전환(판별 기준, 재인코딩, 단축키 진입점 포함)"""
        target = self.normalize_tab_index(tab_index)
        if target != self.TAB_RESULTS:
            self._last_category = target
        self.tab_widget.setCurrentIndex(target)
        self.sidebar.set_current(target)
        if target != self.TAB_RESULTS:
            self._app_state.ui.layout.settings_tab = target

    def set_busy(self, busy: bool) -> None:
        """백그라운드 스캔, 분석 중 판별 기준 조작 잠금 동기화"""
        self.criteria_panel.set_busy(busy)

    def _on_pin_toggled(self, checked: bool) -> None:
        """앱 범위 항상 위 고정을 소유자 전환으로 적용하고 아이콘과 접근성 이름을 갱신"""
        self._pinned = bool(checked)
        self.btn_pin.setIcon(
            get_icon("pin" if checked else "pin-off", palette=ThemeManager().current_palette, size=16)
        )
        self.btn_pin.setAccessibleName("항상 위에 표시 해제" if checked else "항상 위에 표시")
        self.btn_pin.setToolTip(_UNPIN_TOOLTIP if checked else _PIN_TOOLTIP)
        if self._owner is None:
            return
        # - setParent는 창을 숨기므로 표시 중일 때만 위치와 앞 순서를 되돌려 놓음(미리 보기 창 방식과 동일)
        was_visible = self.isVisible()
        geometry = self.geometry()
        self.setParent(self._owner if checked else None, Qt.WindowType.Window)
        if not was_visible:
            return
        self.show()
        self.setGeometry(geometry)
        self.raise_()

    def _request_help(self, anchor: str) -> None:
        """사용 가이드 열기 요청을 현재 설정과 앵커와 함께 알림"""
        self.help_requested.emit(self._cfg.copy(), anchor)

    def _apply_hints_visibility(self, visible: bool) -> None:
        self.criteria_panel.set_hints_visible(visible)
        self.shortcuts_panel.set_hints_visible(visible)
        self.execution_panel.set_hints_visible(visible)
        for row in self._form_rows.values():
            row.set_hint_visible(visible)

    def _on_show_hints_toggled(self, checked: bool) -> None:
        self._apply_hints_visibility(checked)
        self._app_state.ui.show_setting_hints = bool(checked)
        if self._is_loading:
            return
        self._persist()
        self.setting_hints_changed.emit(bool(checked))

    def _persist(self, *, root_dir: str | None = None) -> None:
        try:
            save_app_state(self._app_state, root_dir=root_dir)
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)

    def _on_shortcuts_panel_changed(self, overrides: dict[str, str]) -> None:
        # - 자동 저장은 대화상자가 가진 상태 사본을 기록하므로 단축키 변경을 사본에도 반영해 옛 값이 되돌아가지 않게 함
        self._app_state.ui.shortcuts = dict(overrides)
        self.shortcuts_changed.emit(dict(overrides))


    def _load_values(self) -> None:
        show_hints = bool(self._app_state.ui.show_setting_hints)
        self.chk_show_hints.setChecked(show_hints)
        self._apply_hints_visibility(show_hints)

        idx = self.combo_theme.findData(ThemeManager().mode)
        if idx >= 0:
            self.combo_theme.setCurrentIndex(idx)

        self.edit_root_dir.setText(self._initial_root_dir)
        self.edit_root_dir.setCursorPosition(0)
        self.edit_root_dir.setToolTip(self._initial_root_dir)

        self.execution_panel.load(self._cfg.execution, self._cfg.encode)
        # - 결과 파일 처리로 숨긴 폴더 이름 행을 검색 조정자에도 즉시 반영(검색 결과/배지에서 제외)
        self._update_forced_hidden()
        for key, row in self._form_rows.items():
            if split_setting_key(key) is None:
                continue
            value = read_setting_value(self._cfg, key)
            if value is not None:
                set_row_value(row, value)
        self.criteria_panel.sync_from_config(self._cfg.thresholds, self._cfg.savings)
        self.criteria_panel.set_config(self._cfg)
        self.shortcuts_panel.set_overrides(self._app_state.ui.shortcuts)
        self._reset_search_state()

    def _on_root_dir_text_changed(self, text: str) -> None:
        self.edit_root_dir.setToolTip(text)
        if self._is_loading:
            return
        normalized_input = normalize_root_spec(text)
        if (
            (not normalized_input or are_all_valid_dirs(normalized_input))
            and normalized_input != self._last_saved_root_dir
        ):
            self._auto_save()

    def _on_browse_root_dir(self) -> None:
        curr = normalize_root_spec(self.edit_root_dir.text())
        start = primary_root_dir(curr) if are_all_valid_dirs(curr) else os.path.expanduser("~")
        chosen = browse_directory(self, "작업 폴더 선택", start)
        if chosen:
            normalized = os.path.normpath(chosen)
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            if normalized != self._last_saved_root_dir:
                self._auto_save()

    def sync_root_dir(self, root_dir: str) -> None:
        """외부에서 변경된 작업 폴더 경로를 열려 있는 설정 UI에 반영"""
        normalized = normalize_root_spec(root_dir)
        if (
            normalized == self._last_saved_root_dir
            and self.edit_root_dir.text().strip() == normalized
        ):
            return
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._initial_root_dir = normalized
            self._last_saved_root_dir = normalized
            self.edit_root_dir.setText(normalized)
            self.edit_root_dir.setCursorPosition(0)
            self.edit_root_dir.setToolTip(normalized)
        finally:
            self._is_loading = prev_loading

    def _on_setting_changed(self) -> None:
        if self._is_loading:
            return
        self._auto_save()

    def sync_preview_settings(self, ui: UiState) -> None:
        """미리 보기에서 바뀐 설정(구간 이동 간격, 음량, 음소거, 정보 표시)을 내부 앱 상태 사본에 반영

        - 설정 저장이 이 사본 전체를 기록하므로 옛 값으로 덮어쓰지 않게 하기 위함
        """
        mine = self._app_state.ui
        mine.preview_seek_seconds = ui.preview_seek_seconds
        mine.preview_volume = ui.preview_volume
        mine.preview_muted = ui.preview_muted
        mine.preview_playback_rate = ui.preview_playback_rate
        mine.preview_fullscreen = ui.preview_fullscreen
        mine.preview_always_on_top = ui.preview_always_on_top
        mine.compare_top_bar_collapsed = ui.compare_top_bar_collapsed
        mine.preview_info = replace(
            ui.preview_info,
            summary_items=list(ui.preview_info.summary_items),
            detail_items=list(ui.preview_info.detail_items),
        )

    def _on_criteria_panel_changed(
        self, thresholds: ThresholdsConfig, savings: SavingsConfig
    ) -> None:
        self._cfg = merge_tuner_criteria(self._cfg, thresholds, savings)
        self._app_state.apply_classifier_config(self._cfg)
        self.criteria_changed.emit(thresholds, savings)
        self._auto_save()

    def sync_criteria(
        self,
        thresholds: ThresholdsConfig,
        savings: SavingsConfig,
        *,
        sync_tuner: bool = True,
    ) -> None:
        """외부 또는 튜너 탭에서 변경된 임계값, 효율 기준을 내부 상태 및 튜너 UI에 동기화"""
        self._cfg = merge_tuner_criteria(self._cfg, thresholds, savings)
        self._app_state.apply_classifier_config(self._cfg)
        if sync_tuner:
            self.criteria_panel.sync_from_config(
                self._cfg.thresholds, self._cfg.savings
            )

    def sync_config(self, cfg: ClassifierConfig) -> None:
        """되돌리기, 다시 실행으로 바뀐 환경 설정 값을 열린 입력 위젯에 반영"""
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._cfg = cfg.copy()
            self._load_values()
        finally:
            self._is_loading = prev_loading


    def sync_preferences(self, shortcuts: Mapping[str, str], show_hints: bool) -> None:
        """되돌리기, 다시 실행으로 바뀐 단축키, 테마, 쉬운 설명 표시 여부를 열린 입력 위젯에 반영"""
        prev_loading = self._is_loading
        self._is_loading = True
        try:
            self._app_state.ui.shortcuts = dict(shortcuts)
            self.shortcuts_panel.set_overrides(shortcuts)
            self.chk_show_hints.setChecked(show_hints)
            idx = self.combo_theme.findData(ThemeManager().mode)
            if idx >= 0:
                self.combo_theme.setCurrentIndex(idx)
        finally:
            self._is_loading = prev_loading

    def reload_state(
        self,
        current_config: ClassifierConfig,
        current_root_dir: str,
        app_state: AppState | None,
        *,
        active_tab: int | None,
        busy: bool,
    ) -> None:
        """숨겨 둔 대화상자를 다시 열 때 호출자의 현재 설정, 앱 상태, 작업 폴더로 내부 사본과 입력값을 갱신

        - 생성 시점의 앱 상태 사본을 계속 쓰면 자동 저장이 최신 값을 옛 값으로 덮어쓰므로 항상 교체
        """
        self._app_state = app_state.copy() if app_state is not None else load_app_state()
        self.sync_config(current_config)
        self._app_state.apply_classifier_config(self._cfg)
        self.sync_criteria(self._cfg.thresholds, self._cfg.savings)
        self.sync_root_dir(current_root_dir or resolve_effective_root_dir())
        self.sync_preferences(
            self._app_state.ui.shortcuts, bool(self._app_state.ui.show_setting_hints)
        )
        self.set_busy(busy)
        self.set_active_tab(
            active_tab if active_tab is not None else self._app_state.ui.layout.settings_tab
        )

    def _on_reset_defaults_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "전체 초기화",
            "모든 분석 설정, 필터, 예외 규칙, 재인코딩 설정 및 화면 테마를 기본값으로 복원할까요?\n"
            "(현재 선택된 작업 폴더 경로는 유지됩니다.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # - 테마가 아래에서 먼저 바뀌므로 호출자가 초기화 전 상태를 캡처할 수 있게 변경 전에 알림
        self.settings_reset_confirmed.emit()
        fresh = reset_app_state(
            preserve_root_dir=True,
            preserve_recent_dirs=True,
        )
        self._is_loading = True
        try:
            if ThemeManager().mode != fresh.ui.theme_mode:
                ThemeManager().set_mode(fresh.ui.theme_mode)
            self._cfg = fresh.to_classifier_config()
            self._app_state = fresh.copy()
            self._initial_root_dir = resolve_effective_root_dir() or self._last_saved_root_dir
            self._last_saved_root_dir = self._initial_root_dir
            self._load_values()
        finally:
            self._is_loading = False
        self.settings_reset.emit(fresh)

    def _auto_save(self) -> None:
        if self._is_loading:
            return

        new_config = apply_setting_values(self._cfg, self._form_rows)
        new_config = replace(new_config, execution=self.execution_panel.read())
        new_config = replace(
            new_config,
            encode=replace(new_config.encode, **self.execution_panel.encode_values()),
        )
        new_config = merge_tuner_criteria(
            new_config,
            self.criteria_panel.current_thresholds(),
            self.criteria_panel.current_savings(),
        )

        root_dir_val = normalize_root_spec(self.edit_root_dir.text())
        root_dir_changed = root_dir_val != self._last_saved_root_dir
        if root_dir_changed:
            self._app_state.paths.recent_dirs = record_recent_roots(
                self._app_state.paths.recent_dirs, root_dir_val
            )

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        self._cfg = new_config
        self.criteria_panel.set_config(new_config)
        self.execution_panel.set_baseline_cq(new_config.encode.baseline_cq)
        self._app_state.apply_classifier_config(new_config)
        self._app_state.ui.theme_mode = ThemeManager().mode
        self._persist(root_dir=root_dir_val if root_dir_changed else None)
        if root_dir_changed:
            self._last_saved_root_dir = root_dir_val

        self.settings_applied.emit(new_config, root_dir_val)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        """창 폭이 좁아지면 아이콘 전용 레일로 자동 축소"""
        self.sidebar.set_auto_compact(self.width() < AUTO_RAIL_WIDTH)
        super().resizeEvent(event)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 다이얼로그 스타일시트 및 아이콘 색상을 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        if hasattr(self, "combo_theme"):
            self.combo_theme.setItemIcon(
                0, get_icon("monitor", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                1, get_icon("moon", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                2, get_icon("sun", palette=palette, size=14, pad_right=6)
            )
        if hasattr(self, "btn_browse_root"):
            self.btn_browse_root.setIcon(get_icon("folder", palette=palette, size=14))
        if hasattr(self, "btn_reset_defaults"):
            apply_inline_reset_button_icon(self.btn_reset_defaults, palette)
        if hasattr(self, "btn_pin"):
            self.btn_pin.setIcon(
                get_icon("pin" if self._pinned else "pin-off", palette=palette, size=16)
            )
        if hasattr(self, "btn_help"):
            self.btn_help.setIcon(get_icon("help-circle", palette=palette, size=16))


    def _apply_search(self, text: str) -> None:
        """검색어로 모든 범주의 설정과 단축키를 실시간 필터링"""
        if self._is_loading:
            return
        self._search_controller.apply(text)

    def _reset_search_state(self) -> None:
        """검색을 비우면 0건 숨김, 배지, 전체 결과를 걷어내고 원래 범주와 접힘 상태로 복귀"""
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self._search_controller.reset()

    def _build_search_controller(self) -> SettingsSearchController:
        """검색 반영 조정자를 현재 위젯 상태로 만듦"""
        return SettingsSearchController(
            SearchContext(
                sidebar=self.sidebar,
                results_view=self.results_view,
                shortcuts_panel=self.shortcuts_panel,
                entries=self._entries,
                category_labels={category.index: category.label for category in NAV_CATEGORIES},
                rows=self._rows,
                row_cards=self._row_cards,
                advanced_sections=self._advanced_sections,
                section_rows=self._section_rows,
                category_tabs=self.CATEGORY_TABS,
                results_index=self.TAB_RESULTS,
            ),
            current_page=lambda: self.tab_widget.currentIndex(),
            last_category=lambda: self._last_category,
            set_active_tab=self.set_active_tab,
        )

    def _update_forced_hidden(self) -> None:
        """결과 파일 처리로 숨긴 행을 검색 조정자에 알려 검색 결과에서도 숨김"""
        if not hasattr(self, "_search_controller"):
            return
        self._search_controller.update_forced_hidden(self.execution_panel.hidden_row_keys)

    def _jump_to_row(self, page: int, key: str) -> None:
        """전체 결과에서 설정 항목을 누르면 해당 범주로 이동하고 위치를 보여 줌"""
        self.set_active_tab(page)
        row = self._rows.get(key)
        scroll = self._page_scrolls.get(page)
        if row is not None and scroll is not None:
            scroll.ensureWidgetVisible(row)

    def _jump_to_shortcut(self, key: str) -> None:
        """전체 결과에서 단축키 명령을 누르면 상단 검색어는 그대로 두고 그 명령 행으로 이동"""
        self.set_active_tab(self.TAB_SHORTCUTS)
        self.shortcuts_panel.reveal_row(key.removeprefix("shortcut:"))

