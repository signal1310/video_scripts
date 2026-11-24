import dataclasses
import os
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig, DisplayConfig, EncodeConfig, SavingsConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.messages import MAXRATE_NEAR_NOTICE
from src.core.models import ActiveFrame, DownscaleSkipReason, QualityZone, VideoEvaluation, VideoProps
from src.core.smart_action import SMART_ACTION_REASONS, SmartAction
from src.gui.menus.manual_target_menu import is_manual_target_blocked
from src.gui.table import table_tooltips as tooltips_module
from src.gui.table.column_filters import (
    TEXT_MODE_NOT_CONTAINS,
    TEXT_MODE_STARTS_WITH,
    ColumnDataType,
    ColumnFilterRule,
)
from src.gui.table.numeric_buckets import (
    NumericBucketKind,
    NumericBucketSpec,
    build_numeric_buckets,
    register_numeric_bucket_builder,
    resolve_numeric_bucket,
)
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_columns import COL_DEF_MAP
from src.gui.table.table_model import VideoTableModel, format_duration
from src.gui.table.table_tooltips import format_output_res_tooltip
from src.gui.theme import ThemeManager
from tests._factories import make_eval, make_video
from tests._legacy_model import use_legacy_size_model

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIModelsModule(unittest.TestCase):
    """PyQt6 테이블 뷰모델, 자연 정렬/필터 프록시, 포맷 및 루트 파일 존재 캐싱을 검증"""

    def setUp(self) -> None:
        use_legacy_size_model(self)
        self.cfg = ClassifierConfig()
        self.videos = [
            VideoProps(
                filename="가나다_10_고화질.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=60.0,
                vid_kbps=9500,
                aud_kbps=192,
                vid_size_MB=120.0,
                duration=100.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="가나다_2_정상.mp4",
                width=1280,
                height=720,
                rotate_type=90,
                fps=30.0,
                vid_kbps=1200,
                aud_kbps=128,
                vid_size_MB=45.0,
                duration=240.0,
                codec="hevc",
                keyframe_interval=1.5,
                rotate_source="Display Matrix -90°",
            ),
            VideoProps(
                filename="저화질_KF불량_클립.mkv",
                width=854,
                height=480,
                rotate_type=0,
                fps=30.0,
                vid_kbps=400,
                aud_kbps=96,
                vid_size_MB=25.0,
                duration=400.0,
                codec="mpeg4",
                keyframe_interval=5.0,
            ),
        ]
        self.report = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", self.videos, self.cfg
        )

    def test_format_duration_helper(self) -> None:
        """초 단위 재생 시간이 MM:SS 및 HH:MM:SS 포맷으로 정확히 변환되는지 검증"""
        self.assertEqual(format_duration(65.4), "01:05")
        self.assertEqual(format_duration(3661.0), "01:01:01")

    def _report_model(self) -> VideoTableModel:
        model = VideoTableModel()
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")
        return model

    @staticmethod
    def _header_tooltip(model: VideoTableModel, col: int) -> str:
        return str(model.headerData(col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.ToolTipRole)))

    def test_model_shape_and_over_quality_row_cells(self) -> None:
        """행, 열 수와 첫 번째(과화질) 행의 이름, 등급, 뱃지, 단위 없는 수치 서식을 검증"""
        model = self._report_model()
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.columnCount(), len(VideoTableModel.HEADERS))

        def cell(col: int, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
            return model.data(model.index(0, col), role)

        self.assertEqual(cell(VideoTableModel.COL_NAME), "가나다_10_고화질.mp4")
        self.assertEqual(cell(VideoTableModel.COL_ZONE), QualityZone.OVER.value)
        self.assertIsNotNone(cell(VideoTableModel.COL_ZONE, VideoTableModel.BADGE_ROLE))
        self.assertIsNotNone(cell(VideoTableModel.COL_ACTION, VideoTableModel.BADGE_ROLE))
        self.assertEqual(cell(VideoTableModel.COL_SIZE), "120.00 MB")
        self.assertEqual(cell(VideoTableModel.COL_BITRATE), "9500")
        saved = str(cell(VideoTableModel.COL_SAVED_SIZE))
        self.assertTrue(saved.startswith("+"))
        self.assertIn("MB", saved)

    def test_header_tooltips_explain_units_and_actions(self) -> None:
        """헤더 툴팁이 단위, 조치 목록, 사유 목록, 회전 방향을 안내함을 검증"""
        model = self._report_model()
        self.assertIn("단위: 자동 스케일(KB/MB/GB/TB)", self._header_tooltip(model, VideoTableModel.COL_SIZE))
        self.assertIn("(kbps)", self._header_tooltip(model, VideoTableModel.COL_BITRATE))
        self.assertIn("시계 방향", self._header_tooltip(model, VideoTableModel.COL_ROTATE))

        action = self._header_tooltip(model, VideoTableModel.COL_ACTION)
        for expected in ("품질 22.0 재인코딩", "품질 26.0 재인코딩", "보존"):
            self.assertIn(expected, action)
        self.assertNotIn("셀 툴팁", action)

        reason = self._header_tooltip(model, VideoTableModel.COL_REASON)
        for label in ("과화질", "키프레임 간격 초과", "적정", "화질 보호", "절약 미달", "예외 규칙"):
            self.assertIn(label, reason)

    def test_rotation_icon_is_shown_only_for_rotated_rows(self) -> None:
        """회전이 없는 행은 아이콘이 없고, 90도 회전 행은 SVG 아이콘을 가짐을 검증"""
        model = self._report_model()
        decoration = int(Qt.ItemDataRole.DecorationRole)
        straight = model.index(0, VideoTableModel.COL_ROTATE)
        rotated = model.index(1, VideoTableModel.COL_ROTATE)
        self.assertEqual(model.data(straight), "0°")
        self.assertIsNone(model.data(straight, decoration))
        self.assertEqual(model.data(rotated), "90°")
        icon = model.data(rotated, decoration)
        assert isinstance(icon, QIcon)
        self.assertFalse(icon.isNull())

    def test_rotated_pass_row_cells_and_reason_tooltip(self) -> None:
        """90도 회전 적정 행의 가로, 세로, 조치 알약과 판정 사유 글자색, 사유 툴팁(회전 출처 포함)을 검증"""
        model = self._report_model()
        self.assertEqual(model.data(model.index(1, VideoTableModel.COL_WIDTH)), "1280")
        self.assertEqual(model.data(model.index(1, VideoTableModel.COL_HEIGHT)), "720")

        action = model.index(1, VideoTableModel.COL_ACTION)
        reason = model.index(1, VideoTableModel.COL_REASON)
        self.assertEqual(model.data(action), SmartAction.PASS_OPTIMAL.action_label)
        self.assertEqual(model.data(reason), SmartAction.PASS_OPTIMAL.reason_label)
        # - 원본 보존 행은 채운 알약 없이 흐린 글자만 씀
        self.assertIsNone(model.data(action, VideoTableModel.BADGE_ROLE))
        self.assertEqual(
            model.data(action, int(Qt.ItemDataRole.ForegroundRole)).name().lower(),
            ThemeManager().current_palette.text_muted.lower(),
        )
        self.assertIsNone(model.data(reason, VideoTableModel.BADGE_ROLE))

        tip = str(model.data(reason, int(Qt.ItemDataRole.ToolTipRole)))
        for expected in ("판정 사유:", "회전 90° (원본: Display Matrix -90°)", SmartAction.PASS_OPTIMAL.reason_description):
            self.assertIn(expected, tip)
        self.assertNotIn("세부 옵션", tip)

    def test_exceeded_keyframe_row_shows_warning_icon_and_badges(self) -> None:
        """키프레임 불량 행이 표시 문자열 대신 경고 아이콘과 KF 교정 조치 배지를 표시함을 검증"""
        model = self._report_model()
        keyframe = model.index(2, VideoTableModel.COL_KEYFRAME)
        self.assertEqual(model.data(keyframe), "5.00")
        self.assertNotIn("⚠", str(model.data(keyframe)))
        icon = model.data(keyframe, int(Qt.ItemDataRole.DecorationRole))
        assert isinstance(icon, QIcon)
        self.assertFalse(icon.isNull())
        self.assertIsNone(
            model.data(model.index(0, VideoTableModel.COL_KEYFRAME), int(Qt.ItemDataRole.DecorationRole))
        )
        action = model.index(2, VideoTableModel.COL_ACTION)
        reason = model.index(2, VideoTableModel.COL_REASON)
        self.assertEqual(model.data(action), SmartAction.ENCODE_LOW_KF.action_label)
        self.assertEqual(model.data(reason), SmartAction.ENCODE_LOW_KF.reason_label)
        self.assertIsNotNone(model.data(action, VideoTableModel.BADGE_ROLE))
        self.assertIsNotNone(model.data(model.index(0, VideoTableModel.COL_ACTION), VideoTableModel.BADGE_ROLE))

    def test_proxy_model_filtering_and_natural_sorting(self) -> None:
        """VideoSortFilterProxyModel의 실시간 검색, 열별 필터링 및 자연 정렬을 검증"""
        model = VideoTableModel()
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")

        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)

        # 1. 파일명 오름차순 자연 정렬 검증 ('가나다_2'가 '가나다_10'보다 앞에 위치)
        proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        first_name = proxy.data(proxy.index(0, VideoTableModel.COL_NAME))
        second_name = proxy.data(proxy.index(1, VideoTableModel.COL_NAME))
        self.assertEqual(first_name, "가나다_2_정상.mp4")
        self.assertEqual(second_name, "가나다_10_고화질.mp4")

        # 2. 예상 절감량 내림차순 정렬 검증
        proxy.sort(VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder)
        top_saved_name = proxy.data(proxy.index(0, VideoTableModel.COL_NAME))
        self.assertEqual(top_saved_name, "가나다_10_고화질.mp4")

        # 3. 열 필터 기반 화질 등급 필터 검증 (OVER만 표시)
        proxy.set_column_filter(
            VideoTableModel.COL_ZONE,
            ColumnFilterRule(selected_values=frozenset({QualityZone.OVER.value})),
        )
        self.assertEqual(proxy.rowCount(), 1)
        proxy.clear_column_filter(VideoTableModel.COL_ZONE)
        self.assertEqual(proxy.rowCount(), 3)

        # 4. 열 필터 기반 회전, 사유 필터 검증
        proxy.set_column_filter(
            VideoTableModel.COL_ROTATE,
            ColumnFilterRule(selected_values=frozenset({"90°"})),
        )
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(
            proxy.data(proxy.index(0, VideoTableModel.COL_NAME)),
            "가나다_2_정상.mp4",
        )
        proxy.clear_all_column_filters()

        # 5. 검색어 필터 검증 (유니코드 한글, 코덱, 정규화 조치 검색)
        proxy.set_search_text("mpeg4")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_search_text("품질 22.0 재인코딩")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_search_text("")
        self.assertEqual(proxy.rowCount(), 3)

    def test_table_model_dynamic_header_tooltips(self) -> None:
        """VideoTableModel.set_config() 호출 시 화질 등급, 키프레임 헤더 툴팁이 동적으로 갱신됨을 검증"""
        model = VideoTableModel(config=ClassifierConfig())
        zone_tip_default = model.headerData(
            VideoTableModel.COL_ZONE,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        kf_tip_default = model.headerData(
            VideoTableModel.COL_KEYFRAME,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        self.assertIsInstance(zone_tip_default, str)
        self.assertIsInstance(kf_tip_default, str)
        self.assertIn("1.20배", str(zone_tip_default))
        self.assertIn("2.2초", str(kf_tip_default))

        from src.core.config import ThresholdsConfig

        custom_cfg = ClassifierConfig(
            thresholds=ThresholdsConfig(
                over_bitrate_ratio=1.5,
                low_bitrate_ratio=0.9,
                keyframe_interval_sec=3.5,
            ),
        )
        model.set_config(custom_cfg)
        zone_tip_updated = str(
            model.headerData(
                VideoTableModel.COL_ZONE,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        kf_tip_updated = str(
            model.headerData(
                VideoTableModel.COL_KEYFRAME,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        self.assertIn("1.50배", zone_tip_updated)
        self.assertIn("0.90배", zone_tip_updated)
        self.assertIn("3.5초", kf_tip_updated)

    def test_column_schema_and_value_collection(self) -> None:
        """모든 열의 데이터 타입 스키마와 열거형, 범주형 집계 및 수치 범위 추출을 검증"""
        for col in range(len(VideoTableModel.HEADERS)):
            self.assertIn(col, VideoTableModel.COLUMN_SPECS)

        self.assertIs(
            VideoTableModel.get_column_spec(VideoTableModel.COL_ZONE).data_type,
            ColumnDataType.ENUM,
        )
        self.assertIs(
            VideoTableModel.get_column_spec(VideoTableModel.COL_CODEC).data_type,
            ColumnDataType.CATEGORY,
        )
        self.assertIs(
            VideoTableModel.get_column_spec(VideoTableModel.COL_SIZE).data_type,
            ColumnDataType.NUMBER,
        )
        self.assertIs(
            VideoTableModel.get_column_spec(VideoTableModel.COL_NAME).data_type,
            ColumnDataType.TEXT,
        )

        unmeasured = dataclasses.replace(
            self.videos[1], filename="미측정_샘플.mkv", keyframe_interval=None
        )
        evals = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", [*self.videos, unmeasured], self.cfg
        ).evaluations
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(evals, "C:/dummy_archive")

        zone_counts = dict(model.collect_column_value_counts(VideoTableModel.COL_ZONE))
        self.assertEqual(zone_counts[QualityZone.OVER.value], 1)
        self.assertEqual(zone_counts[QualityZone.OPTIMAL.value], 2)
        self.assertEqual(zone_counts[QualityZone.LOW.value], 1)

        rotate_counts = model.collect_column_value_counts(VideoTableModel.COL_ROTATE)
        self.assertEqual(rotate_counts, [("0°", 2), ("90°", 2)])

        width_buckets = model.collect_numeric_bucket_counts(VideoTableModel.COL_WIDTH)
        self.assertEqual(
            [k for k, _, _ in width_buckets],
            ["w:854_899", "w:1280_1439", "w:1920_2159"],
        )
        height_buckets = model.collect_numeric_bucket_counts(VideoTableModel.COL_HEIGHT)
        self.assertEqual(
            [k for k, _, _ in height_buckets],
            ["h:480_539", "h:720_853", "h:1080_1279"],
        )

        min_kf, max_kf, null_kf = model.collect_numeric_bounds(VideoTableModel.COL_KEYFRAME)
        self.assertEqual(min_kf, 1.0)
        self.assertEqual(max_kf, 5.0)
        self.assertEqual(null_kf, 1)

    def test_proxy_model_column_dynamic_filters(self) -> None:
        """VideoSortFilterProxyModel의 열거형, 범주형, 수치(null 포함), 문자열 동적 필터 결합을 검증"""
        unmeasured = dataclasses.replace(
            self.videos[1], filename="extra_unmeasured.mp4", keyframe_interval=None
        )
        evals = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", [*self.videos, unmeasured], self.cfg
        ).evaluations
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(evals, "C:/dummy_archive")

        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        change_events: list[int] = []
        proxy.column_filters_changed.connect(
            lambda: change_events.append(proxy.active_column_filter_count())
        )

        # 1. 범주형 다중 선택 (h264 + mpeg4)
        proxy.set_column_filter(
            VideoTableModel.COL_CODEC,
            ColumnFilterRule(selected_values=frozenset({"h264", "mpeg4"})),
        )
        self.assertEqual(proxy.rowCount(), 2)

        # 2. 열거형 추가 AND 결합 (조치=KF 교정, 사유=KF 불량)
        proxy.set_column_filter(
            VideoTableModel.COL_ACTION,
            ColumnFilterRule(
                selected_values=frozenset({SmartAction.ENCODE_LOW_KF.action_label})
            ),
        )
        proxy.set_column_filter(
            VideoTableModel.COL_REASON,
            ColumnFilterRule(
                selected_values=frozenset({SmartAction.ENCODE_LOW_KF.reason_label})
            ),
        )
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(
            proxy.data(proxy.index(0, VideoTableModel.COL_NAME)),
            "저화질_KF불량_클립.mkv",
        )
        self.assertIn(VideoTableModel.COL_CODEC, proxy.get_column_filter_summaries())

        # 3. 전체 초기화 후 KF 미측정 전용 / 범위 필터 검증
        proxy.clear_all_column_filters()
        self.assertEqual(proxy.rowCount(), 4)
        self.assertEqual(proxy.active_column_filter_count(), 0)

        proxy.set_column_filter(
            VideoTableModel.COL_KEYFRAME,
            ColumnFilterRule(null_only=True, include_null=True),
        )
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(
            proxy.data(proxy.index(0, VideoTableModel.COL_NAME)),
            "extra_unmeasured.mp4",
        )

        proxy.set_column_filter(
            VideoTableModel.COL_KEYFRAME,
            ColumnFilterRule(min_value=1.2, max_value=6.0, include_null=False),
        )
        self.assertEqual(proxy.rowCount(), 2)

        # 4. 문자열 필터 (시작 문자 / 제외)
        proxy.clear_all_column_filters()
        proxy.set_column_filter(
            VideoTableModel.COL_NAME,
            ColumnFilterRule(text_query="가나다", text_mode=TEXT_MODE_STARTS_WITH),
        )
        self.assertEqual(proxy.rowCount(), 2)

        proxy.set_column_filter(
            VideoTableModel.COL_NAME,
            ColumnFilterRule(text_query="가나다", text_mode=TEXT_MODE_NOT_CONTAINS),
        )
        self.assertEqual(proxy.rowCount(), 2)
        self.assertGreaterEqual(len(change_events), 4)

    def test_numeric_buckets_partition_every_value_exactly_once(self) -> None:
        """모든 수치 버킷 종류가 경계값을 포함한 표본을 누락, 중복 없이 하나의 구간에만 배정함을 검증"""
        sweep = [
            -25.0, -0.01, 0.0, 0.1, 0.39, 0.4, 0.6, 0.8, 0.99, 1.0, 1.2, 1.21, 1.5, 2.0, 2.2, 2.21, 3.0, 5.0,
            23.976, 24.5, 24.99, 25.02, 29.97, 30.0, 59.94, 120.0, 500.0, 1500.0, 5000.0,
        ]
        kinds = (
            NumericBucketKind.FPS, NumericBucketKind.DURATION, NumericBucketKind.SIZE, NumericBucketKind.BITRATE,
            NumericBucketKind.RATIO, NumericBucketKind.KEYFRAME, NumericBucketKind.SAVED_SIZE,
        )
        for kind in kinds:
            buckets = build_numeric_buckets(kind, self.cfg)
            self.assertGreaterEqual(len(buckets), 3)
            for sample in sweep:
                matched = [b.key for b in buckets if b.contains(sample)]
                self.assertEqual(len(matched), 1, msg=f"{kind.value} sample={sample} matched={matched}")

    def test_fps_buckets_absorb_vfr_jitter_and_combine_with_range_filter(self) -> None:
        """FPS 미세 오차가 24, 25, 30 버킷으로 모이고 버킷 선택과 범위 필터가 함께 적용됨을 검증"""
        values = [23.976, 24.0, 24.98, 24.99, 25.0, 25.02, *(29.90 + k * 0.02 for k in range(18))]
        samples = [dataclasses.replace(self.videos[0], filename=f"f_{i}.mp4", fps=fps) for i, fps in enumerate(values)]
        evals = VideoEvaluator.evaluate_all("C:/dummy_archive", samples, self.cfg).evaluations
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(evals, "C:/dummy_archive")

        counts = {key: count for key, _label, count in model.collect_numeric_bucket_counts(VideoTableModel.COL_FPS)}
        self.assertEqual((counts["fps:24"], counts["fps:25"], counts["fps:30"]), (2, 4, 18))

        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_column_filter(
            VideoTableModel.COL_FPS,
            ColumnFilterRule(selected_values=frozenset({"fps:25"}), min_value=25.0),
        )
        self.assertEqual(proxy.rowCount(), 2)
        self.assertIn("~25 fps", proxy.get_column_filter_summaries()[VideoTableModel.COL_FPS])

    def test_bucket_labels_follow_config_changes(self) -> None:
        """최소 절감량, 화질 비율 경계 설정이 용량, 절감량, 비율 버킷에 동적으로 반영됨을 검증"""
        custom = dataclasses.replace(
            self.cfg,
            savings=dataclasses.replace(self.cfg.savings, min_saved_mb=5.0),
            thresholds=dataclasses.replace(self.cfg.thresholds, low_bitrate_ratio=0.75, over_bitrate_ratio=2.50),
        )
        model = VideoTableModel(config=self.cfg)
        model.set_config(custom)

        size_labels = [b.label for b in build_numeric_buckets(NumericBucketKind.SIZE, custom)]
        self.assertIn("30 MB 미만", size_labels)
        self.assertIn("30 ~ 100 MB", size_labels)
        self.assertTrue(all("소형 기준" not in label for label in size_labels))
        saved_labels = [b.label for b in build_numeric_buckets(NumericBucketKind.SAVED_SIZE, custom)]
        self.assertIn("0 ~ 5 MB (절약 미달)", saved_labels)

        ratio_buckets = build_numeric_buckets(NumericBucketKind.RATIO, custom)
        self.assertGreaterEqual(len(ratio_buckets), 7)
        self.assertEqual(
            resolve_numeric_bucket(2.50, ratio_buckets),
            next(b for b in ratio_buckets if b.key == "ratio:1.00_2.50"),
        )

    def _stable_sort_proxy(self) -> tuple[VideoSortFilterProxyModel, Callable[[], list[str]]]:
        """파일명을 알파벳 역순으로 적재한 4개 샘플의 프록시와 현재 행 순서 조회 함수를 반환"""
        # 의도적으로 z -> a 순으로 적재해 COL_NAME 강제 2차 정렬이 개입하는지 드러냄
        samples = [
            make_video(filename="z_first.mp4", vid_kbps=3000, vid_size_MB=50.0, duration=120.0),
            make_video(filename="a_second.mp4", vid_kbps=3000, vid_size_MB=200.0, duration=120.0),
            make_video(filename="m_third.mp4", fps=60.0, vid_kbps=3000, vid_size_MB=100.0, duration=120.0, codec="hevc"),
            make_video(filename="b_fourth.mp4", vid_kbps=3000, vid_size_MB=50.0, duration=120.0),
        ]
        evals = VideoEvaluator.evaluate_all("C:/dummy_archive", samples, self.cfg).evaluations
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(evals, "C:/dummy_archive")
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)

        def names() -> list[str]:
            return [str(proxy.data(proxy.index(r, VideoTableModel.COL_NAME))) for r in range(proxy.rowCount())]

        return proxy, names

    def test_descending_sort_keeps_load_order_for_ties(self) -> None:
        """내림차순 단일 정렬에서도 동값 행은 파일명이 아니라 적재 순서(z_first -> b_fourth)를 유지함을 검증"""
        proxy, names = self._stable_sort_proxy()
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        self.assertEqual(names(), ["a_second.mp4", "m_third.mp4", "z_first.mp4", "b_fourth.mp4"])

    def test_sort_stack_accumulates_and_keeps_secondary_order(self) -> None:
        """다중 열 정렬이 누적되고, 주 정렬 방향을 바꿔도 하위 정렬 순서와 동값 적재 순서가 유지됨을 검증"""
        proxy, names = self._stable_sort_proxy()
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        proxy.sort(VideoTableModel.COL_FPS, Qt.SortOrder.AscendingOrder)
        self.assertEqual(names(), ["a_second.mp4", "z_first.mp4", "b_fourth.mp4", "m_third.mp4"])
        self.assertEqual(
            proxy.sort_stack,
            [
                (VideoTableModel.COL_FPS, Qt.SortOrder.AscendingOrder),
                (VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder),
            ],
        )

        proxy.sort(VideoTableModel.COL_FPS, Qt.SortOrder.DescendingOrder)
        self.assertEqual(names(), ["m_third.mp4", "a_second.mp4", "z_first.mp4", "b_fourth.mp4"])

    def test_sort_order_survives_filter_toggle(self) -> None:
        """필터를 적용했다 해제해도 누적 다중 정렬 우선순위가 유지됨을 검증"""
        proxy, names = self._stable_sort_proxy()
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        proxy.sort(VideoTableModel.COL_FPS, Qt.SortOrder.DescendingOrder)
        expected = names()

        proxy.set_search_text("a_second")
        self.assertEqual(names(), ["a_second.mp4"])
        proxy.set_search_text("")
        self.assertEqual(names(), expected)

    def test_zone_sort_orders_by_grade_then_keeps_secondary_sort(self) -> None:
        """화질 등급 열은 연속 비율이 아닌 등급 단위로 정렬되어 같은 등급 안에서 하위 정렬이 유지됨을 검증"""
        proxy, names = self._stable_sort_proxy()
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        proxy.sort(VideoTableModel.COL_ZONE, Qt.SortOrder.AscendingOrder)
        # m_third는 HEVC 60fps라 적정 구간이므로 과화질 묶음보다 앞 등급에 위치
        self.assertEqual(names(), ["m_third.mp4", "a_second.mp4", "z_first.mp4", "b_fourth.mp4"])

    def test_sort_reset_restores_load_order(self) -> None:
        """sort(-1)이 정렬 스택을 비우고 적재 순서를 복원함을 검증"""
        proxy, names = self._stable_sort_proxy()
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        proxy.sort(-1)
        self.assertEqual(proxy.sort_stack, [])
        self.assertEqual(names(), ["z_first.mp4", "a_second.mp4", "m_third.mp4", "b_fourth.mp4"])

    def test_numeric_bucket_builder_registry_and_badge_tone_consistency(self) -> None:
        """수치 버킷 빌더 레지스트리 확장 및 VideoTableModel의 SmartAction 배지 범주 일관성을 검증"""
        custom_buckets = (
            NumericBucketSpec("fps:custom", "커스텀 (< 50)", lower=None, upper=50.0),
        )
        with patch.dict("src.gui.table.numeric_buckets._NUMERIC_BUCKET_BUILDERS", clear=False):
            register_numeric_bucket_builder(
                NumericBucketKind.FPS, lambda _cfg: custom_buckets
            )
            self.assertEqual(
                build_numeric_buckets(NumericBucketKind.FPS, self.cfg),
                custom_buckets,
            )

        model = VideoTableModel(config=self.cfg)
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")
        self.assertEqual(model.columnCount(), len(VideoTableModel.HEADERS))
        self.assertEqual(len(VideoTableModel.HEADER_TOOLTIPS), len(VideoTableModel.HEADERS))
        self.assertEqual(VideoTableModel.HEADERS[VideoTableModel.COL_BITRATE], "BR")
        self.assertEqual(
            VideoTableModel.HEADERS[VideoTableModel.COL_OPT_BITRATE], "최적 BR"
        )
        self.assertEqual(VideoTableModel.HEADERS[VideoTableModel.COL_RATIO], "BR 비율")
        self.assertEqual(
            VideoTableModel.HEADERS[VideoTableModel.COL_KEYFRAME], "KF 간격"
        )
        self.assertEqual(VideoTableModel.HEADERS[VideoTableModel.COL_AFTER_SIZE], "예상 용량")
        self.assertEqual(VideoTableModel.HEADERS[VideoTableModel.COL_ACTION], "권장 처리")
        action_badge = model.data(
            model.index(0, VideoTableModel.COL_ACTION),
            VideoTableModel.BADGE_ROLE,
        )
        self.assertIsNotNone(action_badge)
        # - 판정 사유는 알약 대신 사유 배지의 글자색만 쓰므로 배지 데이터가 없음
        self.assertIsNone(
            model.data(model.index(0, VideoTableModel.COL_REASON), VideoTableModel.BADGE_ROLE)
        )
        reason_fg = model.data(
            model.index(0, VideoTableModel.COL_REASON),
            int(Qt.ItemDataRole.ForegroundRole),
        )
        self.assertIsInstance(reason_fg, QColor)

    def test_table_model_fast_accessors_and_bucket_cache(self) -> None:
        """VideoTableModel 패스트패스 접근자와 build_numeric_buckets 기본 캐시, 커스텀 우회 동작을 검증"""
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")

        row0 = model.get_evaluation_row(0)
        self.assertIs(row0, self.report.evaluations[0])
        assert row0 is not None
        self.assertIsNone(model.get_evaluation_row(-1))
        self.assertIsNone(model.get_evaluation_row(len(self.report.evaluations)))

        self.assertEqual(
            model.get_sort_value(0, VideoTableModel.COL_SIZE),
            model.data(model.index(0, VideoTableModel.COL_SIZE), VideoTableModel.SORT_ROLE),
        )
        self.assertIsNone(model.get_sort_value(999, VideoTableModel.COL_SIZE))
        self.assertIn(row0.video.filename.lower(), model.get_search_blob(0))
        self.assertEqual(model.get_search_blob(999), "")

        # 기본 빌더는 동일 설정에 대해 캐시된 동일 튜플 인스턴스를 재사용
        b1 = build_numeric_buckets(NumericBucketKind.RATIO, self.cfg)
        b2 = build_numeric_buckets(NumericBucketKind.RATIO, dataclasses.replace(self.cfg))
        self.assertIs(b1, b2)

        custom_ratio = (NumericBucketSpec("ratio:custom", "커스텀", lower=0.0, upper=2.0),)
        with patch.dict("src.gui.table.numeric_buckets._NUMERIC_BUCKET_BUILDERS", clear=False):
            register_numeric_bucket_builder(NumericBucketKind.RATIO, lambda _cfg: custom_ratio)
            self.assertEqual(
                build_numeric_buckets(NumericBucketKind.RATIO, self.cfg),
                custom_ratio,
            )

    def test_table_model_display_config_formatting_and_live_refresh(self) -> None:
        """VideoTableModel이 열별 정밀도, FPS 정수 생략, 용량 단위 및 set_config 실시간 갱신을 반영함을 검증"""
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")

        # 기본 설정: FPS=60.0 -> "60" (정수 생략), 비율 소수 2자리, QualityZone="과화질"
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_FPS)), "60")
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_ZONE)), "과화질")

        config_signals: list[int] = []
        model.config_changed.connect(lambda: config_signals.append(1))

        updated_cfg = ClassifierConfig(
            display=DisplayConfig(
                fps_decimals=3,
                fps_trim_whole=False,
                ratio_decimals=4,
                size_decimals=1,
                size_unit="gb",
            )
        )
        model.set_config(updated_cfg)
        self.assertEqual(len(config_signals), 1)
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_FPS)), "60.000")
        ratio_text = str(model.data(model.index(0, VideoTableModel.COL_RATIO)))
        self.assertEqual(len(ratio_text.split(".")[1]), 4)
        size_text = str(model.data(model.index(0, VideoTableModel.COL_SIZE)))
        self.assertEqual(size_text, "0.1")
        self.assertNotIn("GB", size_text)
        self.assertEqual(
            model.headerData(VideoTableModel.COL_SIZE, Qt.Orientation.Horizontal),
            "용량 (GB)",
        )
        self.assertEqual(
            model.headerData(VideoTableModel.COL_AFTER_SIZE, Qt.Orientation.Horizontal),
            "예상 용량 (GB)",
        )
        self.assertEqual(
            model.headerData(VideoTableModel.COL_SAVED_SIZE, Qt.Orientation.Horizontal),
            "예상 절약 (GB)",
        )

        model.set_config(ClassifierConfig(display=DisplayConfig(size_unit="auto")))
        self.assertEqual(
            model.headerData(VideoTableModel.COL_SIZE, Qt.Orientation.Horizontal),
            "용량",
        )
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_SIZE)), "120.00 MB")

        self.assertEqual(
            model.data(model.index(0, VideoTableModel.COL_NAME), Qt.ItemDataRole.TextAlignmentRole),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )
        model.set_config(ClassifierConfig(display=DisplayConfig(filename_align="right")))
        self.assertEqual(
            model.data(model.index(0, VideoTableModel.COL_NAME), Qt.ItemDataRole.TextAlignmentRole),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
        )

    def test_muted_neutral_cells_use_dimmer_cell_foreground(self) -> None:
        """회전 0°, 미측정 키프레임, 변동 없는 절감량 셀이 text_muted보다 연한 fg_cell_muted를 사용함을 검증"""
        unmeasured = VideoProps(
            filename="neutral_clip.mp4",
            width=1280,
            height=720,
            rotate_type=0,
            fps=30.0,
            vid_kbps=1200,
            aud_kbps=128,
            vid_size_MB=45.0,
            duration=240.0,
            codec="h264",
            keyframe_interval=None,
        )
        neg_kf = dataclasses.replace(unmeasured, filename="neg_kf.mp4", keyframe_interval=-1.0)
        report = VideoEvaluator.evaluate_all("C:/dummy_archive", [unmeasured, neg_kf], self.cfg)

        model = VideoTableModel()
        model.set_data_source(report.evaluations, "C:/dummy_archive", self.cfg)
        palette = ThemeManager().current_palette
        self.assertNotEqual(palette.fg_cell_muted, palette.text_muted)

        fg_role = int(Qt.ItemDataRole.ForegroundRole)
        for row in (0, 1):
            for col in (VideoTableModel.COL_ROTATE, VideoTableModel.COL_KEYFRAME):
                color = model.data(model.index(row, col), fg_role)
                self.assertIsInstance(color, QColor)
                assert isinstance(color, QColor)
                self.assertEqual(color.name().lower(), palette.fg_cell_muted.lower())

        # 키프레임 1초 동일 코덱 영상은 출력 기준 비율이 1배이고 증가가 없어 절감 변화 0 (회색 표시)
        neutral = dataclasses.replace(unmeasured, filename="neutral_1s.mp4", keyframe_interval=1.0, codec="hevc")
        neutral_rep = VideoEvaluator.evaluate_all("C:/dummy_archive", [neutral], self.cfg)
        neutral_model = VideoTableModel()
        neutral_model.set_data_source(neutral_rep.evaluations, "C:/dummy_archive", self.cfg)
        neutral_color = neutral_model.data(neutral_model.index(0, VideoTableModel.COL_SAVED_SIZE), fg_role)
        self.assertIsInstance(neutral_color, QColor)
        assert isinstance(neutral_color, QColor)
        self.assertEqual(neutral_color.name().lower(), palette.fg_cell_muted.lower())

        # 예상 절감이 0인 행은 볼드체를 해제하고 일반 모노스페이스 폰트를 사용
        font_role = int(Qt.ItemDataRole.FontRole)
        saved_zero_font = model.data(model.index(0, VideoTableModel.COL_SAVED_SIZE), font_role)
        self.assertIsInstance(saved_zero_font, QFont)
        assert isinstance(saved_zero_font, QFont)
        self.assertFalse(saved_zero_font.bold())

        # 유의미한 예상 절감이 있는 행은 볼드체를 유지
        pos_model = VideoTableModel()
        pos_model.set_data_source(self.report.evaluations, "C:/dummy_archive", self.cfg)
        saved_pos_font = pos_model.data(
            pos_model.index(0, VideoTableModel.COL_SAVED_SIZE), font_role
        )
        self.assertIsInstance(saved_pos_font, QFont)
        assert isinstance(saved_pos_font, QFont)
        self.assertTrue(saved_pos_font.bold())

        # BR 비율은 별도 색상 강조 및 볼드 없이 기본 전경색, 일반 모노스페이스 폰트를 사용
        ratio_idx = pos_model.index(0, VideoTableModel.COL_RATIO)
        self.assertIsNone(pos_model.data(ratio_idx, fg_role))
        ratio_font = pos_model.data(ratio_idx, font_role)
        self.assertIsInstance(ratio_font, QFont)
        assert isinstance(ratio_font, QFont)
        self.assertFalse(ratio_font.bold())

    def test_pass_row_displays_hypothetical_saved_as_nonbold_secondary_text(self) -> None:
        """원본 보존 행도 가상 인코딩 예상 용량, 절감량을 일반 논볼드 text_secondary 색으로 표시하고 툴팁에 안내함을 검증"""
        cfg_eff = ClassifierConfig(
            savings=SavingsConfig(min_saved_mb=10.0, min_mb_per_min=5.0, exempt_saved_mb=0.0)
        )
        vid_slow = VideoProps("slow_pass.mp4", 1280, 720, 0, 30.0, 3000, 128, 40.0, 600.0, "h264", 1.0)
        vid_low_2s = VideoProps("low_2s.mp4", 1280, 720, 0, 30.0, 900, 128, 100.0, 60.0, "hevc", 2.0)
        rep = VideoEvaluator.evaluate_all("C:/dummy_archive", [vid_slow, vid_low_2s], cfg_eff)

        model = VideoTableModel()
        model.set_data_source(rep.evaluations, "C:/dummy_archive", cfg_eff)
        palette = ThemeManager().current_palette
        fg_role = int(Qt.ItemDataRole.ForegroundRole)
        font_role = int(Qt.ItemDataRole.FontRole)
        tip_role = int(Qt.ItemDataRole.ToolTipRole)

        # 시간 비효율(원본 보존)도 +20.00 MB 가상 절감을 일반 논볼드 text_secondary로 표시
        # 출력 코덱(HEVC) 기준 비율 2.5배로 용량 40MB -> 16MB, 절감 +24MB
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_AFTER_SIZE)), "16.00 MB")
        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_SAVED_SIZE)), "+24.00 MB")
        for col in (VideoTableModel.COL_AFTER_SIZE, VideoTableModel.COL_SAVED_SIZE):
            color = model.data(model.index(0, col), fg_role)
            self.assertIsInstance(color, QColor)
            assert isinstance(color, QColor)
            self.assertEqual(color.name().lower(), palette.text_secondary.lower())
        saved_font = model.data(model.index(0, VideoTableModel.COL_SAVED_SIZE), font_role)
        self.assertIsInstance(saved_font, QFont)
        assert isinstance(saved_font, QFont)
        self.assertFalse(saved_font.bold())

        # 저화질 정상 KF(2.0s)는 보호 품질값 계수 2^(-4/6)을 반영한 가상 절약(약 +2.26MB)을 논볼드 text_secondary로 표시
        self.assertEqual(model.data(model.index(1, VideoTableModel.COL_SAVED_SIZE)), "+2.26 MB")
        neg_ref_color = model.data(model.index(1, VideoTableModel.COL_SAVED_SIZE), fg_role)
        self.assertIsInstance(neg_ref_color, QColor)
        assert isinstance(neg_ref_color, QColor)
        self.assertEqual(neg_ref_color.name().lower(), palette.text_secondary.lower())

        tip = str(model.data(model.index(0, VideoTableModel.COL_NAME), tip_role))
        self.assertIn("재인코딩 시", tip)
        self.assertIn("+2.40 MB/분", tip)

    def test_proxy_visible_evaluations_and_active_filter_state(self) -> None:
        """표시 행 목록 조회와 검색어 기준 활성 필터 판별을 검증"""
        rep = VideoEvaluator.evaluate_all("C:/dummy_qf", self.videos, self.cfg)
        model = VideoTableModel()
        model.set_data_source(rep.evaluations, "C:/dummy_qf", self.cfg)
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)

        self.assertFalse(proxy.is_any_filter_active())
        all_visible = proxy.iter_visible_evaluations()
        self.assertEqual(len(all_visible), len(rep.evaluations))

        proxy.set_search_text("고화질")
        self.assertTrue(proxy.is_any_filter_active())
        filtered_visible = proxy.iter_visible_evaluations()
        self.assertLess(len(filtered_visible), len(all_visible))

        proxy.set_search_text("")
        self.assertFalse(proxy.is_any_filter_active())

    def test_saved_per_min_and_est_encode_time_columns_and_buckets(self) -> None:
        """절감 효율(MB/분)과 예상 소요 시간 열 표시, 시각 구분 및 SAVED_PER_MIN 버킷 필터를 검증"""
        cfg_speed = ClassifierConfig(
            savings=SavingsConfig(
                min_saved_mb=10.0,
                min_mb_per_min=5.0,
                exempt_saved_mb=0.0,
            ),
            encode=EncodeConfig(encode_speed_factor=2.0),
        )
        # 1) 과화질 인코딩 대상: 1080p 30fps 120s @ 2.0배속 -> 예상 소요 60s (01:00)
        vid_enc = VideoProps("enc.mp4", 1920, 1080, 0, 30.0, 9000, 128, 120.0, 120.0, "h264", 1.0)
        # 2) 시간 비효율 원본 보존: 1080p 30fps 600s @ 2.0배속 -> 예상 소요 300s (05:00), 분당 절감 < 5.0
        vid_pass = VideoProps("pass.mp4", 1920, 1080, 0, 30.0, 6000, 128, 40.0, 600.0, "h264", 1.0)
        rep = VideoEvaluator.evaluate_all("C:/dummy_archive", [vid_enc, vid_pass], cfg_speed)

        self.assertTrue(rep.evaluations[0].is_encode_target)
        self.assertFalse(rep.evaluations[1].is_encode_target)

        model = VideoTableModel()
        model.set_data_source(rep.evaluations, "C:/dummy_archive", cfg_speed)
        palette = ThemeManager().current_palette
        fg_role = int(Qt.ItemDataRole.ForegroundRole)
        font_role = int(Qt.ItemDataRole.FontRole)

        self.assertEqual(
            model.headerData(VideoTableModel.COL_SAVED_PER_MIN, Qt.Orientation.Horizontal),
            "분당 절약",
        )
        rate_tip = str(
            model.headerData(
                VideoTableModel.COL_SAVED_PER_MIN,
                Qt.Orientation.Horizontal,
                int(Qt.ItemDataRole.ToolTipRole),
            )
        )
        self.assertIn("5.0 MB/분", rate_tip)
        self.assertIn("10분 영상 기준 50.0 MB", rate_tip)
        self.assertIn("재생 시간 1분당", rate_tip)

        time_tip = str(
            model.headerData(
                VideoTableModel.COL_EST_ENCODE_TIME,
                Qt.Orientation.Horizontal,
                int(Qt.ItemDataRole.ToolTipRole),
            )
        )
        self.assertIn("실제 재인코딩을 실행할 때", time_tip)
        self.assertIn("폴더 분석이나 파일 이동 시간이 아님", time_tip)

        self.assertEqual(model.data(model.index(0, VideoTableModel.COL_EST_ENCODE_TIME)), "01:00")
        self.assertEqual(model.data(model.index(1, VideoTableModel.COL_EST_ENCODE_TIME)), "05:00")
        expected_enc_rate = f"{rep.evaluations[0].preview_mb_per_min:+.1f} MB/분"
        expected_pass_rate = f"{rep.evaluations[1].preview_mb_per_min:+.1f} MB/분"
        self.assertEqual(
            model.data(model.index(0, VideoTableModel.COL_SAVED_PER_MIN)), expected_enc_rate
        )
        self.assertEqual(
            model.data(model.index(1, VideoTableModel.COL_SAVED_PER_MIN)), expected_pass_rate
        )

        # 인코딩 대상은 강조/기본 전경, 원본 보존은 text_secondary 논볼드
        enc_rate_font = model.data(model.index(0, VideoTableModel.COL_SAVED_PER_MIN), font_role)
        assert isinstance(enc_rate_font, QFont)
        self.assertTrue(enc_rate_font.bold())
        pass_rate_font = model.data(model.index(1, VideoTableModel.COL_SAVED_PER_MIN), font_role)
        assert isinstance(pass_rate_font, QFont)
        self.assertFalse(pass_rate_font.bold())
        pass_time_color = model.data(model.index(1, VideoTableModel.COL_EST_ENCODE_TIME), fg_role)
        assert isinstance(pass_time_color, QColor)
        self.assertEqual(pass_time_color.name().lower(), palette.text_secondary.lower())

        buckets = build_numeric_buckets(NumericBucketKind.SAVED_PER_MIN, cfg_speed)
        low_bucket = resolve_numeric_bucket(2.0, buckets)
        high_bucket = resolve_numeric_bucket(30.0, buckets)
        self.assertIsNotNone(low_bucket)
        self.assertIsNotNone(high_bucket)
        assert low_bucket is not None and high_bucket is not None
        self.assertNotEqual(low_bucket.key, high_bucket.key)


class TestOutputResolutionColumn(unittest.TestCase):
    """출력 해상도 열의 값 표시, 화소 수 정렬, 구간 필터, 사유 배지, 근접 경고 색과 툴팁을 검증"""

    def setUp(self) -> None:
        self.cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
        downscaled = make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)
        in_box = make_video(
            filename="small.mp4", width=640, height=360, vid_kbps=400, vid_size_MB=10.0, duration=120.0
        )
        self.evals = [VideoEvaluator.evaluate_video(v, self.cfg) for v in (downscaled, in_box)]
        self.model = VideoTableModel(config=self.cfg)
        self.model.set_data_source(self.evals, "", self.cfg)
        self.col = VideoTableModel.COL_OUTPUT_RES
        self.palette = ThemeManager().current_palette

    def test_value_is_filled_only_for_downscaled_rows(self) -> None:
        """축소 대상 행에만 출력 해상도가 채워지고 나머지 행은 -로 표시"""
        self.assertTrue(self.evals[0].is_downscaled)
        self.assertFalse(self.evals[1].is_downscaled)
        self.assertEqual(self.model.data(self.model.index(0, self.col), Qt.ItemDataRole.DisplayRole), "1280x720")
        self.assertEqual(self.model.data(self.model.index(1, self.col), Qt.ItemDataRole.DisplayRole), "-")

    def test_sort_key_is_pixel_count_and_zero_for_non_targets(self) -> None:
        """정렬 키는 출력 화소 수이며 축소 대상이 아닌 행은 0"""
        self.assertEqual(self.model.get_sort_value(0, self.col), 1280 * 720)
        self.assertEqual(self.model.get_sort_value(1, self.col), 0)

    def test_filter_bucket_and_reason_label(self) -> None:
        """필터 구간은 규격 표시명이고 비대상은 축소 안 함, 사유 열은 최대 해상도 초과 표기를 따름"""
        self.assertEqual(VideoTableModel.get_filter_value(self.evals[0], self.col), "720p")
        self.assertEqual(VideoTableModel.get_filter_value(self.evals[1], self.col), "축소 안 함")
        self.assertEqual(
            VideoTableModel.get_filter_value(self.evals[0], VideoTableModel.COL_REASON), "최대 해상도 초과"
        )
        self.assertIsNone(
            self.model.data(self.model.index(0, VideoTableModel.COL_REASON), VideoTableModel.BADGE_ROLE)
        )
        self.assertEqual(
            self.model.data(
                self.model.index(0, VideoTableModel.COL_REASON),
                int(Qt.ItemDataRole.ForegroundRole),
            ).name().lower(),
            QColor(self.palette.badge_std_encode[1]).name().lower(),
        )

    def test_cell_tooltip_describes_target_and_skips_non_targets(self) -> None:
        """축소 대상 셀 툴팁은 출력 해상도와 판정 사유를, 비대상 셀 툴팁은 원본 유지 안내를 표시"""
        target_tip = self.model.data(self.model.index(0, self.col), Qt.ItemDataRole.ToolTipRole)
        self.assertIn("출력 해상도: 1280x720 (720p)", target_tip)
        self.assertIn("판정 사유: 최대 해상도 초과", target_tip)
        other_tip = self.model.data(self.model.index(1, self.col), Qt.ItemDataRole.ToolTipRole)
        self.assertNotIn("출력 해상도:", other_tip)

    def test_near_maxrate_uses_warning_color_and_leads_tooltip(self) -> None:
        """최대 비트레이트 여유가 경고 기준 미만이면 셀 경고 색과 툴팁 첫 줄 안내를 적용"""
        near = dataclasses.replace(self.evals[0], target_optimal_kbps=2000)
        self.assertTrue(near.is_maxrate_near)
        fg = COL_DEF_MAP[self.col].foreground_getter
        assert fg is not None
        self.assertEqual(fg(near, self.palette), self.palette.fg_maxrate_warn)
        self.assertEqual(format_output_res_tooltip(near, self.cfg).splitlines()[0], MAXRATE_NEAR_NOTICE)
        self.assertIsNone(fg(self.evals[0], self.palette))

    def test_skipped_downscale_tooltips_explain_reason_from_real_evaluation(self) -> None:
        """실제 평가에서 축소 후보가 버려진 두 사례가 설정 상한 규격 기준의 사유 문구를 표시"""
        low_quality = VideoEvaluator.evaluate_video(
            make_video(filename="lq.mp4", vid_kbps=100, vid_size_MB=10.0, duration=120.0),
            ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p")),
        )
        self.assertEqual(low_quality.downscale_skip_reason, DownscaleSkipReason.LOW_QUALITY_AFTER_DOWNSCALE)
        self.assertIsNone(low_quality.target_width)
        self.assertEqual(
            format_output_res_tooltip(low_quality, self.cfg),
            "720p로 줄여도 화질 보호가 필요해 축소하지 않았습니다.",
        )

        near_same = VideoEvaluator.evaluate_video(
            make_video(filename="ns.mp4", height=1088, vid_kbps=3000, vid_size_MB=300.0, duration=120.0),
            ClassifierConfig(encode=EncodeConfig(max_resolution_tier="1080p")),
        )
        self.assertEqual(near_same.downscale_skip_reason, DownscaleSkipReason.NEAR_SAME_SIZE)
        self.assertEqual(
            format_output_res_tooltip(near_same, ClassifierConfig(encode=EncodeConfig(max_resolution_tier="1080p"))),
            "1080p로 줄이면 원본과 크기가 거의 같아 축소하지 않았습니다.",
        )

    def test_manual_target_tooltip_uses_row_target_instead_of_max_resolution(self) -> None:
        """직접 지정이 있는 행은 설정 상한이 아닌 지정 규격 기준으로 사유를 안내하고, 축소 안 함은 직접 지정 문구를 표시"""
        ns_video = make_video(filename="ns.mp4", height=1088, vid_kbps=3000, vid_size_MB=300.0, duration=120.0)
        big_video = make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)
        cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
        report = VideoEvaluator.evaluate_all(
            "",
            [ns_video, big_video],
            cfg,
            manual_targets={"ns.mp4": "1080p", "big.mp4": "keep"},
        )
        ns_eval, big_eval = report.evaluations
        self.assertEqual(ns_eval.manual_target, "1080p")
        self.assertEqual(ns_eval.downscale_skip_reason, DownscaleSkipReason.NEAR_SAME_SIZE)
        self.assertEqual(
            format_output_res_tooltip(ns_eval, cfg),
            "1080p로 줄이면 원본과 크기가 거의 같아 축소하지 않았습니다.",
        )
        self.assertEqual(format_output_res_tooltip(big_eval, cfg), "직접 지정으로 축소하지 않습니다.")


class TestBitrateFitOutputColumn(unittest.TestCase):
    """비트레이트 맞춤 행의 사유 배지와 필터 목록, 툴팁 사유와 최대 비트레이트 안내, 버려진 후보의 기준 규격을 검증"""

    def setUp(self) -> None:
        use_legacy_size_model(self)
        self.cfg = ClassifierConfig(encode=EncodeConfig(auto_downscale_by_bitrate=True))
        fit_video = make_video(
            filename="uhd.mp4", width=3840, height=2160, vid_kbps=3000, vid_size_MB=223.7, duration=600.0
        )
        self.evals = [VideoEvaluator.evaluate_video(fit_video, self.cfg)]
        self.model = VideoTableModel(config=self.cfg)
        self.model.set_data_source(self.evals, "", self.cfg)
        self.palette = ThemeManager().current_palette

    def test_reason_badge_and_filter_value_for_bitrate_fit(self) -> None:
        """비트레이트 맞춤 행은 사유 배지 글자색만 쓰고, 사유 필터 목록에도 같은 표기가 있음"""
        self.assertEqual(self.evals[0].reason_label, "비트레이트 맞춤")
        self.assertIn("비트레이트 맞춤", SMART_ACTION_REASONS)
        self.assertEqual(
            VideoTableModel.get_filter_value(self.evals[0], VideoTableModel.COL_REASON), "비트레이트 맞춤"
        )
        self.assertIsNone(
            self.model.data(self.model.index(0, VideoTableModel.COL_REASON), VideoTableModel.BADGE_ROLE)
        )
        self.assertEqual(
            self.model.data(
                self.model.index(0, VideoTableModel.COL_REASON),
                int(Qt.ItemDataRole.ForegroundRole),
            ).name().lower(),
            QColor(self.palette.badge_std_encode[1]).name().lower(),
        )

    def test_reason_badge_for_manual_target(self) -> None:
        """직접 지정으로 축소된 행은 사유 배지 글자색만 쓰고, 사유 필터에도 같은 표기가 있음"""
        manual = VideoEvaluator.evaluate_all(
            "C:/dummy",
            [make_video(filename="uhd.mp4", width=3840, height=2160, vid_kbps=20000, vid_size_MB=1400.0, duration=600.0)],
            self.cfg,
            manual_targets={"uhd.mp4": "720p"},
        ).evaluations
        self.assertEqual(manual[0].reason_label, "직접 지정")
        self.assertIn("직접 지정", SMART_ACTION_REASONS)
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(manual, "", self.cfg)
        self.assertIsNone(
            model.data(model.index(0, VideoTableModel.COL_REASON), VideoTableModel.BADGE_ROLE)
        )
        self.assertEqual(
            model.data(
                model.index(0, VideoTableModel.COL_REASON),
                int(Qt.ItemDataRole.ForegroundRole),
            ).name().lower(),
            QColor(self.palette.badge_std_encode[1]).name().lower(),
        )

    def test_output_tooltip_shows_fit_reason_and_maxrate_note(self) -> None:
        """비트레이트 맞춤 축소 툴팁은 1080p 출력 해상도, 판정 사유, 최대 비트레이트 안내를 표시"""
        tip = format_output_res_tooltip(self.evals[0], self.cfg)
        self.assertIn("출력 해상도: 1920x1080 (1080p)", tip)
        self.assertIn("판정 사유: 비트레이트 맞춤", tip)
        self.assertIn("최대 비트레이트 5.4Mbps가 적용됩니다.", tip)

    def test_skipped_fit_candidate_names_fit_tier_as_basis(self) -> None:
        """비트레이트 맞춤 1080p 후보가 블록 노이즈 조건에 걸려 버려지면 축소하지 않은 이유를 1080p 기준으로 안내"""
        legacy = VideoEvaluator.evaluate_video(
            make_video(
                filename="legacy.mp4", width=3840, height=2160, vid_kbps=3200, vid_size_MB=233.2,
                duration=600.0, codec="mpeg4",
            ),
            self.cfg,
        )
        self.assertIsNotNone(legacy.downscale_skip_reason)
        self.assertEqual(
            format_output_res_tooltip(legacy, self.cfg),
            "1080p로 줄여도 화질 보호가 필요해 축소하지 않았습니다.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestOutputBitrateAndPendingOutput(unittest.TestCase):
    """출력 BR 열의 표시와 정렬, 재평가 전 출력 해상도 임시 표시, 새 평가 결과의 덮어쓰기를 검증"""

    def _model(self, filenames: list[str]) -> VideoTableModel:
        model = VideoTableModel()
        evaluations = [
            VideoEvaluator.evaluate_all("C:/work", [make_video(filename=name)], ClassifierConfig())
            .evaluations[0]
            for name in filenames
        ]
        model.set_data_source(evaluations, root_dir="C:/work")
        return model

    def test_output_bitrate_column_is_numeric_kbps(self) -> None:
        """출력 BR 열은 예상 평균 비트레이트(kbps) 숫자로 표시하고 정렬 값도 숫자임을 검증"""
        model = self._model(["a.mp4"])
        index = model.index(0, VideoTableModel.COL_OUT_BITRATE)
        ev = model.get_evaluation_row(0)
        assert ev is not None
        self.assertEqual(model.headerData(VideoTableModel.COL_OUT_BITRATE, Qt.Orientation.Horizontal), "출력 BR")
        self.assertEqual(model.data(index, int(Qt.ItemDataRole.DisplayRole)), f"{ev.target_optimal_kbps:.0f}")
        self.assertEqual(model.data(index, VideoTableModel.SORT_ROLE), float(ev.target_optimal_kbps))

    def test_pending_output_tier_shows_until_new_data_source(self) -> None:
        """재평가 전에는 지정한 규격을 출력 해상도 열에 보여 주고, 새 평가 결과가 들어오면 실제 값으로 바뀜을 검증"""
        from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY

        model = self._model(["a.mp4"])
        index = model.index(0, VideoTableModel.COL_OUTPUT_RES)
        model.set_pending_output_tiers({"a.mp4": RESOLUTION_TIER_BY_KEY["720p"]})
        self.assertEqual(model.data(index, int(Qt.ItemDataRole.DisplayRole)), "720p")
        ev = VideoEvaluator.evaluate_all("C:/work", [make_video(filename="a.mp4")], ClassifierConfig()).evaluations[0]
        model.set_data_source([ev], root_dir="C:/work")
        self.assertNotEqual(model.data(index, int(Qt.ItemDataRole.DisplayRole)), "720p")



class TestSortKeyCache(unittest.TestCase):
    """정렬 키 캐시가 열과 데이터 교체 단위로 계산되고, 데이터 교체 시 이전 키를 버리는지 검증"""

    @staticmethod
    def _evaluations(filenames: list[str]) -> list[VideoEvaluation]:
        return [
            VideoEvaluator.evaluate_all("C:/work", [make_video(filename=name)], ClassifierConfig())
            .evaluations[0]
            for name in filenames
        ]

    def test_cached_sort_values_match_direct_computation(self) -> None:
        """캐시된 정렬 키가 열별 직접 계산 결과와 행 단위로 일치함을 검증"""
        model = VideoTableModel()
        model.set_data_source(self._evaluations(["b.mp4", "a.mp4"]), root_dir="C:/work")
        sort_getter = COL_DEF_MAP[VideoTableModel.COL_NAME].sort_value
        assert sort_getter is not None
        for row, ev in enumerate(model.evaluations):
            self.assertEqual(
                model.get_sort_value(row, VideoTableModel.COL_NAME),
                sort_getter(ev),
            )

    def test_cache_is_rebuilt_when_data_source_changes(self) -> None:
        """데이터 교체 후 같은 열의 정렬 키가 새 목록 기준으로 다시 계산되는지 검증"""
        model = VideoTableModel()
        model.set_data_source(self._evaluations(["b.mp4", "a.mp4"]), root_dir="C:/work")
        self.assertIsNotNone(model.get_sort_value(0, VideoTableModel.COL_NAME))
        model.set_data_source(self._evaluations(["z.mp4"]), root_dir="C:/work")
        self.assertIsNone(model.get_sort_value(1, VideoTableModel.COL_NAME))
        self.assertIsNotNone(model.get_sort_value(0, VideoTableModel.COL_NAME))


class TestManualTargetBlocking(unittest.TestCase):
    """직접 지정 메뉴 차단 대상은 예외, HDR로 한정되고 이미 재인코딩 영상은 규격 지정을 받는지 검증"""

    def test_only_exception_and_hdr_block_manual_target(self) -> None:
        self.assertTrue(is_manual_target_blocked(make_eval(smart_action=SmartAction.PASS_EXCEPTION)))
        self.assertTrue(is_manual_target_blocked(make_eval(smart_action=SmartAction.PASS_HDR)))
        self.assertFalse(is_manual_target_blocked(make_eval(smart_action=SmartAction.PASS_ALREADY_ENCODED)))


class TestOutputResolutionTooltipActiveFrame(unittest.TestCase):
    """출력 해상도 툴팁의 기준 규격이 활성 화면 크기를 따르는지 검증"""

    def test_basis_tier_uses_the_active_frame_size(self) -> None:
        """잘라내기 확정 행은 원본 대신 활성 화면 크기로 후보 규격을 고르는지 검증"""
        ev = make_eval(
            video=make_video(width=3840, height=2160),
            active_frame=ActiveFrame(1216, 2160, 1312, 0),
        )
        with patch.object(tooltips_module, "select_output_candidate", return_value=None) as select:
            tooltips_module._basis_tier(ev, ClassifierConfig())
        passed = select.call_args.args[0]
        self.assertEqual((passed.width, passed.height), (1216, 2160))
