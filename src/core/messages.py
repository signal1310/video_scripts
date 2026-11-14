"""CUI·GUI·서비스 전반 공용 용어, 로그 포맷, 상태·경고 메시지 및 동적 툴팁 생성기 모듈"""

from src.core.config import BitrateModelConfig, ClassifierConfig, ThresholdsConfig
from src.core.models import ClassifyStatus, SmartAction

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


def format_classify_status_tooltip(config: ClassifierConfig) -> str:
    """분류 폴더 설정을 반영한 분류 상태 열 상세 툴팁 문자열 생성"""
    return (
        "실제 파일이 디스크에서 분류 폴더로 이동되었는지 나타내는 상태입니다.\n"
        f"• {ClassifyStatus.MOVED.value}: 이미 하위 분류 폴더({config.dir_standard_cq22} 등)로 실제 이동된 파일입니다.\n"
        f"• {ClassifyStatus.PENDING.value}: 인코딩·교정 대상으로, '폴더 분류 이동' 실행 시 이동됩니다.\n"
        f"• {ClassifyStatus.NOT_TARGET.value}: 이동 없이 작업 폴더(원본 위치)에 그대로 유지됩니다."
    )


def format_action_tooltip(config: ClassifierConfig) -> str:
    """분류 폴더 설정을 반영한 권장 작업 열 상세 툴팁 문자열 생성"""
    return (
        "화질 등급과 키프레임 간격을 종합해 권장하는 처리 방식입니다.\n"
        f"• {SmartAction.ENCODE_CQ22_OVER.action_label}: 표준 화질(CQ22)과 1초 키프레임으로 재인코딩합니다. (분류: {config.dir_standard_cq22})\n"
        f"• {SmartAction.ENCODE_LOW_KF.action_label}: 노이즈 증폭을 억제하며 1초 키프레임으로 교정합니다. (분류: {config.dir_low_quality_keyframe})\n"
        f"• {SmartAction.PASS_OPTIMAL.action_label}: 재인코딩이나 폴더 이동 없이 원본 파일을 그대로 유지합니다."
    )


def format_reason_tooltip(config: ClassifierConfig) -> str:
    """임계값 설정을 반영한 사유 열 상세 툴팁 문자열 생성"""
    t = config.thresholds
    sf = config.small_footage
    return (
        "권장 작업이 결정된 핵심 판정 사유입니다.\n"
        f"• {SmartAction.ENCODE_CQ22_OVER.reason_label}: 비트레이트가 최적 기준의 {t.over_bitrate_ratio:.2f}배를 넘어 용량 절감 효과가 큽니다.\n"
        f"• {SmartAction.ENCODE_CQ22_OPT_KF.reason_label}: 키프레임 간격이 허용 상한({t.keyframe_interval_sec:.1f}초)을 초과해 탐색 교정이 필요합니다.\n"
        f"• {SmartAction.PASS_OPTIMAL.reason_label}: 적정화질 범위이면서 키프레임 간격도 정상 기준을 만족합니다.\n"
        f"• {SmartAction.PASS_LOW.reason_label}: 이미 저화질이고 키프레임도 정상이므로 재인코딩에 따른 화질 저하를 막습니다.\n"
        f"• {SmartAction.PASS_SMALL_FOOTAGE.reason_label}: {sf.size_mb:.0f}MB 이하 소형 과화질 영상이지만 "
        f"예상 절감량({sf.min_saved_mb:.1f}MB 미만)이나 전체 절감 비중이 작아 원본을 유지합니다.\n"
        f"• {SmartAction.PASS_EXCEPTION.reason_label}: 상단 예외 키워드 또는 조건식 규칙에 해당해 분류에서 제외되었습니다."
    )
