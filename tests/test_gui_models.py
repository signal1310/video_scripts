import dataclasses
import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.models import QualityZone, SmartAction, VideoProps
from src.gui.column_filters import (
    TEXT_MODE_NOT_CONTAINS,
    TEXT_MODE_STARTS_WITH,
    ColumnDataType,
    ColumnFilterRule,
    NumericBucketKind,
    build_numeric_buckets,
    resolve_numeric_bucket,
)
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel, format_duration

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIModelsModule(unittest.TestCase):
    """PyQt6 테이블 뷰모델, 자연 정렬/필터 프록시, 포맷 및 루트 파일 존재 캐싱을 검증"""

    def setUp(self) -> None:
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

    def test_video_table_model_display_and_roles(self) -> None:
        """VideoTableModel의 행/열 수, 유니코드 표시, 회전 표시(↻), 정렬 키 및 뱃지 스타일 반환을 검증"""
        model = VideoTableModel()
        model.set_data_source(self.report.evaluations, "C:/dummy_archive")

        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.columnCount(), len(VideoTableModel.HEADERS))

        # 첫 번째 행 (과화질 1080p, SmartAction.ENCODE_CQ22_OVER) 검증
        idx_name_0 = model.index(0, VideoTableModel.COL_NAME)
        self.assertEqual(model.data(idx_name_0), "가나다_10_고화질.mp4")

        idx_zone_0 = model.index(0, VideoTableModel.COL_ZONE)
        self.assertEqual(model.data(idx_zone_0), QualityZone.OVER.value)
        self.assertIsNotNone(model.data(idx_zone_0, VideoTableModel.BADGE_ROLE))

        idx_action_0 = model.index(0, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_0, VideoTableModel.BADGE_ROLE))

        # 셀 단위 텍스트 생략 및 순수 수치 포맷 검증
        idx_size_0 = model.index(0, VideoTableModel.COL_SIZE)
        self.assertEqual(model.data(idx_size_0), "120.00")

        idx_bitrate_0 = model.index(0, VideoTableModel.COL_BITRATE)
        self.assertEqual(model.data(idx_bitrate_0), "9500")

        idx_saved_0 = model.index(0, VideoTableModel.COL_SAVED_SIZE)
        self.assertTrue(model.data(idx_saved_0).startswith("+"))
        self.assertNotIn("MB", model.data(idx_saved_0))

        # 헤더 호버링 툴팁에 단위 명시 여부 검증
        tooltip_size = model.headerData(
            VideoTableModel.COL_SIZE,
            Qt.Orientation.Horizontal,
            int(Qt.ItemDataRole.ToolTipRole),
        )
        self.assertIn("단위: MB", tooltip_size)

        tooltip_br = model.headerData(
            VideoTableModel.COL_BITRATE,
            Qt.Orientation.Horizontal,
            int(Qt.ItemDataRole.ToolTipRole),
        )
        self.assertIn("단위: kbps", tooltip_br)

        # 두 번째 행 (90도 회전 720p, SmartAction.PASS_OPTIMAL) 회전 기호 및 뱃지 검증
        idx_res_1 = model.index(1, VideoTableModel.COL_RES)
        self.assertIn("↻", model.data(idx_res_1))

        idx_action_1 = model.index(1, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_1, VideoTableModel.BADGE_ROLE))

        # 세 번째 행 (키프레임 5.0초 불량, SmartAction.ENCODE_LOW_KF) 경고 기호 및 뱃지 검증
        idx_kf_2 = model.index(2, VideoTableModel.COL_KEYFRAME)
        self.assertIn("⚠", model.data(idx_kf_2))

        idx_action_2 = model.index(2, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_2, VideoTableModel.BADGE_ROLE))

    def test_proxy_model_filtering_and_natural_sorting(self) -> None:
        """VideoSortFilterProxyModel의 실시간 검색, 화질 등급/인코딩 대상 필터링 및 자연 정렬을 검증"""
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

        # 3. 화질 등급 필터 검증 (OVER만 표시)
        proxy.set_zone_filter("OVER")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_zone_filter("ALL")
        self.assertEqual(proxy.rowCount(), 3)

        # 4. 대상 필터 검증 (키프레임 불량만 표시)
        proxy.set_target_filter("DEFECT_KF")
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(
            proxy.data(proxy.index(0, VideoTableModel.COL_NAME)),
            "저화질_KF불량_클립.mkv",
        )
        proxy.set_target_filter("ALL")

        # 5. 검색어 필터 검증 (유니코드 한글 및 코덱 검색)
        proxy.set_search_text("mpeg4")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_search_text("")
        self.assertEqual(proxy.rowCount(), 3)

    def test_table_model_status_preview_and_moved_without_disk_io(self) -> None:
        """VideoTableModel이 디스크 조회 없이 moved_dirname(실이동)·target_dirname(모의)·원본 위치를 구분 표시함을 검증"""
        moved_vid = dataclasses.replace(
            self.videos[0], moved_dirname="_1_표준인코딩_CQ22"
        )
        preview_vid = dataclasses.replace(self.videos[0], filename="preview.mp4", moved_dirname=None)
        pass_vid = self.videos[1]
        evals = VideoEvaluator.evaluate_all(
            "C:/nonexistent_dir", [moved_vid, preview_vid, pass_vid], self.cfg
        ).evaluations

        model = VideoTableModel(config=self.cfg)
        with patch("src.gui.table_model.os.path.isfile", side_effect=AssertionError("disk I/O prohibited")):
            model.set_data_source(evals, "C:/nonexistent_dir")
            self.assertIn("이동됨", model.data(model.index(0, VideoTableModel.COL_STATUS)))
            self.assertIn("모의 분류", model.data(model.index(1, VideoTableModel.COL_STATUS)))
            self.assertEqual(model.data(model.index(2, VideoTableModel.COL_STATUS)), "원본 위치")
            moved_tooltip = model.data(
                model.index(0, VideoTableModel.COL_NAME), int(Qt.ItemDataRole.ToolTipRole)
            )
            self.assertIn("_1_표준인코딩_CQ22", moved_tooltip)

    def test_table_model_dynamic_header_tooltips(self) -> None:
        """VideoTableModel.set_config() 호출 시 화질 등급·키프레임 헤더 툴팁이 동적으로 갱신됨을 검증"""
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
        """모든 열의 데이터 타입 스키마와 열거형·범주형 집계 및 수치 범위 추출을 검증"""
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

        res_counts = model.collect_column_value_counts(VideoTableModel.COL_RES)
        self.assertEqual([label for label, _ in res_counts], ["1920×1080", "1280×720", "854×480"])

        min_kf, max_kf, null_kf = model.collect_numeric_bounds(VideoTableModel.COL_KEYFRAME)
        self.assertEqual(min_kf, 1.0)
        self.assertEqual(max_kf, 5.0)
        self.assertEqual(null_kf, 1)

    def test_proxy_model_column_dynamic_filters(self) -> None:
        """VideoSortFilterProxyModel의 열거형·범주형·수치(null 포함)·문자열 동적 필터 결합을 검증"""
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

        # 2. 열거형 추가 AND 결합 (SmartAction.ENCODE_LOW_KF만)
        proxy.set_column_filter(
            VideoTableModel.COL_ACTION,
            ColumnFilterRule(selected_values=frozenset({SmartAction.ENCODE_LOW_KF.value})),
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

    def test_numeric_buckets_partition_and_config_sync(self) -> None:
        """도메인 수치 버킷의 무누락·무중복 분할과 설정 연동 및 결합 필터링을 검증"""
        cfg = ClassifierConfig()
        sweep_values = [
            -25.0,
            -0.01,
            0.0,
            0.1,
            0.39,
            0.4,
            0.6,
            0.8,
            0.99,
            1.0,
            1.2,
            1.21,
            1.5,
            2.0,
            2.2,
            2.21,
            3.0,
            5.0,
            23.976,
            24.5,
            24.99,
            25.02,
            29.97,
            30.0,
            59.94,
            120.0,
            500.0,
            1500.0,
            5000.0,
        ]
        for kind in (
            NumericBucketKind.FPS,
            NumericBucketKind.DURATION,
            NumericBucketKind.SIZE,
            NumericBucketKind.BITRATE,
            NumericBucketKind.RATIO,
            NumericBucketKind.KEYFRAME,
            NumericBucketKind.SAVED_SIZE,
        ):
            buckets = build_numeric_buckets(kind, cfg)
            self.assertGreaterEqual(len(buckets), 3)
            for sample in sweep_values:
                matched = [b for b in buckets if b.contains(sample)]
                self.assertEqual(
                    len(matched),
                    1,
                    msg=f"{kind.value} sample={sample} matched={matched}",
                )

        # FPS 미세 오차와 24 vs 25 분리 및 20+종 VFR에서도 버킷 유지 확인
        fps_samples = [
            dataclasses.replace(self.videos[0], filename=f"f_{i}.mp4", fps=fps_val)
            for i, fps_val in enumerate(
                [
                    23.976,
                    24.0,
                    24.98,
                    24.99,
                    25.0,
                    25.02,
                    *([29.90 + k * 0.02 for k in range(18)]),
                ]
            )
        ]
        evals = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", fps_samples, cfg
        ).evaluations
        model = VideoTableModel(config=cfg)
        model.set_data_source(evals, "C:/dummy_archive")

        fps_counts = {
            key: count
            for key, _label, count in model.collect_numeric_bucket_counts(
                VideoTableModel.COL_FPS
            )
        }
        self.assertEqual(fps_counts["fps:24"], 2)
        self.assertEqual(fps_counts["fps:25"], 4)
        self.assertEqual(fps_counts["fps:30"], 18)

        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_column_filter(
            VideoTableModel.COL_FPS,
            ColumnFilterRule(selected_values=frozenset({"fps:25"}), min_value=25.0),
        )
        self.assertEqual(proxy.rowCount(), 2)
        self.assertIn("~25 fps", proxy.get_column_filter_summaries()[VideoTableModel.COL_FPS])

        # 설정 연동: 소형 영상 기준/최소 절감/비율 경계 변경 시 버킷 동적 반영
        custom_cfg = dataclasses.replace(
            cfg,
            small_footage=dataclasses.replace(
                cfg.small_footage, size_mb=50.0, min_saved_mb=5.0
            ),
            thresholds=dataclasses.replace(
                cfg.thresholds, low_bitrate_ratio=0.75, over_bitrate_ratio=2.50
            ),
        )
        model.set_config(custom_cfg)
        size_labels = [
            b.label for b in build_numeric_buckets(NumericBucketKind.SIZE, custom_cfg)
        ]
        self.assertIn("30 ~ 50 MB (소형 기준)", size_labels)
        saved_labels = [
            b.label for b in build_numeric_buckets(NumericBucketKind.SAVED_SIZE, custom_cfg)
        ]
        self.assertIn("0 ~ 5 MB (최소 미만)", saved_labels)
        ratio_buckets = build_numeric_buckets(NumericBucketKind.RATIO, custom_cfg)
        self.assertEqual(
            resolve_numeric_bucket(2.50, ratio_buckets),
            next(b for b in ratio_buckets if b.key == "ratio:1.00_2.50"),
        )
        self.assertGreaterEqual(len(ratio_buckets), 7)

    def test_excel_style_stable_multicolumn_sorting(self) -> None:
        """엑셀식 순수 안정 정렬이 동값 순서 보존, 다중 열 누적 및 필터 전환 후에도 유지됨을 검증"""
        # 의도적으로 파일명을 알파벳 역순(z→a)으로 적재해 COL_NAME 강제 2차 정렬 개입 여부를 검증
        samples = [
            VideoProps(
                filename="z_first.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=3000,
                aud_kbps=128,
                vid_size_MB=50.0,
                duration=120.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="a_second.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=3000,
                aud_kbps=128,
                vid_size_MB=200.0,
                duration=120.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="m_third.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=60.0,
                vid_kbps=3000,
                aud_kbps=128,
                vid_size_MB=100.0,
                duration=120.0,
                codec="hevc",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="b_fourth.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=3000,
                aud_kbps=128,
                vid_size_MB=50.0,
                duration=120.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
        ]
        evals = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", samples, self.cfg
        ).evaluations
        model = VideoTableModel(config=self.cfg)
        model.set_data_source(evals, "C:/dummy_archive")
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)

        def _names() -> list[str]:
            return [
                str(proxy.data(proxy.index(r, VideoTableModel.COL_NAME)))
                for r in range(proxy.rowCount())
            ]

        # 1. 내림차순 단일 정렬 시에도 동값 행은 파일명이 아니라 원본 상대 순서(z_first -> b_fourth) 유지
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            _names(), ["a_second.mp4", "m_third.mp4", "z_first.mp4", "b_fourth.mp4"]
        )

        # 2. 직전 COL_SIZE 내림차순 위에 COL_FPS 오름차순 누적 (같은 30fps 내에서 200 -> 50 순 유지)
        proxy.sort(VideoTableModel.COL_FPS, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            _names(), ["a_second.mp4", "z_first.mp4", "b_fourth.mp4", "m_third.mp4"]
        )
        self.assertEqual(
            proxy.sort_stack,
            [
                (VideoTableModel.COL_FPS, Qt.SortOrder.AscendingOrder),
                (VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder),
            ],
        )

        # 3. 주 정렬 열을 내림차순으로 전환해도 하위 COL_SIZE 순서(200 -> 50) 및 동값 원본 순서 유지
        proxy.sort(VideoTableModel.COL_FPS, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            _names(), ["m_third.mp4", "a_second.mp4", "z_first.mp4", "b_fourth.mp4"]
        )

        # 4. 필터 적용 후 해제해도 누적 다중 정렬 우선순위가 흐트러지지 않음
        proxy.set_search_text("a_second")
        self.assertEqual(_names(), ["a_second.mp4"])
        proxy.set_search_text("")
        self.assertEqual(
            _names(), ["m_third.mp4", "a_second.mp4", "z_first.mp4", "b_fourth.mp4"]
        )

        # 5. COL_ZONE(화질 등급)은 연속 비율이 아닌 등급(LOW < OPTIMAL < OVER)으로 정렬해 동일 등급 내 하위 기준 유지
        proxy.sort(-1)
        proxy.sort(VideoTableModel.COL_SIZE, Qt.SortOrder.DescendingOrder)
        proxy.sort(VideoTableModel.COL_ZONE, Qt.SortOrder.AscendingOrder)
        # samples 모두 bitrate_ratio 기준 동일한 OPTIMAL/LOW/OVER 분포 확인 후 등급 내 크기 역순 검증
        zones = [
            proxy.data(proxy.index(r, VideoTableModel.COL_ZONE))
            for r in range(proxy.rowCount())
        ]
        self.assertEqual(len(zones), 4)
        self.assertEqual(
            _names(), ["a_second.mp4", "m_third.mp4", "z_first.mp4", "b_fourth.mp4"]
        )

        # 6. sort(-1)로 정렬 초기화 시 정렬 스택 비워지고 원본 적재 순서 복원
        proxy.sort(-1)
        self.assertEqual(proxy.sort_stack, [])
        self.assertEqual(
            _names(), ["z_first.mp4", "a_second.mp4", "m_third.mp4", "b_fourth.mp4"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
