"""화질 분석, 검은 여백 감지 대상 선정과 요청 생성 규칙 검증"""

import os
import tempfile
import unittest

from src.core.config import FRAME_ANALYSIS_MIN_TIER_ALL
from src.core.smart_action import SmartAction
from src.gui.controllers.frame_analysis_targets import (
    NO_CROP_TARGETS_TEXT,
    NO_FRAME_TARGETS_TEXT,
    build_requests,
    frame_estimate_text,
    is_below_frame_min_tier,
    skip_for_crop_scan,
    skip_for_frame_analysis,
)
from tests._factories import make_eval, make_video


class SkipRuleTests(unittest.TestCase):
    """자동 화질 분석, 검은 여백 감지에서 건너뛸 영상 판정 검증"""

    def test_skip_rules_cover_exception_encoded_hdr_manual_and_interlaced(self) -> None:
        """예외 규칙, 이미 인코딩, HDR, 인터레이스, 직접 지정 영상은 자동 대상에서 빠짐"""
        for action in (
            SmartAction.PASS_EXCEPTION,
            SmartAction.PASS_ALREADY_ENCODED,
            SmartAction.PASS_HDR,
        ):
            with self.subTest(action=action):
                self.assertTrue(skip_for_frame_analysis(make_eval(smart_action=action)))
        self.assertTrue(skip_for_frame_analysis(make_eval(manual_target="keep")))
        self.assertTrue(skip_for_frame_analysis(make_eval(manual_target="720p")))
        self.assertFalse(skip_for_frame_analysis(make_eval()))
        interlaced = make_eval(
            video=make_video(
                stream_summary=(
                    '[{"kind":"video","codec":"h264","attached_pic":false,'
                    '"field_order":"tt"}]'
                )
            )
        )
        self.assertTrue(skip_for_frame_analysis(interlaced))
        self.assertTrue(skip_for_crop_scan(interlaced))

    def test_crop_scan_skip_rules_keep_manual_target_videos(self) -> None:
        """검은 여백 감지는 직접 지정 영상을 건너뛰지 않고 보존 대상과 인터레이스만 제외"""
        manual = make_eval(manual_target="720p")
        self.assertFalse(skip_for_crop_scan(manual))
        self.assertFalse(skip_for_crop_scan(make_eval(manual_target="keep")))
        self.assertTrue(skip_for_crop_scan(make_eval(smart_action=SmartAction.PASS_HDR)))
        self.assertFalse(skip_for_crop_scan(make_eval()))


class MinTierTests(unittest.TestCase):
    """화질 분석 대상 해상도 판정 검증"""

    def test_min_tier_filter_uses_source_tier(self) -> None:
        """원본 규격이 대상 해상도 미만인지 판정하고 전체와 모르는 값은 걸러내지 않음"""
        self.assertTrue(is_below_frame_min_tier(1280, 720, "1080p"))
        self.assertFalse(is_below_frame_min_tier(1920, 1080, "1080p"))
        self.assertFalse(is_below_frame_min_tier(1280, 720, FRAME_ANALYSIS_MIN_TIER_ALL))
        self.assertFalse(is_below_frame_min_tier(1280, 720, "unknown"))

    def test_estimate_counts_only_measureable_videos(self) -> None:
        """예상 대상 수가 해상도 미만과 건너뛸 영상을 빼고 셈"""
        report = [
            make_eval(video=make_video(filename="a.mp4")),
            make_eval(video=make_video(filename="b.mp4", width=1280, height=720)),
            make_eval(
                video=make_video(filename="c.mp4"), smart_action=SmartAction.PASS_HDR
            ),
        ]
        self.assertEqual(frame_estimate_text(report, "1080p"), "대상 1개")
        self.assertEqual(frame_estimate_text(report, "1080p", skip=False), "대상 2개")


class RequestBuildingTests(unittest.TestCase):
    """평가 결과에서 화질 분석 요청을 만들고 VR 메타데이터와 수동 보호를 반영하는지 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        with open(os.path.join(self.root, "a.mp4"), "wb") as handle:
            handle.write(b"video")

    def test_build_requests_reads_stat_stream_summary_and_overrides(self) -> None:
        vr_summary = '[{"kind":"video","codec":"h264","attached_pic":false,"side_data_types":["Spherical Mapping"]}]'
        ev = make_eval(video=make_video(filename="a.mp4", stream_summary=vr_summary))
        requests = build_requests([ev], self.root, frozenset({os.path.join(self.root, "a.mp4")}))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].has_vr_metadata)
        self.assertTrue(requests[0].manual_vr)
        self.assertEqual(requests[0].filesize_bytes, 5)
        self.assertEqual(
            build_requests([ev], self.root, frozenset()),
            build_requests([ev], self.root, frozenset()),
        )

    def test_build_requests_skips_missing_files(self) -> None:
        missing = make_eval(video=make_video(filename="missing.mp4"))
        self.assertEqual(build_requests([missing], self.root, frozenset()), [])


class TargetNoticeTextTests(unittest.TestCase):
    """건너뛸 영상만 있을 때 안내 문구가 대상 종류를 구분하는지 검증"""

    def test_notice_texts_name_each_target_kind(self) -> None:
        self.assertIn("화질 분석 대상이 없습니다", NO_FRAME_TARGETS_TEXT)
        self.assertIn("검은 여백 감지 대상이 없습니다", NO_CROP_TARGETS_TEXT)


if __name__ == "__main__":
    unittest.main()
