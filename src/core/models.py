"""비디오 메타데이터, 평가 결과 및 리포트 도메인 모델 모듈"""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from src.core.quality_target import BASELINE_CQ_DEFAULT
from src.core.resolution_tiers import MAXRATE_HEADROOM_WARN_RATIO, ResolutionTier
from src.core.smart_action import (
    DEFAULT_PROTECT_CQ,
    ActionBadgeTone,
    DownscaleReason,
    SmartAction,
    render_cq_template,
)

BYTES_PER_KB: int = 1024
BYTES_PER_MB: int = BYTES_PER_KB * BYTES_PER_KB
MB_PER_GB: float = float(BYTES_PER_KB)
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = SECONDS_PER_MINUTE * 60
ASPECT_RATIO_DECIMALS: int = 3
PERCENT_MULTIPLIER: float = 100.0


class QualityZone(Enum):
    """해상도 및 코덱별 최적 비트레이트(B_opt) 대비 현재 비트레이트 비율(r)에 따른 화질 등급"""
    LOW = "저화질"    # r < low_bitrate_ratio
    OPTIMAL = "적정" # low_bitrate_ratio <= r <= over_bitrate_ratio
    OVER = "과화질"   # r > over_bitrate_ratio


QUALITY_ZONE_SORT_RANK: dict[QualityZone, int] = {
    QualityZone.LOW: 0,
    QualityZone.OPTIMAL: 1,
    QualityZone.OVER: 2,
}


class DownscaleSkipReason(Enum):
    """축소 후보가 있었지만 축소하지 않은 이유, 툴팁 표기에 쓰임"""

    NEAR_SAME_SIZE = "거의 같은 크기"
    LOW_QUALITY_AFTER_DOWNSCALE = "축소 후에도 화질 보호 대상"


@dataclass(frozen=True, slots=True)
class ActiveFrame:
    """판정에 쓰는 활성 화면 크기(검은 띠를 뺀 영역), 기본은 전체 프레임"""

    width: int
    height: int


@dataclass(slots=True)
class SavingsSummary:
    """과화질 구간의 절약, 분당 절약 선별 결과 및 제외 분량 요약 객체"""

    min_saved_mb: float
    min_mb_per_min: float
    exempt_saved_mb: float
    encode_duration_sec: float
    low_saved_count: int
    low_per_min_count: int
    excluded_over_duration_sec: float
    excluded_over_potential_saved_mb: float

    @property
    def excluded_over_count(self) -> int:
        """절약, 분당 절약 기준 미달로 제외된 과화질 영상 총 개수"""
        return self.low_saved_count + self.low_per_min_count

    def to_dict(self) -> dict[str, Any]:
        """절약, 분당 절약 선별 요약 결과를 직렬화 가능한 딕셔너리로 변환"""
        return asdict(self)


@dataclass(slots=True)
class VideoProps:
    """단일 비디오 파일의 메타데이터와 분석 속성을 보관하는 데이터클래스

    회전 메타데이터를 반영해 정규화된 너비, 높이 해상도 유지
    """
    filename: str
    width: int
    height: int
    rotate_type: int
    fps: float
    vid_kbps: int
    aud_kbps: int
    vid_size_MB: float
    duration: float
    codec: str
    keyframe_interval: float | None = None
    rotate_source: str | None = None  # ffprobe 원시 회전 메타데이터 출처, 값 (예: Display Matrix -90°)
    is_hdr: bool = False  # PQ/HLG 전달 함수 또는 Dolby Vision 구성 레코드 존재 여부
    is_10bit: bool = False  # 10-bit 이상 픽셀 포맷 또는 bits_per_raw_sample >= 10 여부
    has_encode_tag: bool = False  # 처리 표식 태그(재인코딩 기록) 존재 여부, 재처리 방지에 사용
    encode_keywords: str = ""  # 세미콜론 구분 키워드 목록(탐색기 태그 칸, MKV KEYWORDS)
    fingerprint_lookup_size: int = 0  # 재인코딩 이력 조회 키 크기(MP4 계열은 mdat 크기, 그 밖은 파일 크기)
    stream_summary: str = ""  # 저장된 ffprobe 스트림 요약 JSON(내부 요소 집계용)

    @property
    def aspect_ratio(self) -> float:
        """너비를 높이로 나눈 화면 비율 수치 (예: 16:9 -> 1.778)"""
        return round(self.width / max(self.height, 1), ASPECT_RATIO_DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        """비디오 메타데이터를 딕셔너리로 변환"""
        return asdict(self)


@dataclass(slots=True)
class VideoEvaluation:
    """단일 비디오에 대한 목표 비트레이트, 화질 등급, 인코딩 후 예상 크기와 권장 처리를 포함하는 평가 결과"""
    video: VideoProps
    optimal_kbps: int
    bitrate_ratio: float
    quality_zone: QualityZone
    is_keyframe_exceeded: bool
    expected_after_mb: float
    expected_delta_mb: float  # 양수(+)는 용량 절약(MB), 음수(-)는 키프레임 재인코딩에 따른 소폭 증가(MB)를 나타냄
    smart_action: SmartAction
    hypothetical_after_mb: float | None = None
    hypothetical_delta_mb: float | None = None
    estimated_encode_sec: float = 0.0
    target_optimal_kbps: int = 0
    target_bitrate_ratio: float = 0.0  # 출력 코덱 기준 비트레이트 비율 (r_out = B_curr / B_opt_out)
    use_low_quality_preset: bool = False  # 저화질 보호 프리셋(저화질/노이즈/역전/정적 화면) 라우팅 여부
    # - 실제 적용 품질값(기준 또는 저화질 보호 품질값), 권장 처리 명칭과 옵션의 {cq} 자리에 표기. None이면 기본 설정 값으로 유도
    output_cq: float | None = None
    # - 출력 해상도 필드는 축소 후보가 있을 때만 채워지며, 원본 해상도 재인코딩 영상은 None
    target_width: int | None = None
    target_height: int | None = None
    target_tier: ResolutionTier | None = None
    downscale_reason: DownscaleReason | None = None
    downscale_skip_reason: DownscaleSkipReason | None = None
    # - 영상별 직접 지정 값(규격 키 또는 축소 안 함), 자동 판정이면 None. 판정이 아닌 표시(체크 표시, 툴팁)에만 사용
    manual_target: str | None = None

    @property
    def is_encode_target(self) -> bool:
        """재인코딩 판정으로 인코딩 대상인지 여부 반환"""
        return self.smart_action.is_encode

    @property
    def applied_cq(self) -> float:
        """적용 품질값 반환, 명시 값이 없으면 기본 설정 기준으로 저화질 보호 라우팅 여부에 따라 유도"""
        if self.output_cq is not None:
            return self.output_cq
        if self.use_low_quality_preset:
            return DEFAULT_PROTECT_CQ
        return BASELINE_CQ_DEFAULT

    @property
    def action_label(self) -> str:
        """권장 처리 열 표시용 처리 명칭 반환(저화질 보호 라우팅 시 저화질 보호 명칭, 품질값은 적용 값으로 표기)"""
        if self.use_low_quality_preset and self.is_encode_target:
            return render_cq_template(SmartAction.ENCODE_LOW_KF.action_template, self.applied_cq)
        return render_cq_template(self.smart_action.action_template, self.applied_cq)

    @property
    def reason_label(self) -> str:
        """사유 열 표시용 판정 사유 반환(축소 재인코딩은 축소 사유 표기를 따름)"""
        if self.smart_action == SmartAction.ENCODE_DOWNSCALE and self.downscale_reason is not None:
            return self.downscale_reason.value
        return self.smart_action.reason_label

    @property
    def is_downscaled(self) -> bool:
        """축소 재인코딩으로 판정되어 출력 해상도가 실제 적용되는지 여부"""
        return self.smart_action == SmartAction.ENCODE_DOWNSCALE

    @property
    def maxrate_headroom(self) -> float | None:
        """최대 비트레이트를 출력 해상도, 원본 FPS, 목표 코덱 기준 최적값으로 나눈 여유 배수 반환"""
        if self.target_tier is None or self.target_optimal_kbps <= 0:
            return None
        return self.target_tier.max_bitrate_kbps / self.target_optimal_kbps

    @property
    def is_maxrate_near(self) -> bool:
        """여유 배수가 경고 기준 미만이라 최대 비트레이트에 걸릴 가능성이 있는지 여부"""
        headroom = self.maxrate_headroom
        return headroom is not None and headroom < MAXRATE_HEADROOM_WARN_RATIO

    @property
    def action_badge_tone(self) -> ActionBadgeTone:
        """권장 처리 UI 배지 색상 범주 반환"""
        if self.use_low_quality_preset and self.is_encode_target:
            return ActionBadgeTone.LOW_KF_ENCODE
        return self.smart_action.badge_tone

    @property
    def option_label(self) -> str:
        """적용 인코딩 옵션 표시 문구 반환(품질값은 적용 값으로 표기)"""
        if self.use_low_quality_preset and self.is_encode_target:
            return render_cq_template(SmartAction.ENCODE_LOW_KF.option_template, self.applied_cq)
        return render_cq_template(self.smart_action.option_template, self.applied_cq)

    @property
    def preview_after_mb(self) -> float:
        """실제 또는 가상 인코딩 시 예상 파일 크기(MB) 반환"""
        if self.is_encode_target or self.hypothetical_after_mb is None:
            return self.expected_after_mb
        return self.hypothetical_after_mb

    @property
    def preview_delta_mb(self) -> float:
        """실제 또는 가상 인코딩 시 예상 용량 변화량(MB) 반환"""
        if self.is_encode_target or self.hypothetical_delta_mb is None:
            return self.expected_delta_mb
        return self.hypothetical_delta_mb

    @property
    def preview_mb_per_min(self) -> float:
        """실제 또는 재인코딩을 가정한 재생 시간 1분당 예상 절약(MB/분) 반환"""
        if self.video.duration <= 0:
            return 0.0
        return self.preview_delta_mb / (self.video.duration / SECONDS_PER_MINUTE)

    def to_dict(self) -> dict[str, Any]:
        """개별 비디오 평가 결과를 직렬화 가능한 딕셔너리로 변환"""
        return {
            "video": self.video.to_dict(),
            "optimal_kbps": self.optimal_kbps,
            "bitrate_ratio": self.bitrate_ratio,
            "target_optimal_kbps": self.target_optimal_kbps,
            "target_bitrate_ratio": self.target_bitrate_ratio,
            "quality_zone": self.quality_zone.value,
            "is_keyframe_exceeded": self.is_keyframe_exceeded,
            "expected_after_mb": self.expected_after_mb,
            "expected_delta_mb": self.expected_delta_mb,
            "smart_action": self.smart_action.value,
            "estimated_encode_sec": self.estimated_encode_sec,
        }


@dataclass(slots=True)
class SummaryReport:
    """전체 작업 폴더에 대한 통계와 인코딩 전후 용량 변화를 집계한 요약 리포트"""
    root_dir: str
    total_count: int
    encoded_count: int
    passed_count: int
    total_orig_mb: float
    total_expected_after_mb: float
    gross_saved_mb: float            # 과화질 영상을 화질 기준값으로 감축하여 줄어드는 총 용량 (MB)
    keyframe_increase_mb: float      # 적정/저화질 영상의 키프레임을 1초로 맞추면서 발생하는 예상 증가 용량 (MB)
    net_saved_mb: float              # 순 절약 용량 (gross_saved_mb - keyframe_increase_mb)
    savings_summary: SavingsSummary
    evaluations: list[VideoEvaluation]
    total_estimated_encode_sec: float = 0.0

    @property
    def net_saved_pct(self) -> float:
        """현재 총 용량 대비 순 절약률(%)"""
        if self.total_orig_mb <= 0:
            return 0.0
        return (self.net_saved_mb / self.total_orig_mb) * PERCENT_MULTIPLIER

    @property
    def encode_avg_mb_per_min(self) -> float:
        """재인코딩 대상 영상 재생 시간 1분당 평균 순 절약(MB/분)"""
        mins = self.savings_summary.encode_duration_sec / SECONDS_PER_MINUTE
        if mins <= 0:
            return 0.0
        return self.net_saved_mb / mins

    @property
    def unmeasured_keyframe_count(self) -> int:
        """키프레임 간격이 아직 실측되지 않은 영상 개수"""
        return sum(1 for ev in self.evaluations if ev.video.keyframe_interval is None)

    @property
    def hdr_preserved_count(self) -> int:
        """HDR 판정으로 색 정보 보호를 위해 원본을 보존하는 영상 개수"""
        return sum(1 for ev in self.evaluations if ev.smart_action == SmartAction.PASS_HDR)

    def to_dict(self) -> dict[str, Any]:
        """전체 요약 리포트 결과를 직렬화 가능한 딕셔너리로 변환"""
        return {
            "root_dir": self.root_dir,
            "total_count": self.total_count,
            "encoded_count": self.encoded_count,
            "passed_count": self.passed_count,
            "total_orig_mb": self.total_orig_mb,
            "total_expected_after_mb": self.total_expected_after_mb,
            "gross_saved_mb": self.gross_saved_mb,
            "keyframe_increase_mb": self.keyframe_increase_mb,
            "net_saved_mb": self.net_saved_mb,
            "savings_summary": self.savings_summary.to_dict(),
            "total_estimated_encode_sec": self.total_estimated_encode_sec,
            "evaluations": [e.to_dict() for e in self.evaluations],
        }
