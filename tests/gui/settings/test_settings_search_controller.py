"""설정 통합 검색 조정자의 필터 적용, 복귀, 강제 숨김 연동 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.settings_nav import SettingsSidebar
from src.gui.settings.settings_search import SearchEntry
from src.gui.settings.settings_search_controller import (
    SearchContext,
    SettingsSearchController,
)
from src.gui.settings.settings_search_view import SearchResultsView
from src.gui.settings.shortcuts_panel import ShortcutsPanel

_APP = QApplication.instance() or QApplication([])
_LABELS = {0: "일반", 1: "해상도 및 분석", 2: "판별 기준", 3: "인코딩", 4: "단축키"}


class TestSettingsSearchController(unittest.TestCase):
    """검색 적용과 복귀, 강제 숨김 연동 검증"""

    def setUp(self) -> None:
        self.sidebar = SettingsSidebar()
        self.sidebar.show()
        self.addCleanup(self.sidebar.close)
        self.view = SearchResultsView()
        self.view.show()
        self.addCleanup(self.view.close)
        self.shortcuts = ShortcutsPanel()
        self.addCleanup(self.shortcuts.close)

        self.advanced = CollapsibleSection(title="고급 설정", is_expanded=False)
        self.advanced.show()
        self.addCleanup(self.advanced.close)
        self.card = SettingsGroupCard(icon_name="analyze", title="화질 분석 및 검은 여백")
        self.card.show()
        self.addCleanup(self.card.close)
        self.advanced.add_widget(self.card)

        self.rows = {
            "analysis.encode.crop_black_bars": self._row("검은 여백 잘라내기", "화질 분석 여백"),
            "analysis.encode.roundtrip_vmaf_threshold": self._row("축소 후보 VMAF 기준", "축소 후보 화질 분석"),
        }
        self.entries = [
            SearchEntry("analysis.encode.crop_black_bars", 1, "검은 여백 잘라내기", "검은 여백 잘라내기 화질 분석"),
            SearchEntry(
                "analysis.encode.roundtrip_vmaf_threshold",
                1,
                "축소 후보 VMAF 기준",
                "축소 후보 VMAF 기준 화질 분석",
            ),
        ]
        self.pages: list[int] = [0]
        self.controller = SettingsSearchController(
            SearchContext(
                sidebar=self.sidebar,
                results_view=self.view,
                shortcuts_panel=self.shortcuts,
                entries=self.entries,
                category_labels=_LABELS,
                rows=self.rows,
                row_cards={key: self.card for key in self.rows},
                advanced_sections=[self.advanced],
                section_rows={self.advanced: list(self.rows)},
                category_tabs=(0, 1, 2, 3, 4),
                results_index=5,
            ),
            current_page=lambda: self.pages[0],
            last_category=lambda: 2,
            set_active_tab=self.pages.append,
        )

    def _row(self, title: str, desc: str) -> SettingRow:
        return SettingRow(
            key=title,
            title=title,
            desc=desc,
            editor=QLabel(title),
            default_val=0,
        )

    def test_matching_query_keeps_only_matching_rows(self) -> None:
        """검색어와 맞는 행만 남고 나머지는 숨는지 검증"""
        self.controller.apply("VMAF")
        self.assertFalse(self.rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())
        self.assertTrue(self.rows["analysis.encode.crop_black_bars"].isHidden())
        self.assertEqual(
            self.controller.hidden_keys, {"analysis.encode.crop_black_bars"}
        )
        self.assertFalse(self.sidebar.btn_results.isHidden())

    def test_query_expands_advanced_section_and_reset_collapses_it(self) -> None:
        """접힌 고급 설정이 검색 시 펼쳐지고 비우면 원래 접힘으로 돌아가는지 검증"""
        self.assertFalse(self.advanced.is_expanded())
        self.controller.apply("화질 분석")
        self.assertTrue(self.advanced.is_expanded())
        self.controller.reset()
        self.assertFalse(self.advanced.is_expanded())
        self.assertTrue(self.sidebar.btn_results.isHidden())
        self.assertEqual(self.controller.hidden_keys, frozenset())

    def test_zero_result_moves_to_results_page(self) -> None:
        """현재 범주에 일치 항목이 없으면 전체 결과로 이동하는지 검증"""
        self.pages[0] = 1
        self.controller.apply("없는검색어")
        self.assertEqual(self.pages[-1], 5)
        self.assertTrue(all(self.sidebar.button(i).isHidden() for i in range(5)))

    def test_empty_query_is_treated_as_reset(self) -> None:
        """빈 검색어가 복귀로 처리되고 검색 상태가 남지 않는지 검증"""
        self.controller.apply("  ")
        self.assertFalse(self.controller.is_active)
        self.assertTrue(self.sidebar.btn_results.isHidden())

    def test_forced_hidden_rows_stay_hidden_during_search(self) -> None:
        """결과 파일 처리로 숨긴 행이 검색 중에도 숨김을 유지하는지 검증"""
        self.controller.update_forced_hidden(frozenset({"analysis.encode.crop_black_bars"}))
        self.controller.apply("화질 분석")
        self.assertTrue(self.rows["analysis.encode.crop_black_bars"].isHidden())
        self.assertFalse(self.rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())
        self.assertIn("analysis.encode.crop_black_bars", self.controller.hidden_keys)

        self.controller.update_forced_hidden(frozenset())
        self.controller.reset()
        self.assertFalse(self.rows["analysis.encode.crop_black_bars"].isHidden())


    def test_forced_hidden_rows_are_excluded_from_badges_and_results(self) -> None:
        """결과 파일 처리로 숨긴 행이 검색 배지 수와 전체 결과 목록에서 빠지는지 검증"""
        self.controller.update_forced_hidden(frozenset({"analysis.encode.roundtrip_vmaf_threshold"}))
        self.controller.apply("잘라내기")

        self.assertEqual(self.sidebar.button(1).badge.text(), "1")
        self.assertEqual(self.sidebar.btn_results.badge.text(), "1")
        buttons = self.view.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertIn("검은 여백 잘라내기", buttons[0].text())

    def test_update_forced_hidden_reapplies_the_active_query(self) -> None:
        """검색 중에 강제 숨김 키가 바뀌면 현재 검색어로 배지, 결과, 행 표시를 다시 맞추는지 검증"""
        self.controller.apply("VMAF")
        self.assertEqual(self.sidebar.btn_results.badge.text(), "1")

        self.controller.update_forced_hidden(frozenset({"analysis.encode.roundtrip_vmaf_threshold"}))

        self.assertEqual(self.sidebar.btn_results.badge.text(), "0")
        self.assertEqual(self.view.findChildren(QPushButton), [])
        self.assertTrue(self.rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())
        self.assertTrue(self.controller.is_active)

        self.controller.update_forced_hidden(frozenset())
        self.assertEqual(self.sidebar.btn_results.badge.text(), "1")
        self.assertFalse(self.rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())

    def test_forced_hidden_rows_are_excluded_from_card_and_section_visibility(self) -> None:
        """숨긴 행만 일치하는 카드와 고급 설정 구역은 검색 결과에서도 숨는지 검증"""
        self.controller.update_forced_hidden(frozenset({"analysis.encode.crop_black_bars"}))
        self.controller.apply("잘라내기")

        self.assertTrue(self.card.isHidden())
        self.assertTrue(self.advanced.isHidden())
        self.assertEqual(self.sidebar.btn_results.badge.text(), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
