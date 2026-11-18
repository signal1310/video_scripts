"""테스트용 VideoProps, VideoEvaluation, SummaryReport 생성 헬퍼"""

from typing import Any

from src.core.models import (
    QualityZone,
    SavingsSummary,
    SmartAction,
    SummaryReport,
    VideoEvaluation,
    VideoProps,
)

_DEFAULT_VIDEO: dict[str, Any] = {
    "filename": "clip.mp4",
    "width": 1920,
    "height": 1080,
    "rotate_type": 0,
    "fps": 30.0,
    "vid_kbps": 4000,
    "aud_kbps": 128,
    "vid_size_MB": 100.0,
    "duration": 60.0,
    "codec": "h264",
    "keyframe_interval": 1.0,
}


def make_video(**overrides: Any) -> VideoProps:
    """1080p h264 기본값에 지정 필드만 덮어쓴 VideoProps 생성"""
    return VideoProps(**{**_DEFAULT_VIDEO, **overrides})


def make_eval(video: VideoProps | None = None, **overrides: Any) -> VideoEvaluation:
    """과화질 CQ22 인코딩 대상을 기본으로 지정 필드만 덮어쓴 VideoEvaluation 생성"""
    fields: dict[str, Any] = {
        "optimal_kbps": 1500,
        "bitrate_ratio": 2.0,
        "quality_zone": QualityZone.OVER,
        "is_keyframe_exceeded": False,
        "expected_after_mb": 50.0,
        "expected_delta_mb": 50.0,
        "smart_action": SmartAction.ENCODE_OVER,
        **overrides,
    }
    return VideoEvaluation(video=video or make_video(), **fields)


def make_report(evaluations: list[VideoEvaluation], **overrides: Any) -> SummaryReport:
    """평가 목록에서 합계를 도출하고 지정 필드만 덮어쓴 SummaryReport 생성"""
    targets = [ev for ev in evaluations if ev.is_encode_target]
    gross = sum(ev.expected_delta_mb for ev in evaluations if ev.expected_delta_mb > 0)
    increase = -sum(ev.expected_delta_mb for ev in evaluations if ev.expected_delta_mb < 0)
    fields: dict[str, Any] = {
        "root_dir": "C:/dummy",
        "total_count": len(evaluations),
        "encoded_count": len(targets),
        "passed_count": len(evaluations) - len(targets),
        "total_orig_mb": sum(ev.video.vid_size_MB for ev in evaluations),
        "total_expected_after_mb": sum(ev.expected_after_mb for ev in evaluations),
        "gross_saved_mb": gross,
        "keyframe_increase_mb": increase,
        "net_saved_mb": gross - increase,
        "savings_summary": SavingsSummary(
            min_saved_mb=5.0,
            min_mb_per_min=3.0,
            exempt_saved_mb=50.0,
            encode_duration_sec=sum(ev.video.duration for ev in targets),
            low_saved_count=0,
            low_per_min_count=0,
            excluded_over_duration_sec=0.0,
            excluded_over_potential_saved_mb=0.0,
        ),
        **overrides,
    }
    return SummaryReport(evaluations=evaluations, **fields)
