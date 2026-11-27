"""작업 중 분류 설정이 바뀔 때 사라진 열 필터 선택 값 정리를 검증"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.table.column_filter_values import prune_stale_values
from src.gui.table.column_filters import ColumnFilterRule, rule_to_snapshot
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    ACTIVE_FRAME_FAILED_TEXT,
    ACTIVE_FRAME_FULL_TEXT,
)
from src.gui.table.numeric_buckets import NumericBucketKind, build_numeric_buckets, resolve_numeric_bucket
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_LOGGER_NAME: str = "src.gui.table.column_filter_values"


def _build_proxy(config: ClassifierConfig) -> VideoSortFilterProxyModel:
    """과화질 판정 대상 영상 한 개를 담은 표 모델에 연결한 프록시 생성"""
    model = VideoTableModel(config=config)
    model.set_data_source(
        [make_eval(video=make_video(filename="a.mp4"))], root_dir="C:/work"
    )
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    return proxy


def _ratio_filter_key(config: ClassifierConfig, ratio: float) -> str:
    """측정 비트레이트 비율이 현재 설정에서 속하는 구간 키"""
    bucket = resolve_numeric_bucket(
        ratio, build_numeric_buckets(NumericBucketKind.RATIO, config)
    )
    assert bucket is not None
    return bucket.key


class ConfigChangePruneTests(unittest.TestCase):
    """설정 변경으로 구간 키가 사라져도 활성 열 필터가 표를 비우지 않는지 검증"""

    def test_stale_bucket_key_is_dropped_when_the_config_changes(self) -> None:
        """과화질 배수를 3.0으로 바꿔 구간 키가 사라져도 행이 남고 필터가 꺼지는지 검증"""
        base = ClassifierConfig()
        proxy = _build_proxy(base)
        key = _ratio_filter_key(base, 2.0)
        proxy.set_column_filter(
            VideoTableModel.COL_RATIO,
            ColumnFilterRule(selected_values=frozenset({key})),
        )
        self.assertEqual(proxy.rowCount(), 1)
        changes: list[bool] = []
        proxy.column_filters_changed.connect(lambda: changes.append(True))
        changed = replace(
            base, thresholds=replace(base.thresholds, over_bitrate_ratio=3.0)
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            proxy.sourceModel().set_config(changed)  # type: ignore[union-attr]
        self.assertEqual(proxy.rowCount(), 1)
        self.assertIsNone(proxy.get_column_filter(VideoTableModel.COL_RATIO))
        self.assertEqual(proxy.active_column_filter_count(), 0)
        self.assertIn("비트레이트 비율", logs.output[0])
        # - 필터가 꺼진 사실을 창이 저장 상태에 반영하도록 변경 신호를 냄
        self.assertTrue(changes)

    def test_numeric_bounds_survive_when_every_bucket_key_is_dropped(self) -> None:
        """구간 키가 모두 사라져도 수치 범위는 남겨 필터가 유지되는지 검증"""
        base = ClassifierConfig()
        proxy = _build_proxy(base)
        key = _ratio_filter_key(base, 2.0)
        proxy.set_column_filter(
            VideoTableModel.COL_RATIO,
            ColumnFilterRule(
                selected_values=frozenset({key}),
                min_value=1.5,
                max_value=3.0,
                include_null=False,
            ),
        )
        changed = replace(
            base, thresholds=replace(base.thresholds, over_bitrate_ratio=3.0)
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            proxy.sourceModel().set_config(changed)  # type: ignore[union-attr]
        rule = proxy.get_column_filter(VideoTableModel.COL_RATIO)
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertIsNone(rule.selected_values)
        self.assertEqual(rule.min_value, 1.5)
        self.assertEqual(rule.max_value, 3.0)
        self.assertFalse(rule.include_null)
        self.assertEqual(proxy.rowCount(), 1)

    def test_bucket_key_that_still_exists_survives_a_config_change(self) -> None:
        """수치 구간 키를 만들지 않는 설정 변경은 구간 필터를 그대로 두는지 검증"""
        base = ClassifierConfig()
        proxy = _build_proxy(base)
        key = _ratio_filter_key(base, 2.0)
        rule = ColumnFilterRule(selected_values=frozenset({key}))
        proxy.set_column_filter(VideoTableModel.COL_RATIO, rule)
        changed = replace(
            base, thresholds=replace(base.thresholds, keyframe_interval_sec=5.0)
        )
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            proxy.sourceModel().set_config(changed)  # type: ignore[union-attr]
        self.assertEqual(proxy.get_column_filter(VideoTableModel.COL_RATIO), rule)
        self.assertEqual(proxy.rowCount(), 1)

    def test_live_and_stale_bucket_keys_keep_only_the_live_one(self) -> None:
        """여러 구간 키 중 새 설정에도 있는 키만 남아 필터가 유지되는지 검증"""
        base = ClassifierConfig()
        proxy = _build_proxy(base)
        stale = _ratio_filter_key(base, 2.0)
        live = _ratio_filter_key(base, 0.5)
        self.assertNotEqual(stale, live)
        proxy.set_column_filter(
            VideoTableModel.COL_RATIO,
            ColumnFilterRule(selected_values=frozenset({stale, live})),
        )
        changed = replace(
            base, thresholds=replace(base.thresholds, over_bitrate_ratio=3.0)
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            proxy.sourceModel().set_config(changed)  # type: ignore[union-attr]
        rule = proxy.get_column_filter(VideoTableModel.COL_RATIO)
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(rule_to_snapshot(rule).selected_values, (live,))
        # - 2.0 비율 행이 3.0 이상 구간으로 옮겨 가 더는 선택 구간에 들지 않음
        self.assertEqual(proxy.rowCount(), 0)


class ActiveFrameFilterValueTests(unittest.TestCase):
    """활성 화면 열 필터가 현재 표시 문구를 사라진 값으로 버리지 않는지 검증"""

    def test_failed_detection_label_is_kept(self) -> None:
        """감지 실패 문구만 고른 필터가 복원, 설정 변경에서 살아남는지 검증"""
        snapshot = ColumnFilterSnapshot(selected_values=(ACTIVE_FRAME_FAILED_TEXT,))
        kept = prune_stale_values(snapshot, ACTIVE_FRAME_COLUMN, None)
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept.selected_values, (ACTIVE_FRAME_FAILED_TEXT,))

    def test_failed_detection_label_survives_with_full_text(self) -> None:
        """감지 실패 문구와 닫힌 문구를 함께 고른 필터가 모두 남는지 검증"""
        snapshot = ColumnFilterSnapshot(
            selected_values=(ACTIVE_FRAME_FAILED_TEXT, ACTIVE_FRAME_FULL_TEXT)
        )
        kept = prune_stale_values(snapshot, ACTIVE_FRAME_COLUMN, None)
        self.assertEqual(
            None if kept is None else kept.selected_values,
            (ACTIVE_FRAME_FAILED_TEXT, ACTIVE_FRAME_FULL_TEXT),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
