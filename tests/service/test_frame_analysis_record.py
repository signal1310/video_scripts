"""화질 분석 캐시 행, 요청, 분석 결과 사이 변환 검증

- 열 순서 정의와 왕복 변환이 값을 잃거나 바꾸지 않는지 확인
"""

import unittest

from src.core.analysis.frame_analysis import FRAME_ANALYSIS_VERSION, ActiveRegion, TierScore
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.service.frame_analysis_record import (
    RECORD_COLUMNS,
    FrameAnalysisRequest,
    record_from_result,
    record_to_row,
    result_from_record,
    row_to_record,
)


def _request() -> FrameAnalysisRequest:
    return FrameAnalysisRequest(
        filename="a.mp4",
        path="C:/work/a.mp4",
        width=3840,
        height=2160,
        duration_sec=60.0,
        filesize_bytes=1024,
        mtime_ns=42,
        has_vr_metadata=True,
        manual_vr=True,
    )


def _result() -> FrameAnalysisResult:
    return FrameAnalysisResult(
        active_region=ActiveRegion(width=1920, height=1080, x=120, y=0, has_black_bars=True),
        metric_used="VMAF",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),),
        candidate_tier="1080p",
        valid_segment_count=3,
        total_segment_count=3,
        unmeasurable=False,
        is_vr_protected=True,
    )


class RecordConversionTests(unittest.TestCase):
    """캐시 행과 분석 결과의 왕복 변환"""

    def test_request_key_is_filename_size_and_mtime(self) -> None:
        self.assertEqual(_request().key, ("a.mp4", 1024, 42))

    def test_result_roundtrip_keeps_measurements(self) -> None:
        record = record_from_result(_request(), _result(), measured_at="2026-11-23T00:00:00Z")
        restored = result_from_record(record)
        self.assertEqual(restored, _result())
        self.assertEqual(record.key, ("a.mp4", 1024, 42))
        self.assertEqual(record.active_region.to_active_frame().width, 1920)

    def test_row_roundtrip_follows_the_record_columns(self) -> None:
        record = record_from_result(_request(), _result(), measured_at="2026-11-23T00:00:00Z")
        row = record_to_row(record)
        self.assertEqual(len(row), len(RECORD_COLUMNS))
        self.assertEqual(row_to_record(row), record)
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
