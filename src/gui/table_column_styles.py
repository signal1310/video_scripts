"""테이블 열의 전경색, 폰트 굵기 규칙을 담당하는 셀 스타일 함수 모듈"""

from src.core.models import VideoEvaluation
from src.gui.constants import SAVED_COLOR_THRESHOLD_MB
from src.gui.styles.palettes import ThemePalette

FONT_MONO: str = "mono"
FONT_BOLD: str = "bold"

def fg_keyframe(ev: VideoEvaluation, p: ThemePalette) -> str:
    """키프레임 간격 미측정 또는 허용 상한 초과 여부에 따른 셀 전경색 결정"""
    if ev.is_keyframe_exceeded:
        return p.fg_kf_exceeded
    kf = ev.video.keyframe_interval
    return p.fg_cell_muted if kf is None or kf < 0 else p.fg_kf_opt


def fg_after_size(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    """예상 용량 셀의 원본 보존 행 참고값 전경색 결정"""
    if ev.is_encode_target:
        return None
    if abs(ev.preview_delta_mb) > SAVED_COLOR_THRESHOLD_MB:
        return p.text_secondary
    return p.fg_cell_muted


def fg_signed_metric(
    ev: VideoEvaluation, value: float, threshold: float, p: ThemePalette
) -> str:
    """부호 있는 절감 값과 임계값에 따른 셀 전경색 결정"""
    if not ev.is_encode_target:
        return p.text_secondary if abs(value) > threshold else p.fg_cell_muted
    if value > threshold:
        return p.fg_saved_pos
    return p.fg_saved_neg if value < -threshold else p.fg_cell_muted


def font_signed_metric(ev: VideoEvaluation, value: float, threshold: float) -> str:
    """부호 있는 절감 값과 임계값에 따른 셀 폰트 굵기 결정"""
    return (
        FONT_BOLD
        if ev.is_encode_target and abs(value) > threshold
        else FONT_MONO
    )


def fg_saved_size(ev: VideoEvaluation, p: ThemePalette) -> str:
    """예상 절약 셀 전경색 결정"""
    return fg_signed_metric(ev, ev.preview_delta_mb, SAVED_COLOR_THRESHOLD_MB, p)


def font_saved_size(ev: VideoEvaluation) -> str:
    """예상 절약 셀 폰트 굵기 결정"""
    return font_signed_metric(ev, ev.preview_delta_mb, SAVED_COLOR_THRESHOLD_MB)


def fg_saved_per_min(ev: VideoEvaluation, p: ThemePalette) -> str:
    """분당 절약 셀 전경색 결정"""
    return fg_signed_metric(ev, ev.preview_mb_per_min, SAVED_COLOR_THRESHOLD_MB, p)


def font_saved_per_min(ev: VideoEvaluation) -> str:
    """분당 절약 셀 폰트 굵기 결정"""
    return font_signed_metric(ev, ev.preview_mb_per_min, SAVED_COLOR_THRESHOLD_MB)


def fg_est_encode_time(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    """예상 시간 셀 전경색 결정, 원본 보존 행은 참고값으로 회색 처리"""
    if ev.is_encode_target:
        return None
    return p.text_secondary if ev.estimated_encode_sec > 0 else p.fg_cell_muted
