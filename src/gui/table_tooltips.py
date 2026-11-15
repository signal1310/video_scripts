"""테이블 행과 열 헤더의 상세 툴팁 문자열 생성 모듈"""

from src.core.config import ClassifierConfig, get_encode_codec_label
from src.core.file_manager import logical_video_filepath
from src.core.formatting import format_duration_hms
from src.core.messages import LABEL_NO_MOVE_TARGET, LABEL_UNMEASURED_DETAIL
from src.core.models import VideoEvaluation
from src.gui.constants import SAVED_COLOR_THRESHOLD_MB

_SAVED_PER_MIN_EXAMPLE_MINUTES: int = 10


def _format_status_tooltip_detail(ev: VideoEvaluation) -> str:
    status_label = ev.classify_status.value
    if ev.video.moved_dirname:
        return f"{status_label} (현재 위치: {ev.video.moved_dirname})"
    if ev.target_dirname:
        return f"{status_label} (대상 폴더: {ev.target_dirname})"
    return f"{status_label} ({LABEL_NO_MOVE_TARGET})"


def format_saved_per_min_tooltip(cfg: ClassifierConfig) -> str:
    """분당 절약 열 상세 툴팁 문자열 생성"""
    min_rate = cfg.savings.min_mb_per_min
    exempt_mb = cfg.savings.exempt_saved_mb
    if min_rate > 0:
        example_mb = min_rate * _SAVED_PER_MIN_EXAMPLE_MINUTES
        exempt_note = (
            f" (단, 총 {exempt_mb:.0f} MB 이상 절약 시 예외 포함)"
            if exempt_mb > 0
            else ""
        )
        limit_line = (
            f"현재 분당 절약 기준: {min_rate:.1f} MB/분 미만은 원본 보존"
            f"{exempt_note} ({_SAVED_PER_MIN_EXAMPLE_MINUTES}분 영상 기준 {example_mb:.1f} MB 미만 절약 시 제외)"
        )
    else:
        limit_line = "현재 분당 절약 기준: 사용 안 함 (분당 절약 제한 없이 통과)"
    return (
        "원본 영상 재생 1분당 예상 용량 절약(MB/분)입니다.\n"
        "계산: 예상 절약(MB) ÷ 영상 재생 길이(분)  (예: 10분 영상에서 50 MB 절약 시 +5.0 MB/분)\n"
        f"{limit_line}\n"
        "표기: +는 용량 절약, -는 키프레임 재인코딩 등으로 증가, 회색 일반체는 원본 보존 행을 재인코딩할 경우의 참고값"
    )


def format_est_encode_time_tooltip(cfg: ClassifierConfig) -> str:
    """예상 시간 열 상세 툴팁 문자열 생성"""
    return (
        "분류 후 샤나인코더 등으로 실제 재인코딩을 실행할 때 걸리는 예상 시간입니다. "
        "(폴더 분석이나 파일 이동 시간이 아님)\n"
        f"계산: (영상 재생 길이 × 1080p {cfg.bitrate_model.base_fps:g}fps 대비 해상도, FPS 작업량 배수) ÷ "
        f"기준 배속({cfg.encode.encode_speed_factor:.1f}배속)\n"
        f"적용 기준: {get_encode_codec_label(cfg.encode.encode_codec)} (설정 > 환경 설정에서 조정)\n"
        "표기: 일반 색상은 실제 재인코딩 대상의 예상 시간, 회색은 원본 보존 행을 재인코딩할 경우의 참고값"
    )


def format_row_tooltip(ev: VideoEvaluation, root_dir: str) -> str:
    """테이블 행 호버 시 표시할 파일, 회전 출처, 품질, 판정 사유 상세 툴팁 생성"""
    vid = ev.video
    full_path = logical_video_filepath(root_dir, vid)
    kf_str = (
        f"{vid.keyframe_interval:.3f}초"
        if vid.keyframe_interval is not None
        else LABEL_UNMEASURED_DETAIL
    )
    rotate_detail = (
        f"{vid.rotate_type}° (원본: {vid.rotate_source})"
        if vid.rotate_source
        else f"{vid.rotate_type}°"
    )
    est_time_str = format_duration_hms(ev.estimated_encode_sec)
    if ev.is_encode_target:
        size_line = (
            f"용량 변화: {vid.vid_size_MB:.2f} MB → {ev.expected_after_mb:.2f} MB "
            f"(절약 {ev.expected_delta_mb:+.2f} MB, {ev.preview_mb_per_min:+.2f} MB/분)\n"
            f"예상 시간: 약 {est_time_str}\n"
        )
    else:
        per_min = (
            f", {ev.preview_mb_per_min:+.2f} MB/분"
            if vid.duration > 0 and abs(ev.preview_delta_mb) > SAVED_COLOR_THRESHOLD_MB
            else ""
        )
        size_line = (
            f"용량 변화: 원본 보존 ({vid.vid_size_MB:.2f} MB), "
            f"재인코딩 시 {ev.preview_after_mb:.2f} MB ({ev.preview_delta_mb:+.2f} MB{per_min}, "
            f"예상 시간 약 {est_time_str})\n"
        )
    return (
        f"파일명: {vid.filename}\n"
        f"경로: {full_path}\n"
        f"해상도: {vid.width}x{vid.height} (비율 {vid.aspect_ratio:.3f}, 회전 {rotate_detail})\n"
        f"코덱 / FPS: {vid.codec} / {vid.fps:.3f} fps\n"
        f"비트레이트: 현재 {vid.vid_kbps:,} kbps / 최적 {ev.optimal_kbps:,} kbps ({ev.bitrate_ratio:.3f}배)\n"
        f"키프레임 간격: {kf_str}\n"
        f"{size_line}"
        f"권장 처리: {ev.smart_action.action_label} ({ev.smart_action.option_label})\n"
        f"판정 사유: {ev.smart_action.reason_label} — {ev.smart_action.reason_description}\n"
        f"분류 상태: {_format_status_tooltip_detail(ev)}"
    )
