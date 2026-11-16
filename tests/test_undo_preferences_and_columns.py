"""단축키 변경, 전체 초기화, 열 너비, 표시, 순서 변경의 되돌리기와 다시 실행을 검증하는 GUI 테스트"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.classifier import VideoClassifier
from src.core.app_state import load_app_state
from src.core.messages import ColumnId
from src.core.shortcuts import DEFAULT_SHORTCUTS, REDO_SECONDARY_SHORTCUT
from src.gui.column_history import (
    COLUMN_ORDER_HISTORY_LABEL,
    COLUMN_RESET_HISTORY_LABEL,
    COLUMN_VISIBILITY_HISTORY_LABEL,
    COLUMN_WIDTH_HISTORY_LABEL,
    describe_column_change,
)
from src.gui.column_layout_state import ColumnLayout, encode_column_layouts
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.main_window import MainWindow
from src.gui.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests._sandbox import reset_sandbox_files

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_SHORTCUT_ID: str = "view.show_logs"
_CUSTOM_KEY: str = "Ctrl+P"
_EDITED_COL: int = VideoTableModel.COL_CODEC
_WIDTH_DELTA: int = 57


def _layout(order: int, width: int, *, hidden: bool = False) -> ColumnLayout:
    return ColumnLayout(order=order, width=width, hidden=hidden)


class TestDescribeColumnChange(unittest.TestCase):
    """열 상태 전후 비교가 변경 종류에 맞는 되돌리기 라벨을 고르는지 검증"""

    def _text(self, **overrides: ColumnLayout) -> str:
        layouts = {ColumnId.NAME: _layout(0, 300), ColumnId.CODEC: _layout(1, 80)}
        layouts.update({ColumnId(key): value for key, value in overrides.items()})
        return encode_column_layouts(layouts)

    def test_label_follows_kind_of_change(self) -> None:
        """폭만 바뀌면 열 너비, 순서가 바뀌면 열 순서, 숨김이 바뀌면 열 표시 라벨임을 검증"""
        base = self._text()
        self.assertEqual(
            describe_column_change(base, self._text(codec=_layout(1, 120))),
            COLUMN_WIDTH_HISTORY_LABEL,
        )
        self.assertEqual(
            describe_column_change(base, self._text(codec=_layout(2, 80))),
            COLUMN_ORDER_HISTORY_LABEL,
        )
        self.assertEqual(
            describe_column_change(base, self._text(codec=_layout(1, 80, hidden=True))),
            COLUMN_VISIBILITY_HISTORY_LABEL,
        )

    def test_hidden_change_wins_over_width_change(self) -> None:
        """숨김과 폭이 함께 바뀌면 열 표시 라벨이 우선함을 검증"""
        base = self._text()
        changed = self._text(codec=_layout(1, 0, hidden=True), name=_layout(0, 400))
        self.assertEqual(describe_column_change(base, changed), COLUMN_VISIBILITY_HISTORY_LABEL)


class _WindowTestCase(unittest.TestCase):
    """분석 캐시가 준비된 메인 창과 격리된 설정 경로를 제공하는 공통 기반"""

    def setUp(self) -> None:
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        original_theme = ThemeManager().mode
        self.addCleanup(ThemeManager().set_mode, original_theme)

    def _build_window(self) -> MainWindow:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        mw.table_view.setFocus()
        return mw


class TestShortcutHistory(_WindowTestCase):
    """단축키 지정과 초기화가 되돌리기 단계로 기록되는지 검증"""

    def _open_shortcut_panel(self, mw: MainWindow) -> SettingsDialog:
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        assert dlg is not None
        mw.table_view.setFocus()
        return dlg

    def test_custom_shortcut_can_be_undone_and_redone(self) -> None:
        """단축키를 지정한 뒤 되돌리면 기본값과 열린 설정 창 표시가 복구되고 다시 실행하면 지정이 돌아옴을 검증"""
        mw = self._build_window()
        dlg = self._open_shortcut_panel(mw)
        dlg.shortcuts_panel.set_shortcut_for(_SHORTCUT_ID, _CUSTOM_KEY)
        self.assertEqual(mw.menu.act_undo.text(), "되돌리기 (단축키 변경)")

        mw.shortcuts.trigger_undo()
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), DEFAULT_SHORTCUTS[_SHORTCUT_ID])
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {})
        self.assertEqual(load_app_state().ui.shortcuts, {})

        mw.shortcuts.trigger_redo()
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), _CUSTOM_KEY)
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {_SHORTCUT_ID: _CUSTOM_KEY})
        self.assertEqual(load_app_state().ui.shortcuts, {_SHORTCUT_ID: _CUSTOM_KEY})

    def test_settings_dialog_has_its_own_undo_and_redo_shortcuts(self) -> None:
        """설정 창이 활성인 동안에도 되돌리기와 다시 실행이 창 자체 단축키로 동작함을 검증"""
        mw = self._build_window()
        dlg = self._open_shortcut_panel(mw)
        undo_keys = [seq.toString() for seq in dlg._undo_action.shortcuts()]
        redo_keys = [seq.toString() for seq in dlg._redo_action.shortcuts()]
        self.assertEqual(undo_keys, [DEFAULT_SHORTCUTS["edit.undo"]])
        self.assertEqual(redo_keys, [DEFAULT_SHORTCUTS["edit.redo"], REDO_SECONDARY_SHORTCUT])

        dlg.shortcuts_panel.set_shortcut_for(_SHORTCUT_ID, _CUSTOM_KEY)
        dlg._undo_action.trigger()
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {})
        dlg._redo_action.trigger()
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {_SHORTCUT_ID: _CUSTOM_KEY})

    def test_settings_dialog_follows_customized_undo_key(self) -> None:
        """되돌리기 단축키를 바꾸면 이미 열린 설정 창의 되돌리기 키도 함께 바뀜을 검증"""
        mw = self._build_window()
        dlg = self._open_shortcut_panel(mw)
        dlg.shortcuts_panel.set_shortcut_for("edit.undo", "Ctrl+U")
        self.assertEqual([seq.toString() for seq in dlg._undo_action.shortcuts()], ["Ctrl+U"])

    def test_capture_button_keeps_undo_key_while_waiting_for_input(self) -> None:
        """키 입력 대기 중에는 설정 창의 되돌리기 단축키가 입력을 가로채지 않고 칸이 키를 받음을 검증"""
        mw = self._build_window()
        dlg = self._open_shortcut_panel(mw)
        button = dlg.shortcuts_panel._rows[_SHORTCUT_ID].capture
        override = QKeyEvent(
            QEvent.Type.ShortcutOverride, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier
        )
        override.ignore()
        button.event(override)
        self.assertFalse(override.isAccepted())

        button.begin_capture()
        self.addCleanup(button.cancel_capture)
        override.ignore()
        button.event(override)
        self.assertTrue(override.isAccepted())

    def test_reset_all_shortcuts_is_one_undoable_step(self) -> None:
        """단축키 모두 초기화를 되돌리면 지정했던 여러 단축키가 한 번에 복구됨을 검증"""
        mw = self._build_window()
        dlg = self._open_shortcut_panel(mw)
        dlg.shortcuts_panel.set_shortcut_for(_SHORTCUT_ID, _CUSTOM_KEY)
        dlg.shortcuts_panel.set_shortcut_for("view.toggle_top_area", "Ctrl+J")
        dlg.shortcuts_panel.reset_all_shortcuts(confirm=False)
        self.assertEqual(mw.menu.act_undo.text(), "되돌리기 (단축키 초기화)")

        mw.shortcuts.trigger_undo()
        self.assertEqual(
            dlg.shortcuts_panel.current_overrides(),
            {_SHORTCUT_ID: _CUSTOM_KEY, "view.toggle_top_area": "Ctrl+J"},
        )
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), _CUSTOM_KEY)


class TestSettingsResetHistory(_WindowTestCase):
    """전체 초기화가 단축키, 테마, 분석 모드, 검색어까지 되돌려지는지 검증"""

    def test_undo_restores_everything_the_reset_cleared(self) -> None:
        """사용자 지정 단축키, 테마, 분석 모드, 검색어가 있는 상태에서 전체 초기화를 되돌리면 모두 복구됨을 검증"""
        mw = self._build_window()
        mw.open_settings(active_tab=SettingsDialog.TAB_SHORTCUTS)
        dlg = mw.dialogs.settings_dialog
        assert dlg is not None
        dlg.shortcuts_panel.set_shortcut_for(_SHORTCUT_ID, _CUSTOM_KEY)
        ThemeManager().set_mode(ThemeManager.MODE_DARK)
        mw.header_bar.split_analyze.set_mode("full", emit_signal=True)
        mw.filter_bar.search_edit.setText("clip")
        mw.table_view.setFocus()

        with patch(
            "src.gui.dialogs.settings_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dlg.btn_reset_defaults.click()
        self.assertEqual(mw.menu.act_undo.text(), "되돌리기 (전체 초기화)")
        self.assertEqual(ThemeManager().mode, ThemeManager.MODE_SYSTEM)
        self.assertEqual(mw.filter_bar.search_edit.text(), "")
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {})

        mw.shortcuts.trigger_undo()
        state = load_app_state()
        self.assertEqual(mw.menu.act_show_logs.shortcut().toString(), _CUSTOM_KEY)
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {_SHORTCUT_ID: _CUSTOM_KEY})
        self.assertEqual(ThemeManager().mode, ThemeManager.MODE_DARK)
        self.assertEqual(dlg.combo_theme.currentData(), ThemeManager.MODE_DARK)
        self.assertEqual(mw.header_bar.current_analyze_mode, "full")
        self.assertEqual(mw.filter_bar.search_edit.text(), "clip")
        self.assertEqual(state.ui.shortcuts, {_SHORTCUT_ID: _CUSTOM_KEY})
        self.assertEqual(state.ui.theme_mode, ThemeManager.MODE_DARK)

        mw.shortcuts.trigger_redo()
        self.assertEqual(ThemeManager().mode, ThemeManager.MODE_SYSTEM)
        self.assertEqual(mw.filter_bar.search_edit.text(), "")
        self.assertEqual(dlg.shortcuts_panel.current_overrides(), {})


class TestColumnHistory(_WindowTestCase):
    """열 너비, 표시, 순서 변경이 한 번의 조작 단위로 되돌려지는지 검증"""

    def _edit_columns(self, mw: MainWindow, action: object) -> None:
        """헤더 마우스 조작과 같은 시작, 끝 신호 사이에서 열 변경을 수행"""
        header = mw.table_view.filter_header
        header.layout_edit_started.emit()
        assert callable(action)
        action()
        header.layout_edit_finished.emit()

    def test_width_change_is_undone_and_redone(self) -> None:
        """헤더 드래그에 해당하는 열 너비 변경이 한 단계로 되돌려지고 다시 실행됨을 검증"""
        mw = self._build_window()
        view = mw.table_view
        original = view.columnWidth(_EDITED_COL)
        self._edit_columns(mw, lambda: view.setColumnWidth(_EDITED_COL, original + _WIDTH_DELTA))
        self.assertEqual(mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_WIDTH_HISTORY_LABEL})")

        mw.shortcuts.trigger_undo()
        self.assertEqual(view.columnWidth(_EDITED_COL), original)
        mw.shortcuts.trigger_redo()
        self.assertEqual(view.columnWidth(_EDITED_COL), original + _WIDTH_DELTA)

    def test_hide_and_move_are_undone(self) -> None:
        """열 숨김과 열 이동이 각각 별도 단계로 기록되어 역순으로 되돌려짐을 검증"""
        mw = self._build_window()
        view = mw.table_view
        header = view.filter_header
        original_order = header.visualIndex(_EDITED_COL)
        self._edit_columns(mw, lambda: view.setColumnHidden(_EDITED_COL, True))
        self.assertEqual(mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_VISIBILITY_HISTORY_LABEL})")
        self._edit_columns(mw, lambda: header.moveSection(header.visualIndex(_EDITED_COL), 0))
        self.assertEqual(mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_ORDER_HISTORY_LABEL})")

        mw.shortcuts.trigger_undo()
        self.assertEqual(header.visualIndex(_EDITED_COL), original_order)
        self.assertTrue(view.isColumnHidden(_EDITED_COL))
        mw.shortcuts.trigger_undo()
        self.assertFalse(view.isColumnHidden(_EDITED_COL))

    def test_menu_command_reset_is_undoable(self) -> None:
        """메뉴의 기본 열 복원도 한 단계로 기록되어 되돌리면 이전 열 구성이 복구됨을 검증"""
        mw = self._build_window()
        view = mw.table_view
        view.setColumnHidden(_EDITED_COL, not view.isColumnHidden(_EDITED_COL))
        edited_hidden = view.isColumnHidden(_EDITED_COL)
        mw.column_history.run_command(COLUMN_RESET_HISTORY_LABEL, view.reset_columns)
        self.assertNotEqual(view.isColumnHidden(_EDITED_COL), edited_hidden)

        mw.shortcuts.trigger_undo()
        self.assertEqual(view.isColumnHidden(_EDITED_COL), edited_hidden)

    def test_click_without_layout_change_records_nothing(self) -> None:
        """열 구성이 바뀌지 않은 헤더 조작(정렬 클릭 등)은 되돌리기 단계를 만들지 않음을 검증"""
        mw = self._build_window()
        self._edit_columns(mw, lambda: None)
        self.assertFalse(mw.menu.act_undo.isEnabled())

    def test_column_undo_keeps_sort_state(self) -> None:
        """열 변경을 되돌려도 표 정렬 상태는 유지됨을 검증"""
        mw = self._build_window()
        view = mw.table_view
        header = view.filter_header
        header.setSortIndicator(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self._edit_columns(mw, lambda: view.setColumnWidth(_EDITED_COL, view.columnWidth(_EDITED_COL) + _WIDTH_DELTA))

        mw.shortcuts.trigger_undo()
        self.assertTrue(header.isSortIndicatorShown())
        self.assertEqual(header.sortIndicatorSection(), VideoTableModel.COL_NAME)
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

    def test_undoing_other_step_does_not_touch_columns(self) -> None:
        """출력 해상도 지정을 되돌릴 때 그 사이 기록되지 않은 열 너비 변화가 덮어써지지 않음을 검증"""
        mw = self._build_window()
        view = mw.table_view
        view.selectAll()
        view.set_manual_target_for(None, "720p")
        widened = view.columnWidth(_EDITED_COL) + _WIDTH_DELTA
        view.setColumnWidth(_EDITED_COL, widened)

        mw.shortcuts.trigger_undo()
        self.assertEqual(view.columnWidth(_EDITED_COL), widened)


if __name__ == "__main__":
    unittest.main(verbosity=2)
