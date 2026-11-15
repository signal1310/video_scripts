import json
import unittest

from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.models import (
    ClassifyStatus,
    EfficiencySummary,
    QualityZone,
    SmartAction,
)
from tests._factories import make_eval, make_report, make_video


class TestModelsModule(unittest.TestCase):
    """도메인 데이터클래스(VideoProps, VideoEvaluation, SummaryReport 등)의 속성 산출 및 JSON 직렬화 무결성을 검증"""

    def test_video_props_aspect_ratio_and_zero_height_guard(self) -> None:
        """VideoProps.aspect_ratio 계산 및 height=0일 때의 0으로 나누기 방어를 검증"""
        self.assertAlmostEqual(make_video(width=1920, height=1080).aspect_ratio, 1.778, places=3)
        self.assertEqual(make_video(width=1280, height=0).aspect_ratio, 1280.0)

    def test_summary_report_serializes_enums_to_json(self) -> None:
        """SummaryReport.to_dict 결과가 Enum 오류 없이 json.dumps로 직렬화되는지 검증"""
        vid = make_video(filename="clip.mp4", duration=15.0)
        eff = EfficiencySummary(5.0, 3.0, 50.0, 15.0, 1, 2, 30.0, 6.0)
        report = make_report([make_eval(vid, expected_delta_mb=30.0)], efficiency_summary=eff)

        self.assertEqual(eff.skipped_over_count, 3)
        self.assertAlmostEqual(report.encode_avg_mb_per_min, 120.0)
        data = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
        self.assertEqual(data["evaluations"][0]["video"]["filename"], "clip.mp4")
        self.assertEqual(data["evaluations"][0]["smart_action"], SmartAction.ENCODE_CQ22_OVER.value)
        self.assertEqual(data["evaluations"][0]["quality_zone"], QualityZone.OVER.value)
        self.assertEqual(data["efficiency_summary"]["encode_duration_sec"], 15.0)

    def test_smart_action_codes_roundtrip_and_labels(self) -> None:
        """SmartAction 안정 코드의 역직렬화, 설명 문구 존재, 대표 라벨을 검증"""
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.value, "encode_cq22_over")
        for action in SmartAction:
            with self.subTest(action=action):
                self.assertIs(SmartAction(action.value), action)
                self.assertTrue(action.reason_description.strip())
        self.assertEqual(SmartAction.ENCODE_CQ22_OVER.action_label, "CQ22 인코딩")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.reason_label, "KF 불량")
        self.assertEqual(SmartAction.PASS_LOW_EFFICIENCY.action_label, "원본 보존")
        self.assertEqual(SmartAction.PASS_LOW_EFFICIENCY.reason_label, "시간 비효율")
        self.assertTrue(SmartAction.ENCODE_CQ22_OPT_KF.option_label)

    def test_classify_status_follows_target_and_moved_state(self) -> None:
        """이동 예정, 이동 완료, 이동 안 함 상태가 target_dirname과 moved_dirname으로 결정되는지 검증"""
        target = make_eval()
        moved = make_eval(make_video(moved_dirname=target.target_dirname))
        kept = make_eval(target_dirname=None, smart_action=SmartAction.PASS_OPTIMAL)
        self.assertEqual(target.classify_status, ClassifyStatus.PENDING)
        self.assertEqual(moved.classify_status, ClassifyStatus.MOVED)
        self.assertEqual(kept.classify_status, ClassifyStatus.NOT_TARGET)
        self.assertEqual([ev.is_encode_target for ev in (target, moved, kept)], [True, True, False])

    def test_preview_uses_hypothetical_values_only_for_non_targets(self) -> None:
        """원본 보존 항목만 가상 인코딩 수치를 미리보기로 쓰고 실제 예상 수치는 바꾸지 않음을 검증"""
        kept = make_eval(target_dirname=None, expected_after_mb=80.0, expected_delta_mb=0.0)
        self.assertEqual((kept.preview_after_mb, kept.preview_delta_mb), (80.0, 0.0))

        hypothetical = make_eval(
            target_dirname=None,
            smart_action=SmartAction.PASS_LOW_EFFICIENCY,
            expected_after_mb=80.0,
            expected_delta_mb=0.0,
            hypothetical_after_mb=50.0,
            hypothetical_delta_mb=30.0,
        )
        self.assertEqual((hypothetical.preview_after_mb, hypothetical.preview_delta_mb), (50.0, 30.0))
        self.assertEqual(hypothetical.expected_delta_mb, 0.0)

        target = make_eval(hypothetical_after_mb=1.0, hypothetical_delta_mb=1.0)
        self.assertEqual((target.preview_after_mb, target.preview_delta_mb), (50.0, 50.0))

    def test_column_labels_ssot_completeness_and_uniqueness(self) -> None:
        """COLUMN_LABELS가 모든 ColumnId를 빠짐없이 포괄하고 일반, 축약, CUI 헤더가 중복되지 않음을 검증"""
        self.assertEqual(set(COLUMN_LABELS.keys()), set(ColumnId))
        for attr in ("full", "short", "compact_header"):
            labels = [getattr(spec, attr) for spec in COLUMN_LABELS.values()]
            with self.subTest(attr=attr):
                self.assertTrue(all(labels))
                self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
