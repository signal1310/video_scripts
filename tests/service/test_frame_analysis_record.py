"""화질 분석 캐시 행, 요청, 분석 결과 사이 변환 검증

- 열 순서 정의와 왕복 변환이 값을 잃거나 바꾸지 않는지 확인
"""

import unittest
from dataclasses import replace

from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    FRAME_ANALYSIS_VERSION,
    ActiveRegion,
    TierScore,
)
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

    def test_request_content_key_is_size_and_fingerprint(self) -> None:
        self.assertEqual(_request().content_key, (1024, ""))
        self.assertEqual(_request().key, ("a.mp4", 1024, 42))

    def test_result_roundtrip_keeps_measurements(self) -> None:
        record = record_from_result(_request(), _result(), measured_at="2026-11-23T00:00:00Z")
        restored = result_from_record(record)
        self.assertEqual(restored, _result())
        self.assertEqual(record.content_key, (1024, ""))
        self.assertEqual(record.active_region.to_active_frame().width, 1920)

    def test_margins_vary_survives_the_roundtrip(self) -> None:
        """구간별 여백 차이 플래그가 결과, 캐시 행, 행 값 왕복에서 유지됨"""
        result = FrameAnalysisResult(
            active_region=ActiveRegion(
                width=1920, height=1080, x=0, y=0, has_black_bars=False, margins_vary=True
            ),
            metric_used="VMAF",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=3,
            total_segment_count=3,
            unmeasurable=False,
            is_vr_protected=False,
        )
        record = record_from_result(_request(), result, measured_at="2026-11-23T00:00:00Z")
        self.assertTrue(record.margins_vary)
        self.assertTrue(record.active_region.margins_vary)
        self.assertTrue(result_from_record(record).active_region.margins_vary)
        self.assertTrue(row_to_record(record_to_row(record)).margins_vary)

    def test_dark_outliers_ignored_survives_the_roundtrip(self) -> None:
        """어두운 근거 무시 플래그가 결과, 캐시 행, 행 값 왕복에서 유지됨"""
        result = FrameAnalysisResult(
            active_region=ActiveRegion(
                width=1920,
                height=1080,
                x=120,
                y=0,
                has_black_bars=True,
                dark_outliers_ignored=True,
            ),
            metric_used="VMAF",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=3,
            total_segment_count=3,
            unmeasurable=False,
            is_vr_protected=False,
        )
        record = record_from_result(_request(), result, measured_at="2026-11-23T00:00:00Z")
        self.assertTrue(record.dark_outliers_ignored)
        self.assertTrue(record.active_region.dark_outliers_ignored)
        self.assertTrue(result_from_record(record).active_region.dark_outliers_ignored)
        self.assertTrue(row_to_record(record_to_row(record)).dark_outliers_ignored)

    def test_crop_scan_failed_survives_the_roundtrip(self) -> None:
        """감지 실패 표시가 결과, 캐시 행, 행 값 왕복에서 유지됨"""
        result = FrameAnalysisResult(
            active_region=ActiveRegion(
                width=3840, height=2160, x=0, y=0, has_black_bars=False
            ),
            metric_used="",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=0,
            total_segment_count=9,
            unmeasurable=True,
            is_vr_protected=False,
            crop_scan_failed=True,
            analysis_version=CROP_SCAN_VERSION,
        )
        record = record_from_result(_request(), result, measured_at="2026-11-23T00:00:00Z")
        self.assertTrue(record.crop_scan_failed)
        self.assertTrue(result_from_record(record).crop_scan_failed)
        self.assertTrue(row_to_record(record_to_row(record)).crop_scan_failed)
        self.assertFalse(record_from_result(_request(), _result(), measured_at="x").crop_scan_failed)

    def test_hdr_tonemap_flag_and_reason_survive_the_roundtrip(self) -> None:
        """HDR 톤 매핑 표식과 측정 불가 사유가 결과, 캐시 행, 행 값 왕복에서 유지됨"""
        result = FrameAnalysisResult(
            active_region=ActiveRegion(width=720, height=1280, x=0, y=0, has_black_bars=False),
            metric_used="",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=0,
            total_segment_count=9,
            unmeasurable=True,
            is_vr_protected=False,
            crop_scan_failed=True,
            hdr_tonemapped=True,
            unmeasurable_reason="FFmpeg 빌드에 HDR 톤 매핑 필터가 없습니다.",
        )
        record = record_from_result(_request(), result, measured_at="2026-11-28T00:00:00Z")
        self.assertTrue(record.hdr_tonemapped)
        self.assertEqual(record.unmeasurable_reason, result.unmeasurable_reason)
        self.assertEqual(result_from_record(record), result)
        restored = row_to_record(record_to_row(record))
        self.assertTrue(restored.hdr_tonemapped)
        self.assertEqual(restored.unmeasurable_reason, result.unmeasurable_reason)

    def test_row_roundtrip_follows_the_record_columns(self) -> None:
        record = replace(
            record_from_result(_request(), _result(), measured_at="2026-11-23T00:00:00Z"),
            fingerprint="a" * 32,
        )
        row = record_to_row(record)
        self.assertEqual(len(row), len(RECORD_COLUMNS))
        self.assertEqual(row_to_record(row), record)
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
