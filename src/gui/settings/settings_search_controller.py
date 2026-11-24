"""환경 설정 통합 검색의 화면 반영 컨트롤러 모듈

- 검색어를 받아 범주 사이드바 표시, 설정 행과 카드, 접힌 고급 설정, 전체 결과 페이지를 한 번에 맞춤
- 검색을 비우면 숨김, 배지, 전체 결과를 걷어내고 원래 범주와 접힘 상태로 되돌림
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.settings_nav import SettingsSidebar
from src.gui.settings.settings_search import (
    SearchEntry,
    count_matches,
    filter_entries,
    normalize_query,
)
from src.gui.settings.settings_search_view import SearchResultsView
from src.gui.settings.shortcuts_panel import ShortcutsPanel
from src.gui.settings.tuner_slider_row import TunerSliderRow


@dataclass
class SearchContext:
    """검색 반영에 필요한 위젯과 조회 함수 묶음"""

    sidebar: SettingsSidebar
    results_view: SearchResultsView
    shortcuts_panel: ShortcutsPanel
    entries: list[SearchEntry]
    category_labels: dict[int, str]
    rows: Mapping[str, SettingRow | TunerSliderRow] = field(default_factory=dict)
    row_cards: Mapping[str, SettingsGroupCard] = field(default_factory=dict)
    advanced_sections: list[CollapsibleSection] = field(default_factory=list)
    section_rows: dict[CollapsibleSection, list[str]] = field(default_factory=dict)
    category_tabs: tuple[int, ...] = ()
    results_index: int = 0
    forced_hidden: frozenset[str] = frozenset()


class SettingsSearchController:
    """검색어 입력에 따라 설정 화면을 필터링하고 이전 상태로 되돌리는 조정자"""

    def __init__(
        self,
        context: SearchContext,
        *,
        current_page: Callable[[], int],
        last_category: Callable[[], int],
        set_active_tab: Callable[[int], None],
    ) -> None:
        self._ctx = context
        self._current_page = current_page
        self._last_category = last_category
        self._set_active_tab = set_active_tab
        self._hidden: set[str] = set()
        self._saved_advanced: dict[CollapsibleSection, bool] | None = None
        self._last_query: str = ""

    def update_forced_hidden(self, keys: frozenset[str]) -> None:
        """결과 파일 처리 설정으로 항상 숨기는 행 키를 갱신하고, 검색 중이면 같은 검색어로 다시 맞춤

        - 배지, 전체 결과, 카드, 구역 표시를 모두 다시 계산해야 숨긴 행이 목록과 개수에 남지 않음
        """
        self._ctx.forced_hidden = keys
        if self._last_query:
            self.apply(self._last_query)

    @property
    def hidden_keys(self) -> frozenset[str]:
        """검색으로 숨긴 행 키 집합(테스트와 상태 확인용)"""
        return frozenset(self._hidden)

    @property
    def is_active(self) -> bool:
        """검색 필터가 화면에 적용 중인지 여부"""
        return bool(self._last_query)

    def apply(self, text: str) -> None:
        """검색어를 반영하고, 일치 항목이 하나도 없으면 결과 페이지로 이동"""
        query = normalize_query(text)
        if not query:
            self.reset()
            return
        ctx = self._ctx
        self._last_query = query
        if self._saved_advanced is None:
            self._saved_advanced = {
                section: section.is_expanded() for section in ctx.advanced_sections
            }
        forced = set(ctx.forced_hidden)
        # - 결과 파일 처리로 숨긴 행은 배지와 전체 결과에서도 빼야 실제 보이는 항목과 개수가 맞음
        searchable = [entry for entry in ctx.entries if entry.key not in forced]
        matches = filter_entries(searchable, query)
        counts = count_matches(searchable, query)
        matched = {entry.key for entry in matches}
        self._hidden.clear()
        for key, row in ctx.rows.items():
            visible = key in matched
            row.setVisible(visible)
            if not visible:
                self._hidden.add(key)
        self._apply_card_visibility(matches=matched)
        self._apply_section_visibility(matches=matched)
        ctx.shortcuts_panel.apply_search(query)
        shortcut_entries = ctx.shortcuts_panel.shortcut_entries(query)
        if shortcut_entries:
            counts[ctx.category_tabs[-1]] = len(shortcut_entries)
        ctx.sidebar.set_visible_categories(counts)
        ctx.sidebar.set_results_visible(True)
        total = len(matches) + len(shortcut_entries)
        ctx.sidebar.set_badges(counts)
        ctx.sidebar.set_results_badge(total)
        ctx.results_view.show_results(matches, shortcut_entries, total, ctx.category_labels)
        current = self._current_page()
        if current in ctx.category_tabs and counts.get(current, 0) == 0:
            target = next(
                (page for page in ctx.category_tabs if counts.get(page, 0) > 0),
                ctx.results_index,
            )
            self._set_active_tab(target)

    def reset(self) -> None:
        """검색을 비우면 숨김, 배지, 전체 결과를 걷어내고 원래 범주와 접힘 상태로 복귀"""
        ctx = self._ctx
        self._last_query = ""
        forced = set(ctx.forced_hidden)
        for key in self._hidden:
            row = ctx.rows.get(key)
            if row is not None:
                row.setVisible(key not in forced)
        self._hidden.clear()
        for card in set(ctx.row_cards.values()):
            card.setVisible(True)
        if self._saved_advanced is not None:
            for section, expanded in self._saved_advanced.items():
                section.setVisible(True)
                section.set_expanded(expanded)
            self._saved_advanced = None
        else:
            for section in ctx.advanced_sections:
                section.setVisible(True)
        ctx.shortcuts_panel.apply_search("")
        ctx.sidebar.set_badges({})
        ctx.sidebar.set_results_visible(False)
        ctx.sidebar.show_all_categories()
        ctx.results_view.clear()
        if self._current_page() == ctx.results_index:
            self._set_active_tab(self._last_category())

    def _apply_card_visibility(self, *, matches: set[str]) -> None:
        ctx = self._ctx
        keys_by_card: dict[SettingsGroupCard, list[str]] = {}
        for key, card in ctx.row_cards.items():
            keys_by_card.setdefault(card, []).append(key)
        for card, keys in keys_by_card.items():
            card.setVisible(any(k in matches for k in keys))

    def _apply_section_visibility(self, *, matches: set[str]) -> None:
        ctx = self._ctx
        for section in ctx.advanced_sections:
            keys = ctx.section_rows.get(section, [])
            hit = any(k in matches for k in keys)
            section.setVisible(hit)
            if hit and not section.is_expanded():
                section.set_expanded(True)
