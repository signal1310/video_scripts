"""비디오 메타데이터, 평가 결과 및 리포트 도메인 모델 모듈"""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

BYTES_PER_KB: int = 1024
BYTES_PER_MB: int = BYTES_PER_KB * BYTES_PER_KB
MB_PER_GB: float = float(BYTES_PER_KB)
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = SECONDS_PER_MINUTE * 60
ASPECT_RATIO_DECIMALS: int = 3
PERCENT_MULTIPLIER: float = 100.0


class QualityZone(Enum):
    """해상도 및 코덱별 최적 비트레이트(B_opt) 대비 현재 비트레이트 비율(r)에 따른 화질 등급"""
    LOW = "저화질"       # r < low_bitrate_ratio
    OPTIMAL = "적정화질" # low_bitrate_ratio <= r <= over_bitrate_ratio
    OVER = "과화질"      # r > over_bitrate_ratio


class SmartAction(Enum):
    """퀄리티 기반 통합 인코딩 전략에서 각 비디오 파일에 부여되는 최종 처리 처방"""
    ENCODE_CQ22_OVER = "과화질 감축 (CQ22 + 1초 키프레임)"
    ENCODE_CQ22_OPT_KF = "적정화질 키프레임 교정 (CQ22 + 1초 키프레임, 용량 증가 수용)"
    ENCODE_LOW_KF = "저화질 키프레임 교정 (노이즈 증폭 방지 + 1초 키프레임)"
    PASS_OPTIMAL = "원본 보존 (적정화질 · 정상 키프레임)"
    PASS_LOW = "원본 보존 (저화질 · 정상 키프레임)"
    PASS_SMALL_FOOTAGE = "원본 보존 (소형 영상 절감 미미)"
    PASS_EXCEPTION = "원본 보존 (예외 규칙 제외)"


class FootageStrategy(Enum):
    """작업 폴더 내 소형 영상 분포 특성에 따라 자동으로 선택되는 최적화 모드"""
    ACCUMULATE = "ACCUMULATE (누적 최적화 모드)"
    IGNORE_SMALL = "IGNORE_SMALL (대용량 선별 집중 모드)"

    @property
    def short_label(self) -> str:
        """대시보드 등 좁은 UI 영역에서 사용하는 간결한 전략 표기"""
        if self is FootageStrategy.ACCUMULATE:
            return "누적 최적화 (ACCUMULATE)"
        return "대용량 집중 (IGNORE_SMALL)"


@dataclass(slots=True)
class SmallFootageDistribution:
    """작업 폴더 전체에서 소형 영상이 차지하는 비중과 총 절감 기여도를 분석한 결과 객체"""
    small_threshold_mb: float
    min_saved_mb: float
    total_over_count: int
    small_over_count: int
    total_potential_saved_mb: float
    small_potential_saved_mb: float
    small_share_ratio: float
    strategy: FootageStrategy
    reason: str

    @property
    def share_pct(self) -> float:
        """전체 절감 가능량 중 소형 영상 비중 백분율(%)"""
        return self.small_share_ratio * PERCENT_MULTIPLIER

    def to_dict(self) -> dict[str, Any]:
        """소형 영상 분포 분석 결과를 직렬화 가능한 딕셔너리로 변환"""
        data = asdict(self)
        data["strategy"] = self.strategy.value
        return data


@dataclass(slots=True)
class VideoProps:
    """단일 비디오 파일의 메타데이터와 분석 속성을 보관하는 데이터클래스

    회전 메타데이터를 반영해 정규화된 가로·세로 해상도 유지
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
    moved_dirname: str | None = None

    @property
    def aspect_ratio(self) -> float:
        """가로 해상도를 세로 해상도로 나눈 화면 비율 수치 (예: 16:9 -> 1.778)"""
        return round(self.width / max(self.height, 1), ASPECT_RATIO_DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        """비디오 메타데이터를 딕셔너리로 변환"""
        return asdict(self)


@dataclass(slots=True)
class VideoEvaluation:
    """단일 비디오에 대한 목표 비트레이트, 화질 등급, 인코딩 후 예상 크기 및 분류 폴더를 포함하는 평가 결과"""
    video: VideoProps
    optimal_kbps: int
    bitrate_ratio: float
    quality_zone: QualityZone
    is_keyframe_defective: bool
    is_small_footage: bool
    expected_after_mb: float
    expected_delta_mb: float  # 양수(+)는 용량 절감(MB), 음수(-)는 키프레임 교정에 따른 소폭 증가(MB)를 나타냄
    smart_action: SmartAction
    target_dirname: str | None

    def to_dict(self) -> dict[str, Any]:
        """개별 비디오 평가 결과를 직렬화 가능한 딕셔너리로 변환"""
        return {
            "video": self.video.to_dict(),
            "optimal_kbps": self.optimal_kbps,
            "bitrate_ratio": self.bitrate_ratio,
            "quality_zone": self.quality_zone.value,
            "is_keyframe_defective": self.is_keyframe_defective,
            "is_small_footage": self.is_small_footage,
            "expected_after_mb": self.expected_after_mb,
            "expected_delta_mb": self.expected_delta_mb,
            "smart_action": self.smart_action.value,
            "target_dirname": self.target_dirname,
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
    gross_saved_mb: float            # 과화질 영상을 CQ22로 감축하여 줄어드는 총 용량 (MB)
    keyframe_increase_mb: float      # 적정/저화질 영상의 키프레임을 1초로 맞추면서 발생하는 예상 증가 용량 (MB)
    net_saved_mb: float              # 순수하게 절약되는 순 절감 용량 (gross_saved_mb - keyframe_increase_mb)
    small_distribution: SmallFootageDistribution
    evaluations: list[VideoEvaluation]

    @property
    def net_saved_pct(self) -> float:
        """현재 총 용량 대비 최종 순수 절감률(%)"""
        if self.total_orig_mb <= 0:
            return 0.0
        return (self.net_saved_mb / self.total_orig_mb) * PERCENT_MULTIPLIER

    @property
    def unmeasured_keyframe_count(self) -> int:
        """키프레임 간격이 아직 실측되지 않은 영상 개수"""
        return sum(1 for ev in self.evaluations if ev.video.keyframe_interval is None)

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
            "small_distribution": self.small_distribution.to_dict(),
            "evaluations": [e.to_dict() for e in self.evaluations],
        }
