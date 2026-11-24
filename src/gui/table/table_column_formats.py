"""테이블 셀 표시 문자열, 글자색, 배지 매핑, 크기 단위 설명을 만드는 순수 포맷 모듈

- 열 스키마(`table_columns.py`)가 이 모듈의 함수와 표를 가져가 씀
"""

from collections.abc import Callable

from src.core.config import ClassifierConfig
from src.core.encode.encode_batch_book import (
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    STATUS_SKIPPED,
)
from src.core.encode.encode_previous_discard import PREVIOUS_DISCARD_LABEL
from src.core.formatting import (
    format_duration_hms,
    format_float_precision,
    format_keyframe_interval,
)
from src.core.models import QualityZone, VideoEvaluation
from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.smart_action import (
    ActionBadgeTone,
    DownscaleReason,
    SmartAction,
)
from src.gui.styles.palettes import ThemePalette

HALF_TURN_DEG: int = 180
SAVED_PER_MIN_DECIMALS: int = 1
OUTPUT_RES_NOT_DOWNSCALED_LABEL: str = "축소 안 함"
OUTPUT_RES_NOT_DOWNSCALED_CELL: str = "-"
OUTPUT_RES_FILTER_LABELS: tuple[str, ...] = (
    OUTPUT_RES_NOT_DOWNSCALED_LABEL,
    *(tier.display for tier in RESOLUTION_TIERS),
)

BADGE_BY_ZONE: dict[QualityZone, Callable[[ThemePalette], tuple[str, str, str]]] = {
    QualityZone.OVER: lambda p: p.badge_over,
    QualityZone.OPTIMAL: lambda p: p.badge_optimal,
    QualityZone.LOW: lambda p: p.badge_low,
}
BADGE_BY_ACTION_TONE: dict[
    ActionBadgeTone, Callable[[ThemePalette], tuple[str, str, str]]
] = {
    ActionBadgeTone.STD_ENCODE: lambda p: p.badge_std_encode,
    ActionBadgeTone.LOW_KF_ENCODE: lambda p: p.badge_low_kf_encode,
    ActionBadgeTone.PASS: lambda p: p.badge_pass,
}
BADGE_BY_REASON: dict[str, Callable[[ThemePalette], tuple[str, str, str]]] = {
    SmartAction.ENCODE_OVER.reason_label: lambda p: p.badge_over,
    SmartAction.ENCODE_OPT_KF.reason_label: lambda p: p.badge_exceeded,
    SmartAction.ENCODE_CODEC_UPGRADE.reason_label: lambda p: p.badge_std_encode,
    SmartAction.ENCODE_COMPAT.reason_label: lambda p: p.badge_pending,
    SmartAction.PASS_OPTIMAL.reason_label: lambda p: p.badge_optimal,
    SmartAction.PASS_LOW.reason_label: lambda p: p.badge_low,
    SmartAction.PASS_LOW_SAVED.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_LOW_PER_MIN.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_CODEC_SAVED.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_EXCEPTION.reason_label: lambda p: p.badge_exception,
    SmartAction.PASS_ALREADY_ENCODED.reason_label: lambda p: p.badge_pass,
    DownscaleReason.MAX_RESOLUTION.value: lambda p: p.badge_std_encode,
    DownscaleReason.FRAME_ANALYSIS.value: lambda p: p.badge_std_encode,
    DownscaleReason.BITRATE_FIT.value: lambda p: p.badge_std_encode,
    DownscaleReason.MANUAL.value: lambda p: p.badge_std_encode,
}
KEYFRAME_EXCEEDED_ICON: str = "alert-triangle"
_ENCODE_DONE_ICON: str = "check"


def action_badge(
    ev: VideoEvaluation, p: ThemePalette
) -> tuple[str, str, str] | None:
    """권장 처리 배지. 원본 보존 행은 배지 없이 흐린 일반 글자로 표시"""
    if ev.action_badge_tone is ActionBadgeTone.PASS:
        return None
    return BADGE_BY_ACTION_TONE[ev.action_badge_tone](p)


def action_foreground(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    """권장 처리 글자색. 배지가 없는 원본 보존 행만 흐린 색을 씀"""
    return p.text_muted if ev.action_badge_tone is ActionBadgeTone.PASS else None


def reason_foreground(ev: VideoEvaluation, p: ThemePalette) -> str:
    """판정 사유 글자색. 사유 배지의 글자색을 그대로 씀"""
    return BADGE_BY_REASON.get(ev.reason_label, lambda pal: pal.badge_pass)(p)[1]


def format_rotate_text(ev: VideoEvaluation) -> str:
    """회전 값의 표시 문자열"""
    return f"{ev.video.rotate_type}°"


def rotate_icon_name(ev: VideoEvaluation) -> str | None:
    """회전 표시 아이콘 이름, 회전이 없으면 None"""
    deg = ev.video.rotate_type
    if deg == 0:
        return None
    if deg == HALF_TURN_DEG:
        return "rotate-180"
    return "rotate-ccw" if deg < 0 else "rotate-cw"


def fg_rotate(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    """회전 표시 글자색, 기본 색이면 None"""
    return p.fg_cell_muted if ev.video.rotate_type == 0 else None


def keyframe_filter_value(ev: VideoEvaluation) -> float | None:
    """키프레임 간격 필터 값, 측정값이 없으면 None"""
    kf = ev.video.keyframe_interval
    return None if kf is None or kf < 0 else float(kf)


def format_keyframe_display(ev: VideoEvaluation, cfg: ClassifierConfig) -> str:
    """키프레임 간격 표시 문자열"""
    return format_keyframe_interval(ev.video.keyframe_interval, cfg.display)


def keyframe_icon_name(ev: VideoEvaluation) -> str | None:
    """키프레임 간격 초과 표시 아이콘 이름, 초과가 아니면 None"""
    return KEYFRAME_EXCEEDED_ICON if ev.is_keyframe_exceeded else None


SIZE_UNIT_DESCRIPTIONS: dict[str, str] = {
    "mb": "MB",
    "gb": "GB",
    "auto": "자동 스케일(KB/MB/GB/TB)",
}


def size_unit_desc(size_unit: str) -> str:
    """용량 단위 설명 문구"""
    return SIZE_UNIT_DESCRIPTIONS.get(size_unit, "MB")


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    return format_duration_hms(seconds)


def output_res_filter_value(ev: VideoEvaluation) -> str:
    """출력 해상도 필터 값"""
    if not ev.is_downscaled or ev.target_tier is None:
        return OUTPUT_RES_NOT_DOWNSCALED_LABEL
    return ev.target_tier.display


def output_res_sort_value(ev: VideoEvaluation) -> int:
    """출력 해상도 정렬 값. 축소하면 출력 화소 수, 여백만 잘라내면 활성 화면 화소 수"""
    if ev.is_downscaled:
        return (ev.target_width or 0) * (ev.target_height or 0)
    frame = ev.active_frame
    return 0 if frame is None else frame.width * frame.height


def format_output_res_display(ev: VideoEvaluation, _cfg: ClassifierConfig) -> str:
    """출력 해상도 표시 문자열. 크기가 바뀌면 실제 최종 픽셀, 그대로면 `-`"""
    if ev.is_downscaled:
        return f"{ev.target_width}x{ev.target_height}"
    frame = ev.active_frame
    if frame is not None:
        return f"{frame.width}x{frame.height}"
    return OUTPUT_RES_NOT_DOWNSCALED_CELL


def fg_output_res(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    """출력 해상도 글자색, 최대 비트레이트에 가까우면 경고색"""
    return p.fg_maxrate_warn if ev.is_downscaled and ev.is_maxrate_near else None


def format_saved_per_min_display(ev: VideoEvaluation, _cfg: ClassifierConfig) -> str:
    """분당 절약 용량 표시 문자열"""
    val_str = format_float_precision(
        ev.preview_mb_per_min, SAVED_PER_MIN_DECIMALS, signed=True
    )
    return f"{val_str} MB/분"


_ENCODE_BADGE_BY_LABEL: dict[str, Callable[[ThemePalette], tuple[str, str, str]]] = {
    STATUS_RUNNING: lambda p: p.badge_encode_running,
    STATUS_RUNNING_PAUSED: lambda p: p.badge_pending,
    STATUS_DONE: lambda p: p.badge_pass,
    STATUS_DISCARDED: lambda p: p.badge_low_kf_encode,
    STATUS_FAILED: lambda p: p.badge_over,
    STATUS_SKIPPED: lambda p: p.badge_pass,
    STATUS_REPLACE_PENDING: lambda p: p.badge_exception,
    PREVIOUS_DISCARD_LABEL: lambda p: p.badge_low_kf_encode,
}


def encode_state_badge(label: str | None, p: ThemePalette) -> tuple[str, str, str] | None:
    """재인코딩 상태 표기의 배지 색. 대기나 배치 대상이 아니면 배지 없이 None"""
    if label is None:
        return None
    getter = _ENCODE_BADGE_BY_LABEL.get(label)
    return getter(p) if getter is not None else None


def encode_state_icon_name(label: str | None) -> str | None:
    """재인코딩 상태 표기의 아이콘 이름. 완료 표기만 체크 아이콘을 씀"""
    return _ENCODE_DONE_ICON if label == STATUS_DONE else None


def encode_state_foreground(label: str | None, p: ThemePalette) -> str | None:
    """대기 상태 표기의 보조 글자색. 배지가 있는 표기는 None"""
    return p.text_muted if label == STATUS_PENDING else None


