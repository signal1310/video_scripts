"""화질 비교 상단 2줄 조작 바 검증: 도킹, 접기, 호버 오버레이, 안전장치, 툴팁, 배율"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.analysis.frame_compare import resolve_compare_plan
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_top_bar import TOP_HOTZONE_PX, CompareTopBar
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _image() -> QImage:
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("#FF0000"))
    return image


class CompareTopBarTests(GuiTestCase):
    """상단 바 구성과 접기, 오버레이, 배율 조작"""

    def setUp(self) -> None:
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.window.resize(900, 600)
        self.modes: list[str] = []
        self.zooms: list[str] = []
        self.targets: list[str] = []
        self.collapsed_changes: list[bool] = []
        self.stage = layout.build_stage(self.window, lambda _scale: None, lambda _side: None)
        self.top = CompareTopBar(
            self.window,
            self.stage.canvas,
            on_mode=self.modes.append,
            on_zoom=self.zooms.append,
            on_target=self.targets.append,
            on_collapsed_changed=self.collapsed_changes.append,
        )
        self.window.show()
        _APP.processEvents()

    def _margins(self) -> tuple[int, int, int, int]:
        wrap = self.stage.canvas.parentWidget()
        assert wrap is not None
        layout_obj = wrap.layout()
        assert layout_obj is not None
        margins = layout_obj.contentsMargins()
        return margins.left(), margins.top(), margins.right(), margins.bottom()

    def _move_canvas(self, pos: QPoint) -> None:
        """캔버스 좌표로 마우스 이동 사건을 보내 상단 바의 반응을 확인"""
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(pos),
            QPointF(self.stage.canvas.mapToGlobal(pos)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.stage.canvas, event)

    def test_default_expanded_bar_is_docked_above_the_canvas(self) -> None:
        self.assertFalse(self.top.is_collapsed())
        self.assertTrue(self.top.container.isVisible())
        self.assertGreater(self._margins()[1], 0)
        self.assertFalse(self.top.is_overlay_visible())
        self.assertEqual(self.top.filename.text(), "")
        self.assertEqual(self.top.collapse_button.accessibleName(), "조작 바 접기")

    def test_mode_zoom_and_mode_buttons_call_back(self) -> None:
        self.top.mode_buttons[MODE_SPLIT].click()
        self.top.mode_buttons[MODE_TOGGLE].click()
        self.top.mode_buttons[MODE_SIDE].click()
        self.top.zoom_buttons["200"].click()
        self.assertEqual(self.modes, [MODE_SPLIT, MODE_TOGGLE, MODE_SIDE])
        self.assertEqual(self.zooms, ["200"])

    def test_collapse_removes_the_docking_margin_and_shows_overlay_on_activity(self) -> None:
        """접으면 도킹 여백이 사라지고 마우스 움직임에 반투명 오버레이가 뜨는지 검증"""
        expanded_top = self._margins()[1]
        self.top.set_collapsed(True)
        self.assertTrue(self.top.is_collapsed())
        self.assertTrue(self.top.container.isHidden())
        self.assertLess(self._margins()[1], expanded_top)
        self.assertEqual(self.top.collapse_button.accessibleName(), "조작 바 펼치기")
        self.top.note_mouse_activity(QPoint(120, TOP_HOTZONE_PX // 2))
        self.assertTrue(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isVisible())
        self.assertEqual(self.top.container.property("overlay"), True)
        self.assertGreater(self.stage.canvas._top_inset, 0)

    def test_mouse_moves_below_the_top_hotzone_keep_the_overlay_hidden(self) -> None:
        """캔버스 중앙과 하단을 움직여도 상단 오버레이가 뜨지 않는지 검증"""
        self.top.set_collapsed(True)
        self._move_canvas(QPoint(200, self.stage.canvas.height() - 20))
        self._move_canvas(QPoint(200, self.stage.canvas.height() // 2))
        self._move_canvas(QPoint(200, TOP_HOTZONE_PX + 1))
        self.assertFalse(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isHidden())
        self.assertEqual(self.stage.canvas._top_inset, 0)

    def test_mouse_at_the_top_edge_hotzone_reveals_the_overlay(self) -> None:
        """캔버스 위쪽 가장자리 안에서만 오버레이를 띄우는지 검증"""
        self.top.set_collapsed(True)
        self._move_canvas(QPoint(200, TOP_HOTZONE_PX))
        self.assertTrue(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isVisible())

    def test_open_overlay_holds_the_hide_timer_inside_the_hotzone(self) -> None:
        """오버레이가 열린 동안 상단 핫존에 머물면 숨김 타이머를 멈추는지 검증"""
        self.top.set_collapsed(True)
        self.top.reveal()
        self._move_canvas(QPoint(200, TOP_HOTZONE_PX // 2))
        self.assertFalse(self.top._hide_timer.isActive())
        self._move_canvas(QPoint(200, self.stage.canvas.height() // 2))
        self.assertTrue(self.top._hide_timer.isActive())

    def test_mouse_over_the_open_overlay_keeps_it_and_leaving_starts_the_timer(self) -> None:
        """오버레이 위에서는 유지하고 밖으로 나가면 짧은 지연 뒤 숨기는지 검증"""
        self.top.set_collapsed(True)
        self.top.reveal()
        self.top._hide_timer.start()
        QApplication.sendEvent(self.top.container, QEvent(QEvent.Type.Enter))
        self.assertFalse(self.top._hide_timer.isActive())
        QApplication.sendEvent(self.top.container, QEvent(QEvent.Type.Leave))
        self.assertTrue(self.top._hide_timer.isActive())

    def test_safety_check_blocks_auto_hide_until_released(self) -> None:
        allowed = [False]
        self.top.set_collapsed(True)
        self.top.set_safety_check(lambda: allowed[0])
        self.top.reveal()
        self.top.hide_overlay_if_allowed()
        self.assertTrue(self.top.is_overlay_visible())
        allowed[0] = True
        self.top.hide_overlay_if_allowed()
        self.assertFalse(self.top.is_overlay_visible())
        self.assertTrue(self.top.container.isHidden())
        self.assertEqual(self.stage.canvas._top_inset, 0)

    def test_toggle_collapsed_flips_the_state_and_reports_it(self) -> None:
        self.top.toggle_collapsed()
        self.assertTrue(self.top.is_collapsed())
        self.top.toggle_collapsed()
        self.assertFalse(self.top.is_collapsed())
        self.assertEqual(self.collapsed_changes, [True, False])

    def test_initial_collapsed_state_is_applied_without_saving(self) -> None:
        """저장된 접힘 상태로 처음 만들 때는 저장을 부르지 않고, 버튼을 누를 때만 알리는지 검증"""
        changes: list[bool] = []
        host = QWidget()
        self.addCleanup(host.deleteLater)
        stage = layout.build_stage(host, lambda _scale: None, lambda _side: None)
        top = CompareTopBar(
            host,
            stage.canvas,
            on_mode=lambda _mode: None,
            on_zoom=lambda _zoom: None,
            on_target=lambda _target: None,
            collapsed=True,
            on_collapsed_changed=changes.append,
        )
        self.assertTrue(top.is_collapsed())
        self.assertEqual(changes, [])
        top.collapse_button.click()
        self.assertFalse(top.is_collapsed())
        self.assertEqual(changes, [False])

    def test_shortcut_tooltips_include_custom_keys(self) -> None:
        """사용자가 바꾼 키가 상단 버튼 툴팁에 반영되는지 검증"""
        shortcuts = dict(DEFAULT_SHORTCUTS)
        shortcuts["compare.mode_split"] = "F2"
        shortcuts["compare.zoom_fit"] = "Z"
        shortcuts["compare.toggle_controls"] = "G"
        self.top.sync_shortcut_tooltips(shortcuts)
        self.assertIn("[F2]", self.top.mode_buttons[MODE_SPLIT].toolTip())
        self.assertIn("[Z]", self.top.zoom_buttons["fit"].toolTip())
        self.assertIn("[G]", self.top.collapse_button.toolTip())
        self.assertEqual(self.top.target_button.toolTip(), layout.TARGET_MENU_TIP)

    def test_target_menu_is_rebuilt_and_triggers_the_callback(self) -> None:
        plan = resolve_compare_plan(source_path=__file__, width=3840, height=2160, size_bytes=1024)
        self.top.rebuild_target_menu(plan)
        actions = self.top.target_menu.actions()
        self.assertTrue(actions)
        self.assertTrue(actions[0].isChecked())
        actions[0].trigger()
        self.assertEqual(self.targets, [plan.targets[0].key])

    def test_target_menu_marks_the_frame_analysis_candidate(self) -> None:
        """화질 분석 축소 후보 규격만 메뉴에서 (축소 후보)로 표시되고 기본 선택인지 검증"""
        plan = resolve_compare_plan(
            source_path=__file__, width=3840, height=2160, size_bytes=1024, candidate_tier="1080p"
        )
        self.top.rebuild_target_menu(plan)
        labels = [action.text() for action in self.top.target_menu.actions()]
        self.assertIn("1080p 축소 비교 (축소 후보)", labels)
        self.assertNotIn("1440p 축소 비교 (축소 후보)", labels)
        action = next(a for a in self.top.target_menu.actions() if a.data() == "1080p")
        self.assertTrue(action.isChecked())
        action.trigger()
        self.assertEqual(self.targets, ["1080p"])

    def test_output_row_shows_the_value_and_apply_button_state(self) -> None:
        """출력 드롭다운 문구와 이 규격으로 지정 버튼의 활성, 툴팁, 클릭 연결을 검증"""
        applied: list[bool] = []
        top = CompareTopBar(
            self.window,
            self.stage.canvas,
            on_mode=lambda _mode: None,
            on_zoom=lambda _zoom: None,
            on_target=lambda _target: None,
            on_apply_output=lambda: applied.append(True),
        )
        self.assertEqual(top.output_button.text(), "출력 -")
        self.assertFalse(top.output_button.isEnabled())
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(top.apply_output_button.accessibleName(), layout.APPLY_OUTPUT_NAME)
        self.assertEqual(top.output_button.accessibleName(), "출력 해상도")
        top.set_output_state(
            "자동 (1080p)", enabled=True, action_enabled=True, tip="열기", action_tip="지정"
        )
        self.assertEqual(top.output_button.text(), "출력 자동 (1080p)")
        self.assertTrue(top.output_button.isEnabled())
        self.assertEqual(top.output_button.toolTip(), "열기")
        self.assertTrue(top.apply_output_button.isEnabled())
        self.assertEqual(top.apply_output_button.toolTip(), "지정")
        self.assertFalse(top.apply_output_button.icon().isNull())
        top.apply_output_button.click()
        self.assertEqual(applied, [True])
        top.set_output_state(
            "축소 안 함", enabled=False, action_enabled=False, tip="닫기", action_tip="사유"
        )
        self.assertFalse(top.apply_output_button.isEnabled())
        self.assertEqual(top.output_button.toolTip(), "닫기")
        top.apply_theme(LIGHT_PALETTE)
        self.assertFalse(top.output_button.icon().isNull())
        self.assertFalse(top.apply_output_button.icon().isNull())

    def test_close_open_menu_reports_whether_it_closed(self) -> None:
        self.assertFalse(self.top.close_open_menu())
        with (
            mock.patch.object(self.top.output_menu, "isVisible", return_value=True),
            mock.patch.object(self.top.output_menu, "close") as output_close,
        ):
            self.assertTrue(self.top.close_open_menu())
        output_close.assert_called_once()
        with (
            mock.patch.object(self.top.target_menu, "isVisible", return_value=True),
            mock.patch.object(self.top.target_menu, "close") as close,
        ):
            self.assertTrue(self.top.close_open_menu())
        close.assert_called_once()

    def test_zoom_input_and_manual_zoom_update_checks_and_value(self) -> None:
        self.stage.canvas.set_images(_image(), _image())
        self.top.set_zoom_checks("1")
        self.assertTrue(self.top.zoom_buttons["1"].isChecked())
        self.top.zoom_input.setValue(250)
        self.assertTrue(self.top.apply_zoom_input())
        self.assertAlmostEqual(self.stage.canvas.scale(), 2.5)
        self.assertFalse(any(button.isChecked() for button in self.top.zoom_buttons.values()))
        self.top.manual_zoom(1)
        self.assertAlmostEqual(self.stage.canvas.scale(), 2.5 * 1.25)
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))
        self.top.sync_zoom_input()
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))

    def test_target_and_zoom_inputs_do_not_take_the_tab_focus(self) -> None:
        """대상 버튼과 배율 입력이 창 활성화 때 자동으로 포커스를 가져가지 않는지 검증"""
        self.assertEqual(self.top.target_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(self.top.zoom_input.focusPolicy(), Qt.FocusPolicy.ClickFocus)
        editor = self.top.zoom_input.lineEdit()
        assert editor is not None
        self.assertEqual(editor.focusPolicy(), Qt.FocusPolicy.ClickFocus)

    def test_commit_zoom_input_applies_the_value_and_returns_focus(self) -> None:
        """입력 확정이 배율을 적용하고 입력 칸에 포커스가 있으면 창으로 돌려주는지 검증"""
        self.stage.canvas.set_images(_image(), _image())
        self.top.zoom_input.setValue(250)
        with (
            mock.patch.object(self.top.zoom_input, "hasFocus", return_value=True),
            mock.patch.object(self.window, "setFocus") as set_focus,
        ):
            self.top.commit_zoom_input()
        self.assertAlmostEqual(self.stage.canvas.scale(), 2.5)
        set_focus.assert_called_once()

    def test_manual_zoom_clears_the_preset_checks(self) -> None:
        """휠이나 드래그로 배율이 바뀌면 프리셋 체크를 풀고 입력 칸을 맞추는지 검증"""
        self.stage.canvas.set_images(_image(), _image())
        self.top.set_zoom_checks("1")
        self.stage.canvas.zoom_step(1)
        self.top.on_manual_zoom()
        self.assertFalse(any(button.isChecked() for button in self.top.zoom_buttons.values()))
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))

    def test_escape_in_the_zoom_input_restores_the_canvas_scale(self) -> None:
        """배율 입력에서 Esc를 누르면 캔버스 배율로 되돌리고 창에 포커스를 돌려주는지 검증"""
        self.stage.canvas.set_images(_image(), _image())
        editor = self.top.zoom_input.lineEdit()
        assert editor is not None
        self.top.zoom_input.setValue(250)
        with mock.patch.object(self.window, "setFocus") as set_focus:
            QApplication.sendEvent(
                editor,
                QKeyEvent(
                    QEvent.Type.KeyPress,
                    Qt.Key.Key_Escape,
                    Qt.KeyboardModifier.NoModifier,
                    "",
                ),
            )
        set_focus.assert_called_once()
        self.assertEqual(self.top.zoom_input.value(), round(self.stage.canvas.scale() * 100))

    def test_mode_checks_follow_the_current_mode(self) -> None:
        self.top.set_mode_checks(MODE_SIDE)
        self.assertTrue(self.top.mode_buttons[MODE_SIDE].isChecked())
        self.assertFalse(self.top.mode_buttons[MODE_SPLIT].isChecked())

    def test_apply_theme_and_resize_keep_the_bar_aligned(self) -> None:
        self.top.apply_theme(LIGHT_PALETTE)
        self.assertFalse(self.top.collapse_button.icon().isNull())
        self.assertFalse(self.top.target_button.icon().isNull())
        self.stage.canvas.resize(500, 300)
        QApplication.sendEvent(self.stage.canvas, QEvent(QEvent.Type.Resize))
        self.assertLessEqual(self.top.container.width(), max(self.stage.canvas.width(), 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
