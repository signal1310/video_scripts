"""기준 패널, 입력 위젯 휠 처리, 설정 탭, 진행 힌트, 인코딩 속도와 단축키 탭 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.config import DEFAULT_SAVINGS, DEFAULT_THRESHOLDS, ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.paths import ROOTDIR_FILENAME
from src.core.settings.app_state import CONFIG_FILENAME, DATA_DIR_NAME, load_app_state
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_GROUPS, SHORTCUT_SPEC_BY_ID
from src.gui.settings.setting_row import SettingRow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.settings.shortcuts_panel import ShortcutsPanel, key_event_to_shortcut_str
from src.gui.theme import ThemeManager
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

    def test_settings_dialog_tab_layout_titles_and_compact_width(self) -> None:
        """통합 설정 창의 탭 상수, 탭 제목, 컴팩트 폭, 판별 기준 행 제목을 검증"""
        dialog, _root = self._criteria_dialog()
        self.assertEqual((SettingsDialog.TAB_ENVIRONMENT, SettingsDialog.TAB_CRITERIA), (0, 1))
        self.assertLessEqual(dialog.width(), 500)
        self.assertLessEqual(dialog.minimumWidth(), 450)
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_CRITERIA)
        self.assertEqual(dialog.tab_widget.tabText(SettingsDialog.TAB_CRITERIA), "판별 기준")
        self.assertEqual(dialog.tab_widget.tabText(SettingsDialog.TAB_ENVIRONMENT), "환경 설정")
        self.assertEqual(dialog.btn_reset_defaults.objectName(), "InlineResetButton")
        rows = dialog.criteria_panel.rows
        self.assertEqual(rows["min_saved_mb"].lbl_title.text(), "최소 총 절약량")
        self.assertEqual(rows["min_mb_per_min"].lbl_title.text(), "분당 최소 절약량")
        self.assertEqual(rows["exempt_saved_mb"].lbl_title.text(), "대용량 절약 예외 허용")

    def test_settings_dialog_dark_theme_tab_bar_and_card_widths(self) -> None:
        """다크 모드에서 탭바 배경이 밝게 뜨지 않고 카드가 컴팩트 폭을 넘지 않음을 검증"""
        theme = ThemeManager()
        self.addCleanup(theme.set_mode, theme.mode)
        theme.set_mode(ThemeManager.MODE_DARK)
        dialog, _root = self._criteria_dialog()
        dialog.show()
        _APP.processEvents()

        viewport = dialog.criteria_panel.scroll_area.viewport()
        assert viewport is not None
        for card in dialog.criteria_panel.cards:
            self.assertLessEqual(card.minimumSizeHint().width(), viewport.width())
        tab_bar = dialog.tab_widget.tabBar()
        assert tab_bar is not None
        bar_img = tab_bar.grab().toImage()
        self.assertLess(bar_img.pixelColor(bar_img.width() - 6, bar_img.height() // 2).lightness(), 60)

    def test_settings_dialog_tab_switch_emits_and_keeps_edited_criteria(self) -> None:
        """탭 전환이 시그널로 전달되어 다시 열 때 복원되고, 환경 설정 저장 시 편집한 판별 기준이 보존됨을 검증"""
        dialog, root = self._criteria_dialog()
        tab_events: list[int] = []
        dialog.tab_changed.connect(tab_events.append)

        dialog.criteria_panel.spin_over_ratio.setValue(3.35)
        dialog.set_active_tab(SettingsDialog.TAB_ENVIRONMENT)
        self.assertEqual(tab_events, [SettingsDialog.TAB_ENVIRONMENT])
        self.assertEqual(dialog.tab_widget.currentIndex(), SettingsDialog.TAB_ENVIRONMENT)

        reopened = SettingsDialog(current_root_dir=root, app_state=dialog._app_state)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.tab_widget.currentIndex(), SettingsDialog.TAB_ENVIRONMENT)

        dialog.spin_w_hevc.setValue(0.65)
        dialog.spin_w_hevc.editingFinished.emit()
        self.assertAlmostEqual(dialog._cfg.thresholds.over_bitrate_ratio, 3.35, places=2)

    def test_unfocused_inputs_ignore_wheel_events(self) -> None:
        """포커스가 없는 콤보박스와 실수, 정수 스핀박스는 휠 입력을 무시함을 검증"""
        dlg = self._advanced_dialog()
        widgets = (dlg.combo_theme, dlg.spin_w_hevc, dlg.spin_base_bitrate, dlg.combo_size_unit)
        dlg.clearFocus()
        for widget in widgets:
            widget.clearFocus()
        before = (
            dlg.combo_theme.currentIndex(),
            dlg.spin_w_hevc.value(),
            dlg.spin_base_bitrate.value(),
            dlg.combo_size_unit.currentIndex(),
        )

        for widget in widgets:
            event = self._wheel_event()
            QApplication.sendEvent(widget, event)
            self.assertFalse(event.isAccepted())

        after = (
            dlg.combo_theme.currentIndex(),
            dlg.spin_w_hevc.value(),
            dlg.spin_base_bitrate.value(),
            dlg.combo_size_unit.currentIndex(),
        )
        self.assertEqual(after, before)

    def test_focused_integer_spinbox_accepts_wheel(self) -> None:
        """내부 입력란에 포커스가 있을 때만 정수 스핀박스가 휠 증감을 받아들임을 검증"""
        dlg = self._advanced_dialog()
        before = dlg.spin_base_bitrate.value()
        dlg.spin_base_bitrate.edit.setFocus()
        _APP.processEvents()
        if not dlg.spin_base_bitrate.edit.hasFocus():
            self.skipTest("오프스크린 환경에서 포커스를 얻지 못함")
        event = self._wheel_event()
        QApplication.sendEvent(dlg.spin_base_bitrate, event)
        self.assertTrue(event.isAccepted())
        self.assertGreater(dlg.spin_base_bitrate.value(), before)

    def test_row_reset_button_hides_without_shifting_editor(self) -> None:
        """개별 복원 버튼이 숨겨져도 입력 위젯의 x 좌표가 흔들리지 않음을 검증"""
        dlg = self._advanced_dialog()
        row = next(r for r in dlg.findChildren(SettingRow) if r.key == "analysis.thresholds.keyframe_1s_increase_rate")
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
                self.assertIn("3,200 kbps", row_over.lbl_example.text())

                # 환경 탭 SettingRow 인라인 설명도 함께 토글, 저장, 복원됨
                dlg.set_active_tab(SettingsDialog.TAB_ENVIRONMENT)
                dlg.section_advanced.set_expanded(True)
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

    def test_tab_is_third_and_index_is_normalized(self) -> None:
        """단축키 탭이 세 번째 탭으로 열리고 인덱스 2가 유효한 탭으로 정규화됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            isolate_app_state(self, tmpdir)
            dlg = SettingsDialog(current_root_dir=tmpdir)
            self.addCleanup(dlg.close)
            self.assertEqual(dlg.tab_widget.count(), 4)
            self.assertEqual(dlg.tab_widget.tabText(SettingsDialog.TAB_SHORTCUTS), "단축키")
            self.assertEqual(dlg.tab_widget.tabText(SettingsDialog.TAB_EXECUTION), "재인코딩")
            dlg.set_active_tab(SettingsDialog.TAB_SHORTCUTS)
            self.assertEqual(dlg.tab_widget.currentIndex(), SettingsDialog.TAB_SHORTCUTS)
            self.assertEqual(SettingsDialog.normalize_tab_index(2), 2)
            self.assertEqual(SettingsDialog.normalize_tab_index(5), SettingsDialog.TAB_ENVIRONMENT)

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
