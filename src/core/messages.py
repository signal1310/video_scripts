"""CUI·GUI·서비스 전반 공용 용어, 로그 포맷, 상태·경고 메시지 및 동적 툴팁 생성기 모듈"""

from src.core.config import BitrateModelConfig, ThresholdsConfig

# 로그 레벨 태그
LOG_TAG_DEBUG: str = "[DEBUG]"
LOG_TAG_INFO: str = "[INFO]"
LOG_TAG_WARN: str = "[WARN]"
LOG_TAG_ERROR: str = "[ERROR]"
LOG_TAG_DONE: str = "[DONE]"
LOG_TAG_CANCEL: str = "[CANCEL]"
LOG_TAG_EXPORT: str = "[EXPORT]"

# 공용 도메인 표기 (GUI/CUI 즉시 읽기용 간결 표기)
LABEL_ORIGINAL_PRESERVED: str = "원본 보존"
LABEL_ORIGINAL_LOCATION: str = "원본 위치"
LABEL_PSEUDO_CLASSIFY: str = "모의 분류"
LABEL_MOVED: str = "이동됨"
LABEL_UNMEASURED: str = "미측정"
LABEL_UNMEASURED_DETAIL: str = "미측정 (정밀 분석 시 측정됨)"
LABEL_NO_MOVE_TARGET: str = "원본 보존 (이동 안 함)"

# 공용 중단·오류 메시지
MSG_ANALYSIS_CANCELLED: str = "사용자에 의해 분석 작업이 중단되었습니다."
MSG_PROBING_CANCELLED: str = "프로빙 작업이 중단되었습니다."
MSG_WORK_CANCELLED: str = "작업이 중단되었습니다."
MOVE_FAILED_PREFIX: str = "파일 이동 실패:"


def format_log(tag: str, message: str) -> str:
    """지정한 로그 태그를 접두사로 붙인 단일 로그 문자열 생성"""
    return f"{tag} {message}"


def format_warning_log(message: str) -> str:
    """파일 작업 경고·실패 문자열에 중복 태그 없이 로그 레벨([WARN]/[ERROR]) 부여"""
    clean = message.strip()
    if clean.startswith((LOG_TAG_WARN, LOG_TAG_ERROR, LOG_TAG_INFO)):
        return clean
    tag = LOG_TAG_ERROR if clean.startswith(MOVE_FAILED_PREFIX) else LOG_TAG_WARN
    return format_log(tag, clean)


def format_invalid_root_dir_msg(root_dir: str) -> str:
    """유효하지 않은 작업 폴더 예외 메시지 생성"""
    return f"지정된 작업 폴더를 찾을 수 없거나 유효하지 않습니다: '{root_dir}'"


def format_unclassify_done(moved_count: int, warning_count: int = 0) -> str:
    """분류 복원 완료 상태 문자열 생성"""
    warn_suffix = f" (경고 {warning_count}건)" if warning_count else ""
    return f"분류 복원 완료: {moved_count:,}개 파일이 작업 폴더로 복원됨{warn_suffix}"


def format_over_bitrate_desc(
    bitrate_model: BitrateModelConfig, ratio: float
) -> str:
    """기준 해상도·FPS·목표 비트레이트 기반 과화질 기준(kbps) 설명 생성"""
    over_kbps = round(bitrate_model.base_target_kbps * ratio)
    return (
        f"{bitrate_model.base_width}x{bitrate_model.base_height} "
        f"{bitrate_model.base_fps:g}fps H.264 기준 > {over_kbps:,} kbps"
    )


def format_quality_zone_tooltip(thresholds: ThresholdsConfig) -> str:
    """화질 경계 배수 설정을 반영한 화질 등급 툴팁 문자열 생성"""
    return (
        "최적 비트레이트 대비 비율에 따른 화질 판별 등급입니다.\n"
        f"• 과화질: {thresholds.over_bitrate_ratio:.2f}배 초과\n"
        f"• 적정화질: {thresholds.low_bitrate_ratio:.2f}~{thresholds.over_bitrate_ratio:.2f}배\n"
        f"• 저화질: {thresholds.low_bitrate_ratio:.2f}배 미만"
    )


def format_keyframe_tooltip(thresholds: ThresholdsConfig) -> str:
    """키프레임 상한 설정을 반영한 키프레임 간격 툴팁 문자열 생성"""
    return (
        "영상의 최대 키프레임 간격입니다. (단위: 초)\n"
        f"⚠ 표시는 허용 상한({thresholds.keyframe_interval_sec:.1f}초)을 초과해 교정 대상임을 뜻합니다."
    )
