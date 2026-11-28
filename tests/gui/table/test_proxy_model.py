"""검색과 열 필터, 다중 정렬 우선순위를 담당하는 표 프록시 모델 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.encode.encode_batch_book import STATUS_DONE, STATUS_PENDING, EncodeBatchBook
from src.core.encode.encode_state import EncodeState
from src.core.workspace_paths import resolve_source_filepath
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class ProxyModelTests(unittest.TestCase):
    """검색, 열 필터, 정렬 우선순위, 표시 행 순회 검증"""

    def setUp(self) -> None:
        self.evals = [
            make_eval(video=make_video(filename="a.mp4", codec="h264")),
            make_eval(video=make_video(filename="b.mkv", codec="hevc")),
            make_eval(video=make_video(filename="c.mp4", codec="mpeg4")),
        ]
        self.model = VideoTableModel(config=ClassifierConfig())
        self.model.set_data_source(self.evals, root_dir="C:/work")
        self.proxy = VideoSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)

    def test_search_and_column_filters(self) -> None:
        self.proxy.set_search_text("b.mkv")
        self.assertEqual([ev.video.filename for ev in self.proxy.iter_visible_evaluations()], ["b.mkv"])
        self.proxy.set_search_text("")
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264", "mpeg4"})))
        visible = {ev.video.filename for ev in self.proxy.iter_visible_evaluations()}
        self.assertEqual(visible, {"a.mp4", "c.mp4"})
        self.assertEqual(self.proxy.active_column_filter_count(), 1)
        self.assertTrue(self.proxy.is_any_filter_active())
        self.assertIn("선택:", self.proxy.get_column_filter_summaries()[VideoTableModel.COL_CODEC])
        self.proxy.clear_column_filter(VideoTableModel.COL_CODEC)
        self.assertIsNone(self.proxy.get_column_filter(VideoTableModel.COL_CODEC))
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        self.proxy.clear_all_column_filters()
        self.assertEqual(self.proxy.active_column_filter_count(), 0)

    def test_search_text_signal_carries_the_typed_text(self) -> None:
        """검색어가 바뀌면 표기 그대로를 한 번만 알리고 같은 값은 다시 알리지 않는지 검증"""
        received: list[str] = []
        self.proxy.search_text_changed.connect(received.append)
        self.proxy.set_search_text("  B.MKV  ")
        self.proxy.set_search_text("  B.MKV  ")
        self.proxy.set_search_text("")
        self.assertEqual(received, ["B.MKV", ""])
        self.assertEqual(self.proxy.search_text, "")

    def test_sort_stack_is_delegated_to_source_model(self) -> None:
        from PyQt6.QtCore import Qt

        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.model.sort_stack, [(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)])
        self.proxy.sort(-1)
        self.assertEqual(self.model.sort_stack, [])

    def test_config_change_invalidates_filters(self) -> None:
        changes: list[bool] = []
        self.proxy.column_filters_changed.connect(lambda: changes.append(True))
        self.proxy.set_column_filter(VideoTableModel.COL_CODEC, ColumnFilterRule(selected_values=frozenset({"h264"})))
        changes.clear()
        self.model.set_config(ClassifierConfig())
        self.assertEqual(changes, [True])
        self.assertEqual(self.proxy.get_active_column_filters(), {VideoTableModel.COL_CODEC: ColumnFilterRule(selected_values=frozenset({"h264"}))})
        self.proxy.setSourceModel(None)
        self.assertEqual(self.proxy.iter_visible_evaluations(), [])


    def test_encode_state_filter_matches_the_cell_label(self) -> None:
        """인코딩 상태 열 필터가 셀 표기와 같은 값을 비교해 선택한 상태의 행만 남기는지 검증"""
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source(
            [
                make_eval(video=make_video(filename="a.mp4")),
                make_eval(video=make_video(filename="b.mp4")),
                make_eval(video=make_video(filename="c.mp4")),
            ],
            root_dir="C:/work",
        )
        book = EncodeBatchBook()
        book.start(
            [
                resolve_source_filepath("C:/work", "a.mp4"),
                resolve_source_filepath("C:/work", "b.mp4"),
            ]
        )
        book.apply(resolve_source_filepath("C:/work", "a.mp4"), EncodeState.PENDING)
        book.apply(resolve_source_filepath("C:/work", "b.mp4"), EncodeState.DONE)
        model.set_encode_book(book)
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_column_filter(
            VideoTableModel.COL_ENCODE_STATE,
            ColumnFilterRule(selected_values=frozenset({STATUS_DONE})),
        )
        self.assertEqual(
            [ev.video.filename for ev in proxy.iter_visible_evaluations()], ["b.mp4"]
        )
        proxy.set_column_filter(
            VideoTableModel.COL_ENCODE_STATE,
            ColumnFilterRule(selected_values=frozenset({"", STATUS_PENDING})),
        )
        self.assertEqual(
            [ev.video.filename for ev in proxy.iter_visible_evaluations()],
            ["a.mp4", "c.mp4"],
        )
        counts = dict(model.collect_column_value_counts(VideoTableModel.COL_ENCODE_STATE))
        self.assertEqual(counts[STATUS_DONE], 1)
        self.assertEqual(counts[""], 1)

    def test_review_only_chip_filters_flagged_rows(self) -> None:
        """여백 확인 필요 칩이 켜지면 표시된 영상만 남기고 검색과 함께 AND로 거르는지 검증"""
        from src.core.analysis.frame_analysis import ActiveRegion
        from src.core.analysis.frame_analysis_runner import FrameAnalysisResult

        flagged = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source(
            [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))],
            root_dir="C:/work",
        )
        model.set_frame_analysis_results({"a.mp4": flagged})
        model.set_review_flagged(["a.mp4"])
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_review_only(True)
        self.assertEqual([ev.video.filename for ev in proxy.iter_visible_evaluations()], ["a.mp4"])
        self.assertTrue(proxy.is_any_filter_active())
        proxy.set_search_text("b.mp4")
        self.assertEqual(proxy.iter_visible_evaluations(), [])
        proxy.set_search_text("")
        proxy.set_review_only(False)
        self.assertEqual(
            [ev.video.filename for ev in proxy.iter_visible_evaluations()], ["a.mp4", "b.mp4"]
        )
        self.assertFalse(proxy.review_only)

    def test_active_frame_filter_matches_size_or_review_name(self) -> None:
        """활성 화면 열 필터가 크기와 여백 확인 필요 값 중 하나만 골라도 그 행을 남기는지 검증"""
        from src.core.analysis.frame_analysis import ActiveRegion
        from src.core.analysis.frame_analysis_runner import FrameAnalysisResult

        flagged = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source(
            [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))],
            root_dir="C:/work",
        )
        model.set_frame_analysis_results({"a.mp4": flagged})
        model.set_review_flagged(["a.mp4"])
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_column_filter(
            VideoTableModel.COL_ACTIVE_FRAME,
            ColumnFilterRule(selected_values=frozenset({"여백 확인 필요"})),
        )
        self.assertEqual([ev.video.filename for ev in proxy.iter_visible_evaluations()], ["a.mp4"])
        proxy.set_column_filter(
            VideoTableModel.COL_ACTIVE_FRAME,
            ColumnFilterRule(selected_values=frozenset({"1836x1008"})),
        )
        self.assertEqual([ev.video.filename for ev in proxy.iter_visible_evaluations()], ["a.mp4"])
        proxy.set_column_filter(
            VideoTableModel.COL_ACTIVE_FRAME,
            ColumnFilterRule(selected_values=frozenset({"1836x1008", ""})),
        )
        self.assertEqual(
            [ev.video.filename for ev in proxy.iter_visible_evaluations()], ["a.mp4", "b.mp4"]
        )


    def test_refilter_reapplies_the_review_chip_after_flags_change(self) -> None:
        """표시 집합이 바뀐 뒤 refilter를 부르면 칩 필터가 새 집합으로 다시 거르는지 검증"""
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source(
            [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))],
            root_dir="C:/work",
        )
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_review_only(True)
        self.assertEqual(proxy.iter_visible_evaluations(), [])
        # - 표 모델의 신호가 프록시 refilter를 부르는 경로를 직접 재현
        model.review_flags_changed.connect(proxy.refilter)
        model.set_review_flagged(["b.mp4"])
        self.assertEqual(
            [ev.video.filename for ev in proxy.iter_visible_evaluations()], ["b.mp4"]
        )


class ProxyModelChipFilterTests(unittest.TestCase):
    """두 칩 OR 필터, 고정 행 수명, 확인 행 표시 수명 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel(config=ClassifierConfig())
        self.model.set_data_source(
            [
                make_eval(video=make_video(filename="review.mp4")),
                make_eval(video=make_video(filename="vary.mp4")),
                make_eval(video=make_video(filename="both.mp4")),
                make_eval(video=make_video(filename="plain.mp4")),
            ],
            root_dir="C:/work",
        )
        self.model.set_review_flagged(["review.mp4", "both.mp4"])
        self.model.set_vary_flagged(["vary.mp4", "both.mp4"])
        self.proxy = VideoSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)

    def _visible(self) -> list[str]:
        return [ev.video.filename for ev in self.proxy.iter_visible_evaluations()]

    def test_two_chips_union_their_rows(self) -> None:
        self.proxy.set_chip_filters(review=True, vary=False)
        self.assertEqual(self._visible(), ["review.mp4", "both.mp4"])
        self.proxy.set_chip_filters(review=False, vary=True)
        self.assertEqual(self._visible(), ["vary.mp4", "both.mp4"])
        self.proxy.set_chip_filters(review=True, vary=True)
        self.assertEqual(self._visible(), ["review.mp4", "vary.mp4", "both.mp4"])

    def test_sticky_rows_stay_while_a_chip_is_active(self) -> None:
        self.proxy.set_chip_filters(review=True, vary=False)
        self.model.set_review_flagged(["both.mp4"])
        self.assertEqual(self._visible(), ["both.mp4"])

        self.proxy.add_sticky({"review.mp4": "manual"})
        self.assertEqual(self._visible(), ["review.mp4", "both.mp4"])
        self.assertEqual(self.proxy.sticky_acknowledged(), {"review.mp4": "manual"})

        # - 다른 칩으로 바꿔도 이번 세션의 확인 행은 남음
        self.proxy.set_chip_filters(review=False, vary=True)
        self.assertIn("review.mp4", self._visible())

    def test_discard_sticky_drops_only_the_matching_acknowledgement_kind(self) -> None:
        self.proxy.set_chip_filters(review=True, vary=False)
        self.proxy.add_sticky({"review.mp4": "full", "both.mp4": "manual"})

        events: list[int] = []
        self.proxy.sticky_changed.connect(lambda: events.append(1))
        self.proxy.discard_sticky(["review.mp4", "both.mp4"], kind="full")
        self.assertEqual(self.proxy.sticky_acknowledged(), {"both.mp4": "manual"})
        self.assertEqual(events, [1])

        # - 맞는 고정이 없으면 다시 평가하지 않고 알리지도 않음
        self.proxy.discard_sticky(["review.mp4"], kind="full")
        self.assertEqual(events, [1])

    def test_sticky_rows_clear_on_chip_off_and_reset(self) -> None:
        self.proxy.set_chip_filters(review=True, vary=False)
        self.proxy.add_sticky({"review.mp4": "reset"})
        self.assertEqual(self.proxy.sticky_acknowledged(), {"review.mp4": "reset"})

        events: list[int] = []
        self.proxy.sticky_changed.connect(lambda: events.append(1))
        self.proxy.set_chip_filters(review=False, vary=False)
        self.assertEqual(self.proxy.sticky_acknowledged(), {})
        self.assertEqual(events, [1])

        self.proxy.set_chip_filters(review=True, vary=False)
        self.proxy.add_sticky({"both.mp4": "full"})
        self.proxy.reset_chip_filters()
        self.assertEqual(self.proxy.sticky_acknowledged(), {})
        self.assertFalse(self.proxy.chip_filter_active)
        # - 고정 추가 알림까지 세어 고정이 바뀌는 세 시점에만 알림
        self.assertEqual(len(events), 3)

    def test_sticky_rows_clear_when_the_file_list_changes(self) -> None:
        self.proxy.set_chip_filters(review=True, vary=False)
        self.proxy.add_sticky({"review.mp4": "manual"})
        self.model.set_data_source(
            [make_eval(video=make_video(filename="other.mp4"))], root_dir="C:/work"
        )
        self.assertEqual(self.proxy.sticky_acknowledged(), {})

    def test_add_sticky_is_ignored_while_no_chip_is_active(self) -> None:
        self.proxy.add_sticky({"review.mp4": "manual"})
        self.assertEqual(self.proxy.sticky_acknowledged(), {})

    def test_acknowledged_kind_only_shows_outside_the_chip_filter_is_none(self) -> None:
        self.model.set_chip_filters({"review.mp4": "manual"}, active=False)
        self.assertIsNone(self.model.acknowledged_kind("review.mp4"))
        self.model.set_chip_filters({"review.mp4": "manual"}, active=True)
        self.assertEqual(self.model.acknowledged_kind("review.mp4"), "manual")


class ProxyModelFilterHintTests(unittest.TestCase):
    """필터 활성 여부가 검색, 열 필터, 여백 확인 필요 칩을 모두 반영하는지 검증"""

    def test_any_filter_active_counts_the_review_chip(self) -> None:
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source(
            [make_eval(video=make_video(filename="a.mp4"))], root_dir="C:/work"
        )
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        self.assertFalse(proxy.is_any_filter_active())
        proxy.set_review_only(True)
        self.assertTrue(proxy.is_any_filter_active())
        proxy.set_review_only(False)
        self.assertFalse(proxy.is_any_filter_active())


if __name__ == "__main__":
    unittest.main()
