"""프로그램 목적, 판정 원리(핵심 지표, 수식 해설), 예외 조건식 및 단축키 통합 가이드 대화상자 모듈"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.constants import make_ui_font
from src.gui.help.glossary_view import (
    GLOSSARY_CATEGORY_CHOICES,
    build_glossary_html,
    filter_glossary_terms,
)
from src.gui.help.help_blocks import HELP_BODY_FONT_PT, pad_inline_code, render_styles
from src.gui.help.help_concepts_page import build_concepts_html
from src.gui.help.help_guide_pages import build_guide_html, build_syntax_shortcuts_html
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager

_DIALOG_DEFAULT_WIDTH: int = 780
_DIALOG_DEFAULT_HEIGHT: int = 640
_DIALOG_MIN_WIDTH: int = 560
_DIALOG_MIN_HEIGHT: int = 440
_DIALOG_MARGINS: tuple[int, int, int, int] = (14, 12, 14, 14)
_DIALOG_SPACING: int = 10
_GLOSSARY_INTRO: str = (
    "<p>화면과 가이드에 나오는 낱말을 쉬운 말로 풀이합니다. 위 검색창에 용어나 비슷한 말을 입력하거나 "
    "용어 범주를 골라 범위를 좁혀 보세요. 판별 기준 값은 현재 설정을 반영합니다.</p>"
)

class HelpDialog(QDialog, ThemeAwareMixin):
    """프로그램 목적, 핵심 지표 개념, 예외 조건식 및 단축키를 안내하는 비모달 가이드 대화상자"""

    TAB_GUIDE: int = 0
    TAB_CONCEPTS: int = 1
    TAB_SYNTAX_SHORTCUTS: int = 2
    TAB_GLOSSARY: int = 3

    tab_changed = pyqtSignal(int)

    def __init__(
        self,
        config: ClassifierConfig | None = None,
        *,
        active_tab: int = TAB_GUIDE,
        parent: QWidget | None = None,
        shortcuts: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("사용 가이드")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self._is_loading: bool = True
        self._pending_anchor: str = ""

        self._config: ClassifierConfig = (
            config.copy() if config is not None else ClassifierConfig()
        )
        self._shortcuts: dict[str, str] = dict(
            DEFAULT_SHORTCUTS if shortcuts is None else shortcuts
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(_DIALOG_SPACING)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("SettingsTabs")
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.currentChanged.connect(self._on_tab_widget_changed)
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)

        self.browser_guide = self._create_browser()
        self.browser_concepts = self._create_browser()
        self.browser_syntax = self._create_browser()
        self._build_glossary_page()

        self.tab_widget.addTab(self.browser_guide, "프로그램 안내")
        self.tab_widget.addTab(self.browser_concepts, "판정 원리")
        self.tab_widget.addTab(self.browser_syntax, "고급 활용")
        self.tab_widget.addTab(self.glossary_page, "용어 사전")
        layout.addWidget(self.tab_widget, stretch=1)

        self.set_active_tab(active_tab)
        self.init_theme_listener()
        self._is_loading = False

    def _build_glossary_page(self) -> None:
        self.glossary_page = QWidget(self)
        page_layout = QVBoxLayout(self.glossary_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(_DIALOG_SPACING)
        search_row = QHBoxLayout()
        self.glossary_search = QLineEdit(self.glossary_page)
        self.glossary_search.setPlaceholderText("용어, 뜻, 다른 말로 검색")
        self.glossary_search.setClearButtonEnabled(True)
        self.glossary_category = QComboBox(self.glossary_page)
        for key, label in GLOSSARY_CATEGORY_CHOICES:
            self.glossary_category.addItem(label, key)
        search_row.addWidget(self.glossary_search, stretch=1)
        search_row.addWidget(self.glossary_category)
        page_layout.addLayout(search_row)
        self.browser_glossary = self._create_browser()
        page_layout.addWidget(self.browser_glossary, stretch=1)
        self.glossary_search.textChanged.connect(self._on_glossary_filter_changed)
        self.glossary_category.currentIndexChanged.connect(self._on_glossary_filter_changed)

    @staticmethod
    def _create_browser() -> QTextBrowser:
        browser = QTextBrowser()
        browser.setObjectName("HelpViewer")
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        font = make_ui_font(HELP_BODY_FONT_PT)
        browser.setFont(font)
        doc = browser.document()
        if doc is not None:
            doc.setDefaultFont(font)
        return browser

    @classmethod
    def normalize_tab_index(cls, index: int) -> int:
        """허용된 도움말 탭 범위(0~3)로 인덱스 정규화"""
        if index in (cls.TAB_GUIDE, cls.TAB_CONCEPTS, cls.TAB_SYNTAX_SHORTCUTS, cls.TAB_GLOSSARY):
            return index
        return cls.TAB_GUIDE

    def _on_glossary_filter_changed(self, _index: int | str | None = None) -> None:
        if self._is_loading:
            return
        self._render_glossary(ThemeManager().current_palette)

    def _on_tab_widget_changed(self, index: int) -> None:
        if self._is_loading:
            return
        self.tab_changed.emit(self.normalize_tab_index(index))

    def set_active_tab(self, index: int) -> None:
        """지정된 도움말 탭 인덱스로 전환"""
        prev = self._is_loading
        self._is_loading = True
        try:
            self.tab_widget.setCurrentIndex(self.normalize_tab_index(index))
        finally:
            self._is_loading = prev

    def open_anchor(self, anchor: str) -> None:
        """판정 원리 탭의 지정한 절로 즉시 이동(설정 화면의 수식, 내부 기준값 안내 연동)"""
        self._pending_anchor = anchor
        self._render_contents(ThemeManager().current_palette)
        self.set_active_tab(self.TAB_CONCEPTS)
        if anchor:
            QTimer.singleShot(0, self._scroll_to_pending_anchor)

    def _scroll_to_pending_anchor(self) -> None:
        """대기 중인 앵커 위치로 본문을 스크롤"""
        anchor = self._pending_anchor
        if not anchor:
            return
        self.browser_concepts.scrollToAnchor(anchor)
        self._pending_anchor = ""

    def current_anchor(self) -> str:
        """마지막으로 이동을 요청한 앵커 이름(테스트와 상태 확인용)"""
        return self._pending_anchor

    def update_config(self, config: ClassifierConfig) -> None:
        """변경된 판별 기준 설정을 가이드 본문에 즉시 반영"""
        self._config = config.copy()
        self._render_contents(ThemeManager().current_palette)

    def update_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """단축키 변경을 가이드 본문과 단축키 표에 즉시 반영"""
        self._shortcuts = dict(shortcuts)
        self._render_contents(ThemeManager().current_palette)

    def _render_contents(self, palette: ThemePalette) -> None:
        self.browser_guide.setHtml(
            pad_inline_code(build_guide_html(palette, self._shortcuts))
        )
        self.browser_concepts.setHtml(
            pad_inline_code(build_concepts_html(palette, self._config, self._shortcuts))
        )
        self.browser_syntax.setHtml(
            pad_inline_code(build_syntax_shortcuts_html(palette, self._shortcuts))
        )
        self._render_glossary(palette)

    def _render_glossary(self, palette: ThemePalette) -> None:
        terms = filter_glossary_terms(
            self.glossary_search.text(), str(self.glossary_category.currentData())
        )
        body = build_glossary_html(terms, self._config, palette)
        self.browser_glossary.setHtml(
            pad_inline_code(f"{render_styles(palette)}<h2>용어 사전</h2>{_GLOSSARY_INTRO}{body}")
        )

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 스타일시트 및 HTML 고대비 색상 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        self._render_contents(palette)
