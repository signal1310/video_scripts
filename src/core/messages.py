"""CUI, GUI, 서비스 전반 공용 용어, 열 이름 기준 정의, 로그 포맷 및 동적 툴팁 생성기 모듈"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from src.core.config import (
    ENCODE_FAMILY_LABELS,
    BitrateModelConfig,
    ClassifierConfig,
    ThresholdsConfig,
    get_encode_codec_family,
)
from src.core.models import QualityZone, VideoEvaluation
from src.core.quality_target import (
    PROTECT_CQ_OFFSET,
    calc_base_bitrate_kbps,
    format_cq_value,
    protect_cq_value,
)
from src.core.smart_action import DownscaleReason, SmartAction
from src.core.storage_probe import BUS_TYPE_USB, StorageInfo, StorageKind


class ColumnId(Enum):
    """GUI, CUI 테이블 공통 열 식별자"""

    NAME = "name"
    WIDTH = "width"
    HEIGHT = "height"
    ROTATE = "rotate"
    CODEC = "codec"
    FPS = "fps"
    DURATION = "duration"
    BITRATE = "bitrate"
    OPT_BITRATE = "opt_bitrate"
    RATIO = "ratio"
    OUT_BITRATE = "out_bitrate"
    TARGET_RATIO = "target_ratio"
    ZONE = "zone"
    KEYFRAME = "keyframe"
    SIZE = "size"
    AFTER_SIZE = "after_size"
    SAVED_SIZE = "saved_size"
    SAVED_PER_MIN = "saved_per_min"
    EST_ENCODE_TIME = "est_encode_time"
    OUTPUT_RES = "output_res"
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
    ColumnId.WIDTH: ColumnLabelSpec("너비", "너비"),
    ColumnId.HEIGHT: ColumnLabelSpec("높이", "높이"),
    ColumnId.ROTATE: ColumnLabelSpec("회전", "회전"),
    ColumnId.CODEC: ColumnLabelSpec("코덱", "코덱"),
    ColumnId.FPS: ColumnLabelSpec("FPS", "FPS"),
    ColumnId.DURATION: ColumnLabelSpec("길이", "길이", "길이\n(초)"),
    ColumnId.BITRATE: ColumnLabelSpec("비트레이트", "BR"),
    ColumnId.OPT_BITRATE: ColumnLabelSpec("최적 비트레이트", "최적 BR", "최적\nBR"),
    ColumnId.OUT_BITRATE: ColumnLabelSpec("출력 비트레이트", "출력 BR", "출력\nBR"),
    ColumnId.RATIO: ColumnLabelSpec("비트레이트 비율", "BR 비율", "BR\n비율"),
    ColumnId.TARGET_RATIO: ColumnLabelSpec("출력 비트레이트 비율", "출력 BR 비율", "출력\nBR 비율"),
    ColumnId.ZONE: ColumnLabelSpec("화질 등급", "화질 등급", "화질\n등급"),
    ColumnId.KEYFRAME: ColumnLabelSpec("키프레임 간격", "KF 간격", "KF\n간격"),
    ColumnId.SIZE: ColumnLabelSpec("용량", "용량", "용량\n(MB)"),
    ColumnId.AFTER_SIZE: ColumnLabelSpec("예상 용량", "예상 용량", "예상 용량\n(MB)"),
    ColumnId.SAVED_SIZE: ColumnLabelSpec("예상 절약", "예상 절약", "예상 절약\n(MB)"),
    ColumnId.SAVED_PER_MIN: ColumnLabelSpec("분당 절약", "분당 절약", "분당 절약\n(MB/분)"),
    ColumnId.EST_ENCODE_TIME: ColumnLabelSpec("예상 시간", "예상 시간", "예상\n시간"),
    ColumnId.OUTPUT_RES: ColumnLabelSpec("출력 해상도", "출력 해상도", "출력\n해상도"),
    ColumnId.ACTION: ColumnLabelSpec("권장 처리", "권장 처리", "권장\n처리"),
    ColumnId.REASON: ColumnLabelSpec("판정 사유", "판정 사유", "판정\n사유"),
}

# 로그 레벨 태그(표준 4단계만 사용, 작업 상태는 레벨과 문장으로 표현)
LOG_TAG_DEBUG: str = "[DEBUG]"
LOG_TAG_INFO: str = "[INFO]"
LOG_TAG_WARN: str = "[WARN]"
LOG_TAG_ERROR: str = "[ERROR]"

# 공용 도메인 표기 (GUI/CUI 즉시 읽기용 간결 표기)
LABEL_ORIGINAL_PRESERVED: str = "원본 보존"
LABEL_UNMEASURED: str = "미측정"
LABEL_UNMEASURED_DETAIL: str = "미측정 (정밀 분석 시 측정됨)"

# 공용 중단, 오류 메시지
MSG_ANALYSIS_CANCELLED: str = "사용자에 의해 분석 작업이 중단되었습니다."
MSG_PROBING_CANCELLED: str = "영상 정보 읽기 작업이 중단되었습니다."
MSG_WORK_CANCELLED: str = "작업이 중단되었습니다."
KBPS_PER_MBPS: int = 1000


def format_analysis_summary(total: int, reused: int, fresh: int, failed: int) -> str:
    """분석 한 번의 영상 수 구성(전체, 캐시 재사용, 새로 분석, 분석 실패) 로그 문장 생성"""
    return (
        f"분석 결과: 전체 {total:,}개 (캐시 재사용 {reused:,}, "
        f"새로 분석 {fresh:,}, 분석 실패 {failed:,})"
    )


def format_probe_failure(label: str, filename: str, reason: str) -> str:
    """개별 파일 측정 실패 1건의 경고 문장 생성"""
    return f"{label} 실패: {filename} ({reason})"


def format_failure_omitted(label: str, omitted: int) -> str:
    """상한을 넘어 개별 기록하지 않은 측정 실패 개수 안내 문장 생성"""
    return f"{label} 실패 {omitted:,}건은 목록에서 생략했습니다."


def format_task_start(label: str, options: Sequence[str]) -> str:
    """백그라운드 작업 시작 로그 문장 생성, 적용된 옵션이 있으면 괄호로 덧붙임"""
    suffix = f" ({', '.join(options)})" if options else ""
    return f"{label} 시작{suffix}"


def format_cancelled_progress(reason: str, current: int, total: int) -> str:
    """중단 사유에 중단 시점의 처리 개수를 덧붙인 로그 문장 생성"""
    return f"{reason} ({current:,}/{total:,} 처리)"


def format_keyframe_start(count: int) -> str:
    """키프레임 간격 추가 측정 시작 로그 문장 생성"""
    return f"키프레임 간격 측정 시작: {count:,}개"


_STORAGE_KIND_LABELS: dict[StorageKind, str] = {
    StorageKind.SSD: "SSD",
    StorageKind.HDD: "HDD",
    StorageKind.NETWORK: "네트워크 드라이브",
}


def format_storage_kind(info: StorageInfo) -> str:
    """작업 폴더 저장 장치 판별 결과 로그 문장 생성, 판별 불가이면 USB 연결 여부를 덧붙임"""
    if info.kind is StorageKind.UNKNOWN:
        suffix = "(USB 연결)" if info.bus_type == BUS_TYPE_USB else ""
        return f"작업 폴더 저장 장치는 판별 불가입니다{suffix}."
    return f"작업 폴더 저장 장치는 {_STORAGE_KIND_LABELS[info.kind]}입니다."


def format_export_done(count: int, filepath: str) -> str:
    """CSV 파일로 내보내기 완료 로그 문장 생성"""
    return f"CSV 파일로 내보내기 완료: {count:,}행, {filepath}"


def format_preview_start(filename: str) -> str:
    """미리 보기 재생 시작 로그 문장 생성, 재생기가 내는 원시 출력 묶음의 시작 시각 표지 역할"""
    return f"미리 보기 재생 시작: {filename}"


def format_invalid_root_dir_msg(root_dir: str) -> str:
    """유효하지 않은 작업 폴더 예외 메시지 생성"""
    return f"지정된 작업 폴더를 찾을 수 없거나 유효하지 않습니다: '{root_dir}'"


def format_clear_analysis_cache_confirm(entry_count: int) -> str:
    """분석 캐시 비우기 확인 대화상자 본문 생성"""
    return (
        f"분석 캐시 {entry_count:,}건을 삭제합니다.\n"
        "다음 분석부터 영상을 다시 측정합니다. 계속하시겠습니까?"
    )


def format_clear_analysis_cache_done(entry_count: int) -> str:
    """분석 캐시 비우기 완료 안내 문구 생성"""
    return f"분석 캐시 {entry_count:,}건을 비웠습니다."


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
    base_kbps = calc_base_bitrate_kbps(bitrate_model.base_target_kbps, config.encode.baseline_cq)
    over_kbps = round(base_kbps * weight * ratio)
    prefix = _format_bitrate_ref_prefix(bitrate_model, f"{ENCODE_FAMILY_LABELS[family]} 출력 기준")
    return f"{prefix} > {over_kbps:,} kbps"


def format_low_bitrate_desc(config: ClassifierConfig, ratio: float) -> str:
    """원본 코덱(H.264 기준) 최적 비트레이트로 환산한 저화질 상한(kbps) 설명 생성"""
    bitrate_model = config.bitrate_model
    base_kbps = calc_base_bitrate_kbps(bitrate_model.base_target_kbps, config.encode.baseline_cq)
    low_kbps = round(base_kbps * ratio)
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


def format_action_tooltip(config: ClassifierConfig) -> str:
    """권장 처리 열 상세 툴팁 문자열 생성, 설정한 화질 기준값으로 명칭의 품질값을 표기"""
    std_cq = config.encode.baseline_cq
    protect_cq = protect_cq_value(std_cq)
    return (
        "화질 등급과 키프레임 간격을 종합해 권장하는 처리 방식입니다.\n"
        f"• CQ{format_cq_value(std_cq)} 재인코딩: 화질 기준값으로 재인코딩하고 1초 키프레임을 적용합니다.\n"
        f"• CQ{format_cq_value(protect_cq)} 재인코딩: 저화질 프리셋 영상을 화질 기준값보다 "
        f"{PROTECT_CQ_OFFSET:.1f} 높은 값으로 재인코딩해 압축 노이즈 재현에 비트를 덜 씁니다.\n"
        f"• {SmartAction.ENCODE_DOWNSCALE.action_label}: 출력 해상도를 규격에 맞춰 줄이고 최대 비트레이트를 적용해 재인코딩합니다. 사유는 최대 해상도 초과, 비트레이트 맞춤, 직접 지정입니다.\n"
        f"• {SmartAction.PASS_OPTIMAL.action_label}: 재인코딩 없이 원본 파일을 그대로 보존합니다."
    )


def format_maxrate_detail(ev: VideoEvaluation) -> str:
    """축소 대상의 최대 비트레이트 적용 안내 문구 생성, 축소 대상이 아니면 빈 문자열"""
    tier = ev.target_tier
    headroom = ev.maxrate_headroom
    if tier is None or headroom is None:
        return ""
    max_mbps = tier.max_bitrate_kbps / KBPS_PER_MBPS
    avg_mbps = ev.target_optimal_kbps / KBPS_PER_MBPS
    return (
        f"최대 비트레이트 {max_mbps:.1f}Mbps가 적용됩니다. "
        "움직임이 많은 장면은 이 상한에 걸려 화질이 다소 낮아질 수 있습니다. "
        f"(예상 평균 {avg_mbps:.1f}Mbps, 여유 {headroom:.1f}배)"
    )


MAXRATE_NEAR_NOTICE: str = "최대 BR 근접: 복잡한 장면에서 화질 저하 가능성이 큽니다."


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
        f"• {SmartAction.ENCODE_OVER.reason_label}: 비트레이트가 최적 비트레이트의 {t.over_bitrate_ratio:.2f}배를 넘어 용량 절약 효과가 큽니다.\n"
        f"• 키프레임 간격 초과: 키프레임 간격이 허용 상한({t.keyframe_interval_sec:.1f}초)을 초과해 구간 이동이 끊기므로 재인코딩합니다.\n"
        f"• {SmartAction.PASS_OPTIMAL.reason_label}: 화질 등급이 {QualityZone.OPTIMAL.value} 범위이면서 키프레임 간격도 정상 기준을 만족합니다.\n"
        f"• {SmartAction.PASS_LOW.reason_label}: 화질 등급이 이미 {QualityZone.LOW.value}이고 키프레임도 정상이므로 재인코딩에 따른 화질 저하를 막습니다.\n"
        f"• {SmartAction.PASS_LOW_SAVED.reason_label}: "
        f"예상 절약이 최소 기준({savings.min_saved_mb:.1f}MB) 미만이어서 원본을 보존합니다.\n"
        f"• {SmartAction.PASS_LOW_PER_MIN.reason_label}: "
        f"재생 시간 1분당 예상 절약({savings.min_mb_per_min:.1f}MB/분)이 기준 미만이라 원본을 보존합니다{exempt_note}.\n"
        f"• {SmartAction.PASS_CODEC_SAVED.reason_label}: 코덱 전환 대상이지만 예상 절약이 기준 미만이라 전환 효과가 낮아 원본을 보존합니다.\n"
        f"• {SmartAction.ENCODE_CODEC_UPGRADE.reason_label}: 압축 성능이 낮은 구형 또는 인트라 전용 코덱을 목표 코덱으로 전환해 용량을 줄입니다.\n"
        f"• {SmartAction.ENCODE_COMPAT.reason_label}: 구형 코덱 호환 재인코딩 설정에 따라 미지원 코덱을 표준 코덱과 1초 키프레임으로 재인코딩합니다.\n"
        f"• {SmartAction.ENCODE_DOWNSCALE.reason_label}: 원본 규격이 최대 해상도 설정을 넘어 출력 해상도로 줄이고 최대 비트레이트를 적용해 재인코딩합니다.\n"
        f"• {DownscaleReason.BITRATE_FIT.value}: 저화질 영상이 더 낮은 규격에서도 화질 기준을 유지할 수 있어 그 규격으로 줄입니다. 설정에서 켠 경우에만 적용됩니다.\n"
        f"• {DownscaleReason.MANUAL.value}: 영상별로 지정한 출력 해상도로 줄입니다. 최대 해상도 설정보다 우선합니다.\n"
        f"• {SmartAction.PASS_HDR.reason_label}: HDR 영상은 색 정보 보호를 위해 원본을 보존합니다.\n"
        f"• {SmartAction.PASS_EXCEPTION.reason_label}: 예외 규칙(키워드 또는 조건식)에 해당해 재인코딩 대상에서 제외되었습니다."
    )
