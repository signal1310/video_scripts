"""설정, 도움말, 공용 대화상자 영역 QSS 조각 생성 검증 테스트"""

import unittest

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_dialogs import build_dialogs_qss


class TestQssDialogs(unittest.TestCase):
    """양쪽 팔레트에서 대화상자 전용 QSS와 대표 선택자가 생성되는지 검증"""

    def test_build_dialogs_qss_for_both_palettes(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_dialogs_qss(palette)
            for selector in (
                "QDialog#SettingsDialog",
                "QTabWidget#SettingsTabs",
                "QListWidget#ExceptionRuleList",
                "QTextBrowser#HelpViewer",
                "QFrame#SettingsCard",
                "QFrame#TwistieHeader",
            ):
                self.assertIn(selector, qss)
