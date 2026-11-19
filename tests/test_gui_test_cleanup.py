"""GUI 테스트 기반 클래스의 최상위 위젯 정리 범위 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


class GuiTestCaseCleanupTests(unittest.TestCase):
    """GuiTestCase.run이 새 최상위 위젯만 지우고 setUpClass 위젯은 보존하는지 검증

    - 가짜 케이스는 메서드 안에서 정의해 모듈 탐색으로 직접 실행되지 않게 함
    """

    def test_widget_created_inside_test_is_deleted_after_run(self) -> None:
        """테스트 메서드가 만든 최상위 위젯은 run 종료 뒤 삭제되는지 검증"""
        created: list[QWidget] = []

        class CreatesWidgetProbe(GuiTestCase):
            """테스트 메서드 안에서 최상위 위젯을 만드는 가짜 케이스"""

            def test_creates_top_level_widget(self) -> None:
                """최상위 위젯을 만들고 참조를 기록"""
                created.append(QWidget())

        result = unittest.TestResult()
        CreatesWidgetProbe("test_creates_top_level_widget").run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(created), 1)
        self.assertTrue(sip.isdeleted(created[0]))

    def test_setup_class_widget_survives_across_tests(self) -> None:
        """setUpClass 위젯은 같은 클래스의 모든 테스트 뒤에도 남는지 검증"""
        shared: list[QWidget] = []
        children: list[QWidget] = []

        class SharesSetUpWidgetProbe(GuiTestCase):
            """setUpClass에서 만든 위젯을 여러 테스트가 공유하는 가짜 케이스"""

            @classmethod
            def setUpClass(cls) -> None:
                """클래스 단위 공유 위젯 생성"""
                shared.append(QWidget())

            def test_first_uses_shared_widget(self) -> None:
                """공유 위젯이 살아 있는지 확인"""
                self.assertFalse(sip.isdeleted(shared[0]))

            def test_second_uses_shared_widget(self) -> None:
                """앞 테스트 정리 뒤에도 공유 위젯이 살아 있는지 확인"""
                self.assertFalse(sip.isdeleted(shared[0]))

            def test_attaches_child_window_to_shared_widget(self) -> None:
                """공유 위젯에 독립 창 하위 객체를 붙이고 참조를 기록"""
                children.append(QWidget(shared[0], Qt.WindowType.Window))

        suite = unittest.TestSuite(
            [
                SharesSetUpWidgetProbe("test_first_uses_shared_widget"),
                SharesSetUpWidgetProbe("test_second_uses_shared_widget"),
                SharesSetUpWidgetProbe("test_attaches_child_window_to_shared_widget"),
            ]
        )
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(shared), 1)
        self.assertFalse(sip.isdeleted(shared[0]))
        self.assertFalse(sip.isdeleted(children[0]))
        shared[0].deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
