"""테이블 열 필터 값 집계(고유 값, 수치 범위, 구간 버킷) 검증"""

import unittest

from src.core.config import ClassifierConfig
from src.core.models import QualityZone
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_value_collection import (
    collect_numeric_bounds,
    collect_numeric_bucket_counts,
    collect_value_counts,
)
from tests._factories import make_eval, make_video


class TableValueCollectionTests(unittest.TestCase):
    """필터 팝오버가 쓰는 값 집계 함수의 결과 검증"""

    def setUp(self) -> None:
        self.evals = [
            make_eval(video=make_video(filename="a.mp4", width=1920, height=1080, codec="h264"), quality_zone=QualityZone.OVER),
            make_eval(
                video=make_video(filename="b.mp4", width=1280, height=720, codec="hevc", keyframe_interval=None),
                quality_zone=QualityZone.OPTIMAL,
            ),
            make_eval(
                video=make_video(filename="c.mp4", width=1920, height=800, codec="h264"),
                quality_zone=QualityZone.OPTIMAL,
            ),
        ]
        self.get_value = VideoTableModel.get_filter_value

    def test_enum_counts_follow_preset_order(self) -> None:
        counts = collect_value_counts(
            self.evals,
            VideoTableModel.COL_ZONE,
            VideoTableModel.get_column_spec(VideoTableModel.COL_ZONE),
            lambda ev, col: (self.get_value(ev, col),),
        )
        self.assertEqual(counts[:2], [("과화질", 1), ("적정", 2)])

    def test_category_counts_are_sorted(self) -> None:
        counts = collect_value_counts(
            self.evals,
            VideoTableModel.COL_CODEC,
            VideoTableModel.get_column_spec(VideoTableModel.COL_CODEC),
            lambda ev, col: (self.get_value(ev, col),),
        )
        self.assertEqual(counts, [("h264", 2), ("hevc", 1)])

    def test_numeric_bounds_count_nullable_rows(self) -> None:
        self.assertEqual(
            collect_numeric_bounds(self.evals, VideoTableModel.COL_WIDTH, self.get_value),
            (1280.0, 1920.0, 0),
        )
        self.assertEqual(
            collect_numeric_bounds(self.evals, VideoTableModel.COL_KEYFRAME, self.get_value),
            (1.0, 1.0, 1),
        )
        self.assertEqual(collect_numeric_bounds([], VideoTableModel.COL_WIDTH, self.get_value), (None, None, 0))

    def test_numeric_bucket_counts_skip_empty_buckets(self) -> None:
        spec = VideoTableModel.get_column_spec(VideoTableModel.COL_WIDTH)
        buckets = collect_numeric_bucket_counts(self.evals, VideoTableModel.COL_WIDTH, spec, ClassifierConfig(), self.get_value)
        self.assertTrue(buckets)
        self.assertTrue(all(count > 0 for _key, _label, count in buckets))
        self.assertEqual(
            collect_numeric_bucket_counts(
                self.evals, VideoTableModel.COL_ZONE, VideoTableModel.get_column_spec(VideoTableModel.COL_ZONE), ClassifierConfig(), self.get_value
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
