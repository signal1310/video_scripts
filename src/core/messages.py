"""CUI, GUI, 서비스 전반 공용 용어, 열 이름 기준 정의, 로그 포맷 및 동적 툴팁 생성기 모듈"""

from dataclasses import dataclass
from enum import Enum

from src.core.config import (
    ENCODE_FAMILY_LABELS,
    BitrateModelConfig,
    ClassifierConfig,
    ThresholdsConfig,
    get_encode_codec_family,
)
from src.core.file_manager import MoveIssue, MoveIssueKind
from src.core.models import ClassifyStatus, QualityZone, SmartAction


class ColumnId(Enum):
    """GUI, CUI 테이블 공통 열 식별자"""

    NAME = "name"
    STATUS = "status"
    WIDTH = "width"
    HEIGHT = "height"
    ROTATE = "rotate"
    CODEC = "codec"
    FPS = "fps"
    DURATION = "duration"
    BITRATE = "bitrate"
    OPT_BITRATE = "opt_bitrate"
    RATIO = "ratio"
    TARGET_RATIO = "target_ratio"
    ZONE = "zone"
    KEYFRAME = "keyframe"
    SIZE = "size"
    AFTER_SIZE = "after_size"
    SAVED_SIZE = "saved_size"
    SAVED_PER_MIN = "saved_per_min"
    EST_ENCODE_TIME = "est_encode_time"
    ACTION = "action"
    REASON = "reason"


@dataclass(frozen=True, slots=True)
class ColumnLabelSpec:
    """테이블 열의 일반(GUI), 축약(CUI), 콘솔 줄바꿈 헤더 명세"""

    full: str
    short: str
    cui_header: str = ""

    @property
    def compact_header(self) -> str:
        """CUI 콘솔 표 출력용 줄바꿈 헤더 문자열 반환"""
        return self.cui_header or f"\n{self.short}"


COLUMN_LABELS: dict[ColumnId, ColumnLabelSpec] = {
    ColumnId.NAME: ColumnLabelSpec("이름", "이름"),
    ColumnId.STATUS: ColumnLabelSpec("분류 상태", "상태"),
    ColumnId.WIDTH: ColumnLabelSpec("너비", "너비"),
    ColumnId.HEIGHT: ColumnLabelSpec("높이", "높이"),
    ColumnId.ROTATE: ColumnLabelSpec("회전", "회전"),
    ColumnId.CODEC: ColumnLabelSpec("코덱", "코덱"),
    ColumnId.FPS: ColumnLabelSpec("FPS", "FPS"),
    ColumnId.DURATION: ColumnLabelSpec("길이", "길이", "길이\n(초)"),
    ColumnId.BITRATE: ColumnLabelSpec("비트레이트", "BR"),
    ColumnId.OPT_BITRATE: ColumnLabelSpec("최적 비트레이트", "최적 BR", "최적\nBR"),
    ColumnId.RATIO: ColumnLabelSpec("비트레이트 비율", "BR 비율", "BR\n비율"),
    ColumnId.TARGET_RATIO: ColumnLabelSpec("출력 비트레이트 비율", "출력 BR 비율", "출력\nBR 비율"),
    ColumnId.ZONE: ColumnLabelSpec("화질 등급", "화질 등급", "화질\n등급"),
    ColumnId.KEYFRAME: ColumnLabelSpec("키프레임 간격", "KF 간격", "KF\n간격"),
    ColumnId.SIZE: ColumnLabelSpec("용량", "용량", "용량\n(MB)"),
    ColumnId.AFTER_SIZE: ColumnLabelSpec("예상 용량", "예상 용량", "예상 용량\n(MB)"),
    ColumnId.SAVED_SIZE: ColumnLabelSpec("예상 절약", "예상 절약", "예상 절약\n(MB)"),
    ColumnId.SAVED_PER_MIN: ColumnLabelSpec("분당 절약", "분당 절약", "분당 절약\n(MB/분)"),
    ColumnId.EST_ENCODE_TIME: ColumnLabelSpec("예상 시간", "예상 시간", "예상\n시간"),
    ColumnId.ACTION: ColumnLabelSpec("권장 처리", "권장 처리", "권장\n처리"),
    ColumnId.REASON: ColumnLabelSpec("판정 사유", "판정 사유", "판정\n사유"),
}

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
LABEL_NO_MOVE_TARGET: str = "원본 보존 (대상 아님)"

# 공용 중단, 오류 메시지
MSG_ANALYSIS_CANCELLED: str = "사용자에 의해 분석 작업이 중단되었습니다."
MSG_PROBING_CANCELLED: str = "영상 정보 읽기 작업이 중단되었습니다."
MSG_WORK_CANCELLED: str = "작업이 중단되었습니다."
_MOVE_FAILED_PREFIX: str = "파일 이동 실패:"


def format_log(tag: str, message: str) -> str:
    """지정한 로그 태그를 접두사로 붙인 단일 로그 문자열 생성"""
    return f"{tag} {message}"


def format_invalid_root_dir_msg(root_dir: str) -> str:
    """유효하지 않은 작업 폴더 예외 메시지 생성"""
    return f"지정된 작업 폴더를 찾을 수 없거나 유효하지 않습니다: '{root_dir}'"


def format_unclassify_done(moved_count: int, warning_count: int = 0) -> str:
    """작업 폴더로 복원 완료 상태 문자열 생성"""
    warn_suffix = f" (경고 {warning_count}건)" if warning_count else ""
    return f"작업 폴더로 복원 완료: {moved_count:,}개 파일을 되돌림{warn_suffix}"


def format_clear_analysis_cache_confirm(entry_count: int) -> str:
    """분석 캐시 비우기 확인 대화상자 본문 생성"""
    return (
        f"분석 캐시 {entry_count:,}건을 삭제합니다.\n"
        "다음 분석부터 영상을 다시 측정합니다. 계속하시겠습니까?"
    )


def format_clear_analysis_cache_done(entry_count: int) -> str:
    """분석 캐시 비우기 완료 안내 문구 생성"""
    return f"분석 캐시 {entry_count:,}건을 비웠습니다."


def format_move_issue(issue: MoveIssue) -> str:
    """파일 이동 이슈 1건을 로그와 대화상자에 표시할 문구로 변환"""
    if issue.kind is MoveIssueKind.CLASSIFY_TARGET_EXISTS:
        return (
            f"대상 폴더에 '{issue.dirname}/{issue.filename}' 파일이 이미 존재하여 "
            "덮어쓰지 않고 건너뛰었습니다."
        )
    if issue.kind is MoveIssueKind.UNCLASSIFY_ROOT_EXISTS:
        return (
            f"'{issue.filename}' 파일이 이미 작업 폴더에 존재하여 "
            f"'{issue.dirname}' 폴더에 유지했습니다."
        )
    return f"{_MOVE_FAILED_PREFIX} {issue.src_path} -> {issue.dst_path} ({issue.error})"


def _format_bitrate_ref_prefix(bitrate_model: BitrateModelConfig, basis: str) -> str:
    return (
        f"{bitrate_model.base_width}x{bitrate_model.base_height} "
        f"{bitrate_model.base_fps:g}fps {basis}"
    )


def format_over_bitrate_desc(config: ClassifierConfig, ratio: float) -> str:
    """목표 인코딩 코덱 기준 최적 비트레이트로 환산한 과화질 기준(kbps) 설명 생성"""
    bitrate_model = config.bitrate_model
    family = get_encode_codec_family(config.encode.encode_codec)
    weight = config.codec_weights.get_weight(family)
    over_kbps = round(bitrate_model.base_target_kbps * weight * ratio)
    prefix = _format_bitrate_ref_prefix(bitrate_model, f"{ENCODE_FAMILY_LABELS[family]} 출력 기준")
    return f"{prefix} > {over_kbps:,} kbps"


def format_low_bitrate_desc(
    bitrate_model: BitrateModelConfig, ratio: float
) -> str:
    """원본 코덱(H.264 기준) 최적 비트레이트로 환산한 저화질 상한(kbps) 설명 생성"""
    low_kbps = round(bitrate_model.base_target_kbps * ratio)
    return f"{_format_bitrate_ref_prefix(bitrate_model, 'H.264 기준')} ≤ {low_kbps:,} kbps"


def format_quality_zone_tooltip(thresholds: ThresholdsConfig) -> str:
    """화질 경계 배수 설정을 반영한 화질 등급 툴팁 문자열 생성"""
    return (
        "최적 비트레이트 대비 비율로 추정한 화질 등급입니다.\n"
        f"• {QualityZone.OVER.value}: {thresholds.over_bitrate_ratio:.2f}배 초과\n"
        f"• {QualityZone.OPTIMAL.value}: {thresholds.low_bitrate_ratio:.2f}~{thresholds.over_bitrate_ratio:.2f}배\n"
        f"• {QualityZone.LOW.value}: {thresholds.low_bitrate_ratio:.2f}배 미만"
    )


def format_keyframe_tooltip(thresholds: ThresholdsConfig) -> str:
    """키프레임 상한 설정을 반영한 키프레임 간격 툴팁 문자열 생성"""
    return (
        "KF(키프레임): 영상의 최대 키프레임 간격입니다. (단위: 초)\n"
        f"⚠ 표시는 허용 상한({thresholds.keyframe_interval_sec:.1f}초)을 초과해 재인코딩 대상임을 뜻합니다."
    )


def format_classify_status_tooltip(config: ClassifierConfig) -> str:
    """분류 폴더 설정을 반영한 분류 상태 열 상세 툴팁 문자열 생성"""
    return (
        "실제 파일이 디스크에서 분류 폴더로 이동되었는지 나타내는 상태입니다.\n"
        f"• {ClassifyStatus.MOVED.value}: 이미 하위 분류 폴더({config.dir_standard_cq22} 등)로 실제 이동된 파일입니다.\n"
        f"• {ClassifyStatus.PENDING.value}: 재인코딩 대상으로, '분류 폴더로 이동' 실행 시 이동됩니다.\n"
        f"• {ClassifyStatus.NOT_TARGET.value}: 이동 없이 작업 폴더(원본 위치)에 그대로 유지됩니다."
    )


def format_action_tooltip(config: ClassifierConfig) -> str:
    """분류 폴더 설정을 반영한 권장 처리 열 상세 툴팁 문자열 생성"""
    return (
        "화질 등급과 키프레임 간격을 종합해 권장하는 처리 방식입니다.\n"
        f"• {SmartAction.ENCODE_CQ22_OVER.action_label}: CQ22(화질 기준값 22)로 재인코딩하고 1초 키프레임을 적용합니다. (분류: {config.dir_standard_cq22})\n"
        f"• {SmartAction.ENCODE_LOW_KF.action_label}: CQ26으로 화질 기준을 낮춰 압축 노이즈 재현에 비트를 덜 쓰고 1초 키프레임으로 재인코딩합니다. (분류: {config.dir_low_quality_keyframe})\n"
        f"• {SmartAction.PASS_OPTIMAL.action_label}: 재인코딩이나 폴더 이동 없이 원본 파일을 그대로 보존합니다."
    )


def format_reason_tooltip(config: ClassifierConfig) -> str:
    """임계값, 절약 설정을 반영한 판정 사유 열 상세 툴팁 문자열 생성"""
    t = config.thresholds
    savings = config.savings
    exempt_note = (
        f" (단, {savings.exempt_saved_mb:.0f}MB 이상 절약 시 예외)"
        if savings.exempt_saved_mb > 0
        else ""
    )
    return (
        "권장 처리가 정해진 핵심 판정 사유입니다.\n"
        f"• {SmartAction.ENCODE_CQ22_OVER.reason_label}: 비트레이트가 최적 비트레이트의 {t.over_bitrate_ratio:.2f}배를 넘어 용량 절약 효과가 큽니다.\n"
        f"• 키프레임 간격 초과: 키프레임 간격이 허용 상한({t.keyframe_interval_sec:.1f}초)을 초과해 구간 이동이 끊기므로 재인코딩합니다.\n"
        f"• {SmartAction.PASS_OPTIMAL.reason_label}: 화질 등급이 {QualityZone.OPTIMAL.value} 범위이면서 키프레임 간격도 정상 기준을 만족합니다.\n"
        f"• {SmartAction.PASS_LOW.reason_label}: 화질 등급이 이미 {QualityZone.LOW.value}이고 키프레임도 정상이므로 재인코딩에 따른 화질 저하를 막습니다.\n"
        f"• {SmartAction.PASS_LOW_SAVED.reason_label}: "
        f"예상 절약이 최소 기준({savings.min_saved_mb:.1f}MB) 미만이어서 원본을 보존합니다.\n"
        f"• {SmartAction.PASS_LOW_PER_MIN.reason_label}: "
        f"재생 1분당 예상 절약({savings.min_mb_per_min:.1f}MB/분)이 기준 미만이라 원본을 보존합니다{exempt_note}.\n"
        f"• {SmartAction.PASS_CODEC_SAVED.reason_label}: 코덱 전환 대상이지만 예상 절약이 기준 미만이라 전환 효과가 낮아 원본을 보존합니다.\n"
        f"• {SmartAction.ENCODE_CODEC_UPGRADE.reason_label}: 압축 성능이 낮은 구형 또는 인트라 전용 코덱을 목표 코덱으로 전환해 용량을 줄입니다.\n"
        f"• {SmartAction.ENCODE_COMPAT.reason_label}: 구형 코덱 호환 재인코딩 설정에 따라 미지원 코덱을 표준 코덱과 1초 키프레임으로 재인코딩합니다.\n"
        f"• {SmartAction.PASS_HDR.reason_label}: HDR 영상은 색 정보 보호를 위해 원본을 보존합니다.\n"
        f"• {SmartAction.PASS_EXCEPTION.reason_label}: 상단 예외 키워드 또는 조건식 규칙에 해당해 분류에서 제외되었습니다."
    )
