"""설정 대화상자의 5범주 배치, 63개 설정 바인딩, 검색, 반응형 폭, 저장과 초기화 검증"""

import os
import tempfile
import unittest
from dataclasses import fields
from typing import Any, cast
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    DEFAULT_SAVINGS,
    DEFAULT_THRESHOLDS,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    DiagnosticsConfig,
    DisplayConfig,
    ExecutionConfig,
    PathsConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.core.diagnostics.settings import DEV_MODE_ENV
from src.core.paths import ROOTDIR_FILENAME, get_root_dir
from src.core.settings.app_state import CONFIG_FILENAME, DATA_DIR_NAME, AppState, load_app_state, save_app_state
from src.core.settings.ui_state import UiFilterState, UiState
from src.gui.settings.setting_row import resolve_field_default
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.settings.settings_field_bindings import (
    SETTING_SECTION_TYPES,
    read_setting_value,
    set_row_value,
    split_setting_key,
)
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.settings_nav import WIDTH_EXPANDED
from src.gui.settings.tuner_slider_row import TunerSliderRow
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.theme import ThemeManager
from tests._gui_settings_base import GuiSettingsTestCase, count_pixels_by_lightness

_APP = QApplication.instance() or QApplication([])


def _general_keys() -> set[str]:
    return {
        "ui.theme_mode",
        "root_dir",
        *(f"display.{f.name}" for f in fields(DisplayConfig)),
        "cui.filename_maxlen",
        *(f"diagnostics.{f.name}" for f in fields(DiagnosticsConfig)),
    }


def _resolution_keys() -> set[str]:
    return {
        "analysis.encode.crop_black_bars",
        "analysis.encode.scale_algorithm",
        "analysis.encode.frame_analysis_mode",
        "analysis.encode.frame_analysis_min_tier",
        "analysis.encode.auto_apply_frame_target",
        "analysis.encode.max_resolution_tier",
        "analysis.encode.auto_downscale_by_bitrate",
        "analysis.encode.roundtrip_vmaf_threshold",
        "analysis.encode.roundtrip_ssim_threshold",
        "analysis.encode.active_area_no_bars_ratio",
    }


def _criteria_keys() -> set[str]:
    return {
        *(f"analysis.thresholds.{f.name}" for f in fields(ThresholdsConfig)),
        *(f"analysis.savings.{f.name}" for f in fields(SavingsConfig)),
        "analysis.encode.baseline_cq",
        "analysis.encode.force_legacy_compat",
        *(f"analysis.codec_weights.{f.name}" for f in fields(CodecWeightsConfig)),
        *(f"analysis.bitrate_model.{f.name}" for f in fields(BitrateModelConfig)),
    }


def _execution_keys() -> set[str]:
    return {
        *(f"execution.{f.name}" for f in fields(ExecutionConfig)),
        "analysis.encode.encode_codec",
        "analysis.encode.encode_speed_factor",
        "analysis.encode.precise_quality_mode",
    }


def _shown(dialog: SettingsDialog, key: str) -> float:
    """행 입력 위젯에 표시된 값 조회(퍼센트 행은 퍼센트 값)"""
    widget = dialog._rows[key].value_widget
    value = getattr(widget, "value", None)
    if value is None:
        raise AssertionError(f"값 위젯이 아닌 행: {key}")
    return float(value())


def _set_row(dialog: SettingsDialog, key: str, value: object) -> None:
    """행 값을 공용 변환 함수로 설정(퍼센트 표시 위젯도 배수로 바꿔 저장)"""
    set_row_value(dialog._rows[key], value)


def _get_row(dialog: SettingsDialog, key: str) -> object:
    """행의 현재 저장 값 조회"""
    return read_setting_value(dialog._cfg, key)


def _change_row(dialog: SettingsDialog, key: str) -> tuple[object, object]:
    """행 하나의 값을 기본값과 다르게 바꾸고 (변경 전, 변경 후) 설정 값을 돌려줌"""
    row = dialog._rows[key]
    before = _get_row(dialog, key)
    if isinstance(row, TunerSliderRow):
        # - 튜너 행은 set_value가 신호를 막으므로 스핀박스를 직접 올려 저장 경로를 태움
        spin = row.spin
        new_value = spin.value() + spin.singleStep()
        spin.setValue(new_value)
        after = _get_row(dialog, key)
        return before, after
    widget = row.value_widget
    if isinstance(widget, QComboBox):
        index = (widget.currentIndex() + 1) % widget.count()
        _set_row(dialog, key, widget.itemData(index))
    elif isinstance(widget, QLineEdit):
        widget.setText(f"{widget.text()}x")
        widget.editingFinished.emit()
    elif hasattr(widget, "singleStep") and isinstance(before, (int, float)) and not isinstance(before, bool):
        # - 퍼센트 표시 위젯은 표시 증감폭을 배율로 나눠 저장 값 기준 증감폭을 구함
        spinner = cast("Any", widget)
        scale = spinner.property("settingScale")
        factor = float(scale) if scale else 1.0
        step = float(spinner.singleStep()) / factor
        new_value = float(before) + step
        _set_row(dialog, key, int(new_value) if isinstance(before, int) else new_value)
    else:
        raise AssertionError(f"값을 바꿀 수 없는 위젯: {key}")
    after = _get_row(dialog, key)
    return before, after


class TestGUISettingsDialog(GuiSettingsTestCase):
    """설정 대화상자의 배치, 바인딩, 검색, 저장과 초기화 검증"""

    def test_rows_cover_every_setting_by_category(self) -> None:
        """범주별 행이 66개 설정(코어 64 + GUI 2)을 빠짐없이 덮는지 검증"""
        dialog, _root, _path = self._isolated_dialog()

        expected = {
            SettingsDialog.TAB_GENERAL: _general_keys(),
            SettingsDialog.TAB_RESOLUTION: _resolution_keys(),
            SettingsDialog.TAB_CRITERIA: _criteria_keys(),
            SettingsDialog.TAB_EXECUTION: _execution_keys(),
        }
        self.assertEqual(len(dialog._rows), 66)
        for page, keys in expected.items():
            with self.subTest(page=page):
                actual = {key for key, row_page in dialog._row_pages.items() if row_page == page}
                self.assertEqual(actual, keys)
        self.assertEqual(set(dialog._rows), set().union(*expected.values()))
        self.assertEqual(
            dialog.shortcuts_panel.matching_count(""), len(dialog.shortcuts_panel._rows)
        )

    def test_every_setting_round_trips_to_the_config(self) -> None:
        """설정 63개를 모두 바꾸면 설정 객체에 반영되고, 화면 값과 저장 값이 같은지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        for key in dialog._rows:
            if split_setting_key(key) is None:
                continue
            with self.subTest(key=key):
                before, after = _change_row(dialog, key)
                self.assertNotEqual(before, after)
                self.assertEqual(after, read_setting_value(dialog._cfg, key))

    def test_empty_text_row_falls_back_to_the_field_default(self) -> None:
        """빈 문자열로 지운 텍스트 행이 그 필드의 기본값으로 저장되는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        for key in ("execution.archive_dirname", "execution.output_dirname", "execution.encode_tag_name"):
            with self.subTest(key=key):
                row = dialog._rows[key]
                widget = row.value_widget
                assert isinstance(widget, QLineEdit)
                widget.setText("")
                widget.editingFinished.emit()
                field = split_setting_key(key)
                assert field is not None
                section, name = field
                default = resolve_field_default(SETTING_SECTION_TYPES[section], name)
                self.assertEqual(read_setting_value(dialog._cfg, key), default)

    def test_percent_rows_store_ratios(self) -> None:
        """퍼센트로 보여 주는 행이 배수로 저장되는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        _set_row(dialog, "analysis.encode.active_area_no_bars_ratio", 0.95)
        self.assertAlmostEqual(dialog._cfg.encode.active_area_no_bars_ratio, 0.95)
        self.assertEqual(_shown(dialog, "analysis.encode.active_area_no_bars_ratio"), 95.0)
        _set_row(dialog, "execution.early_stop_margin", 1.5)
        self.assertAlmostEqual(dialog._cfg.execution.early_stop_margin, 1.5)
        self.assertEqual(_shown(dialog, "execution.early_stop_margin"), 150.0)

    def test_category_tabs_and_entry_points(self) -> None:
        """환경 설정, 판별 기준, 인코딩, 단축키 진입점이 각 범주로 열리는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        titles = [dialog.tab_widget.tabText(i) for i in range(dialog.tab_widget.count())]
        self.assertEqual(
            titles,
            ["일반", "해상도 및 분석", "판별 기준", "인코딩", "단축키", "전체 결과"],
        )
        for page in (
            SettingsDialog.TAB_CRITERIA,
            SettingsDialog.TAB_EXECUTION,
            SettingsDialog.TAB_SHORTCUTS,
        ):
            with self.subTest(page=page):
                dialog.set_active_tab(page)
                self.assertEqual(dialog.tab_widget.currentIndex(), page)
                self.assertEqual(dialog._app_state.ui.layout.settings_tab, page)
        self.assertEqual(SettingsDialog.normalize_tab_index(99), SettingsDialog.TAB_GENERAL)

    def test_responsive_sidebar_switches_at_620_px(self) -> None:
        """기본 720px에서는 158px 사이드바, 620px 미만에서는 52px 아이콘 레일인지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        self.assertEqual(dialog.sidebar.width(), WIDTH_EXPANDED)
        self.assertFalse(dialog.sidebar.is_compact())

        dialog.resize(560, 620)
        _APP.processEvents()
        self.assertEqual(dialog.width(), 560)
        self.assertEqual(dialog.sidebar.width(), 52)
        self.assertTrue(dialog.sidebar.is_compact())
        self.assertEqual(dialog.sidebar.button(2).text(), "판별 기준")
        self.assertEqual(dialog.sidebar.button(2).toolTip(), "판별 기준")

        dialog.resize(720, 620)
        _APP.processEvents()
        self.assertEqual(dialog.sidebar.width(), WIDTH_EXPANDED)

    def test_manual_sidebar_toggle_collapses_and_expands(self) -> None:
        """수동 접기 버튼이 아이콘 레일과 이름 표시를 오가는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        dialog.sidebar.toggle_collapsed()
        self.assertEqual(dialog.sidebar.width(), 52)
        self.assertEqual(dialog.sidebar.btn_toggle.accessibleName(), "범주 이름 펼치기")
        dialog.sidebar.toggle_collapsed()
        self.assertEqual(dialog.sidebar.width(), WIDTH_EXPANDED)
        dialog.resize(560, 620)
        _APP.processEvents()
        dialog.sidebar.toggle_collapsed()
        self.assertEqual(dialog.sidebar.width(), 52)
        self.assertFalse(dialog.sidebar.btn_toggle.isEnabled())


    def test_sidebar_sits_flush_under_the_divider(self) -> None:
        """13pt 큰 글꼴에서도 사이드바가 창 왼쪽과 아래에 밀착하고 내용 칸은 여백을 지키는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        font = QFont(dialog.font())
        font.setPointSize(13)
        for widget in (dialog, *dialog.findChildren(QWidget)):
            widget.setFont(font)
        divider = dialog.findChild(QFrame, "SettingsHeaderDivider")
        assert divider is not None
        for width, height in ((720, 620), (560, 620)):
            with self.subTest(width=width):
                dialog.resize(width, height)
                _APP.processEvents()
                sidebar = dialog.sidebar
                self.assertEqual(sidebar.x(), 0)
                self.assertEqual(sidebar.geometry().bottom(), dialog.rect().bottom())
                self.assertEqual(divider.width(), dialog.width())
                self.assertEqual(divider.height(), 1)
                self.assertEqual(divider.geometry().bottom(), sidebar.geometry().top() - 1)
                self.assertEqual(
                    sidebar.width(), WIDTH_EXPANDED if width >= 620 else 52
                )
                content = dialog.tab_widget.parentWidget()
                assert content is not None
                self.assertEqual(content.x(), sidebar.geometry().right() + 1)
                self.assertEqual(content.geometry().right(), dialog.rect().right())

    def test_sidebar_stylesheet_uses_card_tokens(self) -> None:
        """사이드바와 머리글 구분선 스타일이 카드 바탕과 경계선 토큰만 쓰는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                dialog.apply_theme(palette)
                sheet = dialog.styleSheet()
                self.assertIn(
                    f"QWidget#SettingsSidebar {{\n    background-color: {palette.bg_card};",
                    sheet,
                )
                self.assertIn(
                    f"QFrame#SettingsHeaderDivider {{\n    background-color: {palette.border_subtle};",
                    sheet,
                )
                self.assertIn("border-radius: 6px", sheet)
                self.assertIn(f"background-color: {palette.bg_selected};", sheet)

    def test_sidebar_selection_switches_the_page(self) -> None:
        """사이드바 범주 버튼을 누르면 해당 페이지로 전환되는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()

        dialog.sidebar.button(SettingsDialog.TAB_EXECUTION).click()
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_EXECUTION)
        self.assertTrue(
            dialog.sidebar.button(SettingsDialog.TAB_EXECUTION).isChecked()
        )

    def test_sync_preview_settings_keeps_the_compare_top_bar_collapsed(self) -> None:
        """미리 보기 설정 동기화가 화질 비교 상단 바 접힘 상태를 옛 값으로 덮지 않는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        ui = dialog._app_state.ui.copy()
        ui.compare_top_bar_collapsed = True
        dialog.sync_preview_settings(ui)
        self.assertTrue(dialog._app_state.ui.compare_top_bar_collapsed)

    def test_forced_hidden_folder_rows_are_synced_on_open(self) -> None:
        """설정 창을 열 때 결과 파일 처리로 숨긴 폴더 이름 행이 곧바로 검색 조정자에 반영되는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        hidden = dialog.execution_panel.hidden_row_keys
        self.assertEqual(hidden, frozenset({"execution.output_dirname"}))

        dialog.search_edit.setText("폴더 이름")
        _APP.processEvents()

        self.assertFalse(dialog._rows["execution.archive_dirname"].isHidden())
        self.assertTrue(dialog._rows["execution.output_dirname"].isHidden())
        buttons = dialog.results_view.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertIn("원본 보관 폴더 이름", buttons[0].text())
        self.assertEqual(dialog.sidebar.btn_results.badge.text(), "1")

    def test_forced_hidden_counts_follow_result_policy_while_searching(self) -> None:
        """검색 중 결과 파일 처리를 바꾸면 숨김 행이 배지와 전체 결과 목록에서 빠지거나 돌아오는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.search_edit.setText("폴더 이름")
        _APP.processEvents()
        self.assertEqual(dialog.sidebar.btn_results.badge.text(), "1")

        dialog.execution_panel.combo_result_policy.setCurrentIndex(
            dialog.execution_panel.combo_result_policy.findData("separate_output")
        )
        _APP.processEvents()

        self.assertEqual(dialog.sidebar.btn_results.badge.text(), "1")
        self.assertTrue(dialog._rows["execution.archive_dirname"].isHidden())
        self.assertFalse(dialog._rows["execution.output_dirname"].isHidden())
        buttons = dialog.results_view.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertIn("별도 출력 폴더 이름", buttons[0].text())

    def test_settings_dialog_restores_the_shortcuts_category_after_restart(self) -> None:
        """단축키 범주를 보다 닫으면 앱 상태에 4가 저장되고 다시 열 때 그 범주로 복원되는지 검증"""
        dialog, root, _path = self._isolated_dialog()
        events: list[int] = []
        dialog.tab_changed.connect(events.append)

        dialog.set_active_tab(SettingsDialog.TAB_SHORTCUTS)

        self.assertEqual(events, [SettingsDialog.TAB_SHORTCUTS])
        self.assertEqual(dialog._app_state.ui.layout.settings_tab, SettingsDialog.TAB_SHORTCUTS)
        reopened = SettingsDialog(current_root_dir=root, app_state=dialog._app_state)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)

    def test_results_page_is_never_saved_as_the_settings_tab(self) -> None:
        """전체 결과 페이지를 보는 동안에도 ui.layout.settings_tab에 5가 저장되지 않는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.set_active_tab(SettingsDialog.TAB_CRITERIA)
        events: list[int] = []
        dialog.tab_changed.connect(events.append)

        dialog.set_active_tab(SettingsDialog.TAB_RESULTS)

        self.assertEqual(dialog._app_state.ui.layout.settings_tab, SettingsDialog.TAB_CRITERIA)
        self.assertEqual(events, [])
        self.assertEqual(dialog._last_category, SettingsDialog.TAB_CRITERIA)

    def test_tab_changed_is_silent_for_the_results_page(self) -> None:
        """탭 위젯이 전체 결과로 바뀌어도 상위에 5를 알리지 않는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        events: list[int] = []
        dialog.tab_changed.connect(events.append)

        dialog.tab_widget.setCurrentIndex(SettingsDialog.TAB_RESULTS)
        _APP.processEvents()

        self.assertEqual(events, [])

    def test_shortcut_results_jump_to_the_row_without_overwriting_the_query(self) -> None:
        """전체 결과의 단축키 결과 버튼을 누르면 검색어를 덮지 않고 해당 행으로 스크롤 이동하는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        dialog.search_edit.setText("되돌리기")
        _APP.processEvents()
        query_before = dialog.search_edit.text()

        undo_button = next(
            (
                btn
                for btn in dialog.results_view.findChildren(QPushButton)
                if btn.property("resultKey") == "shortcut:edit.undo"
            ),
            None,
        )
        assert undo_button is not None
        with patch.object(dialog.shortcuts_panel, "reveal_row", wraps=dialog.shortcuts_panel.reveal_row) as reveal:
            undo_button.click()

        reveal.assert_called_once_with("edit.undo")
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)
        self.assertEqual(dialog.search_edit.text(), query_before)
        self.assertTrue(dialog.shortcuts_panel.is_row_visible("edit.undo"))
        self.assertFalse(dialog.shortcuts_panel.is_row_visible("view.show_logs"))

    def test_pin_applies_owner_even_when_the_dialog_is_not_visible_yet(self) -> None:
        """창을 아직 표시하지 않아도 소유자가 있으면 부모 전환으로 앱 범위 고정을 준비하는지 검증"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        dialog = SettingsDialog(current_root_dir=tmp.name, parent=owner)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.isVisible())

        dialog.btn_pin.click()

        self.assertEqual(dialog.parentWidget(), owner)
        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog.btn_pin.isChecked())

        dialog.btn_pin.click()
        self.assertIsNone(dialog.parentWidget())

    def test_create_report_button_path_emits_name_and_opens_the_folder_only_on_success(self) -> None:
        """진단 보고서 만들기가 성공하면 파일 이름을 알리고 폴더를 열며, 실패하면 빈 이름만 알리고 열지 않는지 검증"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        opened: list[bool] = []
        archives = [os.path.join(tmp.name, "diag.zip"), ""]
        dialog = SettingsDialog(
            current_root_dir=tmp.name,
            on_open_diagnostics=lambda: opened.append(True),
            on_create_report=lambda: archives.pop(0),
        )
        self.addCleanup(dialog.close)
        names: list[str] = []
        dialog.diagnostics_report_created.connect(names.append)

        dialog._on_create_report()
        dialog._on_create_report()

        self.assertEqual(names, ["diag.zip", ""])
        self.assertEqual(opened, [True])

    def test_resolution_page_has_no_empty_header_row(self) -> None:
        """복원 버튼 문구가 빈 페이지는 상단 빈 줄 없이 스크롤 본문이 바로 시작하는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.set_active_tab(SettingsDialog.TAB_RESOLUTION)
        dialog.show()
        _APP.processEvents()

        page = dialog.tab_widget.widget(SettingsDialog.TAB_RESOLUTION)
        assert page is not None
        layout = page.layout()
        assert layout is not None
        self.assertEqual(layout.count(), 1)
        first_item = layout.itemAt(0)
        assert first_item is not None
        scroll = dialog._page_scrolls[SettingsDialog.TAB_RESOLUTION]
        self.assertEqual(first_item.widget(), scroll)

    def test_general_page_keeps_the_reset_header_row(self) -> None:
        """복원 버튼이 있는 페이지는 상단 버튼 줄과 스크롤 본문 두 항목을 가지는지 검증"""
        dialog, _root, _path = self._isolated_dialog()

        page = dialog.tab_widget.widget(SettingsDialog.TAB_GENERAL)
        assert page is not None
        layout = page.layout()
        assert layout is not None
        self.assertEqual(layout.count(), 2)
        header_item = layout.itemAt(0)
        assert header_item is not None
        self.assertIsNotNone(header_item.layout())
        self.assertTrue(dialog.btn_reset_defaults.isVisibleTo(page))

    def test_search_hides_empty_categories_and_shows_badges(self) -> None:
        """검색 중 일치 항목이 있는 범주만 남기고 파란 개수 배지를 보이는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        dialog.search_edit.setText("VMAF")
        _APP.processEvents()

        visible = [
            i for i in SettingsDialog.CATEGORY_TABS if not dialog.sidebar.button(i).isHidden()
        ]
        self.assertEqual(visible, [SettingsDialog.TAB_RESOLUTION])
        self.assertEqual(dialog.sidebar.button(SettingsDialog.TAB_RESOLUTION).badge.text(), "2")
        self.assertFalse(dialog.sidebar.btn_results.isHidden())
        self.assertEqual(dialog.sidebar.btn_results.badge.text(), "2")

    def test_search_expands_advanced_and_restores_it_on_clear(self) -> None:
        """접힌 고급 설정 안의 항목이 검색에 걸리면 펼쳐지고, 검색을 비우면 원래 접힘으로 돌아가는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        resolution_advanced = dialog._advanced_sections[1]
        self.assertFalse(resolution_advanced.is_expanded())

        dialog.search_edit.setText("VMAF")
        _APP.processEvents()
        self.assertTrue(resolution_advanced.is_expanded())
        self.assertFalse(dialog._rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())
        self.assertTrue(dialog._rows["analysis.encode.crop_black_bars"].isHidden())

        dialog.search_edit.clear()
        _APP.processEvents()
        self.assertFalse(resolution_advanced.is_expanded())
        self.assertFalse(dialog._rows["analysis.encode.crop_black_bars"].isHidden())
        self.assertTrue(dialog.sidebar.btn_results.isHidden())
        for i in SettingsDialog.CATEGORY_TABS:
            self.assertFalse(dialog.sidebar.button(i).isHidden())

    def test_search_zero_result_hides_every_category(self) -> None:
        """0건 검색이면 모든 범주를 숨기고 전체 결과에 안내만 남기는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        dialog.search_edit.setText("존재하지않는설정검색어")
        _APP.processEvents()

        self.assertTrue(
            all(dialog.sidebar.button(i).isHidden() for i in SettingsDialog.CATEGORY_TABS)
        )
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_RESULTS)
        self.assertEqual(dialog.sidebar.btn_results.badge.text(), "0")
        message = dialog.results_view.findChild(QLabel)
        assert message is not None
        self.assertIn("일치하는 설정이 없습니다", message.text())

    def test_search_results_page_lists_matches_and_jumps(self) -> None:
        """전체 결과 항목을 누르면 그 설정의 범주로 이동하는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        dialog.search_edit.setText("축소 후보 VMAF")
        _APP.processEvents()

        buttons = [
            button
            for button in dialog.results_view.findChildren(QPushButton)
            if "단축키 /" not in button.text()
        ]
        self.assertGreaterEqual(len(buttons), 1)
        buttons[0].click()
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_RESOLUTION)
        self.assertFalse(dialog._rows["analysis.encode.roundtrip_vmaf_threshold"].isHidden())

    def test_search_includes_shortcut_commands(self) -> None:
        """단축키 명령 이름도 통합 검색에 걸리고 결과에서 단축키 범주로 이동하는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        label = dialog.shortcuts_panel._rows["view.show_logs"].spec.label
        dialog.search_edit.setText(label)
        _APP.processEvents()

        self.assertFalse(dialog.sidebar.button(SettingsDialog.TAB_SHORTCUTS).isHidden())
        shortcut_buttons = [
            button
            for button in dialog.results_view.findChildren(QPushButton)
            if "단축키 /" in button.text()
        ]
        self.assertTrue(shortcut_buttons)
        shortcut_buttons[0].click()
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)
        self.assertEqual(dialog.shortcuts_panel.search_edit.text(), label)

    def test_pin_button_states_and_owner_switch(self) -> None:
        """항상 위에 표시 버튼이 접근성 이름과 아이콘을 바꾸고 소유자 전환을 적용하는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.show()
        _APP.processEvents()
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        dialog.set_owner(owner)
        dialog.btn_pin.click()
        self.assertTrue(dialog._pinned)
        self.assertEqual(dialog.btn_pin.accessibleName(), "항상 위에 표시 해제")
        self.assertEqual(dialog.btn_pin.toolTip(), "항상 위에 표시를 풀어 앱의 다른 창 뒤로 갈 수 있게 합니다.")
        self.assertIs(dialog.parentWidget(), owner)
        dialog.btn_pin.click()
        self.assertFalse(dialog._pinned)
        self.assertEqual(dialog.btn_pin.accessibleName(), "항상 위에 표시")
        self.assertIsNone(dialog.parentWidget())

    def test_help_buttons_emit_help_anchor(self) -> None:
        """상단 사용 가이드, 카드 물음표, 고급 설정 내부 기준값 버튼이 같은 도움말 신호를 보내는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        anchors: list[tuple[object, str]] = []
        dialog.help_requested.connect(lambda cfg, anchor: anchors.append((cfg, anchor)))

        dialog.btn_help.click()
        cards = dialog.findChildren(SettingsGroupCard)
        help_card = next(card for card in cards if card.help_anchor == "optimal-bitrate")
        assert help_card.btn_help is not None
        help_card.btn_help.click()
        internal = [
            button
            for button in dialog.findChildren(QPushButton)
            if button.objectName() == "HelpLinkButton" and button.text() == "내부 기준값 보기"
        ]
        internal[0].click()

        self.assertEqual([anchor for _cfg, anchor in anchors], ["", "optimal-bitrate", "internal-thresholds"])
        self.assertIsInstance(anchors[0][0], ClassifierConfig)


    def test_large_font_layout_keeps_rows_inside_the_dialog(self) -> None:
        """13pt 큰 글꼴에서도 기본 폭 720px 안에서 행이 잘리지 않고 배치되는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        dialog.set_active_tab(SettingsDialog.TAB_GENERAL)
        dialog.show()
        _APP.processEvents()
        font = QFont(dialog.font())
        font.setPointSize(13)
        for widget in (dialog, *dialog.findChildren(QWidget)):
            widget.setFont(font)
        dialog.resize(720, 620)
        _APP.processEvents()

        self.assertLessEqual(dialog.minimumSizeHint().width(), 720)
        self.assertEqual(dialog.sidebar.width(), WIDTH_EXPANDED)
        divider = dialog.findChild(QFrame, "SettingsHeaderDivider")
        assert divider is not None
        self.assertLessEqual(divider.geometry().bottom(), dialog.sidebar.geometry().top())
        self.assertEqual(dialog.sidebar.x(), 0)
        self.assertEqual(
            dialog.sidebar.geometry().bottom(), dialog.rect().bottom()
        )

        scroll = dialog._page_scrolls[SettingsDialog.TAB_GENERAL]
        viewport = scroll.viewport()
        assert viewport is not None
        self.assertGreaterEqual(viewport.width(), 380)
        for key in _general_keys():
            row = dialog._rows[key]
            if not row.isVisible():
                continue
            self.assertLessEqual(row.minimumSizeHint().width(), row.width(), key)
            self.assertLessEqual(row.width(), viewport.width(), key)

    def test_autosave_writes_changes_and_settings_applied(self) -> None:
        """행 값을 바꾸면 저장 버튼 없이 signals와 data/config.json에 반영되는지 검증"""
        dialog, _root, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dialog.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        _set_row(dialog, "analysis.bitrate_model.base_target_kbps", 4500)
        self.assertEqual(applied[-1].bitrate_model.base_target_kbps, 4500)
        self.assertEqual(
            self._read_saved(state_path)["analysis"]["bitrate_model"]["base_target_kbps"],
            4500,
        )

        dialog._rows["analysis.bitrate_model.base_target_kbps"].btn_reset.click()
        default_kbps = DEFAULT_BITRATE_MODEL.base_target_kbps
        self.assertEqual(dialog._cfg.bitrate_model.base_target_kbps, default_kbps)
        self.assertEqual(
            self._read_saved(state_path)["analysis"]["bitrate_model"]["base_target_kbps"],
            default_kbps,
        )


    def test_autosave_logs_only_changed_keys_in_developer_mode(self) -> None:
        """환경 변수로 켠 개발자 모드에서 자동 저장이 바뀐 키만 DEBUG로 남기는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        with (
            patch.dict(os.environ, {DEV_MODE_ENV: "1"}, clear=False),
            self.assertLogs("src.diagnostics", level="DEBUG") as logs,
        ):
            _set_row(dialog, "analysis.bitrate_model.base_target_kbps", 4100)
        joined = "\n".join(logs.output)
        self.assertIn("bitrate_model.base_target_kbps: 3000 -> 4100", joined)
        self.assertNotIn("display.size_unit", joined)

    def test_autosave_is_silent_when_developer_mode_is_off(self) -> None:
        """개발자 모드가 꺼져 있으면 자동 저장이 설정 변경 기록을 남기지 않는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        without_dev = {key: value for key, value in os.environ.items() if key != DEV_MODE_ENV}
        with (
            patch.dict(os.environ, without_dev, clear=True),
            self.assertNoLogs("src.diagnostics", level="DEBUG"),
        ):
            _set_row(dialog, "analysis.bitrate_model.base_target_kbps", 4300)

    def test_reset_defaults_persists_unified_state(self) -> None:
        """전체 초기화 확인 시 data/config.json, 테마, 위젯 값이 초기화되고 취소 시 유지됨을 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
            ):
                initial_state = AppState(
                    paths=PathsConfig(recent_dirs=[tmpdir]),
                    analysis=AnalysisConfig(
                        thresholds=ThresholdsConfig(over_bitrate_ratio=1.65),
                        bitrate_model=BitrateModelConfig(base_target_kbps=4200),
                    ),
                    ui=UiState(
                        theme_mode=ThemeManager.MODE_DARK,
                        analyze_mode="full",
                        exception_tokens=["@h264"],
                        filters=UiFilterState(search="clip"),
                    ),
                )
                save_app_state(initial_state, root_dir=tmpdir)

                dlg = SettingsDialog(app_state=initial_state)
                self.addCleanup(dlg.close)
                reset_events: list[AppState] = []
                dlg.settings_reset.connect(reset_events.append)

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.No,
                ):
                    dlg.btn_reset_defaults.click()
                self.assertEqual(len(reset_events), 0)
                self.assertEqual(_shown(dlg, "analysis.bitrate_model.base_target_kbps"), 4200)

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dlg.btn_reset_defaults.click()

                self.assertEqual(len(reset_events), 1)
                self.assertEqual(
                    _shown(dlg, "analysis.bitrate_model.base_target_kbps"),
                    DEFAULT_BITRATE_MODEL.base_target_kbps,
                )
                self.assertEqual(tm.mode, ThemeManager.MODE_SYSTEM)

                reloaded = load_app_state()
                self.assertEqual(get_root_dir(), tmpdir)
                self.assertEqual(reloaded.paths.recent_dirs, [tmpdir])
                self.assertAlmostEqual(
                    reloaded.analysis.thresholds.over_bitrate_ratio,
                    DEFAULT_THRESHOLDS.over_bitrate_ratio,
                )
                self.assertAlmostEqual(
                    reloaded.analysis.savings.min_saved_mb,
                    DEFAULT_SAVINGS.min_saved_mb,
                )
                self.assertEqual(reloaded.ui.exception_tokens, [])
                self.assertEqual(reloaded.ui.analyze_mode, "fast")
                self.assertEqual(reloaded.ui.filters.search, "")
                self.assertEqual(reloaded.ui.theme_mode, "system")


    def test_theme_switch_updates_icons(self) -> None:
        """테마 전환 시 콤보박스, 탐색 버튼, 설정 행 복원 버튼 아이콘이 고대비로 바뀌는지 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                self.addCleanup(dlg.close)
                dlg.combo_theme.setCurrentIndex(dlg.combo_theme.findData(ThemeManager.MODE_LIGHT))
                self.assertEqual(tm.current_palette.name, "light")
                for i in range(3):
                    self.assertGreater(
                        count_pixels_by_lightness(dlg.combo_theme.itemIcon(i), max_lightness=50),
                        0,
                    )
                self.assertGreater(
                    count_pixels_by_lightness(dlg.btn_browse_root.icon(), max_lightness=50),
                    0,
                )
                for key, row in dlg._rows.items():
                    self.assertGreater(
                        count_pixels_by_lightness(row.btn_reset.icon(), max_lightness=50),
                        0,
                        key,
                    )

                dlg.combo_theme.setCurrentIndex(dlg.combo_theme.findData(ThemeManager.MODE_DARK))
                self.assertEqual(tm.current_palette.name, "dark")
                for i in range(3):
                    self.assertGreater(
                        count_pixels_by_lightness(dlg.combo_theme.itemIcon(i), min_lightness=200),
                        0,
                    )

    def test_theme_change_applies_stylesheet_and_icons_once(self) -> None:
        """테마 변경 신호가 설정 창 스타일시트와 아이콘을 한 번에 갱신하는지 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                self.addCleanup(dlg.close)
                self.assertIn(tm.current_palette.bg_card, dlg.styleSheet())

                tm.set_mode(ThemeManager.MODE_LIGHT)

                self.assertIn(tm.current_palette.bg_card, dlg.styleSheet())
                self.assertGreater(
                    count_pixels_by_lightness(dlg.btn_help.icon(), max_lightness=50), 0
                )
                self.assertGreater(
                    count_pixels_by_lightness(dlg.sidebar.btn_toggle.icon(), max_lightness=50),
                    0,
                )
                tm.set_mode(ThemeManager.MODE_DARK)
                self.assertGreater(
                    count_pixels_by_lightness(dlg.btn_help.icon(), min_lightness=200), 0
                )
                self.assertGreater(
                    count_pixels_by_lightness(dlg.sidebar.button(1).icon(), min_lightness=200),
                    0,
                )

    def test_no_orphan_window_flash_during_init(self) -> None:
        """설정 창 생성 중 부모 없는 임시 최상위 위젯이 Show 깜박임을 유발하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            flash_events: list[tuple[str, str, str]] = []

            class _WindowFlashWatcher(QObject):
                def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
                    if (
                        isinstance(a0, QWidget)
                        and a0.isWindow()
                        and a1 is not None
                        and a1.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange)
                    ):
                        flash_events.append((a1.type().name, type(a0).__name__, a0.objectName()))
                    return False

            watcher = _WindowFlashWatcher()
            _APP.installEventFilter(watcher)
            try:
                with (
                    patch("src.core.settings.app_state.get_state_path", return_value=state_path),
                    patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
                ):
                    dlg = SettingsDialog(current_root_dir=tmpdir)
                    self.addCleanup(dlg.close)
                    self.assertEqual(flash_events, [])
            finally:
                _APP.removeEventFilter(watcher)

    def test_dialog_buttons_do_not_take_enter_as_default(self) -> None:
        """입력칸에서 Enter를 눌러도 전체 초기화 등 버튼이 기본 버튼으로 눌리지 않도록 자동 기본 설정이 꺼져 있음"""
        dialog, _root, _path = self._isolated_dialog()
        buttons = dialog.findChildren(QPushButton)
        self.assertTrue(buttons)
        for button in buttons:
            self.assertFalse(button.autoDefault(), button.toolTip())
            self.assertFalse(button.isDefault(), button.toolTip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

