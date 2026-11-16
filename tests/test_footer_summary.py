"""푸터 요약의 범위 그룹 선택 규칙, 표시 행과 선택 행 동시 표시, 빈 목록 합계를 검증"""

import unittest

from src.gui.footer_summary import (
    EMPTY_FOOTER_SUMMARY,
    TableRangeSummary,
    build_footer_summary_spec,
    summarize_rows,
)


def _range(row_count: int, *, encode: int = 0) -> TableRangeSummary:
    return TableRangeSummary(
        row_count=row_count,
        orig_mb=100.0 * row_count,
        duration_sec=60.0 * row_count,
        column_saved_mb=10.0 * row_count,
        column_encode_sec=30.0 * row_count,
        encode_count=encode,
        pass_count=row_count - encode,
        target_saved_mb=5.0 * row_count,
        target_encode_sec=15.0 * row_count,
    )


class FooterSummarySpecTests(unittest.TestCase):
    def test_empty_when_no_rows_or_no_filter_and_selection(self) -> None:
        """분석 전이거나 필터와 선택이 모두 없으면 푸터 요약을 비움을 검증"""
        self.assertIs(
            build_footer_summary_spec(total_count=0, visible=_range(0), selected=None),
            EMPTY_FOOTER_SUMMARY,
        )
        self.assertIs(
            build_footer_summary_spec(total_count=3, visible=_range(3), selected=None),
            EMPTY_FOOTER_SUMMARY,
        )

    def test_filter_only_shows_visible_group(self) -> None:
        """필터만 적용되면 표시 행 그룹 하나만 표시함을 검증"""
        spec = build_footer_summary_spec(total_count=5, visible=_range(2), selected=None)
        self.assertEqual([group.scope_label for group in spec.groups], ["표시 2개"])
        self.assertNotIn("선택", spec.plain_text)

    def test_selection_without_filter_shows_selected_group_only(self) -> None:
        """필터 없이 선택만 있으면 선택 행 그룹 하나만 표시함을 검증"""
        spec = build_footer_summary_spec(total_count=3, visible=_range(3), selected=_range(1))
        self.assertEqual([group.scope_label for group in spec.groups], ["선택 1개"])
        self.assertIn("선택 행 재생 1분당", spec.tooltip)
        self.assertIn("빈 여백, 카드 클릭", spec.tooltip)

    def test_mixed_filter_and_selection_shows_both_groups_in_order(self) -> None:
        """필터와 선택이 함께 있으면 표시 행과 선택 행 그룹을 이 순서로 모두 표시함을 검증"""
        spec = build_footer_summary_spec(total_count=5, visible=_range(4), selected=_range(2))
        self.assertEqual(
            [group.scope_label for group in spec.groups], ["표시 4개", "선택 2개"]
        )
        self.assertIn(" / ", spec.plain_text)
        self.assertIn("표시 행 재생 1분당", spec.tooltip)
        self.assertIn("선택 행 재생 1분당", spec.tooltip)

    def test_empty_visible_without_selection_shows_message(self) -> None:
        """필터 결과가 없고 선택도 없으면 지표 없이 안내 툴팁만 표시함을 검증"""
        spec = build_footer_summary_spec(total_count=5, visible=_range(0), selected=None)
        self.assertEqual(spec.groups[0].scope_label, "표시 0개")
        self.assertEqual(spec.groups[0].metrics, ())
        self.assertIn("일치하는 영상이 없습니다", spec.tooltip)

    def test_summarize_rows_handles_empty_input(self) -> None:
        """빈 행 목록의 합계가 모두 0이고 행 수가 0임을 검증"""
        summary = summarize_rows([])
        self.assertEqual(summary.row_count, 0)
        self.assertEqual(summary.encode_count, 0)
        self.assertEqual(summary.pass_count, 0)
        self.assertEqual(summary.orig_mb, 0.0)


if __name__ == "__main__":
    unittest.main()
