import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt6.QtGui import QIcon, QWheelEvent
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QMessageBox, QWidget

from src.core.app_state import (
    CONFIG_FILENAME,
    DATA_DIR_NAME,
    AppState,
    ensure_default_config_files,
    load_app_state,
    save_app_state,
)
from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    DEFAULT_EFFICIENCY,
    DEFAULT_PATHS,
    DEFAULT_THRESHOLDS,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    EfficiencyConfig,
    PathsConfig,
    ThresholdsConfig,
)
from src.core.paths import ROOTDIR_FILENAME, get_root_dir
from src.core.state_codec import strip_jsonc_comments
from src.core.ui_state import UiFilterState, UiState
from src.gui.dialogs.criteria_tuner_panel import CriteriaTunerPanel
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
            thresholds=ThresholdsConfig(keyframe_1s_overhead_rate=0.08),
            bitrate_model=BitrateModelConfig(base_target_kbps=3000),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                dlg = SettingsDialog(
                    current_config=custom_cfg,
                    current_root_dir=tmpdir,
                )

                # 초기 로드 값 및 튜너 분리(6개 실시간 임계값 미포함) 검증
                self.assertEqual(dlg.spin_base_bitrate.value(), 3000)
                self.assertNotIn("thresholds.over_bitrate_ratio", dlg._rows)
                self.assertNotIn("efficiency.min_saved_mb", dlg._rows)
                self.assertIn("analysis.thresholds.keyframe_1s_overhead_rate", dlg._rows)
                self.assertAlmostEqual(dlg.spin_kf_overhead.value(), 0.08)

                # 하단 버튼(저장, 취소, 전체 복원)이 모두 제거되었는지 검증
                self.assertFalse(hasattr(dlg, "btn_save"))
                self.assertFalse(hasattr(dlg, "btn_reset_all"))
                self.assertFalse(hasattr(dlg, "btn_close"))

                # 설정 버튼들이 텍스트 없이 아이콘만 사용하는지 검증
                self.assertEqual(dlg.btn_browse_root.text(), "")
                self.assertEqual(
                    dlg._rows["analysis.bitrate_model.base_target_kbps"].btn_reset.text(),
                    "",
                )

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
                self.assertEqual(latest_cfg.bitrate_model.base_target_kbps, 4500)
                self.assertTrue(os.path.isfile(dummy_state))

                with open(dummy_state, encoding="utf-8") as f:
                    saved_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    saved_json["analysis"]["bitrate_model"]["base_target_kbps"], 4500
                )

                # 개별 '기본값' 버튼 동작 및 실시간 자동 저장 검증
                dlg._rows["analysis.bitrate_model.base_target_kbps"].btn_reset.click()
                self.assertEqual(
                    dlg.spin_base_bitrate.value(),
                    DEFAULT_BITRATE_MODEL.base_target_kbps,
                )
                latest_cfg_reset, _ = applied_payload[-1]
                self.assertEqual(
                    latest_cfg_reset.bitrate_model.base_target_kbps,
                    DEFAULT_BITRATE_MODEL.base_target_kbps,
                )

                with open(dummy_state, encoding="utf-8") as f:
                    reset_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    reset_json["analysis"]["bitrate_model"]["base_target_kbps"],
                    DEFAULT_BITRATE_MODEL.base_target_kbps,
                )

                # 공통 표 표시 서식(정밀도, FPS 정수 표기, 용량 단위) 저장 및 복원 검증
                dlg.spin_fps_decimals.setValue(3)
                dlg.combo_fps_trim_whole.setCurrentIndex(
                    dlg.combo_fps_trim_whole.findData(False)
                )
                dlg.combo_size_unit.setCurrentIndex(
                    dlg.combo_size_unit.findData("auto")
                )
                latest_display_cfg, _ = applied_payload[-1]
                self.assertEqual(latest_display_cfg.display.fps_decimals, 3)
                self.assertFalse(latest_display_cfg.display.fps_trim_whole)
                self.assertEqual(latest_display_cfg.display.size_unit, "auto")
                with open(dummy_state, encoding="utf-8") as f:
                    disp_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(disp_json["display"]["fps_decimals"], 3)
                self.assertFalse(disp_json["display"]["fps_trim_whole"])
                self.assertEqual(disp_json["display"]["size_unit"], "auto")

                dlg._rows["display.size_unit"].btn_reset.click()
                self.assertEqual(dlg.combo_size_unit.currentData(), "mb")

    def test_settings_dialog_lineedit_saves_on_editing_finished(self) -> None:
        """SettingsDialog QLineEdit가 textEdited만으로 저장하지 않고 editingFinished 시 data/config.json에 저장함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                ensure_default_config_files()
                dlg = SettingsDialog(current_root_dir=tmpdir)
                # textEdited만으로는 _CUSTOM_DIR을 즉시 data/config.json에 저장하지 않음
                dlg.edit_dir_std.setText("_CUSTOM_DIR")
                with open(dummy_state, encoding="utf-8") as f:
                    before_json = json.loads(strip_jsonc_comments(f.read()))
                self.assertEqual(
                    before_json["paths"]["dir_standard_cq22"],
                    DEFAULT_PATHS.dir_standard_cq22,
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
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
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
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
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
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
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
                        scan_mode="full",
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
                    reloaded.analysis.efficiency.min_saved_mb,
                    DEFAULT_EFFICIENCY.min_saved_mb,
                )
                self.assertEqual(reloaded.ui.exception_tokens, [])
                self.assertEqual(reloaded.ui.scan_mode, "fast")
                self.assertEqual(reloaded.ui.filters.search, "")
                self.assertEqual(reloaded.ui.theme_mode, "system")

    def test_criteria_tuner_panel_sliders_optional_and_restore(self) -> None:
        """CriteriaTunerPanel 및 통합 SettingsDialog의 2탭 전환, 컴팩트 크기, 휠 가드, 인라인 복원을 검증"""
        panel = CriteriaTunerPanel(
            ThresholdsConfig(
                over_bitrate_ratio=1.45,
                low_bitrate_ratio=0.70,
                keyframe_interval_sec=3.0,
            ),
            EfficiencyConfig(
                min_saved_mb=12.0,
                min_mb_per_min=4.5,
                exempt_saved_mb=80.0,
            ),
        )
        panel.show()
        self.addCleanup(panel.close)

        # UX 일관성: 상단 인라인 복원 버튼, 가로 스크롤바 비활성, 우측 정렬 스핀박스, 툴팁, 슬롯 유지
        self.assertEqual(
            panel.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(panel.btn_reset_all.objectName(), "InlineResetButton")
        row_over = panel.rows["over_bitrate_ratio"]
        self.assertIn("기본값:", row_over.toolTip())
        self.assertIn("허용 범위:", row_over.toolTip())
        self.assertIn("analysis.thresholds.over_bitrate_ratio", row_over.toolTip())
        self.assertTrue(row_over.btn_reset.sizePolicy().retainSizeWhenHidden())

        self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.45, places=2)
        self.assertAlmostEqual(panel.spin_low_ratio.value(), 0.70, places=2)
        self.assertAlmostEqual(panel.spin_kf_threshold.value(), 3.0, places=1)
        self.assertAlmostEqual(panel.spin_min_saved.value(), 12.0, places=1)
        self.assertAlmostEqual(panel.spin_min_mb_per_min.value(), 4.5, places=1)
        self.assertAlmostEqual(panel.spin_exempt_saved.value(), 80.0, places=1)
        assert panel.chk_exempt_saved is not None
        self.assertTrue(panel.chk_exempt_saved.isChecked())

        emitted: list[tuple[ThresholdsConfig, EfficiencyConfig]] = []
        panel.criteria_changed.connect(lambda th, eff: emitted.append((th, eff)))

        # 슬라이더 <-> 스핀박스 동기화 및 즉시 시그널 반영
        row_over.slider.setValue(180)  # slider_scale=100 -> 1.80x
        self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.80, places=2)
        self.assertGreaterEqual(len(emitted), 1)
        self.assertAlmostEqual(emitted[-1][0].over_bitrate_ratio, 1.80, places=2)

        # 마우스 휠 이벤트는 슬라이더, 비포커스 스핀박스 값을 바꾸지 않음
        ev_slider_wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(row_over.slider, ev_slider_wheel)
        self.assertFalse(ev_slider_wheel.isAccepted())
        self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.80, places=2)

        ev_spin_wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(row_over.spin, ev_spin_wheel)
        self.assertFalse(ev_spin_wheel.isAccepted())
        self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.80, places=2)

        # 되돌리기 버튼 표시/숨김 시에도 스핀박스 가로 위치가 변하지 않음
        spin_x_visible = row_over.spin.geometry().x()

        # 선택적 면제 체크박스 해제(0.0) -> 재활성(직전값 복원)
        panel.chk_exempt_saved.setChecked(False)
        self.assertFalse(panel.spin_exempt_saved.isEnabled())
        self.assertAlmostEqual(emitted[-1][1].exempt_saved_mb, 0.0, places=1)
        panel.chk_exempt_saved.setChecked(True)
        self.assertTrue(panel.spin_exempt_saved.isEnabled())
        self.assertAlmostEqual(panel.spin_exempt_saved.value(), 80.0, places=1)
        self.assertAlmostEqual(emitted[-1][1].exempt_saved_mb, 80.0, places=1)

        # 작업 중 잠금/해제
        panel.set_busy(True)
        self.assertFalse(panel.spin_over_ratio.isEnabled())
        self.assertFalse(panel.btn_reset_all.isEnabled())
        panel.set_busy(False)
        self.assertTrue(panel.spin_over_ratio.isEnabled())
        self.assertTrue(panel.btn_reset_all.isEnabled())

        # 개별 초기화(리셋 버튼 숨김) 시 스핀박스 x 좌표 유지 및 전체 복원 검증
        row_over.btn_reset.click()
        self.assertAlmostEqual(
            panel.spin_over_ratio.value(),
            DEFAULT_THRESHOLDS.over_bitrate_ratio,
            places=2,
        )
        _APP.processEvents()
        self.assertFalse(row_over.btn_reset.isVisible())
        self.assertEqual(row_over.spin.geometry().x(), spin_x_visible)

        panel.btn_reset_all.click()
        self.assertAlmostEqual(
            panel.spin_min_saved.value(),
            DEFAULT_EFFICIENCY.min_saved_mb,
            places=1,
        )
        self.assertAlmostEqual(
            panel.spin_exempt_saved.value(),
            DEFAULT_EFFICIENCY.exempt_saved_mb,
            places=1,
        )

        # 통합 SettingsDialog 컴팩트 폭, 탭 전환, 환경 저장 시 판별 기준 보존 검증
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_root = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            dummy_state = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_root),
                patch("src.core.app_state.get_state_path", return_value=dummy_state),
            ):
                unified = SettingsDialog(
                    current_root_dir=tmpdir,
                    active_tab=SettingsDialog.TAB_CRITERIA,
                )
                self.addCleanup(unified.close)
                self.assertEqual(SettingsDialog.TAB_ENVIRONMENT, 0)
                self.assertEqual(SettingsDialog.TAB_CRITERIA, 1)
                self.assertLessEqual(unified.width(), 500)
                self.assertLessEqual(unified.minimumWidth(), 450)
                self.assertEqual(
                    unified.tab_widget.currentIndex(),
                    SettingsDialog.TAB_CRITERIA,
                )
                self.assertEqual(
                    unified.tab_widget.tabText(SettingsDialog.TAB_CRITERIA),
                    "판별 기준",
                )
                self.assertEqual(
                    unified.tab_widget.tabText(SettingsDialog.TAB_ENVIRONMENT),
                    "환경 설정",
                )
                self.assertEqual(unified.btn_reset_defaults.objectName(), "InlineResetButton")
                self.assertEqual(
                    unified.criteria_panel.rows["min_saved_mb"].lbl_title.text(),
                    "최소 총 절감량",
                )
                self.assertEqual(
                    unified.criteria_panel.rows["min_mb_per_min"].lbl_title.text(),
                    "분당 최소 절감량",
                )
                self.assertEqual(
                    unified.criteria_panel.rows["exempt_saved_mb"].lbl_title.text(),
                    "대용량 절감 예외 허용",
                )
                tab_events: list[int] = []
                unified.tab_changed.connect(tab_events.append)
                # 다크 모드에서 탭바 배경이 밝게 뜨지 않고 2차 카드도 컴팩트 폭을 넘지 않는지 검증
                prev_mode = ThemeManager().mode
                self.addCleanup(ThemeManager().set_mode, prev_mode)
                ThemeManager().set_mode(ThemeManager.MODE_DARK)
                unified.show()
                _APP.processEvents()
                viewport = unified.criteria_panel.scroll_area.viewport()
                assert viewport is not None
                for card in unified.criteria_panel.cards:
                    self.assertLessEqual(card.minimumSizeHint().width(), viewport.width())
                tab_bar = unified.tab_widget.tabBar()
                assert tab_bar is not None
                bar_img = tab_bar.grab().toImage()
                self.assertLess(
                    bar_img.pixelColor(bar_img.width() - 6, bar_img.height() // 2).lightness(),
                    60,
                )
                unified.criteria_panel.spin_over_ratio.setValue(3.35)
                unified.set_active_tab(SettingsDialog.TAB_ENVIRONMENT)
                self.assertEqual(tab_events, [SettingsDialog.TAB_ENVIRONMENT])
                self.assertEqual(
                    unified.tab_widget.currentIndex(),
                    SettingsDialog.TAB_ENVIRONMENT,
                )
                reopened = SettingsDialog(
                    current_root_dir=tmpdir,
                    app_state=unified._app_state,
                )
                self.addCleanup(reopened.close)
                self.assertEqual(
                    reopened.tab_widget.currentIndex(),
                    SettingsDialog.TAB_ENVIRONMENT,
                )
                unified.spin_w_hevc.setValue(0.65)
                unified.edit_dir_std.editingFinished.emit()
                self.assertAlmostEqual(
                    unified._cfg.thresholds.over_bitrate_ratio, 3.35, places=2
                )

    def test_settings_dialog_scroll_wheel_guard_and_reset_slot(self) -> None:
        """SettingsDialog의 콤보, 실수/정수 스핀박스 비포커스 휠 차단과 리셋 슬롯 고정을 검증"""
        custom_cfg = ClassifierConfig(
            thresholds=ThresholdsConfig(keyframe_1s_overhead_rate=0.08),
            bitrate_model=BitrateModelConfig(base_target_kbps=3000),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with (
                patch("src.core.app_state.get_state_path", return_value=state_path),
                patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
            ):
                dlg = SettingsDialog(
                    current_config=custom_cfg,
                    current_root_dir=tmpdir,
                    active_tab=SettingsDialog.TAB_ENVIRONMENT,
                )
                dlg.section_advanced.set_expanded(True)
                dlg.show()
                self.addCleanup(dlg.close)
                _APP.processEvents()

                def _wheel_up() -> QWheelEvent:
                    return QWheelEvent(
                        QPointF(10, 10),
                        QPointF(10, 10),
                        QPoint(0, 0),
                        QPoint(0, 120),
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase,
                        False,
                    )

                dlg.clearFocus()
                for widget in (
                    dlg.combo_theme,
                    dlg.spin_w_hevc,
                    dlg.spin_base_bitrate,
                    dlg.combo_size_unit,
                ):
                    widget.clearFocus()

                theme_before = dlg.combo_theme.currentIndex()
                hevc_before = dlg.spin_w_hevc.value()
                kbps_before = dlg.spin_base_bitrate.value()
                unit_before = dlg.combo_size_unit.currentIndex()

                for widget in (
                    dlg.combo_theme,
                    dlg.spin_w_hevc,
                    dlg.spin_base_bitrate,
                    dlg.combo_size_unit,
                ):
                    ev = _wheel_up()
                    QApplication.sendEvent(widget, ev)
                    self.assertFalse(ev.isAccepted())

                self.assertEqual(dlg.combo_theme.currentIndex(), theme_before)
                self.assertAlmostEqual(dlg.spin_w_hevc.value(), hevc_before, places=2)
                self.assertEqual(dlg.spin_base_bitrate.value(), kbps_before)
                self.assertEqual(dlg.combo_size_unit.currentIndex(), unit_before)

                # 내부 QLineEdit에 포커스가 있을 때는 CustomIntegerSpinBox 휠 증감 허용
                dlg.spin_base_bitrate.edit.setFocus()
                _APP.processEvents()
                if dlg.spin_base_bitrate.edit.hasFocus():
                    ev_focused = _wheel_up()
                    QApplication.sendEvent(dlg.spin_base_bitrate, ev_focused)
                    self.assertTrue(ev_focused.isAccepted())
                    self.assertGreater(dlg.spin_base_bitrate.value(), kbps_before)

                # 개별 리셋 버튼 숨김 시 스핀박스 x 좌표가 흔들리지 않음
                row_kf = dlg.findChildren(SettingRow)[0]
                for r in dlg.findChildren(SettingRow):
                    if r.key == "analysis.thresholds.keyframe_1s_overhead_rate":
                        row_kf = r
                        break
                self.assertTrue(row_kf.btn_reset.isVisible())
                self.assertTrue(row_kf.btn_reset.sizePolicy().retainSizeWhenHidden())
                editor_x = row_kf.editor.geometry().x()
                row_kf.btn_reset.click()
                _APP.processEvents()
                self.assertFalse(row_kf.btn_reset.isVisible())
                self.assertEqual(row_kf.editor.geometry().x(), editor_x)

    def test_settings_dialog_progressive_hints_toggle_and_live_examples(self) -> None:
        """쉬운 설명 보기 토글의 영속화, 인라인 힌트 표시 전환 및 실시간 기준 예시 갱신을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with (
                patch("src.core.app_state.get_state_path", return_value=state_path),
                patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                dlg.show()
                self.addCleanup(dlg.close)
                _APP.processEvents()

                # 기본값: 쉬운 설명 표시 활성 + 기준 예시 실시간 계산 (판별 기준 탭)
                dlg.set_active_tab(SettingsDialog.TAB_CRITERIA)
                _APP.processEvents()
                self.assertTrue(dlg.chk_show_hints.isChecked())
                row_over = dlg.criteria_panel.rows["over_bitrate_ratio"]
                self.assertTrue(row_over.lbl_hint.isVisible())
                self.assertTrue(row_over.lbl_example.isVisible())
                initial_example = row_over.lbl_example.text()
                self.assertIn("kbps", initial_example)

                dlg.criteria_panel.spin_over_ratio.setValue(2.00)
                dlg.spin_base_bitrate.setValue(2000)
                _APP.processEvents()
                self.assertIn("4,000 kbps", row_over.lbl_example.text())

                # 환경 탭 SettingRow 인라인 설명도 함께 토글, 저장, 복원됨
                dlg.set_active_tab(SettingsDialog.TAB_ENVIRONMENT)
                dlg.section_advanced.set_expanded(True)
                _APP.processEvents()
                row_kf = dlg._rows["analysis.thresholds.keyframe_1s_overhead_rate"]
                self.assertTrue(row_kf.lbl_hint.isVisible())

                dlg.chk_show_hints.setChecked(False)
                _APP.processEvents()
                self.assertFalse(row_over.lbl_hint.isVisible())
                self.assertFalse(row_over.lbl_example.isVisible())
                self.assertFalse(row_kf.lbl_hint.isVisible())

                reloaded = load_app_state()
                self.assertFalse(reloaded.ui.show_setting_hints)

                reopened = SettingsDialog(current_root_dir=tmpdir, app_state=reloaded)
                reopened.show()
                self.addCleanup(reopened.close)
                _APP.processEvents()
                self.assertFalse(reopened.chk_show_hints.isChecked())
                self.assertFalse(
                    reopened.criteria_panel.rows["over_bitrate_ratio"].lbl_hint.isVisible()
                )
                self.assertFalse(
                    reopened._rows["analysis.thresholds.keyframe_1s_overhead_rate"].lbl_hint.isVisible()
                )

    def test_settings_dialog_encode_speed_persistence_and_system_detect(self) -> None:
        """목표 인코딩 방식, 배속 저장/복원, 방식 전환 시 자동 추천 및 튜너 변경 시 보존을 검증"""
        from src.core.system_probe import SystemSpeedRecommendation

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with (
                patch("src.core.app_state.get_state_path", return_value=state_path),
                patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                dlg.show()
                self.addCleanup(dlg.close)
                _APP.processEvents()

                def fake_recommend(target_codec: str = "hevc_nvenc") -> SystemSpeedRecommendation:
                    speed = 1.9 if target_codec == "libx265" else 4.5
                    return SystemSpeedRecommendation(
                        recommended_speed_factor=speed,
                        encoder_label=target_codec,
                        detail_summary=f"추천: {target_codec} -> {speed:.1f}배속",
                    )

                with patch(
                    "src.gui.dialogs.settings_dialog.detect_recommended_encode_speed",
                    side_effect=fake_recommend,
                ):
                    dlg.btn_detect_encode_speed.click()
                    _APP.processEvents()
                    self.assertAlmostEqual(dlg.spin_encode_speed.value(), 4.5)

                    # 목표 코덱을 CPU x265로 전환하면 해당 코덱 기준 배속이 먼저 반영되어 1회만 저장됨
                    applied_events: list[tuple[str, float]] = []

                    def _capture_applied(cfg: ClassifierConfig, _root: str) -> None:
                        applied_events.append(
                            (cfg.efficiency.encode_codec, cfg.efficiency.encode_speed_factor)
                        )

                    dlg.settings_applied.connect(_capture_applied)
                    x265_idx = dlg.combo_encode_codec.findData("libx265")
                    self.assertGreaterEqual(x265_idx, 0)
                    dlg.combo_encode_codec.setCurrentIndex(x265_idx)
                    _APP.processEvents()

                self.assertEqual(applied_events, [("libx265", 1.9)])

                self.assertAlmostEqual(dlg.spin_encode_speed.value(), 1.9)
                reloaded = load_app_state()
                self.assertEqual(
                    reloaded.to_classifier_config().efficiency.encode_codec, "libx265"
                )
                self.assertAlmostEqual(
                    reloaded.to_classifier_config().efficiency.encode_speed_factor, 1.9
                )

                # 판별 기준 튜너에서 다른 효율 항목을 조정해도 목표 코덱, 배속은 유지
                dlg.criteria_panel.spin_min_saved.setValue(12.0)
                eff_after = dlg.criteria_panel.current_efficiency()
                self.assertEqual(eff_after.encode_codec, "libx265")
                self.assertAlmostEqual(eff_after.encode_speed_factor, 1.9)

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
                    patch("src.core.app_state.get_state_path", return_value=state_path),
                    patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
                ):
                    dlg = SettingsDialog(current_root_dir=tmpdir)
                    self.addCleanup(dlg.close)
                    self.assertEqual(flash_events, [])
            finally:
                _APP.removeEventFilter(watcher)


if __name__ == "__main__":
    unittest.main(verbosity=2)
