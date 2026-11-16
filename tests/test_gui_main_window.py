"""헤더의 작업 폴더 추가, 하위 폴더 포함 컨트롤과 테이블 경로 열의 글꼴, 구분자 표기를 검증하는 GUI 테스트"""

import os
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.gui.constants import PATH_FONT_FAMILIES
from src.gui.table_column_styles import FONT_PATH
from src.gui.table_model import VideoTableModel
from src.gui.widgets.header_bar import HeaderBar
from tests._factories import make_eval, make_video

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestHeaderRecursiveAndAddDir(unittest.TestCase):
    """헤더의 하위 폴더 포함 토글과 작업 폴더 추가 버튼의 상태, 신호, 툴팁을 검증"""

    def setUp(self) -> None:
        self.header = HeaderBar("")
        self.addCleanup(self.header.deleteLater)

    def test_recursive_button_is_checkable_and_syncs_without_signal(self) -> None:
        """하위 폴더 포함 버튼이 선택 상태를 가지며, 외부 설정 동기화는 신호를 내지 않음을 검증"""
        emitted: list[bool] = []
        self.header.recursive_toggled.connect(emitted.append)

        self.assertTrue(self.header.btn_recursive.isCheckable())
        self.header.set_recursive(True)
        self.assertTrue(self.header.is_recursive)
        self.assertEqual(emitted, [])

        self.header.btn_recursive.click()
        self.assertFalse(self.header.is_recursive)
        self.assertEqual(emitted, [False])

    def test_add_dir_button_emits_and_shows_shortcut_in_tooltip(self) -> None:
        """작업 폴더 추가 버튼이 신호를 내고 툴팁에 사용자 지정 단축키가 표시됨을 검증"""
        clicked: list[bool] = []
        self.header.add_dir_clicked.connect(lambda: clicked.append(True))
        self.header.btn_add_dir.click()
        self.assertEqual(clicked, [True])

        self.header.update_shortcut_tooltips(
            cancel_key="Esc",
            collapse_key="Ctrl+F1",
            add_dir_key="Ctrl+Shift+O",
            recursive_key="",
        )
        self.assertTrue(self.header.btn_add_dir.toolTip().endswith("[Ctrl+Shift+O]"))
        self.assertEqual(self.header.btn_add_dir.accessibleName(), "작업 폴더 추가")


class TestPathColumnFontAndSeparator(unittest.TestCase):
    """파일 이름 열의 경로 전용 글꼴과 '/' 구분자 표기를 검증"""

    def test_name_column_uses_path_font_without_mono_family(self) -> None:
        """파일 이름 열이 경로 글꼴을 쓰고 고정폭 글꼴을 쓰지 않아 원화 기호가 그려지지 않음을 검증"""
        model = VideoTableModel()
        model.set_data_source([make_eval(make_video(filename="시즌1/01.mp4"))], "C:/work")
        index = model.index(0, VideoTableModel.COL_NAME)

        font = model.data(index, int(Qt.ItemDataRole.FontRole))
        self.assertEqual(font.families()[0], PATH_FONT_FAMILIES[0])
        self.assertNotIn("Consolas", font.families())
        self.assertEqual(FONT_PATH, "path")
        self.assertEqual(model.data(index), "시즌1/01.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
