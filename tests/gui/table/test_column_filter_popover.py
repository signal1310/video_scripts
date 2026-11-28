"""ColumnFilterPopover의 규칙 생성, 입력 모드, 초기화, 위치 보정, 닫기 동작 검증"""

import os
import unittest
from typing import TypeVar

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel

from src.gui.table.column_filter_popover import ColumnFilterPopover
from src.gui.table.column_filters import (
    TEXT_MODE_ENDS_WITH,
    ColumnDataType,
    ColumnFilterRule,
    ColumnSpec,
)
from tests._gui_helpers import GuiTestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_COL = 12
_NUMBER = ColumnSpec(_COL, ColumnDataType.NUMBER, unit="초", decimals=1, filter_step=0.5, nullable_label="미측정")
_CATEGORY = ColumnSpec(_COL, ColumnDataType.CATEGORY)
_TEXT = ColumnSpec(_COL, ColumnDataType.TEXT)
_BUCKETS = [("a", "구간 A", 3), ("b", "구간 B", 2), ("c", "구간 C", 1)]
_T = TypeVar("_T")


def _need(value: _T | None) -> _T:
    assert value is not None
    return value


class TestColumnFilterPopover(GuiTestCase):
    """열 데이터 타입별 팝오버 입력이 ColumnFilterRule로 변환되는 규칙을 검증"""

    def _popover(
        self,
        spec: ColumnSpec,
        *,
        rule: ColumnFilterRule | None = None,
        values: list[tuple[str, int]] | None = None,
        bounds: tuple[float | None, float | None, int] = (0.5, 8.0, 2),
        buckets: list[tuple[str, str, int]] | None = None,
        special_values: list[tuple[str, int]] | None = None,
        toggle_rect: QRect | None = None,
    ) -> ColumnFilterPopover:
        popover = ColumnFilterPopover(
            _COL,
            "열",
            spec,
            current_rule=rule,
            value_counts=values,
            special_value_counts=special_values,
            numeric_bounds=bounds,
            number_bucket_counts=buckets,
            toggle_rect_global=toggle_rect,
        )
        self.addCleanup(popover.close)
        return popover

    def _emissions(self, popover: ColumnFilterPopover) -> list[tuple[int, ColumnFilterRule | None]]:
        events: list[tuple[int, ColumnFilterRule | None]] = []
        popover.filter_changed.connect(lambda col, rule: events.append((col, rule)))
        return events

    @staticmethod
    def _set_null_mode(popover: ColumnFilterPopover, mode: str) -> None:
        combo = _need(popover.combo_null_mode)
        combo.setCurrentIndex(combo.findData(mode))

    def test_special_values_are_appended_after_the_cell_values(self) -> None:
        """필터 전용 특수 항목이 셀 값 목록 뒤에 따로 붙고 선택 시 필터 값이 되는지 검증"""
        popover = self._popover(
            ColumnSpec(_COL, ColumnDataType.CATEGORY),
            values=[("1836x1008", 2)],
            special_values=[("구간별 여백 차이 존재", 1)],
        )
        checklist = _need(popover._checklist)
        items = [
            _need(checklist.list_values.item(row))
            for row in range(checklist.list_values.count())
        ]
        self.assertEqual(
            [item.text() for item in items],
            ["1836x1008 (2)", "구간별 여백 차이 존재 (1)"],
        )
        self.assertIsNone(popover.build_rule())
        item = _need(checklist.list_values.item(0))
        item.setCheckState(Qt.CheckState.Unchecked)
        rule = popover.build_rule()
        assert rule is not None
        self.assertEqual(rule.selected_values, frozenset({"구간별 여백 차이 존재"}))

    def test_number_rule_depends_on_range_and_null_mode(self) -> None:
        """범위 입력과 미측정 처리 모드의 조합이 규칙으로 변환되고, 조건이 없으면 필터 없음이 됨을 검증"""
        popover = self._popover(_NUMBER)
        spin_min, spin_max = _need(popover.spin_min), _need(popover.spin_max)
        cases = [
            ("default", None, None, None),
            ("include_null", None, None, ColumnFilterRule(include_null_only=True)),
            ("default", 1.0, None, ColumnFilterRule(min_value=1.0, include_null=False)),
            ("include_null", 1.0, 5.0, ColumnFilterRule(min_value=1.0, max_value=5.0, include_null=True)),
            ("measured_only", None, None, ColumnFilterRule(include_null=False)),
            ("null_only", None, None, ColumnFilterRule(null_only=True, include_null=True)),
            ("null_only", 1.0, 5.0, ColumnFilterRule(null_only=True, include_null=True)),
        ]
        for mode, low, high, expected in cases:
            with self.subTest(mode=mode, low=low, high=high):
                spin_min.setValue(low)
                spin_max.setValue(high)
                self._set_null_mode(popover, mode)
                self.assertEqual(popover.build_rule(), expected)

    def test_number_rule_includes_bucket_selection_only_when_partial(self) -> None:
        """구간을 모두 선택하면 구간 조건이 없고, 일부만 선택하면 선택된 구간 키가 규칙에 담김을 검증"""
        popover = self._popover(_NUMBER, buckets=_BUCKETS)
        self.assertIsNone(popover.build_rule())

        item = _need(_need(popover.list_values).item(1))
        item.setCheckState(Qt.CheckState.Unchecked)
        rule = _need(popover.build_rule())
        self.assertEqual(rule.selected_values, frozenset(popover.selected_values()))
        self.assertEqual(len(_need(rule.selected_values)), 2)

    def test_null_only_mode_disables_range_and_bucket_inputs(self) -> None:
        """미측정만 보기 모드에서는 범위와 구간 입력이 잠기고 다른 모드로 바꾸면 풀림을 검증"""
        popover = self._popover(_NUMBER, buckets=_BUCKETS)
        self._set_null_mode(popover, "null_only")
        self.assertFalse(_need(popover.spin_min).isEnabled())
        self.assertFalse(_need(popover.spin_max).isEnabled())
        self.assertFalse(_need(popover.btn_select_all).isEnabled())

        self._set_null_mode(popover, "include_null")
        self.assertTrue(_need(popover.spin_min).isEnabled())
        self.assertTrue(_need(popover.spin_max).isEnabled())
        self.assertTrue(_need(popover.btn_select_all).isEnabled())

    def test_initial_null_mode_follows_existing_rule(self) -> None:
        """기존 규칙으로 팝오버를 열면 미측정 처리 모드가 규칙에 맞게 선택됨을 검증"""
        cases = [
            (ColumnFilterRule(null_only=True), "null_only"),
            (ColumnFilterRule(min_value=1.0), "include_null"),
            (ColumnFilterRule(min_value=1.0, include_null=False), "default"),
            (ColumnFilterRule(include_null=False), "measured_only"),
            (ColumnFilterRule(selected_values=frozenset(), include_null=False), "default"),
        ]
        for rule, expected in cases:
            with self.subTest(expected=expected, rule=rule):
                popover = self._popover(_NUMBER, rule=rule)
                self.assertEqual(_need(popover.combo_null_mode).currentData(), expected)

    def test_numeric_bounds_summary_text(self) -> None:
        """데이터 범위, 단위, 미측정 개수가 있을 때만 해당 문구를 표시함을 검증"""
        cases = [
            (_NUMBER, (0.5, 8.0, 2), "데이터 범위: 0.5 ~ 8.0 초, 미측정 2개"),
            (_NUMBER, (0.5, 8.0, 0), "데이터 범위: 0.5 ~ 8.0 초"),
            (_NUMBER, (None, None, 3), "미측정 3개"),
            (_NUMBER, (1.0, None, 2), "미측정 2개"),
            (_NUMBER, (None, None, 0), "수치 데이터 없음"),
            (ColumnSpec(_COL, ColumnDataType.NUMBER, decimals=0), (1.0, 9.0, 2), "데이터 범위: 1 ~ 9"),
        ]
        for spec, bounds, expected in cases:
            with self.subTest(expected=expected):
                popover = self._popover(spec, bounds=bounds)
                texts = [label.text() for label in popover.findChildren(QLabel)]
                self.assertIn(expected, texts)

    def test_category_rule_is_none_unless_some_value_is_unchecked(self) -> None:
        """전체 선택과 값 없음은 필터 없음이고, 일부 해제하면 남은 값만 허용하는 규칙이 됨을 검증"""
        self.assertIsNone(self._popover(_CATEGORY, values=[]).build_rule())

        popover = self._popover(_CATEGORY, values=[("h264", 3), ("hevc", 1)])
        self.assertIsNone(popover.build_rule())
        _need(_need(popover.list_values).item(0)).setCheckState(Qt.CheckState.Unchecked)
        rule = _need(popover.build_rule())
        self.assertEqual(len(_need(rule.selected_values)), 1)

        _need(_need(popover.list_values).item(1)).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(_need(popover.build_rule()).selected_values, frozenset())

    def test_text_rule_uses_query_and_mode(self) -> None:
        """검색어가 비면 필터 없음이고, 검색어와 모드가 함께 규칙에 담김을 검증"""
        popover = self._popover(_TEXT)
        edit, combo = _need(popover.edit_text_query), _need(popover.combo_text_mode)
        edit.setText("   ")
        self.assertIsNone(popover.build_rule())
        edit.setText("ab")
        combo.setCurrentIndex(combo.findData(TEXT_MODE_ENDS_WITH))
        self.assertEqual(popover.build_rule(), ColumnFilterRule(text_query="ab", text_mode=TEXT_MODE_ENDS_WITH))

    def test_apply_emits_rule_and_clear_resets_inputs(self) -> None:
        """적용은 현재 규칙을 방출하고 해제 버튼 상태를 갱신하며, 해제는 입력을 초기화하고 None을 방출함을 검증"""
        popover = self._popover(_TEXT)
        events = self._emissions(popover)
        edit, combo = _need(popover.edit_text_query), _need(popover.combo_text_mode)
        self.assertFalse(popover.btn_clear.isEnabled())

        edit.setText("abc")
        combo.setCurrentIndex(combo.findData(TEXT_MODE_ENDS_WITH))
        popover.apply_current()
        self.assertEqual(events[-1], (_COL, ColumnFilterRule(text_query="abc", text_mode=TEXT_MODE_ENDS_WITH)))
        self.assertTrue(popover.btn_clear.isEnabled())

        popover.clear_filter()
        self.assertEqual(events[-1], (_COL, None))
        self.assertEqual((edit.text(), combo.currentIndex()), ("", 0))
        self.assertFalse(popover.btn_clear.isEnabled())

    def test_clear_resets_number_inputs_without_intermediate_emissions(self) -> None:
        """수치 팝오버 해제가 범위와 모드를 초기화하고 방출은 None 한 번뿐임을 검증"""
        popover = self._popover(_NUMBER, buckets=_BUCKETS)
        spin_min, spin_max = _need(popover.spin_min), _need(popover.spin_max)
        spin_min.setValue(1.0)
        spin_max.setValue(5.0)
        self._set_null_mode(popover, "null_only")
        _need(_need(popover.list_values).item(0)).setCheckState(Qt.CheckState.Unchecked)
        events = self._emissions(popover)

        popover.clear_filter()
        self.assertEqual(events, [(_COL, None)])
        self.assertIsNone(spin_min.value())
        self.assertIsNone(spin_max.value())
        self.assertEqual(_need(popover.combo_null_mode).currentIndex(), 0)
        self.assertTrue(spin_min.isEnabled())
        self.assertEqual(len(popover.selected_values()), len(_BUCKETS))

    def test_initial_range_and_empty_value_label(self) -> None:
        """기존 규칙의 범위가 입력란에 채워지고, 빈 값은 별도 라벨로 표시됨을 검증"""
        popover = self._popover(_NUMBER, rule=ColumnFilterRule(min_value=2.0, max_value=6.0))
        self.assertEqual((_need(popover.spin_min).value(), _need(popover.spin_max).value()), (2.0, 6.0))
        empty = self._popover(_NUMBER)
        self.assertEqual((_need(empty.spin_min).value(), _need(empty.spin_max).value()), (None, None))

        popover = self._popover(_CATEGORY, values=[("", 2), ("h264", 1)])
        texts = [_need(_need(popover.list_values).item(i)).text() for i in range(2)]
        self.assertTrue(any("(비어 있음)" in text for text in texts), texts)

    def test_width_grows_with_long_values_up_to_cap(self) -> None:
        """짧은 값은 기본 최소 폭, 긴 값은 상한(그림자 여백 포함)까지만 폭이 늘어남을 검증"""
        short = self._popover(_CATEGORY, values=[("h264", 1)])
        long = self._popover(_CATEGORY, values=[("x" * 300, 1)])
        self.assertEqual(short.minimumWidth(), 280)
        self.assertEqual(short.maximumWidth(), 440 + 12)
        self.assertEqual(long.minimumWidth(), 440 + 12)

    def test_clear_resets_option_search_text(self) -> None:
        """범주형 해제가 값 검색창 입력도 비움을 검증"""
        popover = self._popover(_CATEGORY, values=[("h264", 1), ("hevc", 1)])
        search = _need(popover.edit_option_search)
        search.setText("h2")
        popover.clear_filter()
        self.assertEqual(search.text(), "")

    def test_popup_positions_below_anchor_and_stays_on_screen(self) -> None:
        """그림자 여백을 보정한 위치에 열리고, 화면 밖 좌표는 가용 영역 안으로 당겨짐을 검증"""
        screen = _need(QApplication.primaryScreen()).availableGeometry()
        popover = self._popover(_CATEGORY, values=[("h264", 1)])

        popover.popup_at(QPoint(screen.left() + 100, screen.top() + 100))
        self.assertEqual(popover.pos(), QPoint(screen.left() + 94, screen.top() + 98))

        for anchor in (QPoint(screen.right() + 5000, screen.bottom() + 5000), QPoint(screen.left() - 500, screen.top() - 500)):
            with self.subTest(anchor=anchor):
                popover.popup_at(anchor)
                self.assertTrue(screen.contains(popover.geometry()), popover.geometry())

    def test_event_filter_closes_on_escape_outside_click_and_deactivate(self) -> None:
        """ESC, 바깥 클릭, 앱 비활성화로 닫히고 내부 클릭은 유지되며, 토글 영역 클릭만 이벤트를 소비함을 검증"""
        toggle = QRect(4900, 4900, 50, 50)
        popover = self._popover(_CATEGORY, values=[("h264", 1)], toggle_rect=toggle)

        def click(global_pos: QPoint) -> QMouseEvent:
            point = QPointF(global_pos)
            return QMouseEvent(
                QMouseEvent.Type.MouseButtonPress, point, point,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )

        popover.show()
        self.assertFalse(popover.eventFilter(popover, click(popover.mapToGlobal(QPoint(20, 20)))))
        self.assertTrue(popover.isVisible())
        self.assertFalse(popover.eventFilter(popover, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)))
        self.assertTrue(popover.isVisible())

        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(popover.eventFilter(popover, escape))
        self.assertFalse(popover.isVisible())

        popover.show()
        self.assertFalse(popover.eventFilter(popover, click(QPoint(5000, 5000))))
        self.assertFalse(popover.isVisible())

        popover.show()
        self.assertTrue(popover.eventFilter(popover, click(QPoint(4920, 4920))))
        self.assertFalse(popover.isVisible())

        popover.show()
        popover.eventFilter(popover, QEvent(QEvent.Type.ApplicationDeactivate))
        self.assertFalse(popover.isVisible())
        self.assertFalse(popover.eventFilter(popover, escape))

    def test_closing_flushes_pending_debounced_input(self) -> None:
        """입력 직후 닫기 버튼이나 숨김이 디바운스 대기 중인 입력을 한 번 반영함을 검증"""
        for close in (lambda p: p.btn_close.click(), lambda p: p.hide()):
            popover = self._popover(_TEXT)
            popover.show()
            events = self._emissions(popover)
            _need(popover.edit_text_query).setText("pending")
            self.assertEqual(events, [])
            close(popover)
            self.assertEqual(events, [(_COL, ColumnFilterRule(text_query="pending"))])
            self.assertFalse(popover.isVisible())

    def test_quality_value_null_modes_are_selectable(self) -> None:
        """품질값 없음 열에서 값 없음만/측정된 값만 고를 수 있고 그 규칙이 만들어짐을 검증"""
        spec = ColumnSpec(_COL, ColumnDataType.NUMBER, decimals=0, filter_step=0.1, nullable_label="품질값 없음")
        popover = self._popover(spec, bounds=(None, None, 3))
        combo = _need(popover.combo_null_mode)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], [
            "기본 (조건 입력 시 제외)",
            "품질값 없음 포함",
            "측정된 항목만",
            "품질값 없음 항목만 보기",
        ])
        for mode, expected in (
            ("null_only", ColumnFilterRule(null_only=True, include_null=True)),
            ("measured_only", ColumnFilterRule(include_null=False)),
        ):
            with self.subTest(mode=mode):
                self._set_null_mode(popover, mode)
                self.assertEqual(popover.build_rule(), expected)
        self.assertEqual(
            _need(popover.build_rule()).describe(spec), "품질값 없음 제외"
        )

    def test_null_mode_only_rule_is_kept_and_restored(self) -> None:
        """조건 없이 품질값 없음 포함만 고른 규칙이 적용되고 다시 열 때 같은 모드로 복원됨을 검증"""
        spec = ColumnSpec(_COL, ColumnDataType.NUMBER, decimals=0, filter_step=0.1, nullable_label="품질값 없음")
        popover = self._popover(spec, bounds=(None, None, 3))
        events = self._emissions(popover)
        self._set_null_mode(popover, "include_null")
        rule = events[-1][1]
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertTrue(rule.include_null)
        self.assertTrue(rule.is_active(ColumnDataType.NUMBER))

        popover.hide()
        reopened = self._popover(spec, rule=rule)
        self.assertEqual(_need(reopened.combo_null_mode).currentData(), "include_null")

    def test_null_mode_choice_without_nullable_label_stays_no_filter(self) -> None:
        """nullable_label 이 없는 수치 열은 조건이 없으면 필터 없음이고 null 전용 모드만 규칙이 됨을 검증"""
        popover = self._popover(ColumnSpec(_COL, ColumnDataType.NUMBER, decimals=1))
        self.assertIsNone(popover.combo_null_mode)
        self.assertIsNone(popover.build_rule())

    def test_click_on_the_null_mode_list_keeps_the_popover_open(self) -> None:
        """품질값 없음 선택 목록을 누르는 동안 팝오버가 닫히지 않아 선택이 취소되지 않음을 검증

        - 콤보 목록은 별도 Popup 창으로 떠서 팝오버의 같은 창 자식이 아님
        """
        spec = ColumnSpec(_COL, ColumnDataType.NUMBER, decimals=0, filter_step=0.1, nullable_label="품질값 없음")
        popover = self._popover(spec)
        popover.show()
        combo = _need(popover.combo_null_mode)
        combo.showPopup()
        try:
            view = _need(combo.view())
            point = QPointF(4000.0, 4000.0)
            click = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                point,
                point,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.assertFalse(popover.isAncestorOf(view))
            self.assertTrue(popover._has_active_child_popup())
            self.assertFalse(popover.eventFilter(popover, click))
            self.assertTrue(popover.isVisible())
        finally:
            combo.hidePopup()


if __name__ == "__main__":
    unittest.main()
