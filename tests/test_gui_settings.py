import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QMessageBox

from src.core.app_state import AppState, load_app_state, save_app_state
from src.core.config import DEFAULT_CONFIGS, ClassifierConfig
from src.core.state_codec import strip_jsonc_comments
from src.gui.dialogs.setting_row import SettingRow
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.settings_group_card import SettingsGroupCard

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _count_pixels_by_lightness(icon: QIcon, *, min_lightness: int | None = None, max_lightness: int | None = None) -> int:
    img = icon.pixmap(16, 16).toImage()
    count = 0
    for y in range(img.height()):
        for x in range(img.width()):
            color = img.pixelColor(x, y)
            if color.alpha() <= 100:
                continue
            lightness = color.lightness()
            if min_lightness is not None and lightness <= min_lightness:
                continue
            if max_lightness is not None and lightness >= max_lightness:
                continue
            count += 1
    return count


class TestGUISettingsDialogModule(unittest.TestCase):
    """SettingsDialog의 설정값 로드, 실시간 자동 저장, 개별 복원 및 테마 갱신을 검증"""

    def test_settings_dialog_individual_reset_and_save(self) -> None:
        """SettingsDialog의 설정값 로드, 실시간 자동 저장 및 개별 기본값 복원을 검증"""
        custom_cfg = ClassifierConfig(
            base_target_bitrate=3000,
            over_bitrate_ratio=1.50,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")

            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(
                    current_config=custom_cfg,
                    current_root_dir=tmpdir,
                )

                # 초기 로드 값 검증
                self.assertEqual(dlg.spin_base_bitrate.value(), 3000)
                self.assertEqual(dlg.spin_over_ratio.value(), 1.50)

                # 하단 버튼(저장, 취소, 전체 복원)이 모두 제거되었는지 검증
                self.assertFalse(hasattr(dlg, "btn_save"))
                self.assertFalse(hasattr(dlg, "btn_reset_all"))
                self.assertFalse(hasattr(dlg, "btn_close"))

                # 설정 버튼들이 텍스트 없이 아이콘만 사용하는지 검증
                self.assertEqual(dlg.btn_browse_root.text(), "")
                self.assertEqual(dlg._rows["BASE_TARGET_BITRATE"].btn_reset.text(), "")

                # 중복 제목 제거, 콤보박스 비절단 너비, 수치 입력 우측 정렬 일관성 및 경로 툴팁 검증
                self.assertTrue(
                    all(lbl.text() != "환경설정" for lbl in dlg.findChildren(QLabel))
                )
                self.assertGreaterEqual(
                    dlg.combo_theme.width(), dlg.combo_theme.sizeHint().width()
                )
                for spin in dlg.findChildren(QDoubleSpinBox):
                    self.assertTrue(bool(spin.alignment() & Qt.AlignmentFlag.AlignRight))
                self.assertTrue(
                    bool(dlg.spin_base_bitrate.edit.alignment() & Qt.AlignmentFlag.AlignRight)
                )
                self.assertEqual(dlg.edit_root_dir.toolTip(), tmpdir)

                # 실시간 변경 및 자동 저장 검증
                applied_payload: list[tuple[ClassifierConfig, str]] = []
                dlg.settings_applied.connect(
                    lambda cfg, rdir: applied_payload.append((cfg, rdir))
                )

                # 값 변경 시 별도의 저장 버튼 클릭 없이 즉시 저장 및 시그널 방출
                dlg.spin_base_bitrate.setValue(4500)
                self.assertGreaterEqual(len(applied_payload), 1)
                latest_cfg, _ = applied_payload[-1]
                self.assertEqual(latest_cfg.base_target_bitrate, 4500)
                self.assertTrue(os.path.isfile(dummy_state))

                with open(dummy_state, encoding="utf-8") as f:
                    saved_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    saved_json["analysis"]["bitrate_model"]["base_target_kbps"], 4500
                )

                # 개별 '기본값' 버튼 동작 및 실시간 자동 저장 검증
                dlg._rows["BASE_TARGET_BITRATE"].btn_reset.click()
                self.assertEqual(
                    dlg.spin_base_bitrate.value(),
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )
                latest_cfg_reset, _ = applied_payload[-1]
                self.assertEqual(
                    latest_cfg_reset.base_target_bitrate,
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )

                with open(dummy_state, encoding="utf-8") as f:
                    reset_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    reset_json["analysis"]["bitrate_model"]["base_target_kbps"],
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )

    def test_settings_dialog_lineedit_saves_on_editing_finished(self) -> None:
        """SettingsDialog QLineEdit가 textEdited만으로 저장하지 않고 editingFinished 시 data/config.json에 저장함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                # textEdited만으로는 _CUSTOM_DIR을 즉시 data/config.json에 저장하지 않음
                dlg.edit_dir_std.setText("_CUSTOM_DIR")
                with open(dummy_state, encoding="utf-8") as f:
                    before_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    before_json["paths"]["dir_standard_cq22"],
                    DEFAULT_CONFIGS["DIR_STANDARD_CQ22"],
                )
                # editingFinished 시 저장됨
                dlg.edit_dir_std.editingFinished.emit()
                with open(dummy_state, encoding="utf-8") as f:
                    after_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    after_json["paths"]["dir_standard_cq22"],
                    "_CUSTOM_DIR",
                )
                dlg.close()

    def test_settings_dialog_theme_switch_updates_all_icons(self) -> None:
        """설정 모달에서 테마 전환 시 콤보박스 아이콘, 탐색 버튼, 리셋 버튼의 색상이 모두 고대비로 갱신되는지 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                self.addCleanup(dlg.close)

                # 다크 -> 라이트 전환
                dlg.combo_theme.setCurrentIndex(
                    dlg.combo_theme.findData(ThemeManager.MODE_LIGHT)
                )
                self.assertEqual(tm.current_palette.name, "light")

                for i in range(3):
                    dark_px = _count_pixels_by_lightness(
                        dlg.combo_theme.itemIcon(i), max_lightness=50
                    )
                    self.assertGreater(
                        dark_px, 0, f"combo item {i} icon is not dark in light mode"
                    )

                self.assertGreater(
                    _count_pixels_by_lightness(
                        dlg.btn_browse_root.icon(), max_lightness=50
                    ),
                    0,
                )

                for key, row in dlg._rows.items():
                    dark_px_rst = _count_pixels_by_lightness(
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
                    light_px = _count_pixels_by_lightness(
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
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
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
        """전체 기본값 복원 버튼 확인 시 data/config.json·테마·위젯 값이 초기화되고 취소 시 유지됨을 검증"""
        tm = ThemeManager()
        prev_mode = tm.mode
        self.addCleanup(tm.set_mode, prev_mode)
        tm.set_mode(ThemeManager.MODE_DARK)

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, ".rootdir")
            dummy_state = os.path.join(tmpdir, "data", "config.json")
            with (
                patch("src.core.config.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                initial_state = AppState(
                    config=ClassifierConfig(
                        base_target_bitrate=4200,
                        over_bitrate_ratio=1.65,
                    ),
                    root_dir=tmpdir,
                    recent_dirs=[tmpdir],
                    exception_tokens=["@h264"],
                    scan_mode="full",
                    filter_search="clip",
                    filter_zone="OVER",
                    filter_target="ENCODE_ONLY",
                    theme_mode=ThemeManager.MODE_DARK,
                )
                save_app_state(initial_state, sync_rootdir=True)

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
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )
                self.assertAlmostEqual(
                    dlg.spin_over_ratio.value(),
                    float(DEFAULT_CONFIGS["THRESHOLD_OPTIMAL_BITRATE_RATE"]),
                )
                self.assertEqual(tm.mode, ThemeManager.MODE_SYSTEM)

                reloaded = load_app_state()
                self.assertEqual(reloaded.root_dir, tmpdir)
                self.assertEqual(reloaded.recent_dirs, [tmpdir])
                self.assertEqual(reloaded.exception_tokens, [])
                self.assertEqual(reloaded.scan_mode, "fast")
                self.assertEqual(reloaded.filter_search, "")
                self.assertEqual(reloaded.filter_zone, "ALL")
                self.assertEqual(reloaded.filter_target, "ALL")
                self.assertEqual(reloaded.theme_mode, "system")


if __name__ == "__main__":
    unittest.main(verbosity=2)
