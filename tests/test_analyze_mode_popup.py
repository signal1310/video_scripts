"""분석 모드 팝업의 비차단 열기, 선택, 닫기 규칙과 분할 버튼 연동을 검증"""

import os
import unittest

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeManager
from src.gui.widgets.header_bar import SplitAnalyzeButton

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _press_at(global_pos: QPoint) -> QMouseEvent:
    point = QPointF(global_pos)
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class AnalyzeModePopupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.split = SplitAnalyzeButton()
        self.split.resize(200, 36)
        self.split.show()
        self.addCleanup(self.split.close)
        self.popup = self.split._mode_popup

    def _open_popup(self) -> None:
        self.split.btn_arrow.click()
        QApplication.processEvents()

    def test_popup_paints_styled_card_background(self) -> None:
        """테마 스타일시트 적용 시 팝업 프레임이 배경과 테두리를 그려 뒤 화면과 구분됨을 검증"""
        self.split.setStyleSheet(build_stylesheet(ThemeManager().current_palette))
        self.addCleanup(self.split.setStyleSheet, "")
        self._open_popup()
        image = self.popup.grab().toImage()
        top_border = image.pixelColor(image.width() // 2, 0)
        self.assertEqual(top_border.alpha(), 255)

    def test_arrow_click_opens_popup_without_blocking(self) -> None:
        """화살표 클릭 시 팝업이 블로킹 없이 열려 호출이 즉시 반환되고 열린 상태가 유지됨을 검증"""
        self._open_popup()
        self.assertTrue(self.popup.isVisible())
        self.assertGreaterEqual(self.popup.width(), self.split.width())

    def test_item_click_selects_mode_closes_and_emits(self) -> None:
        """항목 클릭 시 분석 모드가 바뀌고 팝업이 닫히며 mode_changed가 방출됨을 검증"""
        emitted: list[str] = []
        self.split.mode_changed.connect(emitted.append)
        self._open_popup()
        self.popup.btn_full.click()
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FULL)
        self.assertEqual(emitted, [SplitAnalyzeButton.MODE_FULL])

    def test_same_mode_selection_does_not_emit(self) -> None:
        """이미 선택된 모드를 다시 고르면 모드는 유지되고 변경 시그널은 방출되지 않음을 검증"""
        emitted: list[str] = []
        self.split.mode_changed.connect(emitted.append)
        self._open_popup()
        self.popup.btn_fast.click()
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FAST)
        self.assertEqual(emitted, [])

    def test_escape_closes_popup(self) -> None:
        """Esc 입력 시 팝업이 닫히고 이벤트가 소비됨을 검증"""
        self._open_popup()
        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(self.popup.eventFilter(self.popup, escape))
        self.assertFalse(self.popup.isVisible())

    def test_arrow_press_while_open_closes_without_reopening(self) -> None:
        """열린 상태에서 화살표를 누르면 팝업만 닫히고 같은 클릭으로 다시 열리지 않음을 검증"""
        self._open_popup()
        arrow_center = self.split.btn_arrow.mapToGlobal(
            QPoint(self.split.btn_arrow.width() // 2, self.split.btn_arrow.height() // 2)
        )
        self.assertTrue(self.popup.eventFilter(self.popup, _press_at(arrow_center)))
        self.assertFalse(self.popup.isVisible())

    def test_outside_press_closes_and_passes_through(self) -> None:
        """바깥 클릭은 팝업만 닫고 이벤트는 대상 위젯으로 넘겨 원클릭 동작을 막지 않음을 검증"""
        self._open_popup()
        far_away = QPoint(9000, 9000)
        self.assertFalse(self.popup.eventFilter(self.popup, _press_at(far_away)))
        self.assertFalse(self.popup.isVisible())

    def test_keyboard_activation_of_arrow_closes_open_popup(self) -> None:
        """키보드로 화살표를 다시 활성화하면 열린 팝업을 닫아 토글 동작을 유지함을 검증"""
        self._open_popup()
        self.split.btn_arrow.click()
        QApplication.processEvents()
        self.assertFalse(self.popup.isVisible())


if __name__ == "__main__":
    unittest.main()
