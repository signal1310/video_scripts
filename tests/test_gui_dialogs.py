"""도움말 단축키 표와 본문 안내가 유효 단축키 변경에 맞춰 실시간 갱신되는지 검증하는 GUI 테스트"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.classifier import VideoClassifier
from src.core.app_state import load_app_state
from src.core.config import SavingsConfig, ThresholdsConfig
from src.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    REDO_SECONDARY_SHORTCUT,
    SHORTCUT_SPEC_BY_ID,
    resolve_shortcuts,
)
from src.gui.dialogs.help_blocks import inline_cmd
from src.gui.dialogs.help_dialog import HelpDialog
from src.gui.dialogs.help_guide_pages import build_shortcut_table_rows
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.main_window import MainWindow
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _row_keys(rows: list[tuple[str, str]]) -> list[str]:
    """도움말 표 행의 키 문자열 목록"""
    return [key for key, _desc in rows]


class TestShortcutHelpRows(unittest.TestCase):
    """도움말 단축키 표 행 구성과 본문 괄호 표기 규칙을 검증"""

    def test_default_rows_show_redo_pair_and_play_double_click(self) -> None:
        """기본 설정에서 다시 실행은 두 키를 함께, 선택 영상 재생은 더블클릭을 포함해 표시함을 검증"""
        keys = _row_keys(build_shortcut_table_rows(DEFAULT_SHORTCUTS))
        self.assertIn(f"Ctrl+Y / {REDO_SECONDARY_SHORTCUT}", keys)
        self.assertIn("Return / 더블클릭", keys)

    def test_overridden_key_replaces_default_and_cleared_command_is_removed(self) -> None:
        """변경한 명령은 새 키로 바뀌고, 해제한 명령은 표에서 사라짐을 검증"""
        effective = resolve_shortcuts({"view.show_logs": "Ctrl+P", "file.exit": ""})
        rows = build_shortcut_table_rows(effective)
        keys = _row_keys(rows)
        self.assertIn("Ctrl+P", keys)
        self.assertNotIn("Ctrl+L", keys)
        self.assertNotIn("Ctrl+Q", keys)
        descriptions = [desc for _key, desc in rows]
        self.assertNotIn(SHORTCUT_SPEC_BY_ID["file.exit"].description, descriptions)

    def test_cleared_play_key_keeps_double_click_row(self) -> None:
        """재생 단축키를 해제해도 더블클릭 재생 행은 남음을 검증"""
        keys = _row_keys(build_shortcut_table_rows(resolve_shortcuts({"classify.play_selected": ""})))
        self.assertIn("더블클릭", keys)

    def test_redo_pair_collapses_when_another_command_takes_secondary_key(self) -> None:
        """보조 다시 실행 키를 다른 명령이 쓰면 다시 실행 행은 기본 키만 표시함을 검증"""
        effective = resolve_shortcuts({"file.open_dir": REDO_SECONDARY_SHORTCUT})
        keys = _row_keys(build_shortcut_table_rows(effective))
        self.assertIn("Ctrl+Y", keys)
        self.assertNotIn(f"Ctrl+Y / {REDO_SECONDARY_SHORTCUT}", keys)

    def test_inline_command_appends_key_only_when_bound(self) -> None:
        """본문 안내 괄호 표기는 단축키가 있을 때만 붙고, 해제되면 문구만 남음을 검증"""
        self.assertEqual(inline_cmd("판별 기준", {"edit.open_tuner": "Ctrl+T"}, "edit.open_tuner"), "판별 기준(Ctrl+T)")
        self.assertEqual(inline_cmd("판별 기준", {"edit.open_tuner": ""}, "edit.open_tuner"), "판별 기준")


class TestHelpDialogLiveSync(unittest.TestCase):
    """열린 사용 가이드 창의 본문과 단축키 표가 단축키 변경 즉시 바뀌는지 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def test_help_dialog_text_follows_shortcut_updates(self) -> None:
        """대화상자 본문의 괄호 표기와 단축키 표가 키 변경, 해제에 맞춰 갱신됨을 검증"""
        dlg = HelpDialog(shortcuts=DEFAULT_SHORTCUTS)
        self.addCleanup(dlg.close)
        self.assertIn("판별 기준(Ctrl+T)", dlg.browser_guide.toPlainText())

        dlg.update_shortcuts({**DEFAULT_SHORTCUTS, "edit.open_tuner": ""})
        self.assertNotIn("(Ctrl+T)", dlg.browser_guide.toPlainText())
        self.assertIn("판별 기준", dlg.browser_guide.toPlainText())

        dlg.update_shortcuts(resolve_shortcuts({"edit.open_tuner": "Ctrl+Alt+T"}))
        self.assertIn("판별 기준(Ctrl+Alt+T)", dlg.browser_guide.toPlainText())
        self.assertIn("Ctrl+Alt+T", dlg.browser_syntax.toPlainText())
        self.assertNotIn("Ctrl+T", dlg.browser_syntax.toPlainText())

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
        self.assertIn("Ctrl+L", dlg.browser_syntax.toPlainText())

        mw.shortcuts.apply_shortcuts({"view.show_logs": "Ctrl+P"})
        self.assertIn("Ctrl+P", dlg.browser_syntax.toPlainText())
        self.assertNotIn("Ctrl+L", dlg.browser_syntax.toPlainText())
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), "Ctrl+P")


class TestSettingsShortcutTabIntegration(unittest.TestCase):
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
            "src.gui.dialogs.settings_dialog.QMessageBox.question",
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
