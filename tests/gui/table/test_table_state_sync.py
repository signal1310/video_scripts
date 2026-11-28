"""표 열 필터와 정렬 우선순위의 저장 형식 변환, 복원 시 사라진 선택 값 정리를 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.encode.encode_batch_book import STATUS_ENCODE_TARGET, EncodeBatchBook
from src.core.encode.encode_gate import DONE_SURGE_BADGE
from src.core.encode.encode_previous_discard import PREVIOUS_DISCARD_LABEL
from src.core.encode.encode_state import EncodeState
from src.core.settings.ui_state import TEXT_MODE_STARTS_WITH, ColumnFilterSnapshot
from src.core.smart_action import SmartAction
from src.core.workspace_paths import resolve_source_filepath
from src.gui.table.column_filters import ColumnFilterRule, rule_to_snapshot
from src.gui.table.frame_analysis_columns import ACTIVE_FRAME_FULL_TEXT, ACTIVE_FRAME_VR_TEXT
from src.gui.table.numeric_buckets import NumericBucketKind, build_numeric_buckets, resolve_numeric_bucket
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_columns import COL_DEF_MAP, ENCODE_STATE_LABELS
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_state_sync import (
    apply_saved_filters,
    apply_saved_sort,
    snapshot_column_filters,
    snapshot_sort_keys,
)
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_STALE_ACTION_VALUE: str = "품질 23.9 인코딩"
_STALE_BUCKET_KEY: str = "ratio:lt_0.10"
_STALE_ACTIVE_FRAME_VALUE: str = "전체"
_STALE_ENCODE_STATE_VALUE: str = "이미 재인코딩"
_LOGGER_NAME: str = "src.gui.table.table_state_sync"


def _build_proxy() -> VideoSortFilterProxyModel:
    """파일 두 개를 담은 표 모델에 연결한 프록시 생성"""
    model = VideoTableModel()
    model.set_data_source(
        [
            make_eval(video=make_video(filename="a.mp4", codec="h264")),
            make_eval(video=make_video(filename="b.mp4", codec="hevc")),
        ],
        root_dir="C:/work",
    )
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    return proxy


def _frame_result(
    *, active: ActiveRegion | None = None, vr: bool = False
) -> FrameAnalysisResult:
    """화질 분석 결과 표본, 기본값은 화면 여백가 없는 전체 화면"""
    return FrameAnalysisResult(
        active_region=active or ActiveRegion(1920, 1080, 0, 0, False),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier="1080p",
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=vr,
    )


def _build_frame_proxy(
    results: dict[str, FrameAnalysisResult] | None = None,
) -> VideoSortFilterProxyModel:
    """화질 분석 결과를 넣은 표 모델에 연결한 프록시 생성

    - 기본값은 a.mp4 가 전체 화면, b.mp4 가 화면 여백가 있는 화면
    """
    proxy = _build_proxy()
    model = proxy.sourceModel()
    assert isinstance(model, VideoTableModel)
    model.set_frame_analysis_results(
        results
        if results is not None
        else {
            "a.mp4": _frame_result(),
            "b.mp4": _frame_result(active=ActiveRegion(1920, 800, 0, 140, True)),
        }
    )
    return proxy


def _visible(proxy: VideoSortFilterProxyModel) -> list[str]:
    """프록시가 보여 주는 파일 이름 목록"""
    return [ev.video.filename for ev in proxy.iter_visible_evaluations()]


class SavedFilterRestoreTests(unittest.TestCase):
    """저장된 열 필터 복원이 사라진 선택 값 때문에 표를 비우지 않는지 검증"""

    def setUp(self) -> None:
        self.proxy = _build_proxy()

    def test_stale_selected_values_are_dropped_so_the_table_stays_filled(self) -> None:
        """이전 표기의 선택 값만 있던 규칙은 버려져 모든 행이 보이고 안내 한 줄을 남기는지 검증"""
        stale = {"action": ColumnFilterSnapshot(selected_values=(_STALE_ACTION_VALUE,))}
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            apply_saved_filters(self.proxy, stale)
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])
        self.assertEqual(self.proxy.active_column_filter_count(), 0)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("권장 처리", logs.output[0])

    def test_selection_of_a_column_whose_values_are_not_presets_survives(self) -> None:
        """값이 데이터에서 오는 인자 열의 조건은 정리하지 않고 그대로 복원하는지 검증"""
        columns = {
            "ffmpeg_args": ColumnFilterSnapshot(
                text_query="-vf", text_mode=TEXT_MODE_STARTS_WITH
            ),
            "action": ColumnFilterSnapshot(selected_values=(_STALE_ACTION_VALUE,)),
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            apply_saved_filters(self.proxy, columns)
        ffmpeg_args = self.proxy.get_column_filter(VideoTableModel.COL_FFMPEG_ARGS)
        self.assertIsNotNone(ffmpeg_args)
        self.assertEqual(
            rule_to_snapshot(ffmpeg_args) if ffmpeg_args is not None else None,
            columns["ffmpeg_args"],
        )
        self.assertIsNone(self.proxy.get_column_filter(VideoTableModel.COL_ACTION))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("권장 처리", logs.output[0])

    def test_live_values_survive_next_to_stale_ones(self) -> None:
        """남아 있는 값이 있으면 그 값만 남긴 규칙으로 복원하고 안내를 한 줄 남기는지 검증"""
        live = make_eval().action_label
        mixed = {"action": ColumnFilterSnapshot(selected_values=(live, _STALE_ACTION_VALUE))}
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            apply_saved_filters(self.proxy, mixed)
        rule = self.proxy.get_column_filter(VideoTableModel.COL_ACTION)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({live}))
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])
        self.assertEqual(len(logs.records), 1)

    def test_matching_values_and_free_form_columns_are_left_alone(self) -> None:
        """유효한 열거형 선택과 값 집합이 없는 문자열 열 조건은 그대로 복원하고 기록하지 않는지 검증"""
        live = make_eval().action_label
        columns = {
            "action": ColumnFilterSnapshot(selected_values=(live,)),
            "codec": ColumnFilterSnapshot(selected_values=("h264",)),
        }
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            apply_saved_filters(self.proxy, columns)
        action = self.proxy.get_column_filter(VideoTableModel.COL_ACTION)
        codec = self.proxy.get_column_filter(VideoTableModel.COL_CODEC)
        self.assertIsNotNone(action)
        self.assertIsNotNone(codec)
        action_selected = action.selected_values if action is not None else None
        codec_selected = codec.selected_values if codec is not None else None
        self.assertEqual(action_selected, frozenset({live}))
        self.assertEqual(codec_selected, frozenset({"h264"}))
        self.assertEqual(_visible(self.proxy), ["a.mp4"])

    def test_numeric_range_and_text_query_are_left_alone(self) -> None:
        """수치 범위와 문자열 조건 스냅샷은 값을 버리지 않고 그대로 복원하는지 검증"""
        columns = {
            "width": ColumnFilterSnapshot(
                min_value=720.0, max_value=2160.0, include_null=False
            ),
            "name": ColumnFilterSnapshot(
                text_query="a.", text_mode=TEXT_MODE_STARTS_WITH
            ),
        }
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(self.proxy, columns)
        self.assertEqual(changes, {})
        width = self.proxy.get_column_filter(VideoTableModel.COL_WIDTH)
        name = self.proxy.get_column_filter(VideoTableModel.COL_NAME)
        self.assertIsNotNone(width)
        self.assertIsNotNone(name)
        self.assertEqual(
            rule_to_snapshot(width) if width is not None else None, columns["width"]
        )
        self.assertEqual(rule_to_snapshot(name) if name is not None else None, columns["name"])
        self.assertEqual(_visible(self.proxy), ["a.mp4"])

    def test_pruned_keys_are_reported_as_changes(self) -> None:
        """버린 열과 남긴 값이 바뀐 열을 저장 상태 수정용 변경 목록으로 돌려주는지 검증"""
        live = make_eval().action_label
        columns = {
            "action": ColumnFilterSnapshot(selected_values=(live, _STALE_ACTION_VALUE)),
            "reason": ColumnFilterSnapshot(selected_values=(_STALE_ACTION_VALUE,)),
            "codec": ColumnFilterSnapshot(selected_values=("h264",)),
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(self.proxy, columns)
        self.assertEqual(
            changes,
            {
                "action": ColumnFilterSnapshot(selected_values=(live,)),
                "reason": None,
            },
        )

    def test_stale_numeric_bucket_key_is_dropped_so_the_table_stays_filled(self) -> None:
        """임계값이 바뀌어 사라진 수치 구간 키는 버려 표가 비지 않고 안내를 남기는지 검증"""
        columns = {"ratio": ColumnFilterSnapshot(selected_values=(_STALE_BUCKET_KEY,))}
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(self.proxy, columns)
        self.assertEqual(changes, {"ratio": None})
        self.assertIsNone(self.proxy.get_column_filter(VideoTableModel.COL_RATIO))
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])
        self.assertIn("비트레이트 비율", logs.output[0])

    def test_stale_bucket_keys_keep_numeric_bounds_and_null_choice(self) -> None:
        """구간 키가 모두 사라져도 수치 범위와 미측정 포함 여부는 남겨 복원하는지 검증"""
        columns = {
            "ratio": ColumnFilterSnapshot(
                selected_values=(_STALE_BUCKET_KEY,),
                min_value=1.5,
                max_value=3.0,
                include_null=False,
            )
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(self.proxy, columns)
        self.assertEqual(
            changes,
            {
                "ratio": ColumnFilterSnapshot(
                    min_value=1.5, max_value=3.0, include_null=False
                )
            },
        )
        rule = self.proxy.get_column_filter(VideoTableModel.COL_RATIO)
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertIsNone(rule.selected_values)
        self.assertEqual(rule.min_value, 1.5)
        self.assertEqual(rule.max_value, 3.0)
        self.assertFalse(rule.include_null)
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])
        self.assertIn("비트레이트 비율", logs.output[0])

    def test_current_numeric_bucket_key_is_kept(self) -> None:
        """현재 설정에도 있는 수치 구간 키는 그대로 복원하고 기록하지 않는지 검증"""
        ratio = make_eval().bitrate_ratio
        bucket_key = resolve_numeric_bucket(
            ratio, build_numeric_buckets(NumericBucketKind.RATIO, self.proxy.sourceModel().config)  # type: ignore[union-attr]
        )
        self.assertIsNotNone(bucket_key)
        assert bucket_key is not None
        columns = {"ratio": ColumnFilterSnapshot(selected_values=(bucket_key.key,))}
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(self.proxy, columns)
        self.assertEqual(changes, {})
        rule = self.proxy.get_column_filter(VideoTableModel.COL_RATIO)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({bucket_key.key}))
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])


class FrameAnalysisFilterRestoreTests(unittest.TestCase):
    """화질 분석 열과 인코딩 상태 열의 저장된 선택 값이 복원 뒤 표를 비우지 않는지 검증"""

    def test_stale_active_frame_value_is_dropped_so_the_table_stays_filled(self) -> None:
        """이전 표기의 활성 화면 선택 값은 버려 모든 행이 보이는지 검증"""
        proxy = _build_frame_proxy()
        columns = {
            "active_frame": ColumnFilterSnapshot(selected_values=(_STALE_ACTIVE_FRAME_VALUE,))
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {"active_frame": None})
        self.assertIsNone(proxy.get_column_filter(VideoTableModel.COL_ACTIVE_FRAME))
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])
        self.assertIn("활성 화면", logs.output[0])

    def test_active_frame_filter_with_a_current_label_is_kept(self) -> None:
        """현재 표기 문구와 크기 표기는 그대로 복원해 해당 행만 남기는지 검증"""
        proxy = _build_frame_proxy()
        columns = {
            "active_frame": ColumnFilterSnapshot(selected_values=(ACTIVE_FRAME_FULL_TEXT,))
        }
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        rule = proxy.get_column_filter(VideoTableModel.COL_ACTIVE_FRAME)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({ACTIVE_FRAME_FULL_TEXT}))
        self.assertEqual(_visible(proxy), ["a.mp4"])

    def test_active_frame_size_text_and_vr_text_are_kept(self) -> None:
        """크기 표기와 VR 보호 문구는 현재 표시 값으로 인정하는지 검증"""
        proxy = _build_frame_proxy(
            {
                "a.mp4": _frame_result(vr=True),
                "b.mp4": _frame_result(active=ActiveRegion(1920, 800, 0, 140, True)),
            }
        )
        columns = {
            "active_frame": ColumnFilterSnapshot(
                selected_values=(ACTIVE_FRAME_VR_TEXT, "1920x800")
            )
        }
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])

    def test_stale_encode_state_value_is_dropped_so_the_table_stays_filled(self) -> None:
        """사라진 인코딩 상태 표기는 버려 모든 행이 보이는지 검증"""
        proxy = _build_proxy()
        columns = {
            "encode_state": ColumnFilterSnapshot(selected_values=(_STALE_ENCODE_STATE_VALUE,))
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {"encode_state": None})
        self.assertIsNone(proxy.get_column_filter(VideoTableModel.COL_ENCODE_STATE))
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])
        self.assertIn("인코딩 상태", logs.output[0])

    def test_current_encode_state_label_is_kept(self) -> None:
        """빈 인코딩 상태도 현재 값으로 인정해 그 값을 고른 필터가 배치 대상이 아닌 행만 남기는지 검증"""
        model = VideoTableModel()
        model.set_data_source(
            [
                make_eval(video=make_video(filename="a.mp4")),
                make_eval(
                    video=make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL
                ),
            ],
            root_dir="C:/work",
        )
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        columns = {"encode_state": ColumnFilterSnapshot(selected_values=("",))}
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        rule = proxy.get_column_filter(VideoTableModel.COL_ENCODE_STATE)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({""}))
        self.assertEqual(_visible(proxy), ["b.mp4"])

    def test_surge_done_badge_is_a_current_encode_state_value(self) -> None:
        """급증 상한을 넘고 배치된 완료 표기도 현재 값으로 인정해 필터에서 버리지 않고 그 행만 남기는지 검증(안 A)"""
        proxy = _build_proxy()
        model = proxy.sourceModel()
        assert isinstance(model, VideoTableModel)
        book = EncodeBatchBook()
        book.start(
            [
                resolve_source_filepath("C:/work", "a.mp4"),
                resolve_source_filepath("C:/work", "b.mp4"),
            ]
        )
        book.apply(
            resolve_source_filepath("C:/work", "a.mp4"),
            EncodeState.DONE,
            "원본 대비 +42% 급증 / 사용자 확인 후 원본을 결과 파일로 교체했습니다",
        )
        book.apply(resolve_source_filepath("C:/work", "b.mp4"), EncodeState.DONE)
        model.set_encode_book(book)
        columns = {"encode_state": ColumnFilterSnapshot(selected_values=(DONE_SURGE_BADGE,))}
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        rule = proxy.get_column_filter(VideoTableModel.COL_ENCODE_STATE)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({DONE_SURGE_BADGE}))
        self.assertEqual(_visible(proxy), ["a.mp4"])

    def test_target_label_is_a_current_encode_state_value_and_filters_target_rows(self) -> None:
        """배치 전 대상 표기도 현재 값으로 인정해 그 값만 고른 필터가 대상 행만 남기는지 검증"""
        model = VideoTableModel()
        model.set_data_source(
            [
                make_eval(video=make_video(filename="a.mp4")),
                make_eval(
                    video=make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL
                ),
            ],
            root_dir="C:/work",
        )
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        columns = {"encode_state": ColumnFilterSnapshot(selected_values=(STATUS_ENCODE_TARGET,))}
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        self.assertEqual(_visible(proxy), ["a.mp4"])

    def test_surge_done_badge_is_offered_in_the_encode_state_presets(self) -> None:
        """인코딩 상태 열의 선택 목록에 급증 완료 주의 배지가 들어 있어 필터로 고를 수 있음"""
        self.assertIn(DONE_SURGE_BADGE, ENCODE_STATE_LABELS)
        spec = COL_DEF_MAP[VideoTableModel.COL_ENCODE_STATE].spec
        self.assertIn(DONE_SURGE_BADGE, spec.preset_values)

    def test_previous_discard_label_is_a_current_encode_state_value(self) -> None:
        """선언된 상태 문구와 이전 폐기 문구도 현재 값으로 인정해 버리지 않고 해당 행만 남기는지 검증"""
        proxy = _build_proxy()
        model = proxy.sourceModel()
        assert isinstance(model, VideoTableModel)
        book = EncodeBatchBook()
        book.start(
            [
                resolve_source_filepath("C:/work", "a.mp4"),
                resolve_source_filepath("C:/work", "b.mp4"),
            ]
        )
        book.apply(resolve_source_filepath("C:/work", "b.mp4"), EncodeState.DONE)
        model.set_encode_book(book)
        saved = ("", *ENCODE_STATE_LABELS, PREVIOUS_DISCARD_LABEL)
        columns = {"encode_state": ColumnFilterSnapshot(selected_values=saved)}
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {})
        rule = proxy.get_column_filter(VideoTableModel.COL_ENCODE_STATE)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset(saved))
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])

    def test_stale_downscale_candidate_value_is_dropped_next_to_a_current_one(self) -> None:
        """축소 후보 열의 이전 표기 값은 버리고 현재 규격 표기는 남겨 표가 유지되는지 검증"""
        proxy = _build_frame_proxy()
        columns = {
            "downscale_candidate": ColumnFilterSnapshot(
                selected_values=("720p (구)", "1080p")
            )
        }
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(
            changes,
            {"downscale_candidate": ColumnFilterSnapshot(selected_values=("1080p",))},
        )
        rule = proxy.get_column_filter(VideoTableModel.COL_DOWNSCALE_CANDIDATE)
        self.assertIsNotNone(rule)
        selected = rule.selected_values if rule is not None else None
        self.assertEqual(selected, frozenset({"1080p"}))
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])
        self.assertIn("축소 후보", logs.output[0])

    def test_stale_quality_metric_value_is_dropped_so_the_table_stays_filled(self) -> None:
        """측정 지표 열의 사라진 지표 표기는 버려 모든 행이 다시 보이는지 검증"""
        proxy = _build_frame_proxy()
        columns = {"quality_metric": ColumnFilterSnapshot(selected_values=("MAD",))}
        with self.assertLogs(_LOGGER_NAME, level="INFO") as logs:
            changes = apply_saved_filters(proxy, columns)
        self.assertEqual(changes, {"quality_metric": None})
        self.assertIsNone(proxy.get_column_filter(VideoTableModel.COL_QUALITY_METRIC))
        self.assertEqual(_visible(proxy), ["a.mp4", "b.mp4"])
        self.assertIn("측정 지표", logs.output[0])


class SnapshotRoundTripTests(unittest.TestCase):
    """활성 필터와 정렬 우선순위가 열 ID 키 저장 형식으로 왕복하는지 검증"""

    def setUp(self) -> None:
        self.proxy = _build_proxy()

    def test_filter_and_sort_snapshots_use_column_ids(self) -> None:
        """활성 필터와 정렬 우선순위가 열 ID 키로 저장되고 정렬이 같은 값으로 복원되는지 검증"""
        self.proxy.set_column_filter(
            VideoTableModel.COL_CODEC,
            ColumnFilterRule(selected_values=frozenset({"h264"})),
        )
        self.proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            snapshot_column_filters(self.proxy),
            {"codec": ColumnFilterSnapshot(selected_values=("h264",))},
        )
        self.assertEqual(snapshot_sort_keys(self.proxy), [("name", False)])
        header = QHeaderView(Qt.Orientation.Horizontal)
        self.addCleanup(header.deleteLater)
        apply_saved_sort(self.proxy, header, [("name", False)])
        self.assertEqual(
            self.proxy.sourceModel().sort_stack,  # type: ignore[union-attr]
            [(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)],
        )

    def test_include_null_only_filter_survives_save_and_restore(self) -> None:
        """값 조건 없이 미측정 포함만 고른 필터가 저장 값과 복원 모두에서 그대로 남는지 검증"""
        self.proxy.set_column_filter(
            VideoTableModel.COL_QUALITY_VALUE, ColumnFilterRule(include_null_only=True)
        )
        saved = snapshot_column_filters(self.proxy)
        self.assertEqual(
            saved, {"quality_value": ColumnFilterSnapshot(include_null_only=True)}
        )
        self.proxy.clear_all_column_filters()
        apply_saved_filters(self.proxy, saved)
        rule = self.proxy.get_column_filter(VideoTableModel.COL_QUALITY_VALUE)
        self.assertEqual(rule, ColumnFilterRule(include_null_only=True))
        self.assertEqual(self.proxy.active_column_filter_count(), 1)
        self.assertEqual(_visible(self.proxy), ["a.mp4", "b.mp4"])

    def test_unknown_keys_are_ignored(self) -> None:
        """알 수 없는 열 ID는 필터와 정렬 모두에서 무시되는지 검증"""
        apply_saved_filters(self.proxy, {"nope": ColumnFilterSnapshot(min_value=1.0)})
        header = QHeaderView(Qt.Orientation.Horizontal)
        self.addCleanup(header.deleteLater)
        apply_saved_sort(self.proxy, header, [("nope", True)])
        self.assertEqual(self.proxy.active_column_filter_count(), 0)
        self.assertEqual(self.proxy.sort_stack, [])
        self.assertFalse(header.isSortIndicatorShown())


if __name__ == "__main__":
    unittest.main(verbosity=2)

