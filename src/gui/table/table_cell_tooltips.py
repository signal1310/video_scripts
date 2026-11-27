"""표 셀별 영상 맞춤 툴팁 문구 모듈, 파일 이름 셀의 종합 상세 툴팁은 table_tooltips가 담당"""

from src.core.formatting import format_duration_hms
from src.core.messages import LABEL_UNMEASURED_DETAIL
from src.core.models import VideoEvaluation
from src.core.quality_target import format_cq_value
from src.core.smart_action import SmartAction
from src.service.encode.encode_preview import PlannedCommand, plan_approximation_note

_REFERENCE_NOTE: str = " (원본 보존 행, 인코딩 가정 참고값)"
_NOT_TARGET_TIP: str = "인코딩 배치 대상이 아닙니다."
# - 인코딩 대상이 아니라 예정 명령이 없는 행의 인자 열 툴팁
FFMPEG_ARGS_NO_COMMAND_TIP: str = "인코딩 배치 대상이 아니므로 예정 명령이 없습니다."
_KEPT_NO_QUALITY_TIP: str = "원본을 보존하는 영상이라 계획한 품질값이 없습니다."
_REANALYSIS_NO_QUALITY_TIP: str = (
    "재분석 필요 상태라 계획한 품질값이 없습니다. 다시 분석하면 품질값이 정해집니다."
)


def _reference_note(ev: VideoEvaluation) -> str:
    """원본 보존 행의 예상 값에 붙이는 참고값 안내"""
    return "" if ev.is_encode_target else _REFERENCE_NOTE


def width_cell_tip(ev: VideoEvaluation) -> str:
    """너비 셀 툴팁"""
    return f"회전 보정 반영 너비 {ev.video.width} px"


def height_cell_tip(ev: VideoEvaluation) -> str:
    """높이 셀 툴팁"""
    return f"회전 보정 반영 높이 {ev.video.height} px"


def rotate_cell_tip(ev: VideoEvaluation) -> str:
    """회전 셀 툴팁, 메타데이터 출처가 있으면 함께 표시"""
    source = f" (원본: {ev.video.rotate_source})" if ev.video.rotate_source else ""
    return f"회전 {ev.video.rotate_type}°{source}"


def codec_cell_tip(ev: VideoEvaluation) -> str:
    """코덱 셀 툴팁"""
    return f"현재 코덱 {ev.video.codec}"


def fps_cell_tip(ev: VideoEvaluation) -> str:
    """FPS 셀 툴팁"""
    return f"현재 {ev.video.fps:.3f} fps"


def duration_cell_tip(ev: VideoEvaluation) -> str:
    """재생 시간 셀 툴팁"""
    return f"재생 시간 {format_duration_hms(ev.video.duration)}"


def bitrate_cell_tip(ev: VideoEvaluation) -> str:
    """비트레이트 셀 툴팁"""
    return f"현재 비트레이트 {ev.video.vid_kbps:,} kbps"


def optimal_bitrate_cell_tip(ev: VideoEvaluation) -> str:
    """최적 BR 셀 툴팁"""
    return f"해상도, FPS, 코덱 기준 최적 비트레이트 {ev.optimal_kbps:,} kbps"


def ratio_cell_tip(ev: VideoEvaluation) -> str:
    """BR 비율 셀 툴팁"""
    return f"현재 비트레이트는 최적 비트레이트의 {ev.bitrate_ratio:.3f}배"


def target_ratio_cell_tip(ev: VideoEvaluation) -> str:
    """출력 BR 비율 셀 툴팁"""
    return f"출력 코덱 기준 현재 비트레이트는 최적 비트레이트의 {ev.target_bitrate_ratio:.3f}배"


def zone_cell_tip(ev: VideoEvaluation) -> str:
    """화질 등급 셀 툴팁"""
    return f"{ev.quality_zone.value}, BR 비율 {ev.bitrate_ratio:.3f}배"


def keyframe_cell_tip(ev: VideoEvaluation) -> str:
    """키프레임 간격 셀 툴팁, 허용 상한을 넘으면 그 사실을 표시"""
    interval = ev.video.keyframe_interval
    if interval is None:
        return f"키프레임 간격 {LABEL_UNMEASURED_DETAIL}"
    exceeded = " (허용 상한 초과)" if ev.is_keyframe_exceeded else ""
    return f"키프레임 간격 {interval:.3f}초{exceeded}"


def size_cell_tip(ev: VideoEvaluation) -> str:
    """현재 용량 셀 툴팁"""
    return f"현재 용량 {ev.video.vid_size_MB:.2f} MB"


def after_size_cell_tip(ev: VideoEvaluation) -> str:
    """인코딩 후 예상 용량 셀 툴팁"""
    return f"인코딩 후 예상 용량 {ev.preview_after_mb:.2f} MB{_reference_note(ev)}"


def saved_size_cell_tip(ev: VideoEvaluation) -> str:
    """인코딩 후 예상 절약 셀 툴팁"""
    return f"예상 절약 {ev.preview_delta_mb:+.2f} MB{_reference_note(ev)}"


def saved_per_min_cell_tip(ev: VideoEvaluation) -> str:
    """분당 절약 셀 툴팁"""
    return f"재생 1분당 예상 절약 {ev.preview_mb_per_min:+.2f} MB{_reference_note(ev)}"


def est_encode_time_cell_tip(ev: VideoEvaluation) -> str:
    """예상 시간 셀 툴팁"""
    return f"예상 인코딩 시간 약 {format_duration_hms(ev.estimated_encode_sec)}"


def action_cell_tip(ev: VideoEvaluation) -> str:
    """권장 처리 셀 툴팁, 처리 종류와 적용 옵션을 함께 표시"""
    return f"{ev.action_label}, {ev.option_label}"


def quality_value_cell_tip(ev: VideoEvaluation) -> str:
    """품질값 셀 툴팁, 계획한 품질값과 실제 인코딩 값이 달라질 수 있음을 안내

    - 계획 값이 없는 사유는 원본 보존과 재분석 필요로 갈리므로 판정을 보고 문구를 고름
    """
    cq = ev.planned_cq
    if cq is None:
        if ev.smart_action is SmartAction.LEGACY_FORMAT:
            return _REANALYSIS_NO_QUALITY_TIP
        return _KEPT_NO_QUALITY_TIP
    return (
        f"평가가 계획한 품질값 {format_cq_value(cq)}입니다. 낮을수록 고화질입니다. "
        "품질값 정밀 결정이나 자동 조정을 켜면 실제 인코딩 값은 이 값에서 달라질 수 있습니다."
    )


def reason_cell_tip(ev: VideoEvaluation) -> str:
    """판정 사유 셀 툴팁, 이 영상의 사유와 그 설명만 표시"""
    return f"{ev.reason_label}: {ev.smart_action.reason_description}"


def out_bitrate_cell_tip(ev: VideoEvaluation) -> str:
    """출력 BR 셀 툴팁"""
    return f"인코딩 후 예상 비트레이트 {ev.target_optimal_kbps:,} kbps"


def encode_state_cell_tip(_ev: VideoEvaluation) -> str:
    """인코딩 상태 셀의 기본 툴팁, 배치 상태가 있으면 표 모델이 그 툴팁을 우선함"""
    return _NOT_TARGET_TIP


def ffmpeg_args_line(planned: PlannedCommand | None) -> str:
    """셀 툴팁과 인자 열에 쓰는 한 줄 요약. 명령을 만들 수 없으면 빈 문자열

    - 표 모델이 행마다 한 번 계산한 결과를 넘겨 툴팁과 열이 같은 값을 씀
    - 스트림 구성을 추정한 계획은 근사 표시를 붙임
    - 결과 경로를 쓸 수 없어 명령을 만들지 않았으면 그 사유를 알려 줌
    """
    if planned is None:
        return ""
    if not planned.command_line:
        return f"FFmpeg 명령을 만들 수 없습니다. {planned.skip_reason}" if planned.skip_reason else ""
    return f"FFmpeg 인자: {planned.summary}{plan_approximation_note(planned)}"
