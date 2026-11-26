"""설정 대화상자: 개발자 모드로 시작한 실행에서만 설정 변경을 기록하는지 검증"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.gui.settings.settings_field_bindings import set_row_value
from tests._gui_settings_base import GuiSettingsTestCase

BITRATE_KEY = "analysis.bitrate_model.base_target_kbps"


class SettingsChangeLoggingTests(GuiSettingsTestCase):
    """자동 저장의 설정 변경 기록이 저장 값이 아니라 이번 실행의 모드로 갈리는지 검증"""

    def test_autosave_logs_only_changed_keys_in_developer_mode(self) -> None:
        """이번 실행이 개발자 모드로 시작했으면 자동 저장이 바뀐 키만 DEBUG로 남기는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        with (
            patch("src.gui.settings.settings_dialog.running_developer_mode", return_value=True),
            self.assertLogs("src.diagnostics", level="DEBUG") as logs,
        ):
            set_row_value(dialog._rows[BITRATE_KEY], 4100)
        joined = "\n".join(logs.output)
        self.assertIn("bitrate_model.base_target_kbps: 3000 -> 4100", joined)
        self.assertNotIn("display.size_unit", joined)

    def test_autosave_is_silent_when_the_run_started_with_developer_mode_off(self) -> None:
        """개발자 모드로 시작하지 않았으면 실행 중에 설정으로 켜도 변경 기록을 남기지 않는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        with (
            patch("src.gui.settings.settings_dialog.running_developer_mode", return_value=False),
            self.assertNoLogs("src.diagnostics", level="DEBUG"),
        ):
            set_row_value(dialog._rows[BITRATE_KEY], 4300)
            set_row_value(dialog._rows["diagnostics.developer_mode"], True)
        self.assertTrue(dialog._cfg.diagnostics.developer_mode)


if __name__ == "__main__":
    unittest.main()
