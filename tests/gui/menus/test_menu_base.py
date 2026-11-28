"""make_action과 MenuContext의 액션 생성 테스트"""

import unittest

from PyQt6.QtWidgets import QApplication, QMainWindow, QMenuBar

from src.gui.menus.menu_base import (
    MenuContext,
    enable_menu_bar_tooltips,
    enable_menu_tooltips,
    make_action,
)


class MakeActionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = QMainWindow()

    def tearDown(self) -> None:
        self.window.close()

    def test_triggered_passes_no_argument_to_slot(self) -> None:
        """triggered의 checked 인자를 슬롯에 전달하지 않아 기본값 슬롯이 기본값으로 호출됨"""
        received: list[str] = []

        def slot(tab: str = "기본") -> None:
            received.append(tab)

        act = make_action(self.window, "테스트", slot)
        act.trigger()
        self.assertEqual(received, ["기본"])

    def test_create_action_triggered_passes_no_argument_to_slot(self) -> None:
        """create_action으로 만든 액션도 슬롯에 checked 인자를 전달하지 않음"""
        context = MenuContext(self.window)
        received: list[str] = []

        def slot(tab: str = "기본") -> None:
            received.append(tab)

        act = context.create_action("테스트", slot, action_id="test")
        act.trigger()
        self.assertEqual(received, ["기본"])
        self.assertIs(context.actions["test"], act)

    def test_status_tip_becomes_the_hover_tooltip(self) -> None:
        """create_action이 받은 상태 안내를 툴팁과 상태 표시줄 문구로 함께 설정하는지 검증"""
        context = MenuContext(self.window)
        act = context.create_action(
            "테스트", lambda: None, action_id="test", status_tip="설명입니다."
        )
        self.assertEqual(act.toolTip(), "설명입니다.")
        self.assertEqual(act.statusTip(), "설명입니다.")


class MenuTooltipVisibilityTest(unittest.TestCase):
    """메뉴 툴팁 표시를 하위 메뉴까지 한 번에 켜는지 검증"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.menu_bar = QMenuBar(self.window)
        self.window.setMenuBar(self.menu_bar)

    def test_enable_menu_tooltips_reaches_nested_submenus(self) -> None:
        """최상위 메뉴와 그 아래 하위 메뉴 모두 툴팁 표시가 켜지는지 검증"""
        top = self.menu_bar.addMenu("보기")
        assert top is not None
        sub = top.addMenu("테마")
        assert sub is not None
        inner = sub.addMenu("세부")
        assert inner is not None

        enable_menu_tooltips(top)

        self.assertTrue(top.toolTipsVisible())
        self.assertTrue(sub.toolTipsVisible())
        self.assertTrue(inner.toolTipsVisible())

    def test_enable_menu_bar_tooltips_covers_every_top_level_menu(self) -> None:
        """메뉴바의 모든 최상위 메뉴에 툴팁 표시를 적용하는지 검증"""
        view_menu = self.menu_bar.addMenu("보기")
        assert view_menu is not None
        edit_menu = self.menu_bar.addMenu("편집")
        assert edit_menu is not None
        columns_menu = view_menu.addMenu("표시할 열")
        assert columns_menu is not None
        self.assertFalse(view_menu.toolTipsVisible())

        enable_menu_bar_tooltips(self.menu_bar)

        self.assertTrue(view_menu.toolTipsVisible())
        self.assertTrue(edit_menu.toolTipsVisible())
        self.assertTrue(columns_menu.toolTipsVisible())

    def test_menus_without_a_menu_are_skipped(self) -> None:
        """메뉴가 없는 메뉴바 항목(구분선)을 만나도 그대로 끝나는지 검증"""
        self.menu_bar.addSeparator()
        enable_menu_bar_tooltips(self.menu_bar)
        self.assertEqual(self.menu_bar.actions()[0].text(), "")


if __name__ == "__main__":
    unittest.main()
