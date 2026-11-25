"""분석 모드 팝업의 비차단 열기, 선택, 닫기 규칙과 분할 버튼 연동을 검증"""

import os
import unittest
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFont, QKeyEvent, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.styles.qss_widgets import build_widgets_qss
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeManager
from src.gui.widgets.header_bar import SplitAnalyzeButton
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])
# - PyQt6 스텁이 QTest.mouseClick을 인스턴스 메서드로 선언해 정적 호출의 형식 검사가 실패하므로 호출 가능한 형태로 받음
_send_mouse_click: Callable[..., None] = QTest.mouseClick


def _press_at(global_pos: QPoint) -> QMouseEvent:
    point = QPointF(global_pos)
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class AnalyzeModePopupTests(GuiTestCase):
    def setUp(self) -> None:
        self.split = SplitAnalyzeButton()
        self.split.resize(200, 36)
        self.split.show()
        self.addCleanup(self.split.close)
        # 앞선 테스트의 창 종료로 큐에 남은 앱 비활성화 이벤트가 팝업을 닫지 않도록 먼저 비움
        QApplication.processEvents()
        self.popup = self.split._mode_popup

    def _open_popup(self) -> None:
        self.split.btn_arrow.click()
        QApplication.processEvents()

    def test_popup_paints_styled_card_background(self) -> None:
        """테마 스타일시트 적용 시 팝업 프레임이 배경과 테두리를 그려 뒤 화면과 구분됨을 검증"""
        self.split.setStyleSheet(build_stylesheet(ThemeManager().current_palette))
        self.addCleanup(self.split.setStyleSheet, "")
        self._open_popup()
        image = self.popup.grab().toImage()
        top_border = image.pixelColor(image.width() // 2, 0)
        self.assertEqual(top_border.alpha(), 255)

    def test_arrow_click_opens_popup_without_blocking(self) -> None:
        """화살표 클릭 시 팝업이 블로킹 없이 열려 호출이 즉시 반환되고 열린 상태가 유지됨을 검증"""
        self._open_popup()
        self.assertTrue(self.popup.isVisible())
        self.assertGreaterEqual(self.popup.width(), self.split.width())

    def test_item_click_selects_mode_closes_and_emits(self) -> None:
        """항목 클릭 시 분석 모드가 바뀌고 팝업이 닫히며 mode_changed가 방출됨을 검증"""
        emitted: list[str] = []
        self.split.mode_changed.connect(emitted.append)
        self._open_popup()
        self.popup.btn_full.click()
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FULL)
        self.assertEqual(emitted, [SplitAnalyzeButton.MODE_FULL])

    def test_same_mode_selection_does_not_emit(self) -> None:
        """이미 선택된 모드를 다시 고르면 모드는 유지되고 변경 시그널은 방출되지 않음을 검증"""
        emitted: list[str] = []
        self.split.mode_changed.connect(emitted.append)
        self._open_popup()
        self.popup.btn_fast.click()
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FAST)
        self.assertEqual(emitted, [])

    def test_escape_closes_popup(self) -> None:
        """Esc 입력 시 팝업이 닫히고 이벤트가 소비됨을 검증"""
        self._open_popup()
        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(self.popup.eventFilter(self.popup, escape))
        self.assertFalse(self.popup.isVisible())

    def test_arrow_press_while_open_closes_without_reopening(self) -> None:
        """열린 상태에서 화살표를 누르면 팝업만 닫히고 같은 클릭으로 다시 열리지 않음을 검증"""
        self._open_popup()
        arrow_center = self.split.btn_arrow.mapToGlobal(
            QPoint(self.split.btn_arrow.width() // 2, self.split.btn_arrow.height() // 2)
        )
        self.assertTrue(self.popup.eventFilter(self.popup, _press_at(arrow_center)))
        self.assertFalse(self.popup.isVisible())

    def test_outside_press_closes_and_passes_through(self) -> None:
        """바깥 클릭은 팝업만 닫고 이벤트는 대상 위젯으로 넘겨 원클릭 동작을 막지 않음을 검증"""
        self._open_popup()
        far_away = QPoint(9000, 9000)
        self.assertFalse(self.popup.eventFilter(self.popup, _press_at(far_away)))
        self.assertFalse(self.popup.isVisible())

    def test_keyboard_activation_of_arrow_closes_open_popup(self) -> None:
        """키보드로 화살표를 다시 활성화하면 열린 팝업을 닫아 토글 동작을 유지함을 검증"""
        self._open_popup()
        self.split.btn_arrow.click()
        QApplication.processEvents()
        self.assertFalse(self.popup.isVisible())

    def test_frame_mode_item_opens_the_slider_without_closing(self) -> None:
        """화질 분석을 고르면 팝업이 열린 채 슬라이더가 펼쳐지고 다시 누르면 닫히는지 검증"""
        emitted: list[str] = []
        self.split.mode_changed.connect(emitted.append)
        self._open_popup()
        self.popup.btn_frame.click()
        QApplication.processEvents()
        self.assertTrue(self.popup.isVisible())
        self.assertTrue(self.popup._tier_controls.isVisible())
        self.assertTrue(self.popup.frame_group.property("selected"))
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FRAME)
        self.assertEqual(self.split.btn_main.text(), "화질 분석")
        self.assertEqual(emitted, [SplitAnalyzeButton.MODE_FRAME])
        self.popup.btn_frame.click()
        QApplication.processEvents()
        self.assertFalse(self.popup.isVisible())

    def test_slider_change_refreshes_the_frame_estimate(self) -> None:
        """측정 대상 슬라이더를 움직이면 팝업 우측 대상 요약을 다시 계산하는지 검증"""
        estimates: list[str] = []
        self.split.set_estimate_provider(lambda mode: estimates.append(mode) or f"{mode} 예상")
        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        controls = self.popup._tier_controls
        controls.slider.setValue(controls.slider.value() + 1)
        self.assertEqual(estimates, ["frame"])
        self.assertEqual(self.popup.lbl_frame_estimate.text(), "frame 예상")

    def test_frame_mode_main_click_emits_frame_request(self) -> None:
        """화질 분석 모드에서 본체를 누르면 화질 분석 실행 신호가 나감"""
        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        requested: list[str] = []
        self.split.analyze_fast_requested.connect(lambda: requested.append("fast"))
        self.split.analyze_full_requested.connect(lambda: requested.append("full"))
        self.split.analyze_frame_requested.connect(lambda: requested.append("frame"))
        self.split.btn_main.click()
        self.assertEqual(requested, ["frame"])

    def test_min_tier_slider_is_shown_only_for_frame_mode(self) -> None:
        """측정 대상 슬라이더는 화질 분석이 선택된 상태로 팝업을 열 때만 보임"""
        self._open_popup()
        self.assertFalse(self.popup._tier_controls.isVisible())
        self.popup.close()
        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        self._open_popup()
        self.assertTrue(self.popup._tier_controls.isVisible())

    def test_min_tier_slider_reports_tier_keys(self) -> None:
        """슬라이더 단계가 규격 키로 바뀌고 저장 값을 되돌려 받음"""
        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        self.split.set_frame_min_tier("720p")
        controls = self.popup._tier_controls
        self.assertEqual(controls.tier_key(), "720p")
        self.assertEqual(controls.lbl_value.text(), "720p 이상")
        changed: list[str] = []
        self.split.frame_min_tier_changed.connect(changed.append)
        controls.slider.setValue(controls.slider.value() + 1)
        self.assertEqual(changed, [controls.tier_key()])
        self.split.set_frame_min_tier("all")
        self.assertEqual(controls.lbl_value.text(), "전체")
        self.split.set_frame_min_tier("unknown")
        self.assertEqual(controls.tier_key(), "1080p")

    def test_arrow_open_fills_the_estimates_of_every_mode(self) -> None:
        """팝업을 열 때 등록한 예상 문구 제공자가 모드별 문구를 채움을 검증"""
        self.split.set_estimate_provider(lambda mode: f"{mode} 예상")
        self._open_popup()
        self.assertEqual(self.popup.lbl_fast_estimate.text(), "fast 예상")
        self.assertEqual(self.popup.lbl_full_estimate.text(), "full 예상")
        self.assertEqual(self.popup.lbl_frame_estimate.text(), "frame 예상")

    def test_rows_wrap_the_button_and_estimate_in_a_hover_frame(self) -> None:
        """항목 버튼과 예상 문구가 같은 행 프레임에 들어 있고 행 호버 QSS가 있음을 검증"""
        for btn, estimate in (
            (self.popup.btn_fast, self.popup.lbl_fast_estimate),
            (self.popup.btn_full, self.popup.lbl_full_estimate),
            (self.popup.btn_frame, self.popup.lbl_frame_estimate),
        ):
            row = btn.parentWidget()
            self.assertIsInstance(row, QFrame)
            if not isinstance(row, QFrame):
                return
            self.assertEqual(row.objectName(), "AnalyzeModeRow")
            self.assertTrue(row.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertIs(estimate.parentWidget(), row)
        self.assertIn("QFrame#AnalyzeModeRow:hover", build_widgets_qss(DARK_PALETTE))

    def test_row_qss_keeps_the_selected_card_flat(self) -> None:
        """두 팔레트의 행/항목 호버 QSS가 선택 카드 배경을 덮지 않고 중복 규칙이 없음을 검증"""
        selected_rule = 'QFrame#AnalyzeFrameGroup[selected="true"] QFrame#AnalyzeModeRow:hover'
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            qss = build_widgets_qss(palette)
            self.assertIn("QFrame#AnalyzeModeRow:hover", qss)
            self.assertIn(
                "QPushButton#AnalyzeModeItem:hover {\n    background-color: transparent;\n}",
                qss,
            )
            self.assertNotIn("QFrame#AnalyzeFrameGroup QPushButton#AnalyzeModeItem:hover", qss)
            self.assertIn(f"{selected_rule} {{\n    background-color: transparent;\n}}", qss)

    def test_row_frame_shrinks_and_grows_when_mode_changes_directly(self) -> None:
        """화질 분석과 빠른 분석을 오갈 때 팝업 높이가 처음 값으로 정확히 돌아옴을 검증"""
        self.split.set_mode(SplitAnalyzeButton.MODE_FAST)
        self._open_popup()
        h_fast_initial = self.popup.height()
        self.popup.set_current_mode("frame")
        QApplication.processEvents()
        h_frame = self.popup.height()
        self.assertGreater(h_frame, h_fast_initial)
        self.popup.set_current_mode("fast")
        QApplication.processEvents()
        self.assertEqual(self.popup.height(), h_fast_initial)

    def test_row_frame_shrinks_from_frame_to_full(self) -> None:
        """화질 분석에서 정밀 분석으로 바로 바꿔도 팝업 높이가 줄어듦을 검증"""
        self.split.set_mode(SplitAnalyzeButton.MODE_FAST)
        self._open_popup()
        h_fast_initial = self.popup.height()
        self.popup.set_current_mode("frame")
        QApplication.processEvents()
        self.assertGreater(self.popup.height(), h_fast_initial)
        self.popup.set_current_mode("full")
        QApplication.processEvents()
        self.assertEqual(self.popup.height(), h_fast_initial)

    def test_slider_text_growth_keeps_the_popup_tall_enough(self) -> None:
        """측정 대상 문구가 한 줄에서 두 줄로 늘어나면 팝업 높이도 함께 늘어남을 검증"""
        font = QFont(self.split.font())
        font.setPointSize(13)
        self.split.setFont(font)
        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        self.split.set_frame_min_tier("all")
        self._open_popup()
        controls = self.popup._tier_controls
        one_line = controls.lbl_summary.heightForWidth(controls.lbl_summary.width())
        controls.slider.setValue(controls.slider.value() + 1)
        QApplication.processEvents()
        two_line = controls.lbl_summary.heightForWidth(controls.lbl_summary.width())
        self.assertGreater(two_line, one_line)
        self.assertGreaterEqual(controls.lbl_summary.height(), two_line)
        self.assertGreaterEqual(self.popup.height(), self.popup.sizeHint().height())

    def test_estimate_label_click_selects_the_row_mode(self) -> None:
        """예상 문구 영역을 눌러도 항목이 선택되고 팝업이 닫히며 시그널이 나감을 검증"""
        self.split.set_estimate_provider(lambda mode: f"{mode} 예상")
        emitted: list[str] = []
        self.popup.fast_selected.connect(lambda: emitted.append("fast"))
        self._open_popup()
        _send_mouse_click(
            self.popup.lbl_fast_estimate,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            self.popup.lbl_fast_estimate.rect().center(),
        )
        QApplication.processEvents()
        self.assertEqual(emitted, ["fast"])
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.split.current_mode, SplitAnalyzeButton.MODE_FAST)

    def test_collapsing_tier_controls_restores_the_initial_height(self) -> None:
        """화질 분석으로 펼쳤다가 빠른 분석으로 다시 열면 처음 높이로 돌아옴을 검증"""
        self.split.set_mode(SplitAnalyzeButton.MODE_FAST)
        self._open_popup()
        h_fast_initial = self.popup.height()
        self.popup.close()
        QApplication.processEvents()

        self.split.set_mode(SplitAnalyzeButton.MODE_FRAME)
        self._open_popup()
        h_frame = self.popup.height()
        self.assertGreater(h_frame, h_fast_initial)
        self.popup.close()
        QApplication.processEvents()

        self.split.set_mode(SplitAnalyzeButton.MODE_FAST)
        self._open_popup()
        h_fast_after = self.popup.height()
        self.assertLess(h_fast_after, h_frame)
        self.assertEqual(h_fast_after, h_fast_initial)


if __name__ == "__main__":
    unittest.main()
