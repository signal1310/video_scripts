"""GUI 진입점: 저장 설정에서 읽는 개발자 모드 기본값 검증"""

import unittest
from unittest.mock import patch

from src.core.settings.state_codec import AppState
from src.gui import configured_developer_mode


class ConfiguredDeveloperModeTests(unittest.TestCase):
    """저장된 설정 파일의 개발자 모드 값을 그대로 읽는지 검증"""

    def test_returns_saved_value(self) -> None:
        """저장 설정이 켜져 있으면 켜진 값, 꺼져 있으면 꺼진 값을 돌려주는지 검증"""
        for saved in (True, False):
            with self.subTest(saved=saved):
                state = AppState()
                state.diagnostics.developer_mode = saved
                with patch("src.gui.load_app_state", return_value=state):
                    self.assertIs(configured_developer_mode(), saved)


if __name__ == "__main__":
    unittest.main()
