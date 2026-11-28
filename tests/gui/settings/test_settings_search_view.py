"""설정 전체 결과 페이지의 목록 구성, 누름 신호, 빈 상태 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from src.gui.settings.settings_search import SearchEntry
from src.gui.settings.settings_search_view import (
    NO_RESULT_TEXT,
    RESULT_BUTTON_OBJECT_NAME,
    SearchResultsView,
)

_APP = QApplication.instance() or QApplication([])
_LABELS = {0: "일반", 1: "해상도 및 분석", 2: "판별 기준", 3: "인코딩", 4: "단축키"}


def _matches() -> list[SearchEntry]:
    return [
        SearchEntry("analysis.encode.crop_black_bars", 1, "화면 여백 잘라내기", "화면 여백 잘라내기 화질 분석"),
        SearchEntry("analysis.encode.roundtrip_vmaf_threshold", 1, "축소 후보 VMAF 기준", "축소 후보 VMAF 기준"),
    ]


class TestSearchResultsView(unittest.TestCase):
    """전체 결과 목록의 표시와 신호 검증"""

    def setUp(self) -> None:
        self.view = SearchResultsView()
        self.view.show()
        self.addCleanup(self.view.close)

    def _buttons(self) -> list[QPushButton]:
        return self.view.findChildren(QPushButton)

    def _labels(self) -> list[QLabel]:
        return self.view.findChildren(QLabel)

    def test_settings_matches_list_with_category_labels(self) -> None:
        """설정 일치 항목이 범주 이름과 함께 버튼으로 나오는지 검증"""
        self.view.show_results(_matches(), [], 2, _LABELS)

        buttons = self._buttons()
        self.assertEqual(len(buttons), 2)
        self.assertIn("화면 여백 잘라내기", buttons[0].text())
        self.assertIn("해상도 및 분석", buttons[0].text())
        self.assertIn("전체 결과 2개", self._labels()[0].text())
        for button in buttons:
            self.assertEqual(button.objectName(), RESULT_BUTTON_OBJECT_NAME)
            self.assertTrue(button.toolTip())

    def test_shortcut_entries_are_listed_with_group_name(self) -> None:
        """단축키 명령이 기능 그룹과 함께 나오고 누르면 행 키를 신호로 보내는지 검증"""
        emitted: list[str] = []
        self.view.shortcut_activated.connect(emitted.append)
        self.view.show_results([], [("shortcut:view.show_logs", "작업 기록 보기", "화면과 보기")], 1, _LABELS)

        buttons = self._buttons()
        self.assertEqual(len(buttons), 1)
        self.assertIn("작업 기록 보기", buttons[0].text())
        self.assertIn("단축키 / 화면과 보기", buttons[0].text())
        self.assertEqual(buttons[0].property("resultKey"), "shortcut:view.show_logs")
        buttons[0].click()
        self.assertEqual(emitted, ["shortcut:view.show_logs"])

    def test_setting_click_emits_page_and_key(self) -> None:
        """설정 항목을 누르면 범주 인덱스와 행 키를 신호로 보내는지 검증"""
        emitted: list[tuple[int, str]] = []
        self.view.row_activated.connect(lambda page, key: emitted.append((page, key)))
        self.view.show_results(_matches(), [], 2, _LABELS)

        self._buttons()[1].click()
        self.assertEqual(emitted, [(1, "analysis.encode.roundtrip_vmaf_threshold")])

    def test_empty_result_shows_guidance_only(self) -> None:
        """0건이면 안내 문구만 남고 버튼이 없는지 검증"""
        self.view.show_results([], [], 0, _LABELS)
        self.assertEqual(self._buttons(), [])
        labels = self._labels()
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].text(), NO_RESULT_TEXT)

    def test_rerender_replaces_previous_rows(self) -> None:
        """다시 그리면 이전 목록이 남지 않고 새 목록만 보이는지 검증"""
        self.view.show_results(_matches(), [], 2, _LABELS)
        self.view.show_results(_matches()[:1], [], 1, _LABELS)
        self.assertEqual(len(self._buttons()), 1)
        self.assertIn("전체 결과 1개", self._labels()[0].text())

    def test_clear_removes_every_widget(self) -> None:
        """clear가 목록 위젯을 모두 지우는지 검증"""
        self.view.show_results(_matches(), [], 2, _LABELS)
        self.view.clear()
        self.assertEqual(self._buttons(), [])
        self.assertEqual(self._labels(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
