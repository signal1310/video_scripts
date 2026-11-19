"""서브창 재사용 정책: 닫은 창은 숨겨 두고 다시 열 때 같은 인스턴스를 최신 상태로 보여 줌을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.app_state import AppState
from src.core.config import ClassifierConfig
from src.gui.dialog_coordinator import DialogCoordinator
from src.gui.dialogs.log_dialog import LogTab
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class DialogReuseTests(GuiTestCase):
    """닫힌 뒤 다시 열면 새로 만들지 않고 같은 창을 재사용하는지 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.close)
        self.coordinator = DialogCoordinator(
            parent=self.parent,
            on_status=lambda _text: None,
            on_settings_applied=lambda _cfg, _root: None,
        )
        self.addCleanup(self.coordinator.close_all)
        self.export_model = QStandardItemModel(self.parent)

    def test_settings_dialog_is_reused_after_close(self) -> None:
        state = AppState()
        first = self.coordinator.open_settings(ClassifierConfig(), "", app_state=state)
        first.close()
        self.assertFalse(first.isVisible())
        second = self.coordinator.open_settings(ClassifierConfig(), "", app_state=state)
        self.assertIs(first, second)
        self.assertTrue(second.isVisible())

    def test_settings_dialog_prewarmed_hidden_then_shown_on_open(self) -> None:
        state = AppState()
        self.coordinator.prewarm_settings_dialog(ClassifierConfig(), "", state)
        prewarmed = self.coordinator.settings_dialog
        if prewarmed is None:
            self.fail("미리 만든 설정창이 없음")
        self.assertFalse(prewarmed.isVisible())
        opened = self.coordinator.open_settings(ClassifierConfig(), "", app_state=state)
        self.assertIs(prewarmed, opened)
        self.assertTrue(opened.isVisible())

    def test_prewarm_does_not_replace_existing_dialog(self) -> None:
        state = AppState()
        first = self.coordinator.open_settings(ClassifierConfig(), "", app_state=state)
        self.coordinator.prewarm_settings_dialog(ClassifierConfig(), "", state)
        self.assertIs(first, self.coordinator.settings_dialog)

    def test_log_dialog_is_reused_after_close(self) -> None:
        first = self.coordinator.show_log_dialog(export_model=self.export_model, base_dir_provider=lambda: "")
        first.close()
        second = self.coordinator.show_log_dialog(
            LogTab.EXPORT, export_model=self.export_model, base_dir_provider=lambda: ""
        )
        self.assertIs(first, second)
        self.assertTrue(second.isVisible())
        self.assertEqual(second.tabs.currentIndex(), 1)

    def test_help_dialog_is_reused_after_close(self) -> None:
        first = self.coordinator.show_help_dialog(ClassifierConfig())
        first.close()
        second = self.coordinator.show_help_dialog(ClassifierConfig())
        self.assertIs(first, second)
        self.assertTrue(second.isVisible())

    def test_exception_dialog_reflects_new_tokens_on_reopen(self) -> None:
        first = self.coordinator.open_exception_editor(["old_keep"], [])
        first.close()
        second = self.coordinator.open_exception_editor(["new_a", "new_b"], ["off_c"])
        self.assertIs(first, second)
        active, disabled = second.get_tokens()
        self.assertEqual(sorted(active), ["new_a", "new_b"])
        self.assertEqual(disabled, ["off_c"])


if __name__ == "__main__":
    unittest.main()
