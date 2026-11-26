"""서브창 재사용 정책: 닫은 창은 숨겨 두고 다시 열 때 같은 인스턴스를 최신 상태로 보여 줌을 검증"""

import os
import unittest
from collections.abc import Mapping

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.core.settings.app_state import AppState
from src.gui.controllers import dialog_coordinator
from src.gui.controllers.dialog_coordinator import DialogCoordinator
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

    def test_settings_open_is_noted_as_a_settings_event(self) -> None:
        """환경 설정 열기가 설정 분류로 열기 기록을 남기는지 검증"""
        with mock.patch.object(dialog_coordinator, "note_event") as noted:
            self.coordinator.open_settings(ClassifierConfig(), "", app_state=AppState())
        self.assertEqual(noted.call_args.args, ("설정", "환경 설정 열기"))

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
        self.assertEqual(second.tabs.currentIndex(), 2)

    def test_help_dialog_is_reused_after_close(self) -> None:
        first = self.coordinator.show_help_dialog(ClassifierConfig())
        first.close()
        second = self.coordinator.show_help_dialog(ClassifierConfig())
        self.assertIs(first, second)
        self.assertTrue(second.isVisible())

    def test_registered_shortcut_listener_receives_sync(self) -> None:
        """단축키 변경이 등록한 리스너(미리 보기, 비교 창)로 전달되는지 검증"""
        received: list[Mapping[str, str]] = []
        self.coordinator.register_shortcut_listener(received.append)
        effective = {"preview.toggle_playback": "X"}
        self.coordinator.sync_shortcuts(effective)
        self.assertEqual(received, [effective])

    def test_exception_dialog_reflects_new_tokens_on_reopen(self) -> None:
        first = self.coordinator.open_exception_editor(["old_keep"], [])
        first.close()
        second = self.coordinator.open_exception_editor(["new_a", "new_b"], ["off_c"])
        self.assertIs(first, second)
        active, disabled = second.get_tokens()
        self.assertEqual(sorted(active), ["new_a", "new_b"])
        self.assertEqual(disabled, ["off_c"])

    def test_settings_dialog_receives_diagnostics_callbacks(self) -> None:
        """설정 창이 진단 폴더 열기와 진단 보고서 만들기 콜백을 받는지 검증"""
        dialog = self.coordinator.open_settings(ClassifierConfig(), "", app_state=AppState())
        self.assertIs(dialog._open_diagnostics, dialog_coordinator.open_diagnostics_folder)
        self.assertIs(dialog._create_report, dialog_coordinator.create_diagnostic_report)

    def test_diagnostics_report_result_is_reported_on_the_status_line(self) -> None:
        """진단 보고서 생성 결과가 상태 줄 문구로 전달되는지 검증"""
        messages: list[str] = []
        coordinator = DialogCoordinator(
            parent=self.parent,
            on_status=messages.append,
            on_settings_applied=lambda _cfg, _root: None,
        )
        self.addCleanup(coordinator.close_all)
        coordinator._on_diagnostics_report_created("diagnostic_20260607-080910.zip")
        coordinator._on_diagnostics_report_created("")
        self.assertEqual(
            messages,
            [
                "진단 보고서를 만들었습니다: diagnostic_20260607-080910.zip",
                "진단 보고서를 만들지 못했습니다.",
            ],
        )


if __name__ == "__main__":
    unittest.main()
