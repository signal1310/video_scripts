import json
import unittest

from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.models import ActiveFrame, QualityZone, SavingsSummary
from src.core.smart_action import ActionBadgeTone, SmartAction
from tests._factories import make_eval, make_report, make_video


class TestModelsModule(unittest.TestCase):
    """도메인 데이터클래스(VideoProps, VideoEvaluation, SummaryReport 등)의 속성 산출 및 JSON 직렬화 무결성을 검증"""

    def test_video_props_aspect_ratio_and_zero_height_guard(self) -> None:
        """VideoProps.aspect_ratio 계산 및 height=0일 때의 0으로 나누기 방어를 검증"""
        self.assertAlmostEqual(make_video(width=1920, height=1080).aspect_ratio, 1.778, places=3)
        self.assertEqual(make_video(width=1280, height=0).aspect_ratio, 1280.0)

    def test_active_frame_keeps_offset_and_builds_crop_filter(self) -> None:
        """활성 화면이 시작 좌표를 보존하고 FFmpeg crop 필터 문자열을 만드는지 검증"""
        self.assertEqual(ActiveFrame(1600, 1000).crop_filter_expr(), "crop=1600:1000:0:0")
        frame = ActiveFrame(1600, 1000, 260, 40)
        self.assertEqual((frame.width, frame.height, frame.x, frame.y), (1600, 1000, 260, 40))
        self.assertEqual(frame.crop_filter_expr(), "crop=1600:1000:260:40")

    def test_active_frame_roundtrips_the_crop_filter_and_rejects_other_text(self) -> None:
        """crop 필터 문자열에서 활성 화면을 복원하고 형식이 다르면 None을 돌려주는지 검증"""
        frame = ActiveFrame(1216, 2160, 1312, 0)
        self.assertEqual(ActiveFrame.from_crop_filter(frame.crop_filter_expr()), frame)
        self.assertIsNone(ActiveFrame.from_crop_filter("scale=1920:1080"))
        self.assertIsNone(ActiveFrame.from_crop_filter("crop=1216:2160:1312"))

    def test_summary_report_serializes_enums_to_json(self) -> None:
        """SummaryReport.to_dict 결과가 Enum 오류 없이 json.dumps로 직렬화되는지 검증"""
        vid = make_video(filename="clip.mp4", duration=15.0)
        summary = SavingsSummary(5.0, 3.0, 50.0, 15.0, 1, 2, 30.0, 6.0)
        report = make_report([make_eval(vid, expected_delta_mb=30.0)], savings_summary=summary)

        self.assertEqual(summary.excluded_over_count, 3)
        self.assertAlmostEqual(report.encode_avg_mb_per_min, 120.0)
        data = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
        self.assertEqual(data["evaluations"][0]["video"]["filename"], "clip.mp4")
        self.assertEqual(data["evaluations"][0]["smart_action"], SmartAction.ENCODE_OVER.value)
        self.assertEqual(data["evaluations"][0]["quality_zone"], QualityZone.OVER.value)
        self.assertEqual(data["savings_summary"]["encode_duration_sec"], 15.0)

    def test_unmeasured_keyframe_count_counts_only_unmeasured_rows(self) -> None:
        """SummaryReport.unmeasured_keyframe_count가 키프레임 간격 미실측 행만 세는지 검증"""
        measured = make_eval(make_video(filename="measured.mp4", keyframe_interval=1.0))
        unmeasured = make_eval(make_video(filename="unmeasured.mp4", keyframe_interval=None))
        report = make_report([measured, unmeasured, measured])
        self.assertEqual(report.unmeasured_keyframe_count, 1)

    def test_smart_action_codes_roundtrip_and_labels(self) -> None:
        """SmartAction 안정 코드의 역직렬화, 설명 문구 존재, 대표 라벨을 검증"""
        self.assertEqual(SmartAction.ENCODE_OVER.value, "encode_over")
        for action in SmartAction:
            with self.subTest(action=action):
                self.assertIs(SmartAction(action.value), action)
                self.assertTrue(action.reason_description.strip())
        self.assertEqual(SmartAction.ENCODE_OVER.action_label, "품질 22.0 인코딩")
        self.assertEqual(SmartAction.ENCODE_LOW_KF.reason_label, "KF 초과")
        self.assertEqual(SmartAction.ENCODE_CODEC_UPGRADE.reason_label, "코덱 전환")
        self.assertEqual(SmartAction.ENCODE_COMPAT.reason_label, "코덱 호환")
        self.assertEqual(SmartAction.PASS_LOW_PER_MIN.action_label, "보존")
        self.assertEqual(SmartAction.PASS_LOW_PER_MIN.reason_label, "분당 미달")
        self.assertEqual(SmartAction.PASS_ALREADY_ENCODED.action_label, "보존")
        self.assertEqual(SmartAction.PASS_ALREADY_ENCODED.reason_label, "이미 인코딩")
        self.assertEqual(SmartAction.PASS_ALREADY_ENCODED.option_label, "인코딩 불필요")
        self.assertEqual(SmartAction.PASS_ALREADY_ENCODED.badge_tone, ActionBadgeTone.PASS)
        self.assertFalse(SmartAction.PASS_ALREADY_ENCODED.is_encode)
        self.assertTrue(SmartAction.ENCODE_OPT_KF.option_label)

        cq26_upgrade = make_eval(
            smart_action=SmartAction.ENCODE_CODEC_UPGRADE,
            use_low_quality_preset=True,
        )
        self.assertEqual(cq26_upgrade.action_label, "품질 26.0 인코딩")
        self.assertEqual(cq26_upgrade.action_badge_tone, ActionBadgeTone.LOW_KF_ENCODE)
        self.assertEqual(cq26_upgrade.option_label, SmartAction.ENCODE_LOW_KF.option_label)

    def test_preview_uses_hypothetical_values_only_for_non_targets(self) -> None:
        """원본 보존 항목만 가상 인코딩 수치를 미리보기로 쓰고 실제 예상 수치는 바꾸지 않음을 검증"""
        kept = make_eval(
            smart_action=SmartAction.PASS_LOW, expected_after_mb=80.0, expected_delta_mb=0.0
        )
        self.assertEqual((kept.preview_after_mb, kept.preview_delta_mb), (80.0, 0.0))

        hypothetical = make_eval(
            smart_action=SmartAction.PASS_LOW_PER_MIN,
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
