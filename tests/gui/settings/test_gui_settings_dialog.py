"""설정 대화상자의 저장, 기본값 초기화, 테마 갱신과 독립 창 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QMessageBox, QPushButton, QWidget

from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    DEFAULT_ENCODE,
    DEFAULT_SAVINGS,
    DEFAULT_THRESHOLDS,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    PathsConfig,
    ThresholdsConfig,
)
from src.core.paths import ROOTDIR_FILENAME, get_root_dir
from src.core.settings.app_state import CONFIG_FILENAME, DATA_DIR_NAME, AppState, load_app_state, save_app_state
from src.core.settings.ui_state import UiFilterState, UiState
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager
from tests._gui_settings_base import GuiSettingsTestCase, count_pixels_by_lightness

_APP = QApplication.instance() or QApplication([])


class TestGUISettingsDialog(GuiSettingsTestCase):
    """설정 대화상자의 저장과 초기화, 테마 갱신 검증"""

    def test_sync_preview_settings_keeps_the_compare_top_bar_collapsed(self) -> None:
        """미리 보기 설정 동기화가 화질 비교 상단 바 접힘 상태를 옛 값으로 덮지 않는지 검증"""
        dialog, _root, _path = self._isolated_dialog()
        ui = dialog._app_state.ui.copy()
        ui.compare_top_bar_collapsed = True
        dialog.sync_preview_settings(ui)
        self.assertTrue(dialog._app_state.ui.compare_top_bar_collapsed)

    def test_max_resolution_choices_order(self) -> None:
        """최대 해상도 선택지는 제한 없음 다음 4K부터 360p 순임을 검증"""
        dialog, _root, _path = self._isolated_dialog()
        combo = dialog.combo_max_resolution
        self.assertEqual(combo.itemText(0), "제한 없음 (기본)")
        self.assertEqual(combo.itemData(0), "none")
        self.assertEqual(combo.itemText(1), "4K (3840x2160)")
        self.assertEqual(combo.itemData(combo.count() - 1), "360p")

    def test_dialog_buttons_do_not_take_enter_as_default(self) -> None:
        """입력칸에서 Enter를 눌러도 전체 초기화 등 버튼이 기본 버튼으로 눌리지 않도록 자동 기본 설정이 꺼져 있음"""
        dialog, _root, _path = self._isolated_dialog()
        buttons = dialog.findChildren(QPushButton)
        self.assertTrue(buttons)
        for button in buttons:
            self.assertFalse(button.autoDefault(), button.text())
            self.assertFalse(button.isDefault(), button.text())

    def test_settings_dialog_loads_values_and_uses_icon_only_controls(self) -> None:
        """초기 값 로드, 실시간 임계값 행 분리, 하단 버튼 부재, 아이콘 전용 버튼, 입력 정렬을 검증"""
        dlg, root, _ = self._isolated_dialog()
        self.assertEqual(dlg.spin_base_bitrate.value(), 3000)
        self.assertAlmostEqual(dlg.spin_kf_increase.value(), 0.08)
        self.assertNotIn("thresholds.over_bitrate_ratio", dlg._rows)
        self.assertNotIn("savings.min_saved_mb", dlg._rows)
        self.assertIn("analysis.thresholds.keyframe_1s_increase_rate", dlg._rows)
        for removed in ("btn_save", "btn_reset_all", "btn_close"):
            self.assertFalse(hasattr(dlg, removed), removed)

        self.assertEqual(dlg.btn_browse_root.text(), "")
        self.assertEqual(dlg._rows["analysis.bitrate_model.base_target_kbps"].btn_reset.text(), "")
        self.assertTrue(all(lbl.text() != "환경설정" for lbl in dlg.findChildren(QLabel)))
        self.assertGreaterEqual(dlg.combo_theme.width(), dlg.combo_theme.sizeHint().width())
        right = Qt.AlignmentFlag.AlignRight
        self.assertTrue(all(spin.alignment() & right for spin in dlg.findChildren(QDoubleSpinBox)))
        self.assertTrue(dlg.spin_base_bitrate.edit.alignment() & right)
        self.assertEqual(dlg.edit_root_dir.toolTip(), root)

    def test_settings_dialog_autosaves_changes_and_resets_single_row(self) -> None:
        """값 변경이 저장 버튼 없이 시그널과 파일에 반영되고, 개별 기본값 버튼이 같은 경로로 복원됨을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        dlg.spin_base_bitrate.setValue(4500)
        self.assertEqual(applied[-1].bitrate_model.base_target_kbps, 4500)
        self.assertEqual(self._read_saved(state_path)["analysis"]["bitrate_model"]["base_target_kbps"], 4500)

        dlg._rows["analysis.bitrate_model.base_target_kbps"].btn_reset.click()
        default_kbps = DEFAULT_BITRATE_MODEL.base_target_kbps
        self.assertEqual(dlg.spin_base_bitrate.value(), default_kbps)
        self.assertEqual(applied[-1].bitrate_model.base_target_kbps, default_kbps)
        self.assertEqual(self._read_saved(state_path)["analysis"]["bitrate_model"]["base_target_kbps"], default_kbps)

    def test_settings_dialog_edits_baseline_cq_and_resets_row(self) -> None:
        """화질 기준값 행의 변경이 설정 파일과 적용 신호에 반영되고, 기본값 버튼이 기본 22.0으로 복원함을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))
        self.assertAlmostEqual(dlg.spin_baseline_cq.value(), DEFAULT_ENCODE.baseline_cq)

        dlg.spin_baseline_cq.setValue(23.9)
        self.assertAlmostEqual(applied[-1].encode.baseline_cq, 23.9)
        self.assertAlmostEqual(self._read_saved(state_path)["analysis"]["encode"]["baseline_cq"], 23.9)

        dlg._rows["analysis.encode.baseline_cq"].btn_reset.click()
        self.assertAlmostEqual(dlg.spin_baseline_cq.value(), DEFAULT_ENCODE.baseline_cq)
        self.assertAlmostEqual(applied[-1].encode.baseline_cq, DEFAULT_ENCODE.baseline_cq)

    def test_settings_dialog_saves_and_resets_display_format(self) -> None:
        """표 표시 서식(FPS 정밀도, 정수 표기, 용량 단위, 파일 이름 정렬) 저장과 개별 복원을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        dlg.spin_fps_decimals.setValue(3)
        dlg.combo_fps_trim_whole.setCurrentIndex(dlg.combo_fps_trim_whole.findData(False))
        dlg.combo_size_unit.setCurrentIndex(dlg.combo_size_unit.findData("auto"))
        dlg.combo_filename_align.setCurrentIndex(dlg.combo_filename_align.findData("right"))
        self.assertEqual(applied[-1].display.fps_decimals, 3)
        self.assertFalse(applied[-1].display.fps_trim_whole)
        self.assertEqual(applied[-1].display.size_unit, "auto")
        self.assertEqual(applied[-1].display.filename_align, "right")
        saved = self._read_saved(state_path)["display"]
        self.assertEqual(
            (saved["fps_decimals"], saved["fps_trim_whole"], saved["size_unit"], saved["filename_align"]),
            (3, False, "auto", "right"),
        )

        dlg._rows["display.size_unit"].btn_reset.click()
        self.assertEqual(dlg.combo_size_unit.currentData(), "auto")
        dlg._rows["display.filename_align"].btn_reset.click()
        self.assertEqual(dlg.combo_filename_align.currentData(), "left")
        self.assertEqual(applied[-1].display.filename_align, "left")

    def test_settings_dialog_saves_and_resets_force_legacy_compat(self) -> None:
        """구형 코덱 호환 재인코딩 콤보박스의 저장, data/config.json 반영 및 개별 복원을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        self.assertIs(dlg.combo_force_legacy_compat.currentData(), False)
        dlg.combo_force_legacy_compat.setCurrentIndex(
            dlg.combo_force_legacy_compat.findData(True)
        )
        self.assertTrue(applied[-1].encode.force_legacy_compat)
        self.assertTrue(self._read_saved(state_path)["analysis"]["encode"]["force_legacy_compat"])

        dlg._rows["encode.force_legacy_compat"].btn_reset.click()
        self.assertIs(dlg.combo_force_legacy_compat.currentData(), False)
        self.assertFalse(applied[-1].encode.force_legacy_compat)
        self.assertFalse(self._read_saved(state_path)["analysis"]["encode"]["force_legacy_compat"])

    def test_settings_dialog_has_no_preview_backend_choice(self) -> None:
        """미리 보기 방식 선택 행과 위젯이 남아 있지 않은지 검증"""
        dlg, _, _state_path = self._isolated_dialog()
        self.assertNotIn("ui.preview_backend", dlg._rows)
        self.assertFalse(hasattr(dlg, "combo_preview_backend"))
        self.assertFalse(hasattr(dlg, "preview_backend_changed"))

    def test_settings_dialog_saves_and_resets_auto_downscale_by_bitrate(self) -> None:
        """비트레이트 맞춤 축소 콤보박스의 기본값, 저장, data/config.json 반영 및 개별 복원을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        self.assertIs(dlg.combo_auto_downscale_by_bitrate.currentData(), False)
        dlg.combo_auto_downscale_by_bitrate.setCurrentIndex(
            dlg.combo_auto_downscale_by_bitrate.findData(True)
        )
        self.assertTrue(applied[-1].encode.auto_downscale_by_bitrate)
        self.assertTrue(self._read_saved(state_path)["analysis"]["encode"]["auto_downscale_by_bitrate"])

        dlg._rows["encode.auto_downscale_by_bitrate"].btn_reset.click()
        self.assertIs(dlg.combo_auto_downscale_by_bitrate.currentData(), False)
        self.assertFalse(applied[-1].encode.auto_downscale_by_bitrate)
        self.assertFalse(self._read_saved(state_path)["analysis"]["encode"]["auto_downscale_by_bitrate"])

    def test_settings_dialog_saves_and_resets_frame_analysis_mode(self) -> None:
        """화질 분석 방식 콤보박스의 기본값, 저장, data/config.json 반영 및 개별 복원을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        self.assertEqual(dlg.combo_frame_analysis_mode.currentData(), "balanced")
        dlg.combo_frame_analysis_mode.setCurrentIndex(
            dlg.combo_frame_analysis_mode.findData("precise")
        )
        self.assertEqual(applied[-1].encode.frame_analysis_mode, "precise")
        self.assertEqual(
            self._read_saved(state_path)["analysis"]["encode"]["frame_analysis_mode"], "precise"
        )

        dlg._rows["encode.frame_analysis_mode"].btn_reset.click()
        self.assertEqual(dlg.combo_frame_analysis_mode.currentData(), "balanced")
        self.assertEqual(applied[-1].encode.frame_analysis_mode, "balanced")
        self.assertEqual(
            self._read_saved(state_path)["analysis"]["encode"]["frame_analysis_mode"], "balanced"
        )

    def test_settings_dialog_saves_and_resets_crop_black_bars(self) -> None:
        """검은 여백 잘라내기 콤보박스의 기본값, 저장, data/config.json 반영 및 개별 복원을 검증"""
        dlg, _, state_path = self._isolated_dialog()
        applied: list[ClassifierConfig] = []
        dlg.settings_applied.connect(lambda cfg, _root: applied.append(cfg))

        self.assertIs(dlg.combo_crop_black_bars.currentData(), True)
        dlg.combo_crop_black_bars.setCurrentIndex(dlg.combo_crop_black_bars.findData(False))
        self.assertFalse(applied[-1].encode.crop_black_bars)
        self.assertFalse(self._read_saved(state_path)["analysis"]["encode"]["crop_black_bars"])

        dlg._rows["encode.crop_black_bars"].btn_reset.click()
        self.assertIs(dlg.combo_crop_black_bars.currentData(), True)
        self.assertTrue(applied[-1].encode.crop_black_bars)
        self.assertTrue(self._read_saved(state_path)["analysis"]["encode"]["crop_black_bars"])

    def test_settings_dialog_theme_switch_updates_all_icons(self) -> None:
        """설정 모달에서 테마 전환 시 콤보박스 아이콘, 탐색 버튼, 리셋 버튼의 색상이 모두 고대비로 갱신되는지 검증"""
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

                # 다크 -> 라이트 전환
                dlg.combo_theme.setCurrentIndex(
                    dlg.combo_theme.findData(ThemeManager.MODE_LIGHT)
                )
                self.assertEqual(tm.current_palette.name, "light")

                for i in range(3):
                    dark_px = count_pixels_by_lightness(
                        dlg.combo_theme.itemIcon(i), max_lightness=50
                    )
                    self.assertGreater(
                        dark_px, 0, f"combo item {i} icon is not dark in light mode"
                    )

                self.assertGreater(
                    count_pixels_by_lightness(
                        dlg.btn_browse_root.icon(), max_lightness=50
                    ),
                    0,
                )

                for key, row in dlg._rows.items():
                    dark_px_rst = count_pixels_by_lightness(
                        row.btn_reset.icon(), max_lightness=50
                    )
                    self.assertGreater(
                        dark_px_rst,
                        0,
                        f"row {key} btn_reset icon is not dark in light mode",
                    )

                # 라이트 -> 다크 전환
                dlg.combo_theme.setCurrentIndex(
                    dlg.combo_theme.findData(ThemeManager.MODE_DARK)
                )
                self.assertEqual(tm.current_palette.name, "dark")

                for i in range(3):
                    light_px = count_pixels_by_lightness(
                        dlg.combo_theme.itemIcon(i), min_lightness=200
                    )
                    self.assertGreater(
                        light_px, 0, f"combo item {i} icon is not light in dark mode"
                    )

    def test_settings_dialog_single_theme_update_per_widget(self) -> None:
        """테마 전환 시 SettingsDialog, SettingsGroupCard, CollapsibleSection, SettingRow의 apply_theme가 중복 호출되지 않고 1회씩만 실행됨을 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        calls: dict[str, int] = {"dlg": 0, "card": 0, "section": 0, "row": 0}
        target_ids: dict[str, set[int]] = {
            "dlg": set(),
            "card": set(),
            "section": set(),
            "row": set(),
        }
        orig_dlg = SettingsDialog.apply_theme
        orig_card = SettingsGroupCard.apply_theme
        orig_sec = CollapsibleSection.apply_theme
        orig_row = SettingRow.apply_theme

        def track_dlg(self_arg: SettingsDialog, palette: ThemePalette) -> None:
            if id(self_arg) in target_ids["dlg"]:
                calls["dlg"] += 1
            orig_dlg(self_arg, palette)

        def track_card(self_arg: SettingsGroupCard, palette: ThemePalette) -> None:
            if id(self_arg) in target_ids["card"]:
                calls["card"] += 1
            orig_card(self_arg, palette)

        def track_sec(self_arg: CollapsibleSection, palette: ThemePalette) -> None:
            if id(self_arg) in target_ids["section"]:
                calls["section"] += 1
            orig_sec(self_arg, palette)

        def track_row(self_arg: SettingRow, palette: ThemePalette) -> None:
            if id(self_arg) in target_ids["row"]:
                calls["row"] += 1
            orig_row(self_arg, palette)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.settings.app_state.get_state_path", return_value=dummy_state),
                patch.object(SettingsDialog, "apply_theme", track_dlg),
                patch.object(SettingsGroupCard, "apply_theme", track_card),
                patch.object(CollapsibleSection, "apply_theme", track_sec),
                patch.object(SettingRow, "apply_theme", track_row),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                target_ids["dlg"].add(id(dlg))
                target_ids["card"].update(id(c) for c in dlg._cards)
                target_ids["section"].add(id(dlg.section_advanced))
                target_ids["row"].update(id(r) for r in dlg._rows.values())

                tm.set_mode(ThemeManager.MODE_LIGHT)
                self.assertEqual(calls["dlg"], 1)
                self.assertEqual(calls["card"], len(target_ids["card"]))
                self.assertEqual(calls["section"], 1)
                self.assertEqual(calls["row"], len(target_ids["row"]))
                dlg.close()

    def test_settings_dialog_reset_defaults_persists_unified_state(self) -> None:
        """전체 기본값 복원 버튼 확인 시 data/config.json, 테마, 위젯 값이 초기화되고 취소 시 유지됨을 검증"""
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
                    paths=PathsConfig(
                        recent_dirs=[tmpdir],
                    ),
                    analysis=AnalysisConfig(
                        thresholds=ThresholdsConfig(over_bitrate_ratio=1.65),
                        bitrate_model=BitrateModelConfig(base_target_kbps=4200),
                    ),
                    ui=UiState(
                        theme_mode=ThemeManager.MODE_DARK,
                        analyze_mode="full",
                        exception_tokens=["@h264"],
                        filters=UiFilterState(
                            search="clip",
                        ),
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
                self.assertEqual(dlg.spin_base_bitrate.value(), 4200)

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dlg.btn_reset_defaults.click()

                self.assertEqual(len(reset_events), 1)
                self.assertEqual(
                    dlg.spin_base_bitrate.value(),
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

    def test_settings_dialog_no_orphan_window_flash_during_init(self) -> None:
        """설정 창 생성 중 부모 없는 임시 최상위 위젯이 Show/WinIdChange 깜박임을 유발하지 않음을 검증"""
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
                        flash_events.append(
                            (a1.type().name, type(a0).__name__, a0.objectName())
                        )
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
