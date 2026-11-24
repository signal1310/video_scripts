"""기준 패널, 입력 위젯 휠 처리, 설정 탭, 진행 힌트, 인코딩 속도와 단축키 탭 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QMessageBox, QWidget

from src.core.config import DEFAULT_SAVINGS, DEFAULT_THRESHOLDS, ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.paths import ROOTDIR_FILENAME
from src.core.settings.app_state import CONFIG_FILENAME, DATA_DIR_NAME, load_app_state
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_GROUPS, SHORTCUT_SPEC_BY_ID
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.settings.shortcut_row import ShortcutCaptureButton
from src.gui.settings.shortcuts_panel import ShortcutsPanel, key_event_to_shortcut_str
from src.gui.theme import ThemeManager
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from tests._gui_helpers import isolate_app_state
from tests._gui_settings_base import GuiSettingsTestCase
from tests._sandbox import reset_sandbox_files

_APP = QApplication.instance() or QApplication([])


class TestGUISettingsPanels(GuiSettingsTestCase):
    """검증"""

    def test_criteria_panel_loads_values_and_syncs_slider_with_signal(self) -> None:
        """초기 설정값 반영, 인라인 복원 버튼과 툴팁 구성, 슬라이더 조작이 스핀박스와 시그널에 즉시 반영됨을 검증"""
        panel = self._tuner_panel()
        self.assertEqual(panel.scroll_area.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(panel.btn_reset_all.objectName(), "InlineResetButton")
        row = panel.rows["over_bitrate_ratio"]
        for text in ("기본값:", "허용 범위:", "analysis.thresholds.over_bitrate_ratio"):
            self.assertIn(text, row.toolTip())
        self.assertTrue(row.btn_reset.sizePolicy().retainSizeWhenHidden())

        for spin, expected in (
            (panel.spin_over_ratio, 1.45),
            (panel.spin_low_ratio, 0.70),
            (panel.spin_kf_threshold, 3.0),
            (panel.spin_min_saved, 12.0),
            (panel.spin_min_mb_per_min, 4.5),
            (panel.spin_exempt_saved, 80.0),
        ):
            self.assertAlmostEqual(spin.value(), expected, places=2)

        emitted: list[tuple[ThresholdsConfig, SavingsConfig]] = []
        panel.criteria_changed.connect(lambda th, savings: emitted.append((th, savings)))
        row.slider.setValue(180)  # slider_scale=100 -> 1.80x
        self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.80, places=2)
        self.assertAlmostEqual(emitted[-1][0].over_bitrate_ratio, 1.80, places=2)

    def test_criteria_panel_ignores_wheel_on_slider_and_spinbox(self) -> None:
        """마우스 휠 이벤트가 슬라이더, 비포커스 스핀박스 값을 바꾸지 않음을 검증"""
        panel = self._tuner_panel()
        row = panel.rows["over_bitrate_ratio"]
        for target in (row.slider, row.spin):
            event = self._wheel_event()
            QApplication.sendEvent(target, event)
            self.assertFalse(event.isAccepted())
            self.assertAlmostEqual(panel.spin_over_ratio.value(), 1.45, places=2)

    def test_criteria_panel_exempt_checkbox_restores_previous_value(self) -> None:
        """대용량 절감 예외를 해제하면 0.0으로 전달되고, 다시 켜면 직전 값이 복원됨을 검증"""
        panel = self._tuner_panel()
        emitted: list[SavingsConfig] = []
        panel.criteria_changed.connect(lambda _th, savings: emitted.append(savings))
        assert panel.chk_exempt_saved is not None

        panel.chk_exempt_saved.setChecked(False)
        self.assertFalse(panel.spin_exempt_saved.isEnabled())
        self.assertAlmostEqual(emitted[-1].exempt_saved_mb, 0.0, places=1)
        panel.chk_exempt_saved.setChecked(True)
        self.assertTrue(panel.spin_exempt_saved.isEnabled())
        self.assertAlmostEqual(panel.spin_exempt_saved.value(), 80.0, places=1)
        self.assertAlmostEqual(emitted[-1].exempt_saved_mb, 80.0, places=1)

    def test_criteria_panel_busy_locks_controls(self) -> None:
        """작업 중에는 입력과 전체 복원 버튼이 잠기고 종료 시 풀림을 검증"""
        panel = self._tuner_panel()
        panel.set_busy(True)
        self.assertFalse(panel.spin_over_ratio.isEnabled())
        self.assertFalse(panel.btn_reset_all.isEnabled())
        panel.set_busy(False)
        self.assertTrue(panel.spin_over_ratio.isEnabled())
        self.assertTrue(panel.btn_reset_all.isEnabled())

    def test_criteria_panel_resets_keep_layout_and_restore_defaults(self) -> None:
        """개별 복원은 버튼을 숨기되 스핀박스 위치를 유지하고, 전체 복원은 모든 기준을 기본값으로 되돌림을 검증"""
        panel = self._tuner_panel()
        panel.resize(900, 900)
        _APP.processEvents()
        row = panel.rows["over_bitrate_ratio"]
        spin_x = row.spin.geometry().x()

        row.btn_reset.click()
        self.assertAlmostEqual(panel.spin_over_ratio.value(), DEFAULT_THRESHOLDS.over_bitrate_ratio, places=2)
        _APP.processEvents()
        self.assertFalse(row.btn_reset.isVisible())
        self.assertEqual(row.spin.geometry().x(), spin_x)

        panel.btn_reset_all.click()
        self.assertAlmostEqual(panel.spin_min_saved.value(), DEFAULT_SAVINGS.min_saved_mb, places=1)
        self.assertAlmostEqual(panel.spin_exempt_saved.value(), DEFAULT_SAVINGS.exempt_saved_mb, places=1)

    def test_restore_defaults_updates_base_snapshots_and_emits_once(self) -> None:
        """기준 초기화가 내부 기본값 사본까지 되돌리고 저장 경로 신호를 한 번만 보내는지 검증"""
        panel = self._tuner_panel()
        criteria_events: list[tuple[object, object]] = []
        changed_events: list[bool] = []
        panel.criteria_changed.connect(lambda th, sv: criteria_events.append((th, sv)))
        panel.changed.connect(lambda: changed_events.append(True))

        panel.restore_defaults()

        self.assertEqual(len(criteria_events), 1)
        # - changed까지 보내면 설정 창이 같은 내용을 두 번 저장하므로 기준 초기화는 criteria_changed만 씀
        self.assertEqual(changed_events, [])
        self.assertAlmostEqual(
            panel.current_thresholds().over_bitrate_ratio,
            DEFAULT_THRESHOLDS.over_bitrate_ratio,
            places=2,
        )
        self.assertAlmostEqual(
            panel.current_savings().min_saved_mb, DEFAULT_SAVINGS.min_saved_mb, places=1
        )
        # - 기본값 사본이 갱신되지 않으면 이후 조정이 기본값 위에 얹혀 엉뚱한 값이 됨
        panel.spin_min_saved.setValue(9.0)
        self.assertAlmostEqual(panel.current_savings().min_saved_mb, 9.0, places=1)
        self.assertAlmostEqual(
            panel.current_savings().min_mb_per_min, DEFAULT_SAVINGS.min_mb_per_min, places=1
        )

    def test_criteria_reset_saves_once_from_the_dialog(self) -> None:
        """설정 창에서 기준 초기화를 누르면 자동 저장이 한 번만 실행되는지 검증"""
        dialog, _root = self._criteria_dialog()
        saves: list[str] = []
        original = type(dialog)._persist

        def counting(self_: object, *, root_dir: str | None = None) -> None:
            saves.append(root_dir or "")
            original(self_, root_dir=root_dir)  # type: ignore[arg-type]

        dialog.criteria_panel.spin_over_ratio.setValue(3.10)
        _APP.processEvents()
        with patch.object(type(dialog), "_persist", counting):
            dialog.criteria_panel.restore_defaults()
            _APP.processEvents()

        self.assertEqual(len(saves), 1)

    def test_tuner_row_exposes_the_shared_setting_row_interface(self) -> None:
        """튜너 행이 저장 키, 제목, 설명, 값 위젯을 설정 행 공용 인터페이스로 제공하는지 검증"""
        panel = self._tuner_panel()
        row = panel.rows["over_bitrate_ratio"]
        self.assertEqual(row.key, "analysis.thresholds.over_bitrate_ratio")
        self.assertEqual(row.title, "과화질 상한 배수")
        self.assertIn("과화질", row.desc)
        self.assertIs(row.value_widget, row.spin)
        self.assertIs(row.editor, row.spin)
        self.assertEqual(panel.setting_rows["analysis.savings.min_saved_mb"], panel.rows["min_saved_mb"])

    def test_selection_basis_change_updates_savings_and_emits(self) -> None:
        """절약 선별 기준을 바꾸면 실시간 예시와 저장 값이 함께 갱신되는지 검증"""
        panel = self._tuner_panel()
        emitted: list[object] = []
        panel.changed.connect(lambda: emitted.append(panel.current_savings()))
        combo = panel.selection_basis_combo
        combo.setCurrentIndex(combo.findData("encode"))
        self.assertEqual(panel.current_savings().selection_basis, "encode")
        self.assertEqual(len(emitted), 1)
        self.assertIn("예상 인코딩 시간 기준", panel.rows["min_mb_per_min"].lbl_example.text())

    def test_busy_state_locks_every_standard_row(self) -> None:
        """작업 중에는 슬라이더가 아닌 기준점 입력 행도 잠기는지 검증"""
        panel = self._tuner_panel()
        panel.set_busy(True)
        for row in panel.standard_rows.values():
            self.assertFalse(row.isEnabled())
        panel.set_busy(False)
        for row in panel.standard_rows.values():
            self.assertTrue(row.isEnabled())

    def test_settings_dialog_tab_layout_titles_and_compact_width(self) -> None:
        """통합 설정 창의 범주 상수, 이름, 기본 폭과 판별 기준 행 제목을 검증"""
        dialog, _root = self._criteria_dialog()
        self.assertEqual(
            (
                SettingsDialog.TAB_GENERAL,
                SettingsDialog.TAB_RESOLUTION,
                SettingsDialog.TAB_CRITERIA,
                SettingsDialog.TAB_EXECUTION,
                SettingsDialog.TAB_SHORTCUTS,
            ),
            (0, 1, 2, 3, 4),
        )
        self.assertEqual(dialog.width(), 720)
        self.assertEqual(dialog.minimumWidth(), 460)
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA)
        self.assertEqual(dialog.tab_widget.tabText(SettingsDialog.TAB_CRITERIA), "판별 기준")
        self.assertEqual(dialog.tab_widget.tabText(SettingsDialog.TAB_GENERAL), "일반")
        self.assertEqual(dialog.tab_widget.tabText(SettingsDialog.TAB_RESOLUTION), "해상도 및 분석")
        self.assertEqual(dialog.btn_reset_defaults.objectName(), "InlineResetButton")
        rows = dialog.criteria_panel.rows
        self.assertEqual(rows["min_saved_mb"].lbl_title.text(), "최소 총 절약량")
        self.assertEqual(rows["min_mb_per_min"].lbl_title.text(), "분당 최소 절약량")
        self.assertEqual(rows["exempt_saved_mb"].lbl_title.text(), "대용량 절약 예외 허용")

    def test_settings_dialog_hides_tab_bar_and_uses_dark_sidebar(self) -> None:
        """탭 줄 대신 좌측 사이드바를 쓰고, 다크 모드에서 사이드바 배경이 어둡게 그려지는지 검증"""
        theme = ThemeManager()
        self.addCleanup(theme.set_mode, theme.mode)
        theme.set_mode(ThemeManager.MODE_DARK)
        dialog, _root = self._criteria_dialog()
        dialog.show()
        _APP.processEvents()

        tab_bar = dialog.tab_widget.tabBar()
        assert tab_bar is not None
        self.assertTrue(tab_bar.isHidden())

        viewport = dialog.criteria_panel.scroll_area.viewport()
        assert viewport is not None
        for card in dialog.criteria_panel.cards:
            self.assertLessEqual(card.minimumSizeHint().width(), viewport.width())

        sidebar_img = dialog.sidebar.grab().toImage()
        self.assertLess(
            sidebar_img.pixelColor(sidebar_img.width() // 2, sidebar_img.height() // 2).lightness(),
            60,
        )

    def test_settings_dialog_tab_switch_emits_and_keeps_edited_criteria(self) -> None:
        """범주 전환이 시그널로 전달되어 다시 열 때 복원되고, 다른 범주 저장 시 편집한 판별 기준이 보존됨을 검증"""
        dialog, root = self._criteria_dialog()
        tab_events: list[int] = []
        dialog.tab_changed.connect(tab_events.append)

        dialog.criteria_panel.spin_over_ratio.setValue(3.35)
        dialog.set_active_tab(SettingsDialog.TAB_GENERAL)
        self.assertEqual(tab_events, [SettingsDialog.TAB_GENERAL])
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_GENERAL)

        reopened = SettingsDialog(current_root_dir=root, app_state=dialog._app_state)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.tab_widget.currentIndex(), SettingsDialog.TAB_GENERAL)

        hevc_row = dialog._rows["analysis.codec_weights.hevc_vp9"]
        assert isinstance(hevc_row, SettingRow)
        hevc_spin = hevc_row.value_widget
        assert isinstance(hevc_spin, QDoubleSpinBox)
        hevc_spin.setValue(0.65)
        self.assertAlmostEqual(dialog._cfg.thresholds.over_bitrate_ratio, 3.35, places=2)

    def test_unfocused_inputs_ignore_wheel_events(self) -> None:
        """포커스가 없는 콤보박스와 실수, 정수 스핀박스는 휠 입력을 무시함을 검증"""
        dlg = self._advanced_dialog()
        hevc_spin = dlg._rows["analysis.codec_weights.hevc_vp9"].value_widget
        bitrate_spin = dlg._rows["analysis.bitrate_model.base_target_kbps"].value_widget
        size_combo = dlg._rows["display.size_unit"].value_widget
        assert isinstance(hevc_spin, QDoubleSpinBox)
        assert isinstance(bitrate_spin, CustomIntegerSpinBox)
        assert isinstance(size_combo, QComboBox)
        widgets = (dlg.combo_theme, hevc_spin, bitrate_spin, size_combo)
        dlg.clearFocus()
        for widget in widgets:
            widget.clearFocus()
        before = (
            dlg.combo_theme.currentIndex(),
            hevc_spin.value(),
            bitrate_spin.value(),
            size_combo.currentIndex(),
        )

        for widget in widgets:
            event = self._wheel_event()
            QApplication.sendEvent(widget, event)
            self.assertFalse(event.isAccepted())

        after = (
            dlg.combo_theme.currentIndex(),
            hevc_spin.value(),
            bitrate_spin.value(),
            size_combo.currentIndex(),
        )
        self.assertEqual(after, before)

    def test_focused_integer_spinbox_accepts_wheel(self) -> None:
        """내부 입력란에 포커스가 있을 때만 정수 스핀박스가 휠 증감을 받아들임을 검증"""
        dlg = self._advanced_dialog()
        spin = dlg._rows["analysis.bitrate_model.base_target_kbps"].value_widget
        assert isinstance(spin, CustomIntegerSpinBox)
        before = spin.value()
        spin.edit.setFocus()
        _APP.processEvents()
        if not spin.edit.hasFocus():
            self.skipTest("오프스크린 환경에서 포커스를 얻지 못함")
        event = self._wheel_event()
        QApplication.sendEvent(spin, event)
        self.assertTrue(event.isAccepted())
        self.assertGreater(spin.value(), before)

    def test_row_reset_button_hides_without_shifting_editor(self) -> None:
        """개별 복원 버튼이 숨겨져도 입력 위젯의 x 좌표가 흔들리지 않음을 검증"""
        dlg = self._advanced_dialog()
        row = dlg._rows["analysis.encode.roundtrip_vmaf_threshold"]
        assert isinstance(row, SettingRow)
        spin = row.value_widget
        assert isinstance(spin, QDoubleSpinBox)
        spin.setValue(97.0)
        _APP.processEvents()
        self.assertTrue(row.btn_reset.isVisible())
        self.assertTrue(row.btn_reset.sizePolicy().retainSizeWhenHidden())
        editor_x = row.editor.geometry().x()

        row.btn_reset.click()
        _APP.processEvents()
        self.assertFalse(row.btn_reset.isVisible())
        self.assertEqual(row.editor.geometry().x(), editor_x)

    def test_settings_dialog_progressive_hints_toggle_and_live_examples(self) -> None:
        """쉬운 설명 보기 토글의 영속화, 인라인 힌트 표시 전환 및 실시간 기준 예시 갱신을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=state_path),
                patch("src.core.paths.get_rootdir_path", return_value=rootdir_path),
            ):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                dlg.show()
                self.addCleanup(dlg.close)
                _APP.processEvents()

                # 기본값: 쉬운 설명 표시 활성 + 기준 예시 실시간 계산 (판별 기준 범주)
                dlg.set_active_tab(SettingsDialog.TAB_CRITERIA)
                _APP.processEvents()
                self.assertTrue(dlg.chk_show_hints.isChecked())
                row_over = dlg.criteria_panel.rows["over_bitrate_ratio"]
                self.assertTrue(row_over.lbl_hint.isVisible())
                self.assertTrue(row_over.lbl_example.isVisible())
                self.assertIn("kbps", row_over.lbl_example.text())

                dlg.criteria_panel.spin_over_ratio.setValue(2.00)
                bitrate_row = dlg._rows["analysis.bitrate_model.base_target_kbps"]
                assert isinstance(bitrate_row, SettingRow)
                bitrate_spin = bitrate_row.value_widget
                assert isinstance(bitrate_spin, CustomIntegerSpinBox)
                bitrate_spin.setValue(2000)
                _APP.processEvents()
                self.assertIn("3,200 kbps", row_over.lbl_example.text())

                # 판별 기준 범주 고급 설정의 SettingRow 인라인 설명도 함께 토글, 저장, 복원됨
                dlg.set_active_tab(SettingsDialog.TAB_CRITERIA)
                assert dlg.criteria_panel.section_advanced is not None
                dlg.criteria_panel.section_advanced.set_expanded(True)
                _APP.processEvents()
                row_kf = dlg._rows["analysis.thresholds.keyframe_1s_increase_rate"]
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
                    reopened._rows["analysis.thresholds.keyframe_1s_increase_rate"].lbl_hint.isVisible()
                )

    def test_settings_dialog_encode_speed_persistence_and_system_detect(self) -> None:
        """목표 인코딩 방식, 배속 저장/복원, 방식 전환 시 자동 추천 및 튜너 변경 시 보존을 검증"""
        from src.core.analysis.system_probe import SystemSpeedRecommendation

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with (
                patch("src.core.settings.app_state.get_state_path", return_value=state_path),
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
                    "src.gui.settings.execution_sections.detect_recommended_encode_speed",
                    side_effect=fake_recommend,
                ):
                    dlg.execution_panel.btn_detect_encode_speed.click()
                    _APP.processEvents()
                    self.assertAlmostEqual(dlg.execution_panel.spin_encode_speed.value(), 4.5)

                    # 목표 코덱을 CPU x265로 전환하면 해당 코덱 기준 배속이 먼저 반영되어 1회만 저장됨
                    applied_events: list[tuple[str, float]] = []

                    def _capture_applied(cfg: ClassifierConfig, _root: str) -> None:
                        applied_events.append(
                            (cfg.encode.encode_codec, cfg.encode.encode_speed_factor)
                        )

                    dlg.settings_applied.connect(_capture_applied)
                    x265_idx = dlg.execution_panel.combo_encode_codec.findData("libx265")
                    self.assertGreaterEqual(x265_idx, 0)
                    dlg.execution_panel.combo_encode_codec.setCurrentIndex(x265_idx)
                    _APP.processEvents()

                self.assertEqual(applied_events, [("libx265", 1.9)])

                self.assertAlmostEqual(dlg.execution_panel.spin_encode_speed.value(), 1.9)
                reloaded = load_app_state()
                self.assertEqual(
                    reloaded.to_classifier_config().encode.encode_codec, "libx265"
                )
                self.assertAlmostEqual(
                    reloaded.to_classifier_config().encode.encode_speed_factor, 1.9
                )

                # 판별 기준 튜너에서 절약 항목을 조정해도 목표 코덱, 배속은 유지
                dlg.criteria_panel.spin_min_saved.setValue(12.0)
                self.assertEqual(dlg.execution_panel.combo_encode_codec.currentData(), "libx265")
                self.assertAlmostEqual(dlg.execution_panel.spin_encode_speed.value(), 1.9)


class TestShortcutsPanelTab(GuiSettingsTestCase):
    """설정 대화상자 단축키 탭의 키 캡처, 해제, 복원, 검색 필터, 중복 확인 규칙을 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def _panel(self) -> ShortcutsPanel:
        panel = ShortcutsPanel()
        self.addCleanup(panel.close)
        return panel

    @staticmethod
    def _capture(panel: ShortcutsPanel, action_id: str, key: Qt.Key, mods: Qt.KeyboardModifier) -> None:
        button = panel.capture_button(action_id)
        button.begin_capture()
        button.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, int(key), mods))

    def test_key_event_maps_modifiers_enter_and_function_keys(self) -> None:
        """수식키 단독 입력은 무시하고, 단독 Enter는 Return, 조합 키는 표준 문자열로 변환함을 검증"""
        ctrl = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        self.assertIsNone(key_event_to_shortcut_str(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Control), ctrl)))
        self.assertEqual(key_event_to_shortcut_str(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Enter), Qt.KeyboardModifier.NoModifier)), "Return")
        self.assertEqual(key_event_to_shortcut_str(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_E), ctrl | shift)), "Ctrl+Shift+E")
        self.assertEqual(key_event_to_shortcut_str(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_F5), Qt.KeyboardModifier.NoModifier)), "F5")

    def test_press_outside_the_capture_button_cancels_capture_and_other_events_keep_it(self) -> None:
        """대기 중 칸 밖의 마우스 누름은 캡처를 취소하고, 다른 종류의 이벤트는 대기를 유지함을 검증"""
        button = ShortcutCaptureButton()
        self.addCleanup(button.deleteLater)
        other = QWidget()
        self.addCleanup(other.deleteLater)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        button.begin_capture()
        button.eventFilter(other, QEvent(QEvent.Type.Paint))
        self.assertTrue(button.is_capturing())
        button.eventFilter(button, press)
        self.assertTrue(button.is_capturing())
        button.eventFilter(other, press)
        self.assertFalse(button.is_capturing())

    def test_internal_search_can_be_hidden_for_the_dialog(self) -> None:
        """설정 창이 내부 검색칸을 숨기고 만들 수 있고, 단독 패널은 기본으로 보이는지 검증"""
        hidden = ShortcutsPanel(show_search=False)
        self.addCleanup(hidden.close)
        self.assertTrue(hidden.search_edit.isHidden())

        shown = self._panel()
        self.assertFalse(shown.search_edit.isHidden())
        shown.apply_search("Ctrl+F")
        self.assertTrue(shown.is_row_visible("edit.focus_search"))

    def test_hidden_search_still_filters_by_the_dialog_query(self) -> None:
        """내부 검색칸을 숨긴 상태에서도 통합 검색어로 행이 걸러지는지 검증"""
        panel = ShortcutsPanel(show_search=False)
        self.addCleanup(panel.close)

        panel.apply_search("검색 포커스")

        self.assertTrue(panel.is_row_visible("edit.focus_search"))
        self.assertFalse(panel.is_row_visible("view.show_logs"))
        self.assertEqual(panel.search_edit.text(), "검색 포커스")
        panel.apply_search("")
        self.assertTrue(panel.is_row_visible("view.show_logs"))

    def test_tab_is_fifth_and_index_is_normalized(self) -> None:
        """단축키 범주가 다섯 번째 항목으로 열리고 범위 밖 인덱스가 일반 범주로 정규화됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            isolate_app_state(self, tmpdir)
            dlg = SettingsDialog(current_root_dir=tmpdir)
            self.addCleanup(dlg.close)
            self.assertEqual(dlg.tab_widget.count(), 6)
            self.assertEqual(dlg.tab_widget.tabText(SettingsDialog.TAB_SHORTCUTS), "단축키")
            self.assertEqual(dlg.tab_widget.tabText(SettingsDialog.TAB_EXECUTION), "재인코딩")
            dlg.set_active_tab(SettingsDialog.TAB_SHORTCUTS)
            self.assertEqual(dlg.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)
            self.assertEqual(SettingsDialog.normalize_tab_index(2), 2)
            self.assertEqual(SettingsDialog.normalize_tab_index(9), SettingsDialog.TAB_GENERAL)

    def test_search_matches_label_group_and_current_key_and_hides_empty_groups(self) -> None:
        """검색어가 명령 이름, 기능 그룹, 단축키 문자열 중 하나와 맞는 행만 남기고 빈 그룹 카드를 숨김을 검증"""
        panel = self._panel()
        panel.search_edit.setText("Ctrl+F")
        self.assertTrue(panel.is_row_visible("edit.focus_search"))
        self.assertFalse(panel.is_row_visible("view.show_logs"))
        # - 'Ctrl+F'는 'Ctrl+F1'의 부분 문자열이므로 상단 영역 접기 명령도 일치함
        self.assertTrue(panel.is_row_visible("view.toggle_top_area"))

        panel.search_edit.setText("검색 포커스")
        self.assertTrue(panel.is_group_visible("filter_table"))
        self.assertFalse(panel.is_group_visible("app_view"))

        panel.search_edit.setText("기능없는검색어")
        for group in SHORTCUT_GROUPS:
            self.assertFalse(panel.is_group_visible(group.key))

        panel.search_edit.setText("")
        self.assertTrue(panel.is_row_visible("view.show_logs"))
        self.assertTrue(panel.is_group_visible("app_view"))

    def test_key_capture_assigns_shortcut_and_emits_minimal_overrides(self) -> None:
        """키 입력 대기 후 누른 조합이 단축키로 지정되고 기본값과 다른 항목만 신호로 나감을 검증"""
        panel = self._panel()
        emitted: list[dict[str, str]] = []
        panel.shortcuts_changed.connect(lambda overrides: emitted.append(dict(overrides)))
        self._capture(panel, "view.show_logs", Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(emitted[-1], {"view.show_logs": "Ctrl+P"})
        self.assertEqual(panel.capture_button("view.show_logs").shortcut_text(), "Ctrl+P")
        self.assertFalse(panel.capture_button("view.show_logs").is_capturing())

    def test_shift_digit_capture_stores_digit_not_symbol(self) -> None:
        """Ctrl+Shift+9를 누르면 기호 키 이벤트로 도착해도 Ctrl+Shift+9로 저장됨을 검증"""
        panel = self._panel()
        emitted: list[dict[str, str]] = []
        panel.shortcuts_changed.connect(lambda overrides: emitted.append(dict(overrides)))
        self._capture(panel, "view.show_logs", Qt.Key.Key_ParenLeft, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(emitted[-1], {"view.show_logs": "Ctrl+Shift+9"})

    def test_modifier_only_press_keeps_waiting_and_cancel_restores_display(self) -> None:
        """수식키만 누르면 대기를 유지하고, 취소하면 기존 단축키 표시로 돌아가며 신호는 나가지 않음을 검증"""
        panel = self._panel()
        emitted: list[dict[str, str]] = []
        panel.shortcuts_changed.connect(lambda overrides: emitted.append(dict(overrides)))
        button = panel.capture_button("view.show_logs")
        button.begin_capture()
        button.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Shift), Qt.KeyboardModifier.ShiftModifier))
        self.assertTrue(button.is_capturing())
        button.cancel_capture()
        self.assertFalse(button.is_capturing())
        self.assertEqual(button.shortcut_text(), DEFAULT_SHORTCUTS["view.show_logs"])
        self.assertEqual(emitted, [])

    def test_clear_and_reset_buttons_show_only_when_applicable(self) -> None:
        """해제 버튼은 단축키가 있을 때, 기본값 복원 버튼은 기본값과 다를 때만 표시되고 숨김 중에도 자리를 유지함을 검증"""
        panel = self._panel()
        self.assertFalse(panel.clear_button("view.show_logs").isHidden())
        self.assertTrue(panel.reset_button("view.show_logs").isHidden())

        panel.clear_button("view.show_logs").click()
        self.assertEqual(panel.current_overrides(), {"view.show_logs": ""})
        self.assertEqual(panel.capture_button("view.show_logs").shortcut_text(), "")
        self.assertTrue(panel.clear_button("view.show_logs").isHidden())
        self.assertFalse(panel.reset_button("view.show_logs").isHidden())
        self.assertTrue(panel.clear_button("view.show_logs").sizePolicy().retainSizeWhenHidden())

        panel.reset_button("view.show_logs").click()
        self.assertEqual(panel.current_overrides(), {})
        self.assertTrue(panel.reset_button("view.show_logs").isHidden())

    def test_clear_button_is_icon_only_with_accessible_name(self) -> None:
        """해제 버튼은 문자 없이 아이콘만 표시하되 접근성 이름과 툴팁으로 용도를 알림을 검증"""
        panel = self._panel()
        button = panel.clear_button("view.show_logs")
        self.assertEqual(button.text(), "")
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.accessibleName(), "단축키 해제")
        self.assertEqual(button.toolTip(), "단축키를 해제합니다.")

    def test_hints_toggle_shows_command_descriptions_under_names(self) -> None:
        """설명 표시를 켜면 명령 이름 아래에 설명이 나타나고, 끄면 다시 숨겨짐을 검증"""
        panel = self._panel()
        row = panel._rows["view.show_logs"]
        self.assertTrue(row.lbl_hint.isHidden())
        panel.set_hints_visible(True)
        self.assertFalse(row.lbl_hint.isHidden())
        self.assertEqual(row.lbl_hint.text(), SHORTCUT_SPEC_BY_ID["view.show_logs"].description)
        panel.set_hints_visible(False)
        self.assertTrue(row.lbl_hint.isHidden())

    def test_search_without_match_shows_empty_message(self) -> None:
        """검색 결과가 없으면 안내 문구가 나타나고, 검색어를 지우면 사라짐을 검증"""
        panel = self._panel()
        self.assertTrue(panel.lbl_no_result.isHidden())
        panel.search_edit.setText("기능없는검색어")
        self.assertFalse(panel.lbl_no_result.isHidden())
        panel.search_edit.setText("")
        self.assertTrue(panel.lbl_no_result.isHidden())

    def test_keyboard_activation_starts_capture(self) -> None:
        """마우스 없이 버튼 활성화(스페이스)만으로도 키 입력 대기가 시작됨을 검증"""
        panel = self._panel()
        button = panel.capture_button("view.show_logs")
        button.click()
        self.assertTrue(button.is_capturing())
        button.cancel_capture()

    def test_empty_capture_shows_icon_placeholder_instead_of_text(self) -> None:
        """미지정 칸은 문자 대신 키보드 아이콘 플레이스홀더를 표시하고, 지정되면 아이콘을 제거함을 검증"""
        panel = self._panel()
        button = panel.capture_button("view.show_logs")
        panel.clear_button("view.show_logs").click()
        self.assertEqual(button.text(), "")
        self.assertFalse(button.icon().isNull())

        panel.reset_button("view.show_logs").click()
        self.assertEqual(button.text(), DEFAULT_SHORTCUTS["view.show_logs"])
        self.assertTrue(button.icon().isNull())

    def test_conflict_declined_keeps_state_and_confirmed_moves_key(self) -> None:
        """중복 확인에서 거절하면 변경이 없고, 승인하면 기존 명령의 단축키가 해제되고 새 명령에 지정됨을 검증"""
        panel = self._panel()
        with patch(
            "src.gui.settings.shortcuts_panel.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as ask:
            self.assertFalse(panel.set_shortcut_for("file.edit_path", "Ctrl+O"))
            self.assertEqual(panel.current_overrides(), {})
            self.assertEqual(ask.call_args.args[1], "단축키 중복 확인")
            self.assertIn("'작업 폴더 열기' 명령에 지정되어 있습니다", ask.call_args.args[2])

        with patch(
            "src.gui.settings.shortcuts_panel.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(panel.set_shortcut_for("file.edit_path", "Ctrl+O"))
        self.assertEqual(panel.current_overrides(), {"file.edit_path": "Ctrl+O", "file.open_dir": ""})

    def test_reset_single_and_reset_all_with_confirmation(self) -> None:
        """개별 복원은 기본값으로 되돌리고, 전체 초기화는 확인에서 거절하면 유지, 승인하면 모두 되돌림을 검증"""
        panel = self._panel()
        panel.set_shortcut_for("view.show_logs", "Ctrl+P")
        panel.set_shortcut_for("file.exit", "")
        self.assertTrue(panel.reset_action("view.show_logs"))
        self.assertEqual(panel.current_overrides(), {"file.exit": ""})

        with patch(
            "src.gui.settings.shortcuts_panel.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.assertFalse(panel.reset_all_shortcuts())
        self.assertEqual(panel.current_overrides(), {"file.exit": ""})

        with patch(
            "src.gui.settings.shortcuts_panel.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(panel.reset_all_shortcuts())
        self.assertEqual(panel.current_overrides(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
