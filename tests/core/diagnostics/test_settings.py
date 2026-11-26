"""개발자 모드 해석과 설정 변경 수집: 환경 변수, 저장 값, 변경 문구 검증"""

import logging
import os
import unittest
from unittest.mock import patch

from src.core.config import ClassifierConfig
from src.core.diagnostics.settings import (
    DEV_MODE_ENV,
    collect_setting_changes,
    env_flag_enabled,
    log_setting_changes,
    resolve_developer_mode,
)


class DeveloperModeResolutionTests(unittest.TestCase):
    """저장 값과 환경 변수에서 개발자 모드를 정하는 규칙 검증"""

    def test_configured_value_wins_when_env_is_absent(self) -> None:
        """환경 변수가 없으면 저장 값을 그대로 쓰는지 검증"""
        self.assertTrue(resolve_developer_mode(True, {}))
        self.assertFalse(resolve_developer_mode(False, {}))

    def test_env_override_turns_the_mode_on(self) -> None:
        """환경 변수에 켜짐 표기가 있으면 저장 값이 꺼져 있어도 켜지는지 검증"""
        for value in ("1", "true", "TRUE", " yes ", "On"):
            with self.subTest(value=value):
                self.assertTrue(resolve_developer_mode(False, {DEV_MODE_ENV: value}))

    def test_env_override_cannot_turn_the_mode_off(self) -> None:
        """환경 변수가 꺼짐 표기여도 저장 값이 켜져 있으면 켜진 상태인지 검증"""
        self.assertTrue(resolve_developer_mode(True, {DEV_MODE_ENV: "0"}))
        self.assertFalse(resolve_developer_mode(False, {DEV_MODE_ENV: "0"}))

    def test_env_flag_reads_the_process_environment_by_default(self) -> None:
        """환경 사전을 넘기지 않으면 실행 환경 변수를 읽는지 검증"""
        with patch.dict(os.environ, {DEV_MODE_ENV: "1"}, clear=False):
            self.assertTrue(resolve_developer_mode(False))

    def test_env_flag_enabled_handles_missing_and_blank_values(self) -> None:
        """환경 값이 없거나 비어 있으면 꺼진 것으로 보는지 검증"""
        self.assertFalse(env_flag_enabled(None))
        self.assertFalse(env_flag_enabled("   "))
        self.assertTrue(env_flag_enabled("1"))


class SettingChangeTests(unittest.TestCase):
    """설정 변경 수집과 기록 조건 검증"""

    def test_collect_reports_only_changed_flat_keys(self) -> None:
        """바뀐 항목만 점 표기 키와 이전, 이후 값으로 나오는지 검증"""
        before = ClassifierConfig()
        after = before.copy()
        after.diagnostics.developer_mode = True
        after.display.size_unit = "gb"
        self.assertEqual(
            collect_setting_changes(before, after),
            [
                "diagnostics.developer_mode: False -> True",
                "display.size_unit: auto -> gb",
            ],
        )

    def test_collect_returns_nothing_for_identical_configs(self) -> None:
        """같은 설정끼리는 변경 문구를 만들지 않는지 검증"""
        self.assertEqual(collect_setting_changes(ClassifierConfig(), ClassifierConfig()), [])

    def test_log_setting_changes_writes_debug_only_in_developer_mode(self) -> None:
        """개발자 모드에서만 설정 변경을 DEBUG로 남기고 문구를 돌려주는지 검증"""
        before = ClassifierConfig()
        after = before.copy()
        after.diagnostics.developer_mode = True
        logger = logging.getLogger("src.diagnostics.settings.tests")
        with self.assertNoLogs(logger, level="DEBUG"):
            self.assertEqual(
                log_setting_changes(before, after, developer_mode=False, logger=logger), []
            )
        with self.assertLogs(logger, level="DEBUG") as logs:
            changes = log_setting_changes(before, after, developer_mode=True, logger=logger)
        self.assertEqual(changes, ["diagnostics.developer_mode: False -> True"])
        self.assertIn("diagnostics.developer_mode: False -> True", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
