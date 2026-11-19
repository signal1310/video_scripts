"""판정 어휘 모듈: 권장 처리 종류, 판정 사유, 배지 범주와 품질값 표기 템플릿

- 도메인 모델(models)이 이 모듈을 import하므로 여기서는 models를 import하지 않음
"""

from dataclasses import dataclass
from enum import Enum

from src.core.quality_target import BASELINE_CQ_DEFAULT, format_cq_value, protect_cq_value

CQ_PLACEHOLDER: str = "{cq}"

class ActionBadgeTone(Enum):
    """판정 결과별 UI 배지 시각 스타일 범주"""

    STD_ENCODE = "std_encode"
    LOW_KF_ENCODE = "low_kf_encode"
    PASS = "pass"


class DownscaleReason(Enum):
    """축소 재인코딩의 판정 사유, 값은 사유 표기로 그대로 쓰임"""

    MANUAL = "직접 지정"
    MAX_RESOLUTION = "최대 해상도 초과"
    BITRATE_FIT = "비트레이트 맞춤"


@dataclass(frozen=True, slots=True)
class _SmartActionMeta:
    """판정 배지 범주, 권장 처리 명칭, 판정 사유, 옵션, 상세 설명 메타데이터"""

    badge_tone: ActionBadgeTone
    action_label: str
    reason_label: str
    option_label: str
    reason_description: str


class SmartAction(Enum):
    """퀄리티 기반 통합 인코딩 전략에서 각 비디오 파일에 부여되는 최종 분류 판정"""

    ENCODE_OVER = "encode_over"
    ENCODE_OPT_KF = "encode_opt_kf"
    ENCODE_LOW_KF = "encode_low_kf"
    ENCODE_CODEC_UPGRADE = "encode_codec_upgrade"
    ENCODE_COMPAT = "encode_compat"
    ENCODE_DOWNSCALE = "encode_downscale"
    PASS_OPTIMAL = "pass_optimal"
    PASS_LOW = "pass_low"
    PASS_LOW_SAVED = "pass_low_saved"
    PASS_LOW_PER_MIN = "pass_low_per_min"
    PASS_CODEC_SAVED = "pass_codec_saved"
    PASS_EXCEPTION = "pass_exception"
    PASS_HDR = "pass_hdr"
    PASS_ALREADY_ENCODED = "pass_already_encoded"
    LEGACY_FORMAT = "legacy_format"

    @property
    def is_encode(self) -> bool:
        """재인코딩 판정(ENCODE_*) 여부 반환"""
        return self in _ENCODE_SMART_ACTIONS

    @property
    def badge_tone(self) -> ActionBadgeTone:
        """UI 배지 색상, 강조 렌더링에 사용할 스타일 범주 반환"""
        return _SMART_ACTION_META[self].badge_tone

    @property
    def action_template(self) -> str:
        """권장 처리 명칭 원문 반환, 품질값 자리는 {cq} 자리표시자로 남음(render_cq_template로 채움)"""
        return _SMART_ACTION_META[self].action_label

    @property
    def action_label(self) -> str:
        """기본 설정의 품질값으로 채운 권장 처리 명칭 반환"""
        return render_cq_template(self.action_template, self._default_cq())

    @property
    def reason_label(self) -> str:
        """사유 열 표시용 원자 판정 사유 반환"""
        return _SMART_ACTION_META[self].reason_label

    @property
    def option_template(self) -> str:
        """적용 인코딩 옵션 원문 반환, 품질값 자리는 {cq} 자리표시자로 남음(render_cq_template로 채움)"""
        return _SMART_ACTION_META[self].option_label

    @property
    def option_label(self) -> str:
        """기본 설정의 품질값으로 채운 적용 인코딩 옵션 반환"""
        return render_cq_template(self.option_template, self._default_cq())

    def _default_cq(self) -> float:
        """기본 설정에서 이 판정이 적용하는 품질값(저화질 보호 판정은 보호 품질값)"""
        if self is SmartAction.ENCODE_LOW_KF:
            return DEFAULT_PROTECT_CQ
        return BASELINE_CQ_DEFAULT

    @property
    def reason_description(self) -> str:
        """사용자 안내용 판정 사유 상세 설명 반환"""
        return _SMART_ACTION_META[self].reason_description


_SMART_ACTION_META: dict[SmartAction, _SmartActionMeta] = {
    SmartAction.ENCODE_OVER: _SmartActionMeta(
        ActionBadgeTone.STD_ENCODE,
        "CQ{cq} 재인코딩",
        "과화질",
        "CQ{cq} + 1초 키프레임",
        "비트레이트가 과화질 상한을 넘어 화질 기준값의 표준 화질로 재인코딩해 용량을 줄입니다.",
    ),
    SmartAction.ENCODE_OPT_KF: _SmartActionMeta(
        ActionBadgeTone.STD_ENCODE,
        "CQ{cq} 재인코딩",
        "KF 초과",
        "CQ{cq} + 1초 키프레임, 용량 증가 수용",
        "화질 등급은 적정하지만 키프레임 간격이 허용 기준을 넘어, 구간 이동 성능을 높이기 위해 용량 증가를 감수하고 재인코딩합니다.",
    ),
    SmartAction.ENCODE_LOW_KF: _SmartActionMeta(
        ActionBadgeTone.LOW_KF_ENCODE,
        "CQ{cq} 재인코딩",
        "KF 초과",
        "CQ{cq} + 1초 키프레임",
        "화질 등급이 저화질인 상태에서 키프레임 간격이 허용 기준을 넘어, 저화질 프리셋으로 화질 기준을 낮춰 압축 노이즈 재현에 비트를 덜 쓰고 1초 키프레임으로 재인코딩합니다.",
    ),
    SmartAction.ENCODE_CODEC_UPGRADE: _SmartActionMeta(
        ActionBadgeTone.STD_ENCODE,
        "CQ{cq} 재인코딩",
        "코덱 전환",
        "CQ{cq} + 1초 키프레임",
        "압축 성능이 낮은 구형 또는 인트라 전용 코덱을 목표 코덱으로 전환해 화질을 유지하면서 용량을 줄입니다.",
    ),
    SmartAction.ENCODE_COMPAT: _SmartActionMeta(
        ActionBadgeTone.STD_ENCODE,
        "CQ{cq} 재인코딩",
        "코덱 호환",
        "CQ{cq} + 1초 키프레임",
        "하드웨어 디코딩 호환성이 낮은 구형 코덱을 표준 코덱과 1초 키프레임으로 재인코딩합니다.",
    ),
    SmartAction.ENCODE_DOWNSCALE: _SmartActionMeta(
        ActionBadgeTone.STD_ENCODE,
        "축소 재인코딩",
        "최대 해상도 초과",
        "CQ{cq} + 최대 BR + 1초 키프레임, 축소",
        "원본 규격이 최대 해상도 설정을 넘어 출력 해상도로 줄이고, 최대 비트레이트를 적용해 화질 기준값으로 재인코딩합니다.",
    ),
    SmartAction.PASS_OPTIMAL: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "적정",
        "재인코딩 불필요",
        "화질 등급이 적정 범위이고 키프레임 간격도 정상 기준을 충족해 재인코딩이 필요하지 않습니다.",
    ),
    SmartAction.PASS_LOW: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "화질 보호",
        "재인코딩 불필요",
        "이미 화질 등급이 저화질이고 키프레임 간격도 정상이므로, 추가 재인코딩에 따른 화질 손실을 막기 위해 원본을 보존합니다.",
    ),
    SmartAction.PASS_LOW_SAVED: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "절약 미달",
        "재인코딩 불필요",
        "예상 절약이 최소 기준 미만이라 재인코딩 효과가 낮아 원본을 보존합니다.",
    ),
    SmartAction.PASS_CODEC_SAVED: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "전환 절약 미달",
        "재인코딩 불필요",
        "코덱 전환 대상이지만 예상 절약 또는 분당 절약이 기준 미만이라 전환 효과가 낮아 원본을 보존합니다.",
    ),
    SmartAction.PASS_LOW_PER_MIN: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "분당 미달",
        "재인코딩 불필요",
        "재생 시간 1분당 예상 절약(MB/분)이 기준 미만이라 원본을 보존합니다.",
    ),
    SmartAction.LEGACY_FORMAT: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "재분석 필요",
        "분석 형식이 바뀌어 다시 분석해야 합니다",
        "재분석 필요 제외",
        "분석 형식이 바뀌어 저장된 결과만 표시 중입니다. 다시 분석하기 전까지 재인코딩 대상에서 제외되었습니다.",
    ),
    SmartAction.PASS_EXCEPTION: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "예외 규칙",
        "예외 규칙 제외",
        "상단 예외 규칙(키워드 또는 조건식)에 해당하여 재인코딩 대상에서 제외되었습니다.",
    ),
    SmartAction.PASS_HDR: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "HDR 보존",
        "재인코딩 불필요",
        "HDR(PQ, HLG, Dolby Vision) 영상은 현재 인코딩 설정으로 색 정보를 잃을 수 있어 원본을 보존합니다.",
    ),
    SmartAction.PASS_ALREADY_ENCODED: _SmartActionMeta(
        ActionBadgeTone.PASS,
        "보존",
        "이미 재인코딩",
        "재인코딩 불필요",
        "이미 재인코딩된 영상이므로 원본을 보존합니다. 출력 해상도를 직접 지정하면 다시 판정합니다.",
    ),
}

def render_cq_template(template: str, quality: float) -> str:
    """{cq} 자리표시자를 소수 첫째 자리 품질값 표기로 치환"""
    return template.replace(CQ_PLACEHOLDER, format_cq_value(quality))


_ENCODE_SMART_ACTIONS: frozenset[SmartAction] = frozenset(
    action for action in SmartAction if action.value.startswith("encode_")
)

DEFAULT_PROTECT_CQ: float = protect_cq_value(BASELINE_CQ_DEFAULT)
# - 열 필터 목록은 정렬 순서용이며 실제 값은 데이터에서 추가되므로 기본 설정 표기로 채움
SMART_ACTION_LABELS: tuple[str, ...] = tuple(
    dict.fromkeys(action.action_label for action in _SMART_ACTION_META)
)
SMART_ACTION_REASONS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *(meta.reason_label for meta in _SMART_ACTION_META.values()),
            *(reason.value for reason in DownscaleReason),
        )
    )
)
