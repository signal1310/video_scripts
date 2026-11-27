"""비정상 종료 안내 막대의 문구, 아이콘 버튼 접근성, 신호 연결 검증"""

import os
import unittest

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

from src.gui.crash_notice_bar import (
    DISMISS_NAME,
    NOTICE_TEXT,
    OPEN_FOLDER_NAME,
    OPEN_FOLDER_TOOLTIP,
    CrashNoticeBar,
    attach_crash_notice,
    attach_pending_crash_notice,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestCrashNoticeBar(unittest.TestCase):
    """안내 문구, 버튼 접근성 이름과 툴팁, 신호 연결 검증"""

    def test_notice_text_and_icon_buttons(self) -> None:
        """안내 문구와 두 아이콘 버튼의 접근성 이름, 툴팁이 기준대로인지 검증"""
        bar = CrashNoticeBar()
        self.addCleanup(bar.deleteLater)
        self.assertIn(NOTICE_TEXT, [child.text() for child in bar.findChildren(type(bar._label))])
        buttons = bar.findChildren(type(bar._open_button))
        names = {button.accessibleName() for button in buttons}
        self.assertEqual(names, {OPEN_FOLDER_NAME, DISMISS_NAME})
        open_button = next(b for b in buttons if b.accessibleName() == OPEN_FOLDER_NAME)
        self.assertEqual(open_button.toolTip(), OPEN_FOLDER_TOOLTIP)
        self.assertEqual(open_button.text(), "")

    def test_notice_text_uses_crash_report_term_only(self) -> None:
        """안내 막대 문구가 비정상 종료 보고서만 가리키고 진단 보고서를 쓰지 않는지 검증"""
        bar = CrashNoticeBar()
        self.addCleanup(bar.deleteLater)
        text = bar._label.text()
        self.assertIn("비정상 종료 보고서", text)
        self.assertNotIn("진단 보고서", text)
        self.assertEqual(OPEN_FOLDER_NAME, "비정상 종료 보고서 폴더 열기")
        self.assertIn("비정상 종료 보고서", OPEN_FOLDER_TOOLTIP)

    def test_open_button_emits_folder_request(self) -> None:
        """폴더 열기 버튼이 신호를 내보내는지 검증"""
        bar = CrashNoticeBar()
        self.addCleanup(bar.deleteLater)
        received: list[int] = []
        bar.folder_requested.connect(lambda: received.append(1))
        bar.open_button.click()
        self.assertEqual(received, [1])

    def test_dismiss_hides_bar_and_emits(self) -> None:
        """닫기 버튼이 막대를 숨기고 닫힘 신호를 내보내는지 검증"""
        bar = CrashNoticeBar()
        self.addCleanup(bar.deleteLater)
        bar.show()
        received: list[int] = []
        bar.dismissed.connect(lambda: received.append(1))
        bar.dismiss_button.click()
        self.assertFalse(bar.isVisible())
        self.assertEqual(received, [1])

    def test_attach_inserts_bar_at_top(self) -> None:
        """부착 함수가 막대를 레이아웃 맨 앞에 끼우고 열기 콜백을 연결하는지 검증"""
        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QHBoxLayout(container)
        placeholder = QWidget(container)
        layout.addWidget(placeholder)
        received: list[int] = []
        bar = attach_crash_notice(container, layout, lambda: received.append(1))
        item = layout.itemAt(0)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertIs(item.widget(), bar)
        bar.folder_requested.emit()
        self.assertEqual(received, [1])


class TestPendingNotice(unittest.TestCase):
    """이번 시작에 준비된 사고 보고서 유무에 따른 부착 검증"""

    def test_no_pending_report_creates_nothing(self) -> None:
        """준비된 사고 보고서가 없으면 안내 막대를 만들지 않는지 검증"""
        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QHBoxLayout(container)
        opened: list[str] = []
        self.assertIsNone(
            attach_pending_crash_notice(container, layout, None, lambda path: opened.append(path) or True)
        )
        self.assertEqual(layout.count(), 0)
        self.assertEqual(opened, [])

    def test_pending_report_creates_notice_and_opens_folder(self) -> None:
        """사고 보고서가 준비되어 있으면 안내 막대를 붙이고 버튼이 폴더 열기를 요청하는지 검증"""
        from src.core.diagnostics.report import CrashReport

        container = QWidget()
        self.addCleanup(container.deleteLater)
        layout = QHBoxLayout(container)
        opened: list[str] = []

        def _open(path: str) -> bool:
            opened.append(path)
            return True

        report = CrashReport(directory="logs/crash_x", report_path="logs/crash_x/report.txt")
        bar = attach_pending_crash_notice(container, layout, report, _open)
        self.assertIsNotNone(bar)
        assert bar is not None
        self.assertEqual(layout.count(), 1)
        bar.open_button.click()
        self.assertEqual(opened, ["logs/crash_x"])


if __name__ == "__main__":
    unittest.main()
