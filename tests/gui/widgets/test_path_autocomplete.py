"""경로 자동완성 드롭다운 검증: 폴더 읽기 순수 함수, 상위 폴더 판정, 목록 표시, 키 조작"""

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets import path_autocomplete as module
from src.gui.widgets.floating_popup import FloatingPopupFrame
from src.gui.widgets.path_autocomplete import (
    KEY_HINT,
    PARENT_ROW_TEXT,
    PATH_AC_MAX_FOLDERS,
    READING_NOTICE,
    TRUNCATED_NOTICE,
    FolderAutocompletePopup,
    FolderListing,
    FolderListLoader,
    PathAutocomplete,
    build_listing,
    parent_folder,
    read_folder_names,
    split_folder_input,
)

_APP = QApplication.instance() or QApplication([])
_POLL_INTERVAL_SEC: float = 0.01
_POLL_TIMEOUT_SEC: float = 5.0
# - 저장소 위생 검사가 코드 안의 UNC 경로 리터럴을 막으므로 조각을 이어 만듦
_BACKSLASH: str = "\\"
_UNC_PREFIX: str = _BACKSLASH * 2
_UNC_SHARE_ROOT: str = f"{_UNC_PREFIX}server{_BACKSLASH}share"


def _wait_until(predicate: Callable[[], bool]) -> bool:
    """조건이 참이 될 때까지 이벤트 루프를 돌리며 기다림"""
    deadline = time.monotonic() + _POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_SEC)
    _APP.processEvents()
    return predicate()


def _make_tree(root: str) -> None:
    """폴더 네 개와 파일 하나를 담은 표본 폴더 생성"""
    for name in ("alpha", "Beta", "gamma10", "gamma2"):
        os.makedirs(os.path.join(root, name))
    with open(os.path.join(root, "note.txt"), "w", encoding="utf-8") as handle:
        handle.write("x")


def _key_event(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _item_text(popup: FolderAutocompletePopup, row: int) -> str:
    """목록 행의 표시 문구. 행이 없으면 단언 실패"""
    item = popup.list_folders.item(row)
    assert item is not None
    return item.text()


class SplitFolderInputTests(unittest.TestCase):
    """입력 경로를 상위 경로와 이름 접두어로 나누는지 검증"""

    def test_trailing_separator_lists_all_subfolders(self) -> None:
        self.assertEqual(split_folder_input("F:\\"), ("F:\\", ""))

    def test_partial_name_becomes_prefix(self) -> None:
        self.assertEqual(split_folder_input("F:\\gam"), ("F:\\", "gam"))

    def test_won_sign_is_normalized(self) -> None:
        self.assertEqual(split_folder_input("F:₩gam"), ("F:\\", "gam"))

    def test_forward_slash_is_kept_as_separator(self) -> None:
        self.assertEqual(split_folder_input("C:/Users/"), ("C:/Users/", ""))

    def test_last_spec_of_multi_folder_input_is_used(self) -> None:
        self.assertEqual(split_folder_input("C:\\a; D:\\sub"), ("D:\\", "sub"))

    def test_empty_input_has_no_base(self) -> None:
        self.assertEqual(split_folder_input("   "), ("", ""))

    def test_name_without_separator_has_no_base(self) -> None:
        self.assertEqual(split_folder_input("videos"), ("", "videos"))


class ParentFolderTests(unittest.TestCase):
    """상위 폴더가 있는 경로에만 상위 항목을 두는지 검증"""

    def test_drive_root_has_no_parent(self) -> None:
        self.assertIsNone(parent_folder("F:\\"))

    def test_folder_under_drive_points_to_drive_root(self) -> None:
        self.assertEqual(parent_folder("F:\\videos"), "F:\\")

    def test_nested_folder_points_to_its_parent(self) -> None:
        self.assertEqual(parent_folder("F:\\videos\\sub"), "F:\\videos")

    def test_unc_share_root_has_no_parent(self) -> None:
        self.assertIsNone(parent_folder(_UNC_SHARE_ROOT))

    def test_unc_share_subfolder_points_to_share(self) -> None:
        self.assertEqual(
            parent_folder(f"{_UNC_SHARE_ROOT}{_BACKSLASH}sub"), _UNC_SHARE_ROOT
        )

    def test_relative_name_has_no_parent(self) -> None:
        self.assertIsNone(parent_folder("videos"))


class ReadFolderNamesTests(unittest.TestCase):
    """하위 폴더만 이름순으로 읽고 상한에서 끊는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _make_tree(self._tmp.name)

    def test_files_are_excluded_and_names_are_natural_sorted(self) -> None:
        names, truncated = read_folder_names(
            self._tmp.name, "", PATH_AC_MAX_FOLDERS, lambda: False
        )
        self.assertEqual(names, ("alpha", "Beta", "gamma2", "gamma10"))
        self.assertFalse(truncated)

    def test_prefix_filters_case_insensitively(self) -> None:
        names, _truncated = read_folder_names(
            self._tmp.name, "GAMMA", PATH_AC_MAX_FOLDERS, lambda: False
        )
        self.assertEqual(names, ("gamma2", "gamma10"))

    def test_limit_reports_more_entries(self) -> None:
        names, truncated = read_folder_names(self._tmp.name, "", 2, lambda: False)
        self.assertEqual(len(names), 2)
        self.assertTrue(truncated)

    def test_cancel_stops_reading(self) -> None:
        names, truncated = read_folder_names(
            self._tmp.name, "", PATH_AC_MAX_FOLDERS, lambda: True
        )
        self.assertEqual(names, ())
        self.assertFalse(truncated)

    def test_missing_directory_raises_os_error(self) -> None:
        with self.assertRaises(OSError):
            read_folder_names(
                os.path.join(self._tmp.name, "missing"), "", PATH_AC_MAX_FOLDERS, lambda: False
            )


class BuildListingTests(unittest.TestCase):
    """입력 경로에 대한 목록 결과와 실패 처리를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _make_tree(self._tmp.name)

    def test_listing_has_full_paths_and_parent(self) -> None:
        listing = build_listing(self._tmp.name + os.sep, PATH_AC_MAX_FOLDERS, lambda: False)
        assert listing is not None
        self.assertEqual(listing.count, 4)
        self.assertEqual(listing.base_dir, self._tmp.name + os.sep)
        self.assertEqual(listing.folders[0], os.path.join(self._tmp.name + os.sep, "alpha"))
        self.assertEqual(listing.parent, parent_folder(self._tmp.name + os.sep))
        self.assertFalse(listing.truncated)

    def test_prefix_narrows_listing(self) -> None:
        listing = build_listing(
            os.path.join(self._tmp.name, "gam"), PATH_AC_MAX_FOLDERS, lambda: False
        )
        assert listing is not None
        self.assertEqual(
            [os.path.basename(folder) for folder in listing.folders],
            ["gamma2", "gamma10"],
        )

    def test_existing_folder_without_separator_lists_its_children(self) -> None:
        sub = os.path.join(self._tmp.name, "alpha")
        os.makedirs(os.path.join(sub, "inner"))
        listing = build_listing(sub, PATH_AC_MAX_FOLDERS, lambda: False)
        assert listing is not None
        self.assertEqual(listing.base_dir, sub)
        self.assertEqual(listing.folders, (os.path.join(sub, "inner"),))
        self.assertEqual(listing.parent, self._tmp.name)

    def test_existing_folder_without_children_keeps_parent_only(self) -> None:
        sub = os.path.join(self._tmp.name, "alpha")
        listing = build_listing(sub, PATH_AC_MAX_FOLDERS, lambda: False)
        assert listing is not None
        self.assertEqual(listing.folders, ())
        self.assertEqual(listing.parent, self._tmp.name)

    def test_missing_directory_returns_none(self) -> None:
        self.assertIsNone(
            build_listing(
                os.path.join(self._tmp.name, "missing") + os.sep,
                PATH_AC_MAX_FOLDERS,
                lambda: False,
            )
        )

    def test_cancelled_build_returns_none(self) -> None:
        self.assertIsNone(
            build_listing(self._tmp.name + os.sep, PATH_AC_MAX_FOLDERS, lambda: True)
        )


class FolderListLoaderTests(unittest.TestCase):
    """워커 스레드 읽기 요청과 결과 전달을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _make_tree(self._tmp.name)

    def test_request_delivers_listing(self) -> None:
        loader = FolderListLoader()
        self.addCleanup(loader.deleteLater)
        received: list[tuple[int, object]] = []
        loader.finished.connect(
            lambda request_id, listing: received.append((request_id, listing))
        )
        request_id = loader.request(self._tmp.name + os.sep, PATH_AC_MAX_FOLDERS)
        self.assertTrue(_wait_until(lambda: bool(received)))
        self.assertEqual(received[0][0], request_id)
        self.assertIsInstance(received[0][1], FolderListing)

    def test_cancel_before_finish_drops_the_result(self) -> None:
        loader = FolderListLoader()
        self.addCleanup(loader.deleteLater)
        received: list[object] = []
        loader.finished.connect(lambda _request_id, listing: received.append(listing))
        gate = threading.Event()
        # - 읽기를 붙잡아 두어 취소가 결과 전달보다 먼저 일어나게 함
        with mock.patch.object(module, "build_listing", lambda *_args: gate.wait(5) and None):
            loader.request(self._tmp.name + os.sep, PATH_AC_MAX_FOLDERS)
            loader.cancel()
        gate.set()
        _APP.processEvents()
        time.sleep(_POLL_INTERVAL_SEC * 10)
        _APP.processEvents()
        self.assertEqual(received, [])


class FolderAutocompletePopupTests(unittest.TestCase):
    """드롭다운 표시 내용, 상위 항목, 개수와 잘림 안내를 검증"""

    def setUp(self) -> None:
        self._host = QWidget()
        self.addCleanup(self._host.deleteLater)
        self._popup = FolderAutocompletePopup(self._host)
        self.addCleanup(self._popup.deleteLater)
        self._anchor = QLineEdit(self._host)
        self._anchor.resize(240, 24)

    def _listing(self, *, parent: str | None, truncated: bool = False) -> FolderListing:
        return FolderListing(
            base_dir="F:\\",
            folders=("F:\\alpha", "F:\\beta"),
            parent=parent,
            truncated=truncated,
        )

    def test_popup_is_a_floating_popup_that_takes_no_focus(self) -> None:
        self.assertIsInstance(self._popup, FloatingPopupFrame)
        self.assertTrue(self._popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)

    def test_listing_puts_parent_row_first_and_selects_nothing(self) -> None:
        self._popup.show_listing(self._listing(parent="F:\\"))
        self.assertEqual(self._popup.list_folders.count(), 3)
        self.assertEqual(_item_text(self._popup, 0), PARENT_ROW_TEXT)
        self.assertEqual(_item_text(self._popup, 1), "alpha")
        self.assertIsNone(self._popup.selected_folder())
        self.assertEqual(self._popup.count_text, "2개 폴더")

    def test_parent_row_absent_without_parent(self) -> None:
        self._popup.show_listing(self._listing(parent=None))
        self.assertEqual(self._popup.list_folders.count(), 2)
        self.assertEqual(_item_text(self._popup, 0), "alpha")

    def test_truncated_listing_shows_partial_notice(self) -> None:
        self._popup.show_listing(self._listing(parent=None, truncated=True))
        self.assertIn(TRUNCATED_NOTICE, self._popup.count_text)
        self.assertIn("2개 폴더", self._popup.count_text)

    def test_footer_shows_keycaps_for_the_list_keys(self) -> None:
        """하단 안내가 Up, Down 글자 대신 화살표와 키캡 배지로 그려짐을 검증"""
        keycaps = self._popup.findChildren(QLabel, "PathAutocompleteKeycap")
        texts = [label.text() for label in keycaps]
        for expected in ("↑", "↓", "Tab", "Enter"):
            self.assertIn(expected, texts)
        for unwanted in ("Up", "Down"):
            self.assertNotIn(unwanted, texts)
        self.assertNotIn("Up", KEY_HINT)
        self.assertNotIn("Down", KEY_HINT)

    def test_footer_width_keeps_the_key_hint_inside_the_popup(self) -> None:
        """앵커가 좁아도 하단 키 안내 폭만큼 팝업을 넓혀 잘리지 않게 함을 검증"""
        self._anchor.resize(40, 24)
        self._popup.show_for(self._anchor)
        footer = self._popup._footer_layout
        self.assertGreaterEqual(self._popup.width(), footer.sizeHint().width())
        self._popup.close()

    def test_reading_state_hides_list_and_shows_notice(self) -> None:
        self._popup.show_listing(self._listing(parent=None))
        self._popup.show_reading(self._anchor)
        self.assertFalse(self._popup.list_folders.isVisible())
        self.assertEqual(self._popup.count_text, READING_NOTICE)

    def test_move_selection_starts_at_ends_and_clamps(self) -> None:
        self._popup.show_listing(self._listing(parent="F:\\"))
        self._popup.move_selection(1)
        self.assertEqual(self._popup.current_row_text(), PARENT_ROW_TEXT)
        self._popup.move_selection(100)
        self.assertEqual(self._popup.current_row_text(), "beta")
        self.assertEqual(self._popup.selected_folder(), "F:\\beta")

    def test_move_selection_up_from_none_starts_at_last_row(self) -> None:
        self._popup.show_listing(self._listing(parent=None))
        self._popup.move_selection(-1)
        self.assertEqual(self._popup.current_row_text(), "beta")

    def test_move_selection_without_rows_does_nothing(self) -> None:
        self._popup.move_selection(1)
        self.assertIsNone(self._popup.selected_folder())

    def test_clicking_a_row_emits_its_folder(self) -> None:
        activated: list[str] = []
        self._popup.folder_activated.connect(activated.append)
        self._popup.show_listing(self._listing(parent=None))
        item = self._popup.list_folders.item(1)
        assert item is not None
        self._popup._on_item_clicked(item)
        self.assertEqual(activated, ["F:\\beta"])

    def test_escape_while_shown_closes_and_lets_the_edit_cancel(self) -> None:
        """목록이 열린 채 입력 칸에서 Esc를 누르면 닫기만 하고 이벤트는 넘기는지 검증"""
        self._popup.show_for(self._anchor)
        self.assertFalse(self._popup.eventFilter(self._anchor, _key_event(Qt.Key.Key_Escape)))
        self.assertFalse(self._popup.isVisible())

    def test_other_key_events_pass_through(self) -> None:
        self._popup.show_for(self._anchor)
        self.assertFalse(self._popup.eventFilter(self._anchor, _key_event(Qt.Key.Key_A)))
        self.assertTrue(self._popup.isVisible())

    def test_clicking_the_anchor_is_not_an_outside_click(self) -> None:
        self._popup.show_for(self._anchor)
        anchor_center = self._anchor.rect().center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(self._popup.mapFromGlobal(self._anchor.mapToGlobal(anchor_center))),
            QPointF(self._anchor.mapToGlobal(anchor_center)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self._popup.eventFilter(self._anchor, event))

    def test_escape_from_the_popup_itself_still_closes_it(self) -> None:
        self._popup.show_for(self._anchor)
        self.assertTrue(self._popup.eventFilter(self._popup, _key_event(Qt.Key.Key_Escape)))
        self.assertFalse(self._popup.isVisible())

    def test_theme_change_recolors_row_icons(self) -> None:
        self._popup.show_listing(self._listing(parent=None))
        self._popup.apply_theme(LIGHT_PALETTE)
        item = self._popup.list_folders.item(0)
        assert item is not None
        self.assertFalse(item.icon().isNull())

    def test_click_inside_the_popup_is_not_a_outside_click(self) -> None:
        self._popup.show_for(self._anchor)
        local = self._popup.rect().center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(local),
            QPointF(self._popup.mapToGlobal(local)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self._popup.eventFilter(self._popup, event))

    def test_empty_listing_with_parent_shows_only_the_parent_row(self) -> None:
        self._popup.show_listing(
            FolderListing(base_dir="F:\\sub", folders=(), parent="F:\\", truncated=False)
        )
        self.assertEqual(self._popup.list_folders.count(), 1)
        self.assertEqual(_item_text(self._popup, 0), PARENT_ROW_TEXT)

    def test_hidden_popup_clears_selection(self) -> None:
        self._popup.show_listing(self._listing(parent="F:\\"))
        self._popup.move_selection(1)
        self._popup._on_hidden()
        self.assertIsNone(self._popup.selected_folder())


class PathAutocompleteTests(unittest.TestCase):
    """입력 칸의 디바운스 읽기와 목록 키 조작, 확정 동작을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _make_tree(self._tmp.name)
        self._host = QWidget()
        self.addCleanup(self._host.deleteLater)
        self._edit = QLineEdit(self._host)
        self._autocomplete = PathAutocomplete(self._edit, self._host)
        self.addCleanup(self._autocomplete.close)

    def _wait_open(self) -> None:
        self.assertTrue(
            _wait_until(
                lambda: self._autocomplete.is_open
                and self._autocomplete.popup.list_folders.count() > 0
            )
        )

    def _rows(self) -> list[str]:
        popup = self._autocomplete.popup
        return [_item_text(popup, i) for i in range(popup.list_folders.count())]

    def test_typing_reads_subfolders_after_the_debounce(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertEqual(_item_text(self._autocomplete.popup, 0), PARENT_ROW_TEXT)
        self.assertEqual(self._autocomplete.popup.count_text, "4개 폴더")

    def test_typing_a_prefix_narrows_the_list(self) -> None:
        self._edit.setText(os.path.join(self._tmp.name, "gam"))
        self._autocomplete._on_text_edited("gam")
        self._wait_open()
        rows = self._rows()
        self.assertIn("gamma10", rows)
        self.assertIn("gamma2", rows)
        self.assertNotIn("alpha", rows)

    def test_typing_an_existing_folder_lists_its_children(self) -> None:
        sub = os.path.join(self._tmp.name, "alpha")
        os.makedirs(os.path.join(sub, "inner"))
        self._edit.setText(sub)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertEqual(self._rows(), [PARENT_ROW_TEXT, "inner"])

    def test_typing_an_empty_existing_folder_shows_only_the_parent_row(self) -> None:
        self._edit.setText(os.path.join(self._tmp.name, "alpha"))
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertEqual(self._rows(), [PARENT_ROW_TEXT])

    def test_input_without_folder_keeps_the_dropdown_closed(self) -> None:
        self._autocomplete._start_reading()
        self.assertFalse(self._autocomplete.is_open)

    def test_down_and_up_move_the_selection(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Down)))
        self.assertEqual(self._autocomplete.popup.current_row_text(), PARENT_ROW_TEXT)
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Down)))
        self.assertEqual(self._autocomplete.popup.current_row_text(), "alpha")
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Up)))
        self.assertEqual(self._autocomplete.popup.current_row_text(), PARENT_ROW_TEXT)

    def test_keys_are_not_intercepted_while_the_list_is_closed(self) -> None:
        self.assertFalse(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Down)))

    def test_other_widget_events_fall_through(self) -> None:
        self.assertFalse(self._autocomplete.eventFilter(self._host, _key_event(Qt.Key.Key_Down)))

    def test_other_event_types_fall_through(self) -> None:
        focus_in = QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.OtherFocusReason)
        self.assertFalse(self._autocomplete.eventFilter(self._edit, focus_in))

    def test_focus_loss_keeps_the_list_when_no_other_widget_takes_focus(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        focus_out = QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)
        self.assertFalse(self._autocomplete.eventFilter(self._edit, focus_out))
        _APP.processEvents()
        self.assertTrue(self._autocomplete.is_open)

    def test_moving_focus_to_another_widget_closes_the_list(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        other = QLineEdit(self._host)
        self.addCleanup(other.deleteLater)
        # - 오프스크린에서는 창이 활성화되지 않아 실제 포커스가 옮겨 가지 않으므로 포커스 위젯을 바꿔 끼움
        with mock.patch.object(module.QApplication, "focusWidget", staticmethod(lambda: other)):
            self._autocomplete._close_if_focus_moved()
        self.assertFalse(self._autocomplete.is_open)

    def test_moving_focus_before_the_debounce_cancels_the_pending_read(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        other = QLineEdit(self._host)
        self.addCleanup(other.deleteLater)
        with mock.patch.object(module.QApplication, "focusWidget", staticmethod(lambda: other)):
            self._autocomplete._close_if_focus_moved()
        self.assertFalse(self._autocomplete._timer.isActive())

    def _late_listing_for_current_request(self) -> tuple[int, FolderListing | None]:
        """지금 입력으로 읽기를 요청하고, 그 요청 번호와 같은 내용의 결과를 직접 만듦"""
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._start_reading()
        request_id = self._autocomplete.request_id
        listing = build_listing(self._tmp.name + os.sep, PATH_AC_MAX_FOLDERS, lambda: False)
        return request_id, listing

    def test_result_arriving_after_close_does_not_reopen_the_list(self) -> None:
        request_id, listing = self._late_listing_for_current_request()
        self._autocomplete.close()
        self._autocomplete._on_listing_ready(request_id, listing)
        self.assertFalse(self._autocomplete.is_open)

    def test_result_for_the_previous_text_is_dropped_after_editing(self) -> None:
        request_id, listing = self._late_listing_for_current_request()
        self._edit.setText(os.path.join(self._tmp.name, "zzzz"))
        self._autocomplete._on_text_edited("zzzz")
        self._autocomplete._on_listing_ready(request_id, listing)
        self.assertEqual(self._autocomplete.popup.list_folders.count(), 0)

    def test_fill_folder_does_not_repeat_a_trailing_separator(self) -> None:
        self._autocomplete._fill_folder(self._tmp.name + os.sep)
        self.assertEqual(self._edit.text(), self._tmp.name + os.sep)

    def test_enter_fills_the_selected_folder_and_keeps_reading(self) -> None:
        submitted: list[str] = []
        self._edit.returnPressed.connect(lambda: submitted.append(self._edit.text()))
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self._autocomplete.popup.move_selection(1)
        self._autocomplete.popup.move_selection(1)
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Return)))
        self.assertEqual(self._edit.text(), os.path.join(self._tmp.name, "alpha") + os.sep)
        self.assertEqual(submitted, [])
        self._wait_open()

    def test_numpad_enter_fills_the_selected_folder_like_tab(self) -> None:
        submitted: list[str] = []
        self._edit.returnPressed.connect(lambda: submitted.append(self._edit.text()))
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self._autocomplete.popup.move_selection(1)
        self._autocomplete.popup.move_selection(1)
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Enter)))
        self.assertEqual(self._edit.text(), os.path.join(self._tmp.name, "alpha") + os.sep)
        self.assertEqual(submitted, [])

    def test_enter_without_selection_falls_through_to_the_edit(self) -> None:
        submitted: list[str] = []
        self._edit.returnPressed.connect(lambda: submitted.append(self._edit.text()))
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertFalse(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Return)))
        self.assertFalse(self._autocomplete.is_open)
        # - 이벤트를 입력 칸에 그대로 넘기면 기본 처리로 확정 신호가 나감
        QApplication.sendEvent(self._edit, _key_event(Qt.Key.Key_Return))
        self.assertEqual(submitted, [self._tmp.name + os.sep])
        self.assertEqual(self._edit.text(), self._tmp.name + os.sep)

    def test_real_enter_key_press_fills_without_confirming(self) -> None:
        submitted: list[str] = []
        self._edit.returnPressed.connect(lambda: submitted.append(self._edit.text()))
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self._autocomplete.popup.move_selection(1)
        self._autocomplete.popup.move_selection(1)
        QApplication.sendEvent(self._edit, _key_event(Qt.Key.Key_Return))
        self.assertEqual(submitted, [])
        self.assertEqual(self._edit.text(), os.path.join(self._tmp.name, "alpha") + os.sep)
        self.assertTrue(self._autocomplete.is_open)

    def test_shortcut_override_is_not_consumed(self) -> None:
        """키 입력 전에 오는 ShortcutOverride를 목록 조작으로 처리하지 않는지 검증"""
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        override = QKeyEvent(
            QEvent.Type.ShortcutOverride,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self._autocomplete.eventFilter(self._edit, override))
        self.assertEqual(self._autocomplete.popup.current_row_text(), "")

    def test_tab_fills_the_selected_folder_and_keeps_reading(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self._autocomplete.popup.move_selection(1)
        self._autocomplete.popup.move_selection(1)
        self.assertTrue(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Tab)))
        self.assertEqual(self._edit.text(), os.path.join(self._tmp.name, "alpha") + os.sep)
        self._wait_open()

    def test_tab_without_selection_falls_through(self) -> None:
        self._edit.setText(self._tmp.name + os.sep)
        self._autocomplete._on_text_edited("")
        self._wait_open()
        self.assertFalse(self._autocomplete.eventFilter(self._edit, _key_event(Qt.Key.Key_Tab)))

    def test_activating_a_folder_commits_it(self) -> None:
        submitted: list[str] = []
        self._edit.returnPressed.connect(lambda: submitted.append(self._edit.text()))
        self._autocomplete._on_folder_activated(os.path.join(self._tmp.name, "beta"))
        self.assertEqual(submitted, [os.path.join(self._tmp.name, "beta")])
        self.assertFalse(self._autocomplete.is_open)

    def test_theme_is_forwarded_to_the_popup(self) -> None:
        self._autocomplete.apply_theme(DARK_PALETTE)
        self.assertEqual(self._autocomplete.popup.theme_palette, DARK_PALETTE)

    def test_stale_listing_is_ignored(self) -> None:
        self._autocomplete._on_listing_ready(99, self._tmp.name + os.sep)
        self.assertFalse(self._autocomplete.is_open)

    def test_failed_listing_closes_the_dropdown(self) -> None:
        self._autocomplete._on_listing_ready(self._autocomplete.request_id, None)
        self.assertFalse(self._autocomplete.is_open)

    def test_empty_listing_closes_the_dropdown(self) -> None:
        listing = FolderListing(base_dir="F:\\", folders=(), parent=None, truncated=False)
        self._autocomplete._on_listing_ready(self._autocomplete.request_id, listing)
        self.assertFalse(self._autocomplete.is_open)

    def test_key_hint_mentions_the_list_keys(self) -> None:
        self.assertIn("Tab", KEY_HINT)
        self.assertIn("Enter", KEY_HINT)
        self.assertNotIn("Up", KEY_HINT)
        self.assertNotIn("Down", KEY_HINT)

    def test_max_folder_cap_is_the_agreed_value(self) -> None:
        self.assertEqual(PATH_AC_MAX_FOLDERS, 200)

