"""make_action과 MenuContext의 액션 생성 테스트"""

import unittest

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.gui.menus.menu_base import MenuContext, make_action


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


if __name__ == "__main__":
    unittest.main()
