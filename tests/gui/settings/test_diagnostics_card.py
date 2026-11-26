"""일반 설정 진단 카드: 개발자 모드 행, 폴더 열기 아이콘 버튼, 저장 연결 검증"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton

from src.gui.settings.diagnostics_card import (
    CREATE_REPORT_ACCESSIBLE_NAME,
    CREATE_REPORT_TOOLTIP,
    DEVELOPER_MODE_DESC,
    DEVELOPER_MODE_KEY,
    OPEN_FOLDER_ACCESSIBLE_NAME,
    OPEN_FOLDER_TOOLTIP,
    build_diagnostics_card,
)
from src.gui.settings.setting_row import SettingRow
from src.gui.styles.icons import get_icon

_APP = QApplication.instance() or QApplication([])


class DiagnosticsCardTests(unittest.TestCase):
    """진단 카드가 토글 행과 접근성 정보를 갖춘 아이콘 버튼을 만드는지 검증"""

    def setUp(self) -> None:
        self.opened: list[bool] = []
        self.created: list[bool] = []
        self.rows: dict[str, SettingRow] = {}
        self.card = build_diagnostics_card(
            self.rows, lambda: self.opened.append(True), lambda: self.created.append(True)
        )
        self.addCleanup(self.card.deleteLater)

    def test_developer_mode_row_is_a_two_choice_combo_bound_to_the_key(self) -> None:
        """개발자 모드 행이 설정 키와 참, 거짓 선택지로 등록되는지 검증"""
        row = self.rows[DEVELOPER_MODE_KEY]
        self.assertEqual(row.key, DEVELOPER_MODE_KEY)
        combo = row.value_widget
        assert isinstance(combo, QComboBox)
        self.assertEqual(combo.count(), 2)
        self.assertEqual([combo.itemData(0), combo.itemData(1)], [False, True])
        self.assertFalse(row.default_val)

    def test_developer_mode_description_says_it_applies_from_the_next_launch(self) -> None:
        """개발자 모드 설명과 툴팁이 다음 실행부터 적용된다고 알리는지 검증"""
        row = self.rows[DEVELOPER_MODE_KEY]
        self.assertIn(DEVELOPER_MODE_DESC, row.desc)
        self.assertIn("다음 실행부터 적용", row.desc)
        self.assertIn("다시 시작해 주십시오", row.desc)
        self.assertIn(DEVELOPER_MODE_DESC, row.toolTip())

    def test_action_buttons_are_icon_only_with_tooltip_and_accessible_name(self) -> None:
        """두 아이콘 버튼이 텍스트 없이 접근성 이름과 툴팁을 갖고 클릭을 전달하는지 검증"""
        buttons = self.card.findChildren(QPushButton, "IconActionButton")
        self.assertEqual(len(buttons), 2)
        folder_button, report_button = buttons
        self.assertEqual(folder_button.text(), "")
        self.assertEqual(folder_button.toolTip(), OPEN_FOLDER_TOOLTIP)
        self.assertEqual(folder_button.accessibleName(), OPEN_FOLDER_ACCESSIBLE_NAME)
        self.assertEqual(report_button.text(), "")
        self.assertEqual(report_button.toolTip(), CREATE_REPORT_TOOLTIP)
        self.assertEqual(report_button.accessibleName(), CREATE_REPORT_ACCESSIBLE_NAME)
        folder_button.click()
        report_button.click()
        self.assertEqual(self.opened, [True])
        self.assertEqual(self.created, [True])

    def test_action_row_is_not_registered_as_a_setting(self) -> None:
        """동작 행이 저장 대상 설정 행 사전에 들어가지 않는지 검증"""
        self.assertEqual(set(self.rows), {DEVELOPER_MODE_KEY})

    def test_action_buttons_use_theme_palette_icons(self) -> None:
        """폴더와 보고서 버튼이 각각 folder-open과 download 아이콘을 요청하는지 검증"""
        with patch("src.gui.settings.diagnostics_card.get_icon", wraps=get_icon) as icon:
            build_diagnostics_card({}, lambda: None, lambda: None)
        names = [call.args[0] for call in icon.call_args_list]
        self.assertEqual(names, ["folder-open", "download"])


if __name__ == "__main__":
    unittest.main()
