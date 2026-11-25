"""GUI 테스트 기반 클래스의 최상위 위젯 정리 범위 검증"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtGui import QGuiApplication, QKeyEvent
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QWidget

from tests._gui_helpers import (
    _ORIGINAL_SOURCE,
    GuiTestCase,
    _guarded_set_source,
    _real_set_source,
    _real_source,
    dispose_new_top_level_widgets,
    real_media_playback,
    release_media_sources,
    snapshot_top_level_widgets,
)

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

    def test_media_sources_are_released_before_dispose(self) -> None:
        """정리 대상 위젯의 재생기 소스가 파괴 전에 비워지는지 검증

        - 소스를 쥔 재생기를 파괴하면 Qt FFmpeg 백엔드가 접근 위반으로 죽어, 정리가 소스를 먼저 놓아야 함
        - 가드는 소스를 기록만 하므로 기록이 지워지고 실제 재생기 소스가 비어 있는지로 확인함
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "garbage.mp4")
        with open(path, "wb") as handle:
            handle.write(b"x")
        before = snapshot_top_level_widgets()
        window = QWidget()
        player = QMediaPlayer(window)
        player.setSource(QUrl.fromLocalFile(path))
        self.assertFalse(player.source().isEmpty())

        release_media_sources(window)
        self.assertTrue(player.source().isEmpty())

        dispose_new_top_level_widgets(before)

        self.assertTrue(sip.isdeleted(player))
        tmp.cleanup()

    def test_guarded_source_is_recorded_without_backend_visibility(self) -> None:
        """가드가 소스를 기록만 하고 실제 재생기 백엔드에는 넘기지 않는지 검증"""
        player = QMediaPlayer()
        self.addCleanup(player.deleteLater)
        source = QUrl.fromLocalFile("guarded.mp4")
        player.setSource(source)

        self.assertEqual(player.source(), source)
        self.assertTrue(_ORIGINAL_SOURCE(player).isEmpty())

    def test_guarded_device_source_is_recorded(self) -> None:
        """가드가 setSourceDevice의 소스도 기록하는지 검증"""
        player = QMediaPlayer()
        self.addCleanup(player.deleteLater)
        source = QUrl.fromLocalFile("device.mp4")
        player.setSourceDevice(None, source)

        self.assertEqual(player.source(), source)

    def test_guarded_source_round_trip_and_empty_clear(self) -> None:
        """기록한 소스를 그대로 돌려주고, 빈 소스 지정이 기록을 지우는지 검증"""
        player = QMediaPlayer()
        self.addCleanup(player.deleteLater)
        self.assertTrue(player.source().isEmpty())
        source = QUrl.fromLocalFile("recorded.mp4")
        player.setSource(source)
        self.assertEqual(player.source(), source)
        player.setSource(QUrl())
        self.assertTrue(player.source().isEmpty())

    def test_real_media_playback_restores_and_reinstalls_guard(self) -> None:
        """컨텍스트 관리자 안에서는 원본 구현이, 밖에서는 다시 가드가 걸리는지 검증"""
        player = QMediaPlayer()
        self.addCleanup(player.deleteLater)
        with real_media_playback():
            self.assertIs(QMediaPlayer.setSource, _real_set_source)
            self.assertIs(QMediaPlayer.source, _real_source)
            player.setSource(QUrl.fromLocalFile("real.mp4"))
            self.assertEqual(player.source(), QUrl.fromLocalFile("real.mp4"))
            player.setSource(QUrl())
        self.assertIs(QMediaPlayer.setSource, _guarded_set_source)
        player.setSource(QUrl.fromLocalFile("guarded.mp4"))
        self.assertEqual(player.source(), QUrl.fromLocalFile("guarded.mp4"))
        self.assertTrue(_ORIGINAL_SOURCE(player).isEmpty())

    def test_run_reinstalls_guard_left_restored_by_test(self) -> None:
        """컨텍스트 관리자를 닫지 않은 테스트 뒤 GuiTestCase.run이 가드를 되돌리는지 검증"""

        class LeavesGuardRestoredProbe(GuiTestCase):
            """컨텍스트 관리자를 열기만 하고 닫지 않는 가짜 케이스"""

            def test_leaves_media_guard_restored(self) -> None:
                """가드를 걷은 상태로 테스트를 끝냄"""
                context = real_media_playback()
                context.__enter__()
                self.assertIs(QMediaPlayer.setSource, _real_set_source)

        result = unittest.TestResult()
        LeavesGuardRestoredProbe("test_leaves_media_guard_restored").run(result)
        self.assertEqual(result.errors, [])
        player = QMediaPlayer()
        self.addCleanup(player.deleteLater)
        player.setSource(QUrl.fromLocalFile("after.mp4"))
        self.assertEqual(player.source(), QUrl.fromLocalFile("after.mp4"))
        self.assertTrue(_ORIGINAL_SOURCE(player).isEmpty())

    def test_leftover_modifier_state_fails_test_and_is_reset(self) -> None:
        """수식키 떼기 없이 끝난 테스트가 실패로 기록되고 수식키 상태가 비워지는지 검증"""

        class LeavesModifierProbe(GuiTestCase):
            """수식키 누름만 보내고 떼기를 보내지 않는 가짜 케이스"""

            def test_presses_modifiers_without_release(self) -> None:
                """Ctrl+Shift 누름 이벤트만 보내는 가짜 테스트"""
                holder = QWidget()
                holder.show()
                modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                QApplication.sendEvent(holder, QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_ParenRight), modifiers))

        result = unittest.TestResult()
        LeavesModifierProbe("test_presses_modifiers_without_release").run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("수식키", result.failures[0][1])
        self.assertEqual(QGuiApplication.keyboardModifiers(), Qt.KeyboardModifier.NoModifier)


if __name__ == "__main__":
    unittest.main()
