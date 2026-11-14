import json
import unittest

from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.models import (
    ClassifyStatus,
    FootageStrategy,
    QualityZone,
    SmallFootageDistribution,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)


class TestModelsModule(unittest.TestCase):
    """도메인 데이터클래스(VideoProps, VideoEvaluation, SummaryReport 등)의 속성 산출 및 JSON 직렬화 무결성을 검증"""

    def test_video_props_aspect_ratio_and_zero_height_guard(self) -> None:
        """VideoProps.aspect_ratio 계산 및 height=0일 때의 0으로 나누기 방어를 검증"""
        props_16_9 = VideoProps(
            filename="test1.mp4",
            width=1920,
            height=1080,
            rotate_type=0,
            fps=30.0,
            vid_kbps=3000,
            aud_kbps=128,
            vid_size_MB=50.0,
            duration=10.0,
            codec="h264",
        )
        self.assertAlmostEqual(props_16_9.aspect_ratio, 1.778, places=3)

        # height=0 방어
        props_zero_h = VideoProps(
            filename="zero.mp4",
            width=1280,
            height=0,
            rotate_type=0,
            fps=30.0,
            vid_kbps=1500,
            aud_kbps=128,
            vid_size_MB=10.0,
            duration=5.0,
            codec="h264",
        )
        self.assertEqual(props_zero_h.aspect_ratio, 1280.0)

    def test_models_to_dict_and_json_serializability(self) -> None:
        """VideoProps, VideoEvaluation, SmallFootageDistribution, SummaryReport가 Enum 오류 없이 json.dumps로 직렬화되는지 검증"""
        vid = VideoProps(
            filename="clip.mp4",
            width=1280,
            height=720,
            rotate_type=0,
            fps=30.0,
            vid_kbps=2400,
            aud_kbps=128,
            vid_size_MB=80.0,
            duration=15.0,
            codec="h264",
            keyframe_interval=1.0,
        )
        dist = SmallFootageDistribution(
            small_threshold_mb=30.0,
            min_saved_mb=3.0,
            total_over_count=1,
            small_over_count=0,
            total_potential_saved_mb=30.0,
            small_potential_saved_mb=0.0,
            small_share_ratio=0.0,
            strategy=FootageStrategy.IGNORE_SMALL,
            reason="테스트",
        )
        ev = VideoEvaluation(
            video=vid,
            optimal_kbps=1500,
            bitrate_ratio=1.60,
            quality_zone=QualityZone.OVER,
            is_keyframe_defective=False,
            is_small_footage=False,
            expected_after_mb=50.0,
            expected_delta_mb=30.0,
            smart_action=SmartAction.ENCODE_CQ22_OVER,
            target_dirname="_1_표준인코딩_CQ22",
        )
        report = SummaryReport(
            root_dir="C:/archive",
            total_count=1,
            encoded_count=1,
            passed_count=0,
            total_orig_mb=80.0,
            total_expected_after_mb=50.0,
            gross_saved_mb=30.0,
            keyframe_increase_mb=0.0,
            net_saved_mb=30.0,
            small_distribution=dist,
            evaluations=[ev],
        )

        data = report.to_dict()
        json_output = json.dumps(data, ensure_ascii=False)

        self.assertIn("clip.mp4", json_output)
        self.assertIn(SmartAction.ENCODE_CQ22_OVER.value, json_output)
        self.assertIn(QualityZone.OVER.value, json_output)
        self.assertIn(FootageStrategy.IGNORE_SMALL.value, json_output)

        # SmartAction 정규화 메타데이터 (권장 작업·사유·옵션·배지 톤) 및 안정 코드 역직렬화 검증
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.value, "encode_cq22_over")
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.action_label, "CQ22 인코딩")
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.reason_label, "과화질")
        self.assertEqual(SmartAction.ENCODE_CQ22_OPT_KF.reason_label, "KF 불량")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.action_label, "저화질 KF 교정")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.reason_label, "KF 불량")
        self.assertEqual(SmartAction.PASS_OPTIMAL.reason_label, "기준 충족")
        self.assertEqual(SmartAction.PASS_LOW.reason_label, "저화질 보호")
        self.assertEqual(SmartAction.PASS_SMALL_FOOTAGE.action_label, "원본 보존")
        self.assertEqual(SmartAction.PASS_SMALL_FOOTAGE.reason_label, "절감 미미")
        self.assertEqual(SmartAction.PASS_EXCEPTION.reason_label, "예외 규칙")
        self.assertTrue(bool(SmartAction.ENCODE_CQ22_OPT_KF.option_label))
        for action in SmartAction:
            self.assertIs(SmartAction(action.value), action)
            self.assertTrue(bool(action.reason_description.strip()))

        # VideoEvaluation.classify_status 원자 상태(이동 완료·이동 예정·이동 안 함) 검증
        self.assertEqual(ev.classify_status, ClassifyStatus.PENDING)
        ev_moved = VideoEvaluation(
            video=VideoProps(
                filename="moved.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=4000,
                aud_kbps=128,
                vid_size_MB=80.0,
                duration=15.0,
                codec="h264",
                keyframe_interval=1.0,
                moved_dirname="_1_표준인코딩_CQ22",
            ),
            optimal_kbps=1500,
            bitrate_ratio=1.60,
            quality_zone=QualityZone.OVER,
            is_keyframe_defective=False,
            is_small_footage=False,
            expected_after_mb=50.0,
            expected_delta_mb=30.0,
            smart_action=SmartAction.ENCODE_CQ22_OVER,
            target_dirname="_1_표준인코딩_CQ22",
        )
        self.assertEqual(ev_moved.classify_status, ClassifyStatus.MOVED)
        ev_none = VideoEvaluation(
            video=vid,
            optimal_kbps=1500,
            bitrate_ratio=1.0,
            quality_zone=QualityZone.OPTIMAL,
            is_keyframe_defective=False,
            is_small_footage=False,
            expected_after_mb=80.0,
            expected_delta_mb=0.0,
            smart_action=SmartAction.PASS_OPTIMAL,
            target_dirname=None,
        )
        self.assertEqual(ev_none.classify_status, ClassifyStatus.NOT_TARGET)

    def test_column_labels_ssot_completeness_and_uniqueness(self) -> None:
        """COLUMN_LABELS가 모든 ColumnId를 빠짐없이 포괄하고 일반·축약·CUI 헤더가 중복되지 않음을 검증"""
        self.assertEqual(set(COLUMN_LABELS.keys()), set(ColumnId))
        full_labels = [spec.full for spec in COLUMN_LABELS.values()]
        short_labels = [spec.short for spec in COLUMN_LABELS.values()]
        cui_headers = [spec.compact_header for spec in COLUMN_LABELS.values()]
        self.assertTrue(all(full_labels))
        self.assertTrue(all(short_labels))
        self.assertTrue(all(cui_headers))
        self.assertEqual(len(full_labels), len(set(full_labels)))
        self.assertEqual(len(short_labels), len(set(short_labels)))
        self.assertEqual(len(cui_headers), len(set(cui_headers)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
