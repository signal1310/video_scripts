"""헤더의 작업 폴더 추가, 하위 폴더 포함 컨트롤과 테이블 경로 열의 글꼴, 구분자 표기를 검증하는 GUI 테스트"""

import os
import tempfile
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.gui.constants import PATH_EMPTY_TEXT, PATH_FONT_FAMILIES
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.table.table_column_styles import FONT_PATH
from src.gui.table_model import VideoTableModel
from src.gui.widgets.header_bar import HeaderBar
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestHeaderRecursiveAndAddDir(GuiTestCase):
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

    def test_checked_recursive_button_has_style_in_both_themes(self) -> None:
        """선택된 하위 폴더 포함 버튼이 꺼진 상태와 구분되는 QSS 규칙을 다크와 라이트 테마 모두에서 갖는지 검증"""
        self.assertIn("IconActionButton:checked", build_stylesheet(DARK_PALETTE))
        self.assertIn("IconActionButton:checked", build_stylesheet(LIGHT_PALETTE))

    def test_recursive_icon_is_drawable(self) -> None:
        """하위 폴더 포함 버튼의 폴더 트리 아이콘이 비어 있지 않게 생성됨을 검증"""
        self.assertFalse(get_icon("folder-tree", color="#FFFFFF", size=16).isNull())

    def test_action_buttons_are_grouped_in_one_segment(self) -> None:
        """작업 폴더 조작 버튼 3개가 하나의 분할 그룹 안에 있고 하위 폴더 포함 버튼은 그룹 밖에 있음을 검증"""
        header = HeaderBar("")
        self.addCleanup(header.deleteLater)
        group = header.action_group
        for button in (header.btn_browse, header.btn_add_dir, header.btn_open_explorer):
            self.assertIs(button.parentWidget(), group)
        self.assertIsNot(header.btn_recursive.parentWidget(), group)


class TestPathInputFeedback(GuiTestCase):
    """경로 입력의 잘못된 값 유지, 포커스 이탈 시 입력 보존, Esc 취소 동작을 검증"""

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.header = HeaderBar(self.work.name)
        self.addCleanup(self.header.deleteLater)

    def test_invalid_path_keeps_input_and_marks_field(self) -> None:
        """존재하지 않는 경로를 확정하면 입력을 지우지 않고 오류 상태로 표시함을 검증"""
        rejected: list[str] = []
        self.header.path_rejected.connect(rejected.append)
        self.header.begin_path_edit()
        bad = os.path.join(self.work.name, "missing_dir")
        self.header.path_edit.setText(bad)
        self.header.path_edit.returnPressed.emit()

        self.assertEqual(self.header.path_edit.text(), bad)
        self.assertEqual(self.header.path_edit.property("invalid"), "true")
        self.assertIs(self.header.path_stack.currentWidget(), self.header.path_edit)
        self.assertEqual(len(rejected), 1)

    def test_editing_clears_invalid_mark(self) -> None:
        """오류 상태에서 입력을 고치면 오류 표시가 사라짐을 검증"""
        self.header.begin_path_edit()
        self.header.path_edit.setText(os.path.join(self.work.name, "missing_dir"))
        self.header.path_edit.returnPressed.emit()
        self.header.path_edit.textEdited.emit("x")
        self.assertEqual(self.header.path_edit.property("invalid"), "false")

    def test_focus_loss_keeps_changed_draft(self) -> None:
        """포커스가 빠져도 바뀐 입력은 편집 상태로 남아 있음을 검증"""
        self.header.begin_path_edit()
        draft = os.path.join(self.work.name, "draft")
        self.header.path_edit.setText(draft)
        self.header.path_edit.focus_lost.emit()

        self.assertIs(self.header.path_stack.currentWidget(), self.header.path_edit)
        self.assertEqual(self.header.path_edit.text(), draft)

    def test_focus_loss_with_unchanged_text_shows_folder_name(self) -> None:
        """입력이 확정 경로와 같으면 포커스 이탈 시 폴더 이름 표시로 복귀함을 검증"""
        self.header.begin_path_edit()
        self.header.path_edit.focus_lost.emit()
        self.assertIs(self.header.path_stack.currentWidget(), self.header.folder_name_btn)

    def test_empty_placeholder_and_key_hint_are_shown(self) -> None:
        """입력창이 빈 상태 안내와 Enter, Esc 사용법을 보여 주고, 오류 안내가 있으면 그 문구가 우선함을 검증"""
        self.assertEqual(self.header.path_edit.placeholderText(), PATH_EMPTY_TEXT)
        self.assertIn("Enter", self.header.path_edit.toolTip())
        self.assertIn("Esc", self.header.path_edit.toolTip())

        self.header.begin_path_edit()
        self.header.path_edit.setText(os.path.join(self.work.name, "missing_dir"))
        self.header.path_edit.returnPressed.emit()
        self.assertNotIn("Enter", self.header.path_edit.toolTip())

    def test_escape_restores_committed_path(self) -> None:
        """Esc 취소 시 확정 경로로 되돌리고 폴더 이름 표시로 복귀함을 검증"""
        self.header.begin_path_edit()
        self.header.path_edit.setText("draft")
        self.header.path_edit.edit_cancelled.emit()

        self.assertEqual(self.header.path_edit.text(), self.work.name)
        self.assertIs(self.header.path_stack.currentWidget(), self.header.folder_name_btn)


class TestPathColumnFontAndSeparator(unittest.TestCase):
    """파일 이름 열의 경로 전용 글꼴과 운영체제 구분자 표기를 검증"""

    def test_name_column_uses_path_font_without_mono_family(self) -> None:
        """파일 이름 열이 경로 글꼴을 쓰고 고정폭 글꼴을 쓰지 않아 원화 기호가 그려지지 않음을 검증"""
        model = VideoTableModel()
        model.set_data_source([make_eval(make_video(filename="시즌1/01.mp4"))], "C:/work")
        index = model.index(0, VideoTableModel.COL_NAME)

        font = model.data(index, int(Qt.ItemDataRole.FontRole))
        self.assertEqual(font.families()[0], PATH_FONT_FAMILIES[0])
        self.assertNotIn("Consolas", font.families())
        self.assertEqual(FONT_PATH, "path")
        self.assertEqual(model.data(index), os.path.join("시즌1", "01.mp4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
