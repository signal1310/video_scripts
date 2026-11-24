"""통합 데이터 정리 및 초기화 대화상자 검증: 범위 전환, 프리셋, 19개 체크박스, 경고와 확인, 테마

- 미리보기와 확인은 대역 함수를 주입해 실제 저장 데이터를 건드리지 않음
- 배지 문구와 버튼 상태는 위젯 문자열로 확인
"""

import os
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.gui.dialogs import data_cleanup_dialog as dialog_module
from src.gui.dialogs.data_cleanup_dialog import (
    DIALOG_DEFAULT_HEIGHT,
    DIALOG_DEFAULT_WIDTH,
    DIALOG_MIN_WIDTH,
    SAFETY_NOTE_TEXT,
    DataCleanupDialog,
    DataCleanupRequest,
    is_irreversible_key,
    item_badge_text,
    item_tooltip,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.service.data_cleanup_service import (
    ALL_CLEANUP_ITEM_KEYS,
    CATEGORY_CLEANUP_ITEM_KEYS,
    CLEANUP_ITEM_LABELS,
    CLEANUP_PRESETS,
    CleanupItemKey,
    CleanupItemPreview,
    CleanupScope,
    DataCleanupService,
)

_APP = QApplication.instance() or QApplication([])
ROOT: str = os.path.join(os.sep, "work")
CURRENT_COUNT: int = 2
ALL_EXTRA_COUNT: int = 5
PART_SIZE_BYTES: int = 3 * 1024 * 1024


def _previews(scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
    """범위에 따라 건수가 달라지는 미리보기 대역. 범위를 바꾸면 배지도 바뀜"""
    count = CURRENT_COUNT if scope == "current" else CURRENT_COUNT + ALL_EXTRA_COUNT
    return {
        key: CleanupItemPreview(key, count, PART_SIZE_BYTES if key == "part_files" else 0, True)
        for key in ALL_CLEANUP_ITEM_KEYS
    }


class _Recorder:
    """미리보기와 확인 호출을 기록하는 대역"""

    def __init__(self, confirm: bool = True) -> None:
        self.scopes: list[CleanupScope] = []
        self.confirm_calls: int = 0
        self._confirm = confirm

    def preview(self, scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
        self.scopes.append(scope)
        return _previews(scope)

    def confirm(self) -> bool:
        self.confirm_calls += 1
        return self._confirm


def _dialog(recorder: _Recorder, **kwargs: object) -> DataCleanupDialog:
    """대역 미리보기와 확인을 물린 대화상자"""
    options: dict[str, object] = {
        "preview_fn": recorder.preview,
        "confirm_fn": recorder.confirm,
        "current_root": ROOT,
    }
    options.update(kwargs)
    return DataCleanupDialog(**options)  # type: ignore[arg-type]


class DataCleanupDialogStructureTest(unittest.TestCase):
    """범주, 항목, 프리셋 구성과 초기 상태"""

    def test_all_categories_and_checkboxes_exist(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertEqual(dialog.windowTitle(), "데이터 정리 및 초기화")
        self.assertEqual(len(dialog._sections), 4)
        self.assertEqual(len(dialog._select_all), 4)
        self.assertEqual(len(dialog._badges), 19)
        self.assertEqual(tuple(dialog._checkboxes), ALL_CLEANUP_ITEM_KEYS)
        for key, checkbox in dialog._checkboxes.items():
            self.assertEqual(checkbox.text(), CLEANUP_ITEM_LABELS[key])
            self.assertIn("원본 영상", checkbox.toolTip())
            self.assertEqual(checkbox.toolTip(), item_tooltip(key))

    def test_preset_buttons_match_service_presets(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertEqual(len(dialog._preset_buttons), 4)
        self.assertEqual(tuple(dialog._preset_buttons), tuple(CLEANUP_PRESETS))
        labels = [button.text() for button in dialog._preset_buttons.values()]
        self.assertEqual(
            labels,
            ["재인코딩 다시 시도", "분석 처음부터 다시", "화면 배치만 초기화", "전체 초기화"],
        )
        for button in dialog._preset_buttons.values():
            self.assertTrue(button.toolTip())

    def test_initial_state_has_no_selection(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertEqual(dialog.checked_keys(), ())
        self.assertIsNone(dialog.selected_request())
        self.assertFalse(dialog.btn_execute.isEnabled())
        self.assertTrue(dialog.warning_box.isHidden())
        self.assertEqual(dialog.btn_execute.text(), "선택 항목 정리")
        self.assertEqual(dialog.btn_close.text(), "닫기")
        self.assertEqual(dialog.lbl_safety_text.text(), SAFETY_NOTE_TEXT)
        self.assertIn("휴지통으로 옮겨집니다", dialog.lbl_safety_text.text())


class DataCleanupDialogPresetTest(unittest.TestCase):
    """프리셋과 범주 전체 선택이 정확한 항목만 선택하는지"""

    def test_each_preset_checks_exact_keys(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        for preset, expected in CLEANUP_PRESETS.items():
            with self.subTest(preset=preset):
                dialog._preset_buttons[preset].click()
                self.assertEqual(dialog.checked_keys(), expected)
                self.assertEqual(recorder.confirm_calls, 0)

    def test_preset_replaces_previous_selection(self) -> None:
        dialog = _dialog(_Recorder())
        dialog._checkboxes["part_files"].setChecked(True)
        dialog.apply_preset("reset_layout")
        self.assertEqual(dialog.checked_keys(), CATEGORY_CLEANUP_ITEM_KEYS["layout"])
        self.assertFalse(dialog._checkboxes["part_files"].isChecked())

    def test_preset_expands_its_categories(self) -> None:
        dialog = _dialog(_Recorder())
        for category in CATEGORY_CLEANUP_ITEM_KEYS:
            dialog._sections[category].set_expanded(False)
        dialog.apply_preset("reanalyze_fresh")
        self.assertTrue(dialog._sections["analysis"].is_expanded())
        self.assertFalse(dialog._sections["encode"].is_expanded())

    def test_category_select_all_toggles_every_item(self) -> None:
        dialog = _dialog(_Recorder())
        dialog._on_select_all_clicked("settings", True)
        self.assertEqual(dialog.checked_keys(), CATEGORY_CLEANUP_ITEM_KEYS["settings"])
        self.assertEqual(dialog.btn_execute.objectName(), "DangerButton")
        dialog._on_select_all_clicked("settings", False)
        self.assertEqual(dialog.checked_keys(), ())
        self.assertEqual(dialog.btn_execute.objectName(), "PrimaryButton")
        dialog.set_category_checked("encode", True)
        self.assertEqual(dialog.checked_keys(), CATEGORY_CLEANUP_ITEM_KEYS["encode"])

    def test_select_all_indicator_follows_items(self) -> None:
        dialog = _dialog(_Recorder())
        for key in CATEGORY_CLEANUP_ITEM_KEYS["encode"]:
            dialog._checkboxes[key].setChecked(True)
        self.assertTrue(dialog._select_all["encode"].isChecked())
        dialog._checkboxes["part_files"].setChecked(False)
        self.assertFalse(dialog._select_all["encode"].isChecked())


class DataCleanupDialogScopeTest(unittest.TestCase):
    """범위 전환이 미리보기를 다시 부르고 배지를 갱신하는지"""

    def test_scope_switch_reinvokes_preview(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        self.assertEqual(recorder.scopes, ["current"])
        self.assertEqual(dialog.current_scope(), "current")
        self.assertEqual(dialog._badges["part_files"].text(), "2건 / 3.0 MB")
        self.assertEqual(dialog._badges["probe_cache"].text(), "2건")
        dialog.set_scope("all")
        self.assertEqual(recorder.scopes, ["current", "all"])
        self.assertEqual(dialog.current_scope(), "all")
        self.assertEqual(dialog._badges["part_files"].text(), "7건 / 3.0 MB")
        self.assertEqual(dialog._badges["probe_cache"].text(), "7건")
        dialog.set_scope("current")
        self.assertEqual(recorder.scopes, ["current", "all", "current"])
        self.assertEqual(dialog._badges["probe_cache"].text(), "2건")

    def test_radio_click_switches_scope(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        dialog._radio_all.setChecked(True)
        self.assertEqual(recorder.scopes, ["current", "all"])
        self.assertEqual(dialog.selected_request(), None)
        dialog._checkboxes["probe_cache"].setChecked(True)
        self.assertEqual(dialog.selected_request(), DataCleanupRequest("all", ("probe_cache",)))

    def test_scope_hint_shows_current_root(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertIn(ROOT, dialog.lbl_scope_hint.text())
        dialog.set_scope("all")
        self.assertNotIn(ROOT, dialog.lbl_scope_hint.text())

    def test_current_scope_disabled_without_root(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder, current_root="")
        self.assertFalse(dialog._radio_current.isEnabled())
        self.assertTrue(dialog._radio_all.isChecked())
        self.assertEqual(dialog.current_scope(), "all")
        self.assertEqual(recorder.scopes, ["all"])
        self.assertIn("작업 폴더", dialog.lbl_scope_hint.text())
        dialog.set_scope("current")
        self.assertEqual(dialog.current_scope(), "all")
        self.assertEqual(recorder.scopes, ["all", "all"])


class DataCleanupDialogSelectionTest(unittest.TestCase):
    """실행 버튼 상태와 되돌릴 수 없음 경고, 확인 흐름"""

    def test_execute_button_follows_selection(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertFalse(dialog.btn_execute.isEnabled())
        dialog._checkboxes["probe_cache"].setChecked(True)
        self.assertTrue(dialog.btn_execute.isEnabled())
        self.assertEqual(dialog.btn_execute.objectName(), "PrimaryButton")
        dialog._checkboxes["probe_cache"].setChecked(False)
        self.assertFalse(dialog.btn_execute.isEnabled())

    def test_settings_item_shows_warning_and_danger_style(self) -> None:
        dialog = _dialog(_Recorder())
        dialog._checkboxes["criteria_settings"].setChecked(True)
        self.assertFalse(dialog.warning_box.isHidden())
        self.assertEqual(dialog.btn_execute.objectName(), "DangerButton")
        self.assertFalse(dialog.lbl_warning_icon.pixmap().isNull())
        dialog._checkboxes["criteria_settings"].setChecked(False)
        self.assertTrue(dialog.warning_box.isHidden())
        self.assertEqual(dialog.btn_execute.objectName(), "PrimaryButton")

    def test_execute_without_settings_skips_confirmation(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        dialog.apply_preset("reset_layout")
        dialog._on_execute()
        self.assertEqual(recorder.confirm_calls, 0)
        self.assertEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))

    def test_declining_confirmation_keeps_dialog_open(self) -> None:
        recorder = _Recorder(confirm=False)
        dialog = _dialog(recorder)
        dialog.apply_preset("reset_all")
        dialog._on_execute()
        self.assertEqual(recorder.confirm_calls, 1)
        self.assertNotEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))
        self.assertEqual(dialog.selected_request(), DataCleanupRequest("current", ALL_CLEANUP_ITEM_KEYS))

    def test_accepting_confirmation_keeps_request(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        dialog._checkboxes["shortcuts_settings"].setChecked(True)
        dialog._on_execute()
        self.assertEqual(recorder.confirm_calls, 1)
        self.assertEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))
        self.assertEqual(dialog.selected_request(), DataCleanupRequest("current", ("shortcuts_settings",)))

    def test_execute_without_selection_does_nothing(self) -> None:
        recorder = _Recorder()
        dialog = _dialog(recorder)
        dialog._on_execute()
        self.assertEqual(recorder.confirm_calls, 0)
        self.assertNotEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))

    def test_ask_confirm_uses_message_box_answer(self) -> None:
        dialog = _dialog(_Recorder())
        dialog._checkboxes["criteria_settings"].setChecked(True)
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes) as warn:
            self.assertTrue(dialog._ask_confirm())
        self.assertIn("되돌릴 수 없습니다", warn.call_args[0][2])
        self.assertIn("1개", warn.call_args[0][2])
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(dialog._ask_confirm())


class DataCleanupDialogThemeTest(unittest.TestCase):
    """테마 전환과 미리보기 대체 경로"""

    def test_apply_theme_dark_and_light(self) -> None:
        dialog = _dialog(_Recorder())
        dialog.apply_theme(DARK_PALETTE)
        self.assertTrue(dialog.styleSheet())
        self.assertFalse(dialog.lbl_warning_icon.pixmap().isNull())
        dialog.apply_theme(LIGHT_PALETTE)
        self.assertTrue(dialog.styleSheet())
        self.assertFalse(dialog.lbl_safety_icon.pixmap().isNull())

    def test_default_preview_without_fn_or_service(self) -> None:
        dialog = DataCleanupDialog()
        self.assertEqual(dialog.current_scope(), "all")
        self.assertEqual(dialog._badges["part_files"].text(), "0건")
        dialog.refresh_preview()
        self.assertEqual(dialog._badges["probe_cache"].text(), "0건")

    def test_service_preview_is_used_when_injected(self) -> None:
        service = mock.Mock(spec=DataCleanupService)
        service.preview.return_value = _previews("current")
        dialog = DataCleanupDialog(service=cast("DataCleanupService", service), current_root=ROOT)
        dialog.refresh_preview()
        service.preview.assert_called_with("current", ROOT)
        self.assertEqual(dialog._badges["probe_cache"].text(), "2건")


class DataCleanupDialogLayoutTest(unittest.TestCase):
    """13pt 글꼴과 최소 너비에서도 프리셋 버튼이 잘리지 않는지"""

    def test_preset_buttons_fit_at_13pt_min_and_default_width(self) -> None:
        dialog = _dialog(_Recorder())
        font = QFont(dialog.font())
        font.setPointSize(13)
        dialog.setFont(font)
        dialog.show()
        try:
            for width, height in ((DIALOG_MIN_WIDTH, 520), (DIALOG_DEFAULT_WIDTH, DIALOG_DEFAULT_HEIGHT)):
                with self.subTest(width=width, height=height):
                    dialog.resize(width, height)
                    QApplication.processEvents()
                    for preset, button in dialog._preset_buttons.items():
                        self.assertGreaterEqual(
                            button.geometry().width(),
                            button.sizeHint().width(),
                            preset,
                        )
        finally:
            dialog.close()


class DataCleanupHelpersTest(unittest.TestCase):
    """배지 문구와 항목 분류 도우미"""

    def test_item_badge_text_variants(self) -> None:
        self.assertEqual(item_badge_text(None), "0건")
        self.assertEqual(item_badge_text(CleanupItemPreview("part_files", 0, 1024, True)), "0건")
        self.assertEqual(item_badge_text(CleanupItemPreview("probe_cache", 12, 0, True)), "12건")
        self.assertEqual(
            item_badge_text(CleanupItemPreview("part_files", 12, 48 * 1024 * 1024, True)),
            "12건 / 48.0 MB",
        )

    def test_is_irreversible_key_matches_settings_category(self) -> None:
        self.assertTrue(is_irreversible_key("criteria_settings"))
        self.assertFalse(is_irreversible_key("column_order"))

    def test_request_irreversible_keys_filters_settings(self) -> None:
        request = DataCleanupRequest("all", ("part_files", "criteria_settings", "exception_rules"))
        self.assertEqual(request.irreversible_keys, ("criteria_settings", "exception_rules"))
        self.assertEqual(DataCleanupRequest("all", ()).irreversible_keys, ())

    def test_module_exports_empty_preview_helper(self) -> None:
        previews = dialog_module._empty_preview("all")
        self.assertEqual(tuple(previews), ALL_CLEANUP_ITEM_KEYS)
        self.assertTrue(all(item.count == 0 for item in previews.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
