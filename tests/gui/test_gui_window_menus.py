"""MainWindow 메뉴, 테마, 독립 창과 대화상자 연동 검증"""

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.classifier import VideoClassifier
from src.core.config import DEFAULT_THRESHOLDS
from src.core.settings.app_state import load_app_state, save_app_state
from src.gui.help.help_dialog import HelpDialog
from src.gui.main_window import MainWindow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.column_layout_state import COL_INDEX_TO_ID, decode_column_layouts
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._gui_helpers import assert_independent_window, isolate_app_state
from tests._gui_window_base import GuiWindowTestCase

_APP = QApplication.instance() or QApplication([])


class TestGUIWindowMenus(GuiWindowTestCase):
    """MainWindow 메뉴, 테마, 독립 창과 대화상자 연동 검증"""

    def test_main_window_theme_switch_integration(self) -> None:
        """ThemeManager 모드 변경 시 MainWindow의 스타일시트가 실시간 동적 갱신되는지 검증"""
        win = MainWindow(initial_dir="C:/dummy")
        self.addCleanup(win.close)
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertIn("#F5F6F8", win.styleSheet())

        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertIn("#121212", win.styleSheet())

    def test_menu_icons_follow_live_theme_switch(self) -> None:
        """테마를 실시간 전환하면 메뉴 아이콘 색이 새 팔레트의 본문 글자색으로 다시 칠해지는지 검증"""
        win = MainWindow(initial_dir="C:/dummy")
        self.addCleanup(win.close)
        mgr = ThemeManager()
        self.addCleanup(mgr.set_mode, mgr.mode)

        def icon_color() -> str:
            image = win.menu.act_play_selected.icon().pixmap(14, 14).toImage()
            colors = [
                image.pixelColor(x, y)
                for x in range(image.width())
                for y in range(image.height())
                if image.pixelColor(x, y).alpha() > 200
            ]
            self.assertTrue(colors)
            return colors[0].name().upper()

        for mode in (ThemeManager.MODE_LIGHT, ThemeManager.MODE_DARK):
            mgr.set_mode(mode)
            text_color = mgr.current_palette.text_primary.upper()
            channels = [int(text_color[i : i + 2], 16) for i in (1, 3, 5)]
            icon = icon_color()
            icon_channels = [int(icon[i : i + 2], 16) for i in (1, 3, 5)]
            self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(channels, icon_channels, strict=True)))

    def test_warning_logs_reach_history_and_log_dialog(self) -> None:
        """src 로거 경고가 로그 이력과 로그 창에 표시됨을 검증"""
        mw, _ = self._loaded_window(with_report=False)
        logging.getLogger("src").warning("테스트 경고 메시지")
        _APP.processEvents()
        self.assertTrue(any("테스트 경고 메시지" in line for line in mw.dialogs.log_history))
        mw.show_log_dialog()
        dialog = mw.dialogs.log_dialog
        assert dialog is not None
        self.assertIn("테스트 경고 메시지", dialog.text_edit.toPlainText())
        dialog.close()

    def test_reset_defaults_from_settings_menu_syncs_main_window(self) -> None:
        """메뉴의 환경 설정에서 전체 복원하면 설정 창, 저장 파일, 메인 창의 예외, 검색어, 스캔 모드가 함께 초기화됨을 검증"""
        self._persist_ui_changes()
        mw = MainWindow()
        self.addCleanup(mw.close)
        mw.open_tuner()
        dialog = mw.dialogs.settings_dialog
        assert dialog is not None
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA)
        mw.menu.act_open_settings.trigger()
        self.assertIs(mw.dialogs.settings_dialog, dialog)
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_ENVIRONMENT)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.btn_reset_defaults.click()
        default_ratio = DEFAULT_THRESHOLDS.over_bitrate_ratio
        self.assertAlmostEqual(dialog.criteria_panel.spin_over_ratio.value(), default_ratio)
        self.assertAlmostEqual(load_app_state().analysis.thresholds.over_bitrate_ratio, default_ratio)
        self.assertEqual(mw.filter_bar.get_exception_tokens(), [])
        self.assertEqual(mw.filter_bar.search_edit.text(), "")
        self.assertEqual(mw.header_bar.current_analyze_mode, "fast")
        dialog.close()

    def test_menu_bar_structure_and_initial_state(self) -> None:
        """최상위 메뉴 구성, 선택 행 액션 초기 비활성, 모든 액션 아이콘 존재를 검증"""
        mw, _, _ = self._menu_window()
        menu_bar = mw.menuBar()
        assert menu_bar is not None
        self.assertEqual(
            [a.text() for a in menu_bar.actions()],
            ["파일(&F)", "편집(&E)", "보기(&V)", "분석(&A)", "재인코딩(&N)", "도움말(&H)"],
        )
        menu = mw.menu
        self.assertFalse(menu.act_copy_paths.isEnabled())
        self.assertFalse(menu.act_play_selected.isEnabled())
        iconless = [
            name
            for name in (
                "act_open_dir", "recent_menu", "act_export_csv", "act_exception_editor", "act_open_settings",
                "theme_menu", "columns_menu", "act_reset_sort", "act_show_logs", "act_analyze_default",
                "act_force_reanalyze", "analyze_mode_menu", "act_play_selected", "act_copy_paths", "act_help",
            )
            if getattr(menu, name).icon().isNull()
        ]
        self.assertEqual(iconless, [])

    def test_recent_menu_returns_to_previous_dir_and_clears(self) -> None:
        """최근 폴더 항목으로 복귀하고 마지막 항목으로 목록을 비움을 검증"""
        mw, dir_a, _ = self._menu_window()
        matches = [a for a in mw.menu.recent_menu.actions() if "dir_a" in a.text()]
        self.assertEqual(len(matches), 1)
        matches[0].trigger()
        self.assertEqual(os.path.normpath(mw.header_bar.path_edit.text()), os.path.normpath(dir_a))

        mw.menu.recent_menu.actions()[-1].trigger()
        self.assertEqual(load_app_state().paths.recent_dirs, [])

    def test_theme_menu_switches_theme_and_checks_action(self) -> None:
        """보기 메뉴의 테마 선택이 ThemeManager 모드와 체크 상태에 반영됨을 검증"""
        mw, _, _ = self._menu_window()
        theme_mgr = ThemeManager()
        self.addCleanup(theme_mgr.set_mode, theme_mgr.mode)
        light = mw.menu._theme_actions[ThemeManager.MODE_LIGHT]
        light.trigger()
        self.assertEqual(theme_mgr.mode, ThemeManager.MODE_LIGHT)
        self.assertTrue(light.isChecked())

    def test_analyze_mode_menu_and_force_reanalyze(self) -> None:
        """기본 스캔 모드 전환이 헤더와 저장 상태에 반영되고, 강제 재스캔이 fast/full 설정을 전달함을 검증"""
        mw, _, _ = self._menu_window()
        mw.menu._analyze_mode_actions["full"].trigger()
        self.assertEqual(mw.header_bar.current_analyze_mode, "full")
        self.assertEqual(load_app_state().ui.analyze_mode, "full")
        with patch.object(mw, "start_analyze") as mock_analyze:
            mw.menu.act_force_reanalyze.trigger()
        mock_analyze.assert_called_once_with(include_keyframe=True, force_reanalyze=True)

        mw.menu._analyze_mode_actions["fast"].trigger()
        self.assertEqual(mw.header_bar.current_analyze_mode, "fast")
        with patch.object(mw, "start_analyze") as mock_analyze:
            mw.menu.act_force_reanalyze.trigger()
        mock_analyze.assert_called_once_with(include_keyframe=False, force_reanalyze=True)

    def test_columns_menu_toggles_visibility_and_reset_sort_clears_indicator(self) -> None:
        """표시할 열 메뉴의 항목 구성과 토글, 기본 정렬 복원의 정렬 표시 해제를 검증"""
        mw, _, _ = self._menu_window()
        mw.menu._populate_columns_menu()
        col_acts = mw.menu.columns_menu.actions()
        self.assertEqual(len(col_acts), mw.table_model.columnCount())
        self.assertTrue(all(act.parent() is mw.menu.columns_menu for act in col_acts))
        self.assertFalse(col_acts[VideoTableModel.COL_NAME].isEnabled())

        opt_act = col_acts[VideoTableModel.COL_OPT_BITRATE]
        self.assertFalse(opt_act.isChecked())
        opt_act.trigger()
        self.assertFalse(mw.table_view.isColumnHidden(VideoTableModel.COL_OPT_BITRATE))

        mw.table_view.sortByColumn(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        self.assertTrue(mw.table_view.filter_header.isSortIndicatorShown())
        mw.menu.act_reset_sort.trigger()
        self.assertFalse(mw.table_view.filter_header.isSortIndicatorShown())

    def test_view_menu_preset_fit_and_compact_stay_in_sync(self) -> None:
        """보기 메뉴의 열 프리셋, 모든 열 크기 맞춤, 좁게 보기가 표와 필터 바, 저장 상태에 함께 반영되는지 검증"""
        mw, _, _ = self._menu_window()
        menu = mw.menu
        self.assertEqual(
            [act.text() for act in menu.preset_menu.actions()], ["기본", "화질, 해상도", "기술 사양", "전체"]
        )
        menu._preset_actions["specs"].trigger()
        self.assertTrue(menu._preset_actions["specs"].isChecked())
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기술 사양")
        self.assertFalse(mw.table_view.isColumnHidden(VideoTableModel.COL_CODEC))
        self.assertTrue(mw.table_view.isColumnHidden(VideoTableModel.COL_ACTION))

        menu.act_fit_all_columns.trigger()
        self.assertGreater(mw.table_view.columnWidth(VideoTableModel.COL_CODEC), 0)

        menu.act_compact_table.trigger()
        self.assertTrue(menu.act_compact_table.isChecked())
        self.assertTrue(mw.filter_bar.is_compact())
        self.assertEqual(mw.table_view.property("compact"), "true")
        self.assertTrue(load_app_state().ui.compact_table)

        menu.act_reset_columns.trigger()
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")
        self.assertTrue(menu._preset_actions["default"].isChecked())

    def test_toggling_a_column_marks_custom_preset_and_unchecks_radios(self) -> None:
        """표시할 열 하나를 토글하면 필터 바가 사용자 지정이 되고 프리셋 라디오가 모두 해제됨을 검증"""
        mw, _, _ = self._menu_window()
        menu = mw.menu
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")
        self.assertTrue(menu._preset_actions["default"].isChecked())

        mw.menu._populate_columns_menu()
        opt_act = mw.menu.columns_menu.actions()[VideoTableModel.COL_OPT_BITRATE]
        opt_act.trigger()

        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 사용자 지정")
        self.assertFalse(any(act.isChecked() for act in menu._preset_actions.values()))

        # - 표 밖에서 숨김이 바뀐 경우(화질 분석 자동 표시 등) 창 수준 동기화로도 라벨이 맞춰짐
        mw.table_view.setColumnHidden(VideoTableModel.COL_OPT_BITRATE, True)
        mw.sync_preset()
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")
        self.assertTrue(menu._preset_actions["default"].isChecked())

    def test_undo_after_preset_switch_resyncs_preset_label_and_radios(self) -> None:
        """프리셋 전환 뒤 되돌리기가 필터 바 라벨과 보기 메뉴 프리셋 체크를 함께 되돌리는지 검증"""
        mw, _, _ = self._menu_window()
        menu = mw.menu
        menu._preset_actions["specs"].trigger()
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기술 사양")
        self.assertTrue(menu._preset_actions["specs"].isChecked())

        mw.shortcuts.trigger_undo()

        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")
        self.assertTrue(menu._preset_actions["default"].isChecked())
        self.assertFalse(menu._preset_actions["specs"].isChecked())

    def test_fit_all_persists_new_column_widths(self) -> None:
        """모든 열 크기 맞춤 직후 저장 열 상태가 새 폭으로 갱신되는지 검증"""
        mw, _, _ = self._menu_window()
        mw.menu.act_fit_all_columns.trigger()
        layouts = decode_column_layouts(mw._app_state.ui.layout.table_header_state)
        assert layouts is not None
        for col, cid in COL_INDEX_TO_ID.items():
            self.assertEqual(layouts[cid].width, mw.table_view.columnWidth(col))

    def test_startup_restores_persisted_compact_table(self) -> None:
        """저장된 좁게 보기 설정을 시작 시 표, 필터 바, 보기 메뉴에 함께 복원하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            isolate_app_state(self, tmp)
            state = load_app_state()
            state.ui.compact_table = True
            save_app_state(state)

            mw = MainWindow()
            self.addCleanup(mw.close)

            self.assertTrue(mw.filter_bar.is_compact())
            self.assertEqual(mw.table_view.property("compact"), "true")
            self.assertTrue(mw.menu.act_compact_table.isChecked())

    def test_exception_editor_opens_independent_window_and_persists_apply(self) -> None:
        """예외 규칙 편집기가 독립 비모달 창으로 열리고, 적용 시 활성/비활성 목록이 분리 저장됨을 검증"""
        mw, _, _ = self._menu_window()
        mw.menu.act_exception_editor.trigger()
        dialog = mw.dialogs.exception_dialog
        assert dialog is not None
        assert_independent_window(self, dialog)

        dialog.sync_tokens(["keep_me"], ["paused_rule"])
        dialog.btn_apply.click()
        self.assertEqual(mw.filter_bar.get_exception_tokens(), ["keep_me"])
        self.assertIn("비활성 1", mw.filter_bar.btn_edit_exceptions.text())
        self.assertEqual(load_app_state().ui.disabled_exception_tokens, ["paused_rule"])

    def test_selection_actions_follow_selected_row_and_busy_state(self) -> None:
        """행 선택 시 경로 복사, 재생 액션이 활성화되고 작업 중에는 스캔이 잠기고 취소가 열림을 검증"""
        mw, dir_a, _ = self._menu_window()
        mw.set_working_directory(dir_a)
        DEFAULT_CACHE_REGISTRY.put(dir_a, TableCache(dir_a, list(self.videos), include_keyframe=True))
        clf = mw.ensure_classifier()
        assert clf is not None
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))

        for vid in self.videos:
            with open(os.path.join(dir_a, vid.filename), "wb") as f:
                f.write(b"v")
        mw.table_view.selectRow(0)
        self.assertTrue(mw.menu.act_copy_paths.isEnabled())
        self.assertTrue(mw.menu.act_play_selected.isEnabled())
        mw.menu.act_copy_paths.trigger()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        self.assertIn(os.path.normpath(dir_a), clipboard.text())

        mw.set_busy_state(True)
        self.assertFalse(mw.menu.act_analyze_default.isEnabled())
        self.assertTrue(mw.menu.act_cancel.isEnabled())
        mw.set_busy_state(False)
        self.assertTrue(mw.menu.act_analyze_default.isEnabled())
        self.assertFalse(mw.menu.act_cancel.isEnabled())

    def test_help_dialog_is_independent_window_and_remembers_last_tab(self) -> None:
        """도움말이 독립 비모달 4탭 창으로 열리고, 탭 이동이 저장되어 다시 열 때 복원됨을 검증"""
        mw, _, _ = self._menu_window()
        self.assertFalse(hasattr(mw.menu, "act_help_guide"))
        self.assertFalse(hasattr(mw.menu, "act_help_concepts"))
        mw.menu.act_help.trigger()
        dialog = mw.dialogs.help_dialog
        assert dialog is not None
        assert_independent_window(self, dialog)
        self.assertFalse(hasattr(dialog, "btn_open_presets"))
        self.assertFalse(hasattr(dialog, "btn_close"))
        self.assertGreaterEqual(dialog.browser_guide.font().pointSize(), 11)
        self.assertEqual(dialog.tab_widget.currentIndex(), HelpDialog.TAB_GUIDE)
        self.assertIn("재인코딩 대상", dialog.browser_guide.toPlainText())

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_CONCEPTS)
        concepts = dialog.browser_concepts.toPlainText()
        for term in (
            "기본 22.0",
            "최적 비트레이트",
            "실제 재인코딩을 실행할 때",
            "분당 절약(MB/분)",
            "코덱 전환",
            "코덱 호환",
            "10-bit",
        ):
            self.assertIn(term, concepts)

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_GLOSSARY)
        self.assertEqual(dialog.tab_widget.tabText(HelpDialog.TAB_GLOSSARY), "용어 사전")
        self.assertIn("분당 절약", dialog.browser_glossary.toPlainText())
        dialog.glossary_search.setText("존재하지않는검색어")
        self.assertIn("검색 결과가 없습니다", dialog.browser_glossary.toPlainText())
        dialog.glossary_search.clear()

        dialog.tab_widget.setCurrentIndex(HelpDialog.TAB_SYNTAX_SHORTCUTS)
        self.assertIn("v.duration", dialog.browser_syntax.toPlainText())
        self.assertEqual(load_app_state().ui.layout.help_tab, HelpDialog.TAB_SYNTAX_SHORTCUTS)

        dialog.close()
        _APP.processEvents()
        mw.menu.act_help.trigger()
        reopened = mw.dialogs.help_dialog
        assert reopened is not None
        self.assertEqual(reopened.tab_widget.currentIndex(), HelpDialog.TAB_SYNTAX_SHORTCUTS)
        reopened.close()

    def test_settings_and_log_dialogs_are_independent_windows(self) -> None:
        """설정, 로그 창이 부모 없는 독립 비모달 창으로 열림을 검증"""
        mw, _, _ = self._menu_window()
        mw.open_settings()
        settings = mw.dialogs.settings_dialog
        assert settings is not None
        log = mw.dialogs.show_log_dialog(export_model=mw.proxy_model, base_dir_provider=lambda: "")
        for dialog in (settings, log):
            assert_independent_window(self, dialog)
            dialog.close()

    def test_column_filters_window_sync_reset_and_dir_switch(self) -> None:
        """열 헤더 동적 필터 적용 시 FilterTuningBar 배지, 카운트, 필터 초기화 및 폴더 전환 연동을 검증"""
        with (
            tempfile.TemporaryDirectory() as dir_a,
            tempfile.TemporaryDirectory() as dir_b,
        ):
            dummy_state = os.path.join(dir_a, "data", "config.json")
            dummy_root = os.path.join(dir_a, ".rootdir.test")
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
            ):
                clf = VideoClassifier(root_dir=dir_a)
                cache = TableCache(dir_a, list(self.videos), include_keyframe=True)
                DEFAULT_CACHE_REGISTRY.put(dir_a, cache)

                mw = MainWindow(classifier=clf)
                self.addCleanup(mw.close)
                mw.show()
                rep = clf.get_summary_report(ensure_keyframe=False)
                mw.apply_report(rep)
                self.assertEqual(mw.proxy_model.rowCount(), 3)

                # 1. 열 필터 적용 -> 배지 표시, 헤더 강조, 행 수 갱신
                mw.proxy_model.set_column_filter(
                    VideoTableModel.COL_CODEC,
                    ColumnFilterRule(selected_values=frozenset({"h264"})),
                )
                self.assertEqual(mw.proxy_model.rowCount(), 1)
                self.assertFalse(mw.filter_bar.btn_clear_col_filters.isHidden())
                self.assertIn("1개", mw.filter_bar.btn_clear_col_filters.text())
                self.assertIn("표시 1개", mw.footer_bar.lbl_summary.text())
                self.assertNotIn("/ 3개", mw.footer_bar.lbl_summary.text())
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(),
                    {VideoTableModel.COL_CODEC},
                )

                # 2. 테마 전환 후 배지, 헤더 유지 검증
                ThemeManager().set_mode(ThemeManager.MODE_DARK)
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(),
                    {VideoTableModel.COL_CODEC},
                )

                # 3. 배지 버튼 클릭 시 열 필터 일괄 해제
                mw.filter_bar.btn_clear_col_filters.click()
                self.assertEqual(mw.proxy_model.rowCount(), 3)
                self.assertTrue(mw.filter_bar.btn_clear_col_filters.isHidden())
                self.assertEqual(
                    mw.table_view.filter_header.active_filter_columns(), set()
                )

                # 4. 필터 재적용 후 작업 폴더 전환 시 열 필터 자동 초기화
                mw.proxy_model.set_column_filter(
                    VideoTableModel.COL_NAME,
                    ColumnFilterRule(text_query="저화질"),
                )
                self.assertEqual(mw.proxy_model.rowCount(), 1)
                mw.set_working_directory(dir_b)
                self.assertEqual(mw.proxy_model.active_column_filter_count(), 0)
                self.assertTrue(mw.filter_bar.btn_clear_col_filters.isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)
