"""도움말 본문의 단축키 괄호 표기와 되돌리기 안내가 유효 단축키 변경에 맞춰 갱신되는지 검증하는 GUI 테스트"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.classifier import VideoClassifier
from src.core.config import SavingsConfig, ThresholdsConfig
from src.core.settings.app_state import load_app_state
from src.core.settings.shortcuts import (
    DEFAULT_SHORTCUTS,
    REDO_SECONDARY_SHORTCUT,
    resolve_shortcuts,
)
from src.gui.help.help_blocks import inline_cmd
from src.gui.help.help_dialog import HelpDialog
from src.gui.main_window import MainWindow
from src.gui.settings.settings_dialog import SettingsDialog
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase, isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestShortcutHelpText(GuiTestCase):
    """도움말은 단축키 목록 없이 단축키 설정 창을 가리키고, 본문 괄호 표기만 유효 단축키를 따름을 검증"""

    def test_guide_has_no_shortcut_table_and_points_to_settings(self) -> None:
        """단축키 표 제목과 열 머리글이 사라지고 단축키 설정 창 안내가 현재 키와 함께 나옴을 검증"""
        from src.gui.help.help_guide_pages import build_syntax_shortcuts_html
        from src.gui.styles.palettes import LIGHT_PALETTE

        html_text = build_syntax_shortcuts_html(LIGHT_PALETTE, DEFAULT_SHORTCUTS)
        self.assertNotIn("단축키 / 조작", html_text)
        self.assertNotIn("키보드 단축키 및 조작 팁", html_text)
        self.assertIn("단축키 설정(Ctrl+K)", html_text)

    def test_default_guide_shows_undo_and_redo_keys_inline(self) -> None:
        """되돌리기 안내 본문에 기본 키가 괄호로 붙고, 단축키 목록에 있던 보조 키 표기는 더 나오지 않음을 검증"""
        dlg = HelpDialog(shortcuts=DEFAULT_SHORTCUTS)
        self.addCleanup(dlg.close)
        text = dlg.browser_guide.toPlainText()
        self.assertIn("되돌리기(Ctrl+Z)", text)
        self.assertIn("다시 실행(Ctrl+Y)", text)
        self.assertIn("되돌리기", text)
        self.assertNotIn(REDO_SECONDARY_SHORTCUT, text)

    def test_inline_command_appends_key_only_when_bound(self) -> None:
        """본문 안내 괄호 표기는 단축키가 있을 때만 붙고, 해제되면 문구만 남음을 검증"""
        self.assertEqual(inline_cmd("판별 기준", {"edit.open_tuner": "Ctrl+T"}, "edit.open_tuner"), "판별 기준(Ctrl+T)")
        self.assertEqual(inline_cmd("판별 기준", {"edit.open_tuner": ""}, "edit.open_tuner"), "판별 기준")


class TestHelpDialogLiveSync(GuiTestCase):
    """열린 사용 가이드 창의 본문이 단축키 변경 즉시 바뀌는지 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def test_help_dialog_text_follows_shortcut_updates(self) -> None:
        """대화상자 본문의 괄호 표기가 키 변경, 해제에 맞춰 갱신됨을 검증"""
        dlg = HelpDialog(shortcuts=DEFAULT_SHORTCUTS)
        self.addCleanup(dlg.close)
        self.assertIn("판별 기준(Ctrl+T)", dlg.browser_guide.toPlainText())

        dlg.update_shortcuts({**DEFAULT_SHORTCUTS, "edit.open_tuner": ""})
        self.assertNotIn("(Ctrl+T)", dlg.browser_guide.toPlainText())
        self.assertIn("판별 기준", dlg.browser_guide.toPlainText())

        dlg.update_shortcuts(resolve_shortcuts({"edit.open_tuner": "Ctrl+Alt+T"}))
        self.assertIn("판별 기준(Ctrl+Alt+T)", dlg.browser_guide.toPlainText())
        self.assertIn("되돌리기(Ctrl+Z)", dlg.browser_guide.toPlainText())

    def test_open_help_window_refreshes_when_controller_changes_a_shortcut(self) -> None:
        """사용 가이드를 연 상태에서 컨트롤러로 단축키를 바꾸면 창의 단축키 표와 메뉴 항목이 함께 갱신됨을 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)

        mw.menu.act_help.trigger()
        dlg = mw.dialogs.help_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        self.assertIn("되돌리기(Ctrl+Z)", dlg.browser_guide.toPlainText())

        mw.shortcuts.apply_shortcuts({"edit.undo": "Ctrl+U"})
        self.assertIn("되돌리기(Ctrl+U)", dlg.browser_guide.toPlainText())
        self.assertNotIn("되돌리기(Ctrl+Z)", dlg.browser_guide.toPlainText())
        self.assertIn("단축키 설정(Ctrl+K)", dlg.browser_syntax.toPlainText())
        self.assertEqual(mw.menu.act_undo.shortcut().toString(), "Ctrl+U")

    def test_help_dialog_opens_the_requested_anchor_of_concepts_tab(self) -> None:
        """앵커를 지정해 열면 판정 원리 탭으로 전환하고 대기 앵커를 기억하는지 검증"""
        dlg = HelpDialog(shortcuts=DEFAULT_SHORTCUTS)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.tab_widget.currentIndex(), HelpDialog.TAB_GUIDE)

        dlg.open_anchor("internal-thresholds")
        _APP.processEvents()

        self.assertEqual(dlg.tab_widget.currentIndex(), HelpDialog.TAB_CONCEPTS)
        self.assertIn("내부 고정 기준값 안내", dlg.browser_concepts.toPlainText())
        self.assertEqual(dlg.current_anchor(), "")

    def test_settings_help_button_opens_concepts_tab(self) -> None:
        """환경 설정 상단 사용 가이드 버튼이 판정 원리 탭의 도움말 창을 여는지 검증"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, [], include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)

        mw.open_settings()
        settings = mw.dialogs.settings_dialog
        assert settings is not None
        settings.btn_help.click()
        _APP.processEvents()

        help_dialog = mw.dialogs.help_dialog
        assert help_dialog is not None
        self.assertEqual(help_dialog.tab_widget.currentIndex(), HelpDialog.TAB_CONCEPTS)


class TestSettingsShortcutTabIntegration(GuiTestCase):
    """설정 대화상자 단축키 탭의 변경이 메뉴, 저장 상태, 전체 초기화, 되돌리기에 연동되는지 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def _build_window(self) -> MainWindow:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        return mw

    def test_shortcut_tab_change_applies_to_menu_and_persists(self) -> None:
        """단축키 탭에서 지정한 키가 메뉴 액션에 즉시 반영되고 상태 파일에 저장됨을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        dlg.shortcuts_panel.set_shortcut_for("view.show_logs", "Ctrl+P")
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), "Ctrl+P")
        self.assertEqual(load_app_state().ui.shortcuts, {"view.show_logs": "Ctrl+P"})

    def test_menu_entry_opens_shortcut_tab(self) -> None:
        """편집 메뉴의 단축키 설정 항목이 설정 대화상자의 단축키 탭을 여는지 검증"""
        mw = self._build_window()
        mw.menu.act_open_shortcuts.trigger()
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        self.assertEqual(dlg.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)

    def test_settings_reset_restores_default_shortcuts(self) -> None:
        """환경 설정의 전체 초기화가 단축키도 기본값으로 되돌리고 저장 상태를 비움을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        dlg.shortcuts_panel.set_shortcut_for("view.show_logs", "Ctrl+P")
        with patch(
            "src.gui.settings.settings_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dlg.btn_reset_defaults.click()
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), DEFAULT_SHORTCUTS["view.show_logs"])
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {})
        self.assertEqual(load_app_state().ui.shortcuts, {})

    def test_show_hints_checkbox_toggles_shortcut_descriptions(self) -> None:
        """설정 창의 쉬운 설명 보기 체크박스가 단축키 탭의 명령 설명 표시도 함께 전환함을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        row = dlg.shortcuts_panel._rows["view.show_logs"]
        dlg.chk_show_hints.setChecked(False)
        self.assertTrue(row.lbl_hint.isHidden())
        dlg.chk_show_hints.setChecked(True)
        self.assertFalse(row.lbl_hint.isHidden())

    def test_default_dialog_width_fits_widest_shortcut_row(self) -> None:
        """기본 크기 설정 대화상자에서 단축키 행의 최소 폭이 탭 너비 안에 들어가 가로로 잘리지 않음을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        dlg.show()
        self.addCleanup(dlg.hide)
        QApplication.processEvents()
        panel = dlg.shortcuts_panel
        widest_row = max(row.minimumSizeHint().width() for row in panel._rows.values())
        self.assertLessEqual(widest_row, panel.width())

    def test_undo_and_redo_update_criteria_widgets_of_open_settings(self) -> None:
        """판별 기준 변경을 되돌리고 다시 실행할 때 열린 설정 창의 슬라이더 값도 함께 바뀜을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_CRITERIA)
        dlg = mw.dialogs.settings_dialog
        self.assertIsNotNone(dlg)
        assert dlg is not None
        default_ratio = dlg.criteria_panel.current_thresholds().over_bitrate_ratio
        mw.table_view.setFocus()

        mw._on_criteria_changed(ThresholdsConfig(over_bitrate_ratio=1.95), SavingsConfig())
        mw.shortcuts.trigger_undo()
        self.assertAlmostEqual(dlg.criteria_panel.current_thresholds().over_bitrate_ratio, default_ratio, places=2)

        mw.shortcuts.trigger_redo()
        self.assertAlmostEqual(dlg.criteria_panel.current_thresholds().over_bitrate_ratio, 1.95, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
