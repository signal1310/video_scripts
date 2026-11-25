"""통합 데이터 정리 및 초기화 대화상자 검증: 범위 전환, 프리셋, 19개 체크박스, 경고와 확인, 테마

- 미리보기와 확인은 대역 함수를 주입해 실제 저장 데이터를 건드리지 않음
- 저장 데이터 건수는 백그라운드 결과로 채워지므로, 스레드 시작을 대역으로 바꿔 실행 순서를 결정적으로 만듦
- 배지 문구와 버튼 상태는 위젯 문자열로 확인
"""

import os
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.gui.dialogs import data_cleanup_dialog as dialog_module
from src.gui.dialogs import data_cleanup_preview_loader as loader_module
from src.gui.dialogs.data_cleanup_dialog import (
    DIALOG_DEFAULT_HEIGHT,
    DIALOG_DEFAULT_WIDTH,
    DIALOG_MIN_WIDTH,
    LOADING_BADGE_TEXT,
    PRESET_TOOLTIPS,
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
    CLEANUP_PRESET_LABELS,
    CLEANUP_PRESETS,
    STORAGE_CLEANUP_ITEM_KEYS,
    UI_RESET_CLEANUP_ITEM_KEYS,
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
    """범위에 따라 건수가 달라지는 저장 데이터 미리보기 대역. 범위를 바꾸면 배지도 바뀜"""
    count = CURRENT_COUNT if scope == "current" else CURRENT_COUNT + ALL_EXTRA_COUNT
    return {
        key: CleanupItemPreview(key, count, PART_SIZE_BYTES if key == "part_files" else 0, True)
        for key in STORAGE_CLEANUP_ITEM_KEYS
    }


def _ui_previews() -> dict[CleanupItemKey, CleanupItemPreview]:
    """화면/설정 항목 미리보기 대역. 모두 기본값과 같은 것으로 봄"""
    return {key: CleanupItemPreview(key, 0, 0, True) for key in UI_RESET_CLEANUP_ITEM_KEYS}


class _Recorder:
    """미리보기와 확인 호출을 기록하는 대역"""

    def __init__(self, confirm: bool = True) -> None:
        self.scopes: list[CleanupScope] = []
        self.confirm_calls: int = 0
        self._confirm = confirm

    def storage(self, scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
        """범위별 저장 데이터 미리보기"""
        self.scopes.append(scope)
        return _previews(scope)

    def confirm(self) -> bool:
        self.confirm_calls += 1
        return self._confirm


def _immediate_spawn(thread: object) -> None:
    """스레드를 띄우지 않고 본체를 바로 실행해 배지를 동기로 채움"""
    thread.run()  # type: ignore[attr-defined]


class _ImmediateLoaderCase(unittest.TestCase):
    """저장 데이터 미리보기 스레드를 즉시 실행하는 대역을 테스트 동안 붙이는 기준 클래스

    - 범위 전환처럼 창을 만든 뒤에 다시 읽는 경로도 결정적으로 채우려고 클래스 단위로 한 번만 붙임
    """

    def setUp(self) -> None:
        patcher = mock.patch.object(loader_module, "spawn_preview_thread", side_effect=_immediate_spawn)
        patcher.start()
        self.addCleanup(patcher.stop)


def _dialog(recorder: _Recorder, **kwargs: object) -> DataCleanupDialog:
    """저장 데이터 미리보기를 동기로 채우는 대화상자"""
    options: dict[str, object] = {
        "storage_preview_fn": recorder.storage,
        "ui_preview_fn": _ui_previews,
        "confirm_fn": recorder.confirm,
        "current_root": ROOT,
    }
    options.update(kwargs)
    return DataCleanupDialog(**options)  # type: ignore[arg-type]


class DataCleanupDialogStructureTest(_ImmediateLoaderCase):
    """범주, 항목, 프리셋 구성과 초기 상태"""

    def test_all_categories_and_checkboxes_exist(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertEqual(dialog.windowTitle(), "데이터 정리 및 초기화")
        self.assertEqual(len(dialog._sections), 4)
        self.assertEqual(len(dialog._select_all), 4)
        self.assertEqual(len(dialog._badges), 20)
        self.assertEqual(tuple(dialog._checkboxes), ALL_CLEANUP_ITEM_KEYS)
        for key, checkbox in dialog._checkboxes.items():
            self.assertEqual(checkbox.text(), CLEANUP_ITEM_LABELS[key])
            self.assertIn("원본 영상", checkbox.toolTip())
            self.assertEqual(checkbox.toolTip(), item_tooltip(key))

    def test_preset_pills_match_service_presets(self) -> None:
        dialog = _dialog(_Recorder())
        self.assertEqual(len(dialog._preset_buttons), 6)
        self.assertEqual(tuple(dialog._preset_buttons), tuple(CLEANUP_PRESETS))
        labels = [pill.text() for pill in dialog._preset_buttons.values()]
        self.assertEqual(
            labels,
            [
                "임시 파일만 정리",
                "인코딩 다시 시도",
                "화질 분석만 다시",
                "분석 처음부터 다시",
                "화면 배치만 초기화",
                "전체 초기화",
            ],
        )
        for preset, pill in dialog._preset_buttons.items():
            self.assertEqual(pill.objectName(), "DataCleanupPresetPill")
            self.assertEqual(pill.toolTip(), PRESET_TOOLTIPS[preset])
            self.assertEqual(pill.accessibleName(), f"{CLEANUP_PRESET_LABELS[preset]} 프리셋")
            self.assertFalse(pill.icon().isNull())
        self.assertEqual(
            [pill.property("presetWarning") for pill in dialog._preset_buttons.values()],
            ["false", "false", "false", "false", "false", "true"],
        )

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


class DataCleanupDialogPresetTest(_ImmediateLoaderCase):
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

    def test_preset_pill_highlight_follows_checked_set(self) -> None:
        """체크한 항목 집합이 프리셋과 같을 때만 그 알약이 눌린 모양인지 검증"""
        dialog = _dialog(_Recorder())
        self.assertEqual(
            {preset: pill.property("presetActive") for preset, pill in dialog._preset_buttons.items()},
            {preset: "false" for preset in CLEANUP_PRESETS},
        )
        for preset in CLEANUP_PRESETS:
            with self.subTest(preset=preset):
                dialog.apply_preset(preset)
                active = [
                    key for key, pill in dialog._preset_buttons.items() if pill.property("presetActive") == "true"
                ]
                self.assertEqual(active, [preset])

        dialog._checkboxes["part_files"].setChecked(False)
        self.assertEqual(
            [pill.property("presetActive") for pill in dialog._preset_buttons.values()],
            ["false"] * len(CLEANUP_PRESETS),
        )

    def test_preset_pill_tooltips_are_verbatim(self) -> None:
        """승인한 프리셋 툴팁 문구가 그대로 들어가는지 검증"""
        dialog = _dialog(_Recorder())
        self.assertEqual(
            {preset: pill.toolTip() for preset, pill in dialog._preset_buttons.items()},
            {
                "part_only": (
                    "분류: 인코딩 기록 및 임시 파일\n"
                    "중간에 끊긴 인코딩이 남긴 .part 임시 파일을 휴지통으로 옮깁니다. 휴지통이 없는 드라이브의 파일은 남깁니다."
                ),
                "retry_encode": (
                    "분류: 인코딩 기록 및 임시 파일\n"
                    "임시 파일, 이전 폐기 기록, 인코딩 완료 이력을 지워 같은 영상을 다시 인코딩 대상으로 되돌립니다."
                ),
                "frame_only": (
                    "분류: 분석 캐시 및 영상별 지정\n화질 분석과 검은 여백 감지 결과를 지워 다음 분석 때 다시 계산합니다."
                ),
                "reanalyze_fresh": (
                    "분류: 분석 캐시 및 영상별 지정\n"
                    "영상 분석 캐시, 화질 분석 캐시, 영상별 출력 해상도, 검은 여백 제외, 직접 지정한 활성 화면, 수동 VR 보호 지정을 지웁니다."
                ),
                "reset_layout": (
                    "분류: 화면 배치 및 기록\n"
                    "표 열 순서와 너비, 표시 열, 정렬과 그룹, 창 크기와 위치, 최근 폴더 기억을 기본값으로 되돌립니다."
                ),
                "reset_all": "위의 모든 항목과 설정, 단축키, 예외 규칙까지 기본값으로 되돌립니다. 설정은 되돌릴 수 없습니다.",
            },
        )

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


class DataCleanupDialogScopeTest(_ImmediateLoaderCase):
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


class DataCleanupDialogSelectionTest(_ImmediateLoaderCase):
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


class DataCleanupDialogConfirmTest(_ImmediateLoaderCase):
    """되돌릴 수 없는 항목(설정, 완료 이력, 영상별 직접 지정)의 실행 전 확인"""

    _CONFIRM_TRIGGERS: tuple[CleanupItemKey, ...] = (
        "criteria_settings",
        "encode_output_history",
        "manual_targets",
    )
    _NO_CONFIRM: tuple[CleanupItemKey, ...] = (
        "part_files",
        "discarded_history",
        "probe_cache",
        "frame_cache",
        "crop_exclusions",
        "vr_overrides",
        "column_order",
        "column_widths",
        "column_visibility",
        "table_sort_group",
        "window_geometry",
        "recent_dirs_selection",
    )

    def test_confirm_asked_for_each_irreversible_trigger(self) -> None:
        """되돌릴 수 없는 세 항목은 하나만 골라도 확인을 한 번 거치는지 검증"""
        for key in self._CONFIRM_TRIGGERS:
            with self.subTest(key=key):
                recorder = _Recorder()
                dialog = _dialog(recorder)
                dialog._checkboxes[key].setChecked(True)
                dialog._on_execute()
                self.assertEqual(recorder.confirm_calls, 1)
                self.assertEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))

    def test_confirm_skipped_for_every_other_item(self) -> None:
        """나머지 항목은 확인 없이 바로 실행하는지 검증"""
        for key in self._NO_CONFIRM:
            with self.subTest(key=key):
                recorder = _Recorder()
                dialog = _dialog(recorder)
                dialog._checkboxes[key].setChecked(True)
                dialog._on_execute()
                self.assertEqual(recorder.confirm_calls, 0)
                self.assertEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))

    def test_declining_one_trigger_keeps_dialog_open(self) -> None:
        """확인을 거절하면 창을 닫지 않고 선택을 그대로 두는지 검증"""
        recorder = _Recorder(confirm=False)
        dialog = _dialog(recorder)
        dialog._checkboxes["manual_targets"].setChecked(True)
        dialog._on_execute()
        self.assertEqual(recorder.confirm_calls, 1)
        self.assertNotEqual(dialog.result(), int(DataCleanupDialog.DialogCode.Accepted))
        self.assertEqual(dialog.checked_keys(), ("manual_targets",))

    def test_ask_confirm_text_lists_each_selected_kind(self) -> None:
        """여러 종류를 고르면 한 상자에 종류별 문장이 모두 들어가는지 검증"""
        dialog = _dialog(_Recorder())
        single_kinds: tuple[tuple[CleanupItemKey, ...], ...] = (
            ("criteria_settings",),
            ("encode_output_history",),
            ("manual_targets",),
        )
        expected_lines = (
            "설정 및 규칙 1개 항목을 초기화합니다",
            "인코딩 완료 이력을 지웁니다",
            "영상별 출력 해상도 직접 지정을 지웁니다",
        )
        for keys, line in zip(single_kinds, expected_lines, strict=True):
            with self.subTest(keys=keys):
                dialog._apply_keys(keys)
                with mock.patch.object(
                    QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes
                ) as warn:
                    self.assertTrue(dialog._ask_confirm())
                text = warn.call_args[0][2]
                self.assertIn("되돌릴 수 없습니다", text)
                self.assertTrue(text.endswith("계속할까요?"))
                self.assertIn(line, text)
                self.assertEqual(
                    warn.call_args[0][3],
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                self.assertEqual(warn.call_args[0][4], QMessageBox.StandardButton.No)

        dialog._apply_keys(("criteria_settings", "encode_output_history", "manual_targets", "part_files"))
        with mock.patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.No
        ) as warn:
            self.assertFalse(dialog._ask_confirm())
        text = warn.call_args[0][2]
        self.assertIn("설정 및 규칙 1개 항목을 초기화합니다", text)
        self.assertIn("인코딩 완료 이력을 지웁니다", text)
        self.assertIn("영상별 출력 해상도 직접 지정을 지웁니다", text)
        self.assertEqual(text.count("계속할까요?"), 1)
        self.assertEqual(
            warn.call_args[0][3],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        self.assertEqual(warn.call_args[0][4], QMessageBox.StandardButton.No)

    def test_confirm_text_helper_composes_kinds(self) -> None:
        """확인 문구 조합 함수가 종류별 문장만 모으는지 검증"""
        self.assertEqual(
            dialog_module.confirm_text(()),
            "계속할까요?",
        )
        self.assertEqual(
            dialog_module.confirm_text(("part_files", "column_order")),
            "계속할까요?",
        )
        combined = dialog_module.confirm_text(
            ("encode_output_history", "discarded_history", "manual_targets")
        )
        self.assertNotIn("설정 및 규칙", combined)
        self.assertIn("인코딩 완료 이력", combined)
        self.assertIn("영상별 출력 해상도 직접 지정", combined)
        self.assertTrue(combined.endswith("계속할까요?"))
        settings_and_history = dialog_module.confirm_text(
            ("criteria_settings", "exception_rules", "encode_output_history")
        )
        self.assertIn("설정 및 규칙 2개 항목을 초기화합니다", settings_and_history)
        self.assertIn("인코딩 완료 이력", settings_and_history)
        self.assertNotIn("영상별 출력 해상도 직접 지정", settings_and_history)

    def test_request_confirm_keys_filters_irreversible_items(self) -> None:
        """요청이 확인 대상 항목만 따로 모으는지 검증"""
        request = DataCleanupRequest(
            "all", ("part_files", "encode_output_history", "manual_targets", "criteria_settings")
        )
        self.assertEqual(
            request.confirm_keys,
            ("encode_output_history", "manual_targets", "criteria_settings"),
        )
        self.assertEqual(DataCleanupRequest("all", ()).confirm_keys, ())


class DataCleanupDialogThemeTest(_ImmediateLoaderCase):
    """테마 전환과 미리보기 대체 경로"""

    def test_apply_theme_dark_and_light(self) -> None:
        dialog = _dialog(_Recorder())
        dialog.apply_theme(DARK_PALETTE)
        self.assertTrue(dialog.styleSheet())
        self.assertFalse(dialog.lbl_warning_icon.pixmap().isNull())
        dialog.apply_theme(LIGHT_PALETTE)
        self.assertTrue(dialog.styleSheet())
        self.assertFalse(dialog.lbl_safety_icon.pixmap().isNull())

    def test_safety_note_states_unmovable_parts_are_left(self) -> None:
        """임시 파일 안내가 휴지통이 없는 드라이브의 파일을 남긴다는 사실을 함께 알리는지 검증"""
        dialog = _dialog(_Recorder())
        self.assertIn("휴지통으로 옮겨집니다", dialog.lbl_safety_text.text())
        self.assertIn("지우지 않고 남깁니다", dialog.lbl_safety_text.text())
        self.assertIn("남깁니다", dialog._checkboxes["part_files"].toolTip())

    def test_default_preview_without_fn_or_service(self) -> None:
        """미리보기 함수를 받지 못해도 0건으로 채워지는지 검증"""
        dialog = DataCleanupDialog()
        self.assertEqual(dialog.current_scope(), "all")
        self.assertEqual(dialog._badges["part_files"].text(), "0건")
        dialog.refresh_preview()
        self.assertEqual(dialog._badges["probe_cache"].text(), "0건")

    def test_service_preview_is_used_when_injected(self) -> None:
        """서비스를 주입하면 그 서비스의 미리보기를 현재 작업 폴더로 부르는지 검증"""
        service = mock.Mock(spec=DataCleanupService)
        service.preview.return_value = _previews("current")
        dialog = DataCleanupDialog(service=cast("DataCleanupService", service), current_root=ROOT)
        dialog.refresh_preview()
        service.preview.assert_called_with("current", ROOT)
        self.assertEqual(dialog._badges["probe_cache"].text(), "2건")

    def test_ui_previews_are_applied_without_background_read(self) -> None:
        """화면/설정 항목은 저장 데이터와 달리 백그라운드 결과를 기다리지 않고 바로 채우는지 검증"""
        changed: dict[CleanupItemKey, CleanupItemPreview] = {
            key: CleanupItemPreview(key, 1, 0, True) for key in UI_RESET_CLEANUP_ITEM_KEYS
        }
        dialog = DataCleanupDialog(ui_preview_fn=lambda: changed, current_root=ROOT)
        self.assertEqual(dialog._badges["column_order"].text(), "1건")

class DataCleanupDialogPendingTest(unittest.TestCase):
    """저장 데이터를 읽는 동안의 표시와 실행 잠금, 실패 처리, 취소"""

    def _dialog_with_slow_loader(self, recorder: _Recorder) -> tuple[DataCleanupDialog, list[object]]:
        """스레드를 띄우지 않아 결과가 아직 도착하지 않은 상태의 대화상자"""
        spawned: list[object] = []
        with mock.patch.object(loader_module, "spawn_preview_thread", side_effect=spawned.append):
            dialog = DataCleanupDialog(
                storage_preview_fn=recorder.storage,
                ui_preview_fn=_ui_previews,
                current_root=ROOT,
            )
        return dialog, spawned

    def test_badges_show_loading_and_execute_stays_locked(self) -> None:
        """읽는 동안 저장 데이터 배지는 진행 문구이고, 항목을 골라도 실행 버튼이 잠긴 상태인지 검증"""
        dialog, spawned = self._dialog_with_slow_loader(_Recorder())
        self.assertTrue(dialog.is_loading())
        self.assertEqual(len(spawned), 1)
        for key in STORAGE_CLEANUP_ITEM_KEYS:
            self.assertEqual(dialog._badges[key].text(), LOADING_BADGE_TEXT)
        dialog._checkboxes["probe_cache"].setChecked(True)
        self.assertFalse(dialog.btn_execute.isEnabled())
        dialog._checkboxes["column_order"].setChecked(True)
        self.assertFalse(dialog.btn_execute.isEnabled())

    def test_construction_does_not_read_storage_synchronously(self) -> None:
        """창을 만드는 동안 저장 데이터 미리보기를 화면 스레드에서 부르지 않는지 검증"""
        recorder = _Recorder()
        dialog, spawned = self._dialog_with_slow_loader(recorder)
        self.assertEqual(recorder.scopes, [])
        self.assertEqual(len(spawned), 1)
        self.assertEqual(dialog.current_scope(), "current")

    def test_result_arrival_fills_badges_and_unlocks_execute(self) -> None:
        """결과가 도착하면 저장 데이터 배지를 채우고 실행 버튼을 여는지 검증"""
        dialog, spawned = self._dialog_with_slow_loader(_Recorder())
        dialog._checkboxes["probe_cache"].setChecked(True)
        spawned[0].run()  # type: ignore[attr-defined]
        QApplication.processEvents()
        self.assertFalse(dialog.is_loading())
        self.assertEqual(dialog._badges["part_files"].text(), "2건 / 3.0 MB")
        self.assertTrue(dialog.btn_execute.isEnabled())

    def test_failure_shows_zero_without_blocking(self) -> None:
        """작업 폴더가 없어 실패하면 0건으로 표시하고 실행을 막지 않는지 검증"""
        failing = _Recorder()
        spawned: list[object] = []
        with mock.patch.object(loader_module, "spawn_preview_thread", side_effect=spawned.append):

            def _explode(_scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
                raise ValueError("no root")

            dialog = DataCleanupDialog(
                storage_preview_fn=_explode,
                ui_preview_fn=_ui_previews,
                current_root=ROOT,
            )
        dialog._checkboxes["column_order"].setChecked(True)
        with self.assertLogs(loader_module.logger, level="INFO"):
            spawned[0].run()  # type: ignore[attr-defined]
        QApplication.processEvents()
        self.assertFalse(dialog.is_loading())
        self.assertEqual(dialog._badges["part_files"].text(), "0건")
        self.assertTrue(dialog.btn_execute.isEnabled())
        self.assertEqual(failing.scopes, [])

    def test_closing_cancels_pending_result(self) -> None:
        """창을 닫은 뒤 도착한 결과를 무시하는지 검증"""
        dialog, spawned = self._dialog_with_slow_loader(_Recorder())
        dialog.done(int(DataCleanupDialog.DialogCode.Rejected))
        spawned[0].run()  # type: ignore[attr-defined]
        QApplication.processEvents()
        self.assertTrue(dialog.is_loading())
        self.assertEqual(dialog._badges["probe_cache"].text(), LOADING_BADGE_TEXT)


class DataCleanupDialogLayoutTest(_ImmediateLoaderCase):
    """13pt 글꼴과 좁은 폭에서도 프리셋 알약이 잘리지 않고 줄바꿈되는지"""

    def test_preset_pills_fit_and_wrap_at_13pt(self) -> None:
        dialog = _dialog(_Recorder())
        font = QFont(dialog.font())
        font.setPointSize(13)
        for widget in (dialog, *dialog.findChildren(QWidget)):
            widget.setFont(font)
        dialog.show()
        try:
            dialog.resize(DIALOG_DEFAULT_WIDTH, DIALOG_DEFAULT_HEIGHT)
            QApplication.processEvents()
            for preset, pill in dialog._preset_buttons.items():
                self.assertGreaterEqual(
                    pill.geometry().width(),
                    pill.sizeHint().width(),
                    preset,
                )
            wide = dialog._preset_flow.heightForWidth(dialog._preset_host.width())
            dialog.resize(DIALOG_MIN_WIDTH, 520)
            QApplication.processEvents()
            for preset, pill in dialog._preset_buttons.items():
                self.assertGreaterEqual(
                    pill.geometry().width(),
                    pill.sizeHint().width(),
                    preset,
                )
                self.assertLessEqual(
                    pill.geometry().right(), dialog._preset_host.width(), preset
                )
            narrow = dialog._preset_flow.heightForWidth(dialog._preset_host.width())
            single_line = dialog._preset_flow.heightForWidth(5000)
            self.assertGreater(narrow, single_line)
            self.assertGreaterEqual(wide, narrow)
            self.assertGreaterEqual(dialog._preset_host.height(), narrow)
        finally:
            dialog.close()

    def test_preset_pill_style_uses_palette_tokens(self) -> None:
        """알약 스타일이 팔레트 토큰만 쓰는지 검증"""
        dialog = _dialog(_Recorder())
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                dialog.apply_theme(palette)
                sheet = dialog.styleSheet()
                self.assertIn("QPushButton#DataCleanupPresetPill {", sheet)
                self.assertIn(f"border: 1px solid {palette.btn_border};", sheet)
                self.assertIn(f"color: {palette.text_secondary};", sheet)
                self.assertIn(
                    f'QPushButton#DataCleanupPresetPill[presetActive="true"] {{\n'
                    f"    background-color: {palette.bg_selected};\n"
                    f"    color: {palette.text_primary};\n"
                    f"    border: 1px solid {palette.accent_blue};",
                    sheet,
                )
                self.assertIn(f"background-color: {palette.bg_hover};", sheet)
                self.assertIn(f"color: {palette.badge_pending[1]};", sheet)
                self.assertIn(f"border: 1px solid {palette.badge_pending[2]};", sheet)
                for preset, pill in dialog._preset_buttons.items():
                    self.assertFalse(pill.icon().isNull(), preset)


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

    def test_module_exports_empty_preview_helpers(self) -> None:
        """미리보기 함수를 받지 못할 때 쓰는 빈 미리보기가 저장 데이터와 화면 항목을 모두 덮는지 검증"""
        storage = dialog_module._empty_storage_preview("all")
        self.assertEqual(tuple(storage), STORAGE_CLEANUP_ITEM_KEYS)
        self.assertTrue(all(item.count == 0 for item in storage.values()))
        ui = dialog_module._empty_ui_preview()
        self.assertEqual(tuple(ui), UI_RESET_CLEANUP_ITEM_KEYS)
        self.assertTrue(all(item.count == 0 for item in ui.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
