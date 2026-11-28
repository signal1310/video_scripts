"""전체 QSS 조립, 동적 상태 속성 설정 및 조각 간 objectName 중복 금지를 검증하는 테스트"""

import re
import unittest

from PyQt6.QtWidgets import QApplication, QLineEdit

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_dialogs import build_dialogs_qss
from src.gui.styles.qss_preview import build_preview_qss
from src.gui.styles.qss_table import build_table_qss
from src.gui.styles.qss_widgets import build_widgets_qss
from src.gui.styles.stylesheet import _build_common_qss, build_stylesheet, set_state_property
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class TestStylesheet(GuiTestCase):
    """QSS 조립과 상태 속성, 조각 간 objectName 분리 무결성을 검증"""

    def test_build_stylesheet_dark_and_light_exclude_dead_selectors(self) -> None:
        """다크, 라이트 팔레트에서 QSS가 생성되고 제거된 죽은 선택자가 없음을 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_stylesheet(palette)
            self.assertIn("QMainWindow, QDialog", qss)
            self.assertIn("QPushButton#PrimaryButton", qss)
            self.assertNotIn("AppTitle", qss)
            self.assertNotIn("SuccessButton", qss)

    def test_global_push_button_rule_has_no_focus_outline(self) -> None:
        """다크, 라이트 모두 공통 QPushButton 규칙이 포커스 사각형을 그리지 않도록 outline을 끄는지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_stylesheet(palette)
            block = re.search(r"(?m)^QPushButton \{(.*?)\}", qss, re.S)
            self.assertIsNotNone(block)
            assert block is not None
            self.assertIn("outline: none;", block.group(1))

    def test_set_state_property_updates_boolean_property(self) -> None:
        """set_state_property가 QSS 동적 속성 문자열을 true/false로 설정함을 검증"""
        edit = QLineEdit()
        set_state_property(edit, "invalid", True)
        self.assertEqual(edit.property("invalid"), "true")
        set_state_property(edit, "invalid", False)
        self.assertEqual(edit.property("invalid"), "false")

    def test_filter_chips_are_transparent_until_checked(self) -> None:
        """검토, 구간 필터 칩이 미선택 시 투명 배경과 1px 테두리, 선택 시 색 배경을 쓰는지 검증"""
        for palette in (LIGHT_PALETTE, DARK_PALETTE):
            qss = build_widgets_qss(palette)
            cases = (
                ("ReviewFilterChip", palette.badge_pending[0], palette.badge_pending[1]),
                ("VaryFilterChip", palette.badge_vary[0], palette.badge_vary[2]),
            )
            for object_name, checked_bg, checked_border in cases:
                selector = f"QFrame#FilterBar QPushButton#{object_name}"
                base = self._qss_block(qss, selector)
                self.assertIn("background-color: transparent;", base)
                self.assertIn("border: 1px solid", base)
                checked = self._qss_block(qss, f"{selector}:checked")
                self.assertIn(f"background-color: {checked_bg};", checked)
                self.assertIn(f"border: 1px solid {checked_border};", checked)
                self.assertNotIn("2px", checked)

    @staticmethod
    def _qss_block(qss: str, selector: str) -> str:
        """지정 선택자의 QSS 선언 블록 본문을 반환"""
        match = re.search(re.escape(selector) + r" \{(.*?)\}", qss, re.S)
        assert match is not None
        return match.group(1)

    def test_object_names_are_not_split_across_qss_fragments(self) -> None:
        """동일한 objectName 선택자가 둘 이상의 QSS 조각 모듈에 중복 배치되지 않음을 검증"""
        fragments = {
            "common": _build_common_qss(DARK_PALETTE),
            "widgets": build_widgets_qss(DARK_PALETTE),
            "table": build_table_qss(DARK_PALETTE),
            "preview": build_preview_qss(DARK_PALETTE),
            "dialogs": build_dialogs_qss(DARK_PALETTE),
        }
        seen_in: dict[str, set[str]] = {}
        for name, qss in fragments.items():
            # 16진수 색상(#rrggbb)을 피하기 위해 선택자 구문(타입#이름)만 추출
            for obj_name in re.findall(r"[A-Za-z0-9_]+#([A-Za-z0-9_]+)", qss):
                seen_in.setdefault(obj_name, set()).add(name)
        duplicated = {k: sorted(v) for k, v in seen_in.items() if len(v) > 1}
        self.assertEqual(duplicated, {})


if __name__ == "__main__":
    unittest.main()
