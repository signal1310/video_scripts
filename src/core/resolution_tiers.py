"""해상도 규격 기준 정의와 축소 출력 크기, 축소 판정 계산 모듈"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

OUTPUT_DIM_ALIGN: int = 4
MIN_DOWNSCALE_AREA_RATIO: Fraction = Fraction(95, 100)
BUFSIZE_MULTIPLIER: int = 2
FFMPEG_TRUNC_EPSILON: float = 0.000001
# - 여유 배수가 이 값 미만이면 복잡한 장면이 최대 비트레이트에 걸려 화질이 낮아질 수 있음
MAXRATE_HEADROOM_WARN_RATIO: float = 1.5


class Orientation(Enum):
    """영상 방향 구분, 값은 필터 구간 라벨과 규격 방향 표기에 그대로 쓰임"""

    LANDSCAPE = "가로형"
    PORTRAIT = "세로형"


@dataclass(frozen=True, slots=True)
class ResolutionTier:
    """단변과 16:9 장변 경계 박스, 최대 비트레이트로 정의된 해상도 규격

    - key는 설정과 저장 값에 쓰이는 고정 계약값이므로 표시 문구와 분리
    """

    key: str
    display: str
    short_side: int
    long_side: int
    max_bitrate_kbps: int

    @property
    def bufsize_kbps(self) -> int:
        """VBV 버퍼 크기(kbps) 반환"""
        return self.max_bitrate_kbps * BUFSIZE_MULTIPLIER

    def box(self, orientation: Orientation) -> tuple[int, int]:
        """영상 방향에 맞춘 경계 박스 (너비, 높이) 반환"""
        if orientation is Orientation.LANDSCAPE:
            return self.long_side, self.short_side
        return self.short_side, self.long_side


# - 최대 비트레이트는 기본 설정 HEVC 60fps 최적값 x 1.5를 100k 단위로 올림한 프리셋 고정값
# - 평범한 장면은 상한에 닿지 않고, 움직임이 많은 장면만 상한에 걸리도록 하는 여유
RESOLUTION_TIERS: tuple[ResolutionTier, ...] = (
    ResolutionTier("360p", "360p", 360, 640, 900),
    ResolutionTier("480p", "480p", 480, 854, 1500),
    ResolutionTier("540p", "540p", 540, 960, 1800),
    ResolutionTier("720p", "720p", 720, 1280, 2800),
    ResolutionTier("900p", "900p", 900, 1600, 4000),
    ResolutionTier("1080p", "1080p", 1080, 1920, 5400),
    ResolutionTier("1440p", "1440p", 1440, 2560, 8600),
    ResolutionTier("2160p", "4K", 2160, 3840, 16600),
)
RESOLUTION_TIER_BY_KEY: dict[str, ResolutionTier] = {tier.key: tier for tier in RESOLUTION_TIERS}
# - 영상별 직접 지정 값으로서 축소하지 않음을 뜻하는 저장 계약값(규격 키와 겹치지 않음)
KEEP_RESOLUTION_KEY: str = "keep"


# - 영상별 직접 지정 값으로서 인코딩을 강제하는 저장 계약값(규격 키와 겹치지 않음)
ENCODE_DESIGNATION_KEY: str = "encode"


def is_valid_manual_target(value: str) -> bool:
    """영상별 직접 지정 값이 인코딩 지정, 축소 안 함 또는 규격 키인지 판별"""
    return value in (KEEP_RESOLUTION_KEY, ENCODE_DESIGNATION_KEY) or value in RESOLUTION_TIER_BY_KEY


def copy_manual_targets(
    store: Mapping[str, Mapping[str, str]], *, drop_empty: bool = False
) -> dict[str, dict[str, str]]:
    """작업 폴더별 직접 지정 맵(폴더 -> 파일명 -> 값)의 독립 복사본, drop_empty이면 항목이 없는 폴더를 제외"""
    return {root: dict(entries) for root, entries in store.items() if entries or not drop_empty}


@dataclass(frozen=True, slots=True)
class TierBoundary:
    """규격 경계 값 하나와 그 값이 장변인지 단변인지 구분"""

    value: int
    tier: ResolutionTier
    is_long_side: bool


# - 너비, 높이 필터 열이 공유하는 경계 집합. 단변과 장변 값은 서로 겹치지 않음
TIER_BOUNDARIES: tuple[TierBoundary, ...] = tuple(
    sorted(
        (
            *(TierBoundary(tier.short_side, tier, False) for tier in RESOLUTION_TIERS),
            *(TierBoundary(tier.long_side, tier, True) for tier in RESOLUTION_TIERS),
        ),
        key=lambda boundary: boundary.value,
    )
)


def orientation_of(width: int, height: int) -> Orientation:
    """너비가 높이 이상이면 가로형, 아니면 세로형 반환"""
    return Orientation.LANDSCAPE if width >= height else Orientation.PORTRAIT


def calc_scaled_dimensions(width: int, height: int, tier: ResolutionTier) -> tuple[int, int]:
    """원본 비율을 유지해 규격 박스에 내접하는 출력 크기 반환

    - 확대하지 않음(배율 상한 1)
    - 4의 배수 내림은 Fraction으로 계산해 부동소수 오차 없이 적용
    """
    box_w, box_h = tier.box(orientation_of(width, height))
    scale = min(Fraction(1), Fraction(box_w, width), Fraction(box_h, height))
    return (
        (Fraction(width) * scale // OUTPUT_DIM_ALIGN) * OUTPUT_DIM_ALIGN,
        (Fraction(height) * scale // OUTPUT_DIM_ALIGN) * OUTPUT_DIM_ALIGN,
    )


def is_meaningful_downscale(width: int, height: int, tier: ResolutionTier) -> bool:
    """원본이 규격 박스를 넘고 출력 화소 수가 원본의 MIN_DOWNSCALE_AREA_RATIO 이하일 때만 축소로 판정

    - 박스 안 원본은 4의 배수 내림만 생기므로 축소가 아님. 작은 원본이 내림 손실만으로 축소 판정되는 것을 막음
    - 박스를 조금 넘는 원본의 미세 차이도 축소로 보지 않아 불필요한 인코딩을 막음
    """
    box_w, box_h = tier.box(orientation_of(width, height))
    if width <= box_w and height <= box_h:
        return False
    out_w, out_h = calc_scaled_dimensions(width, height, tier)
    return Fraction(out_w * out_h) <= MIN_DOWNSCALE_AREA_RATIO * width * height


def find_source_tier(width: int, height: int) -> ResolutionTier | None:
    """축소 없이 원본을 담는 가장 작은 규격 반환, 최상위 규격 박스를 의미 있게 넘으면 None

    - 축소 판정과 같은 허용 오차를 적용하므로 `864x480`은 480p로 판정
    """
    for tier in RESOLUTION_TIERS:
        if not is_meaningful_downscale(width, height, tier):
            return tier
    return None


@dataclass(frozen=True, slots=True)
class ScaleAlgorithm:
    """축소 보간 방식 하나의 FFmpeg `scale=flags=` 값, 표시 이름, 특성 설명

    - value는 FFmpeg `flags=` 인자와 설정 저장 값에 그대로 쓰이는 고정 계약값
    """

    value: str
    label: str
    tooltip: str


# - swscale이 `scale=flags=`에서 받는 보간 방식 전체. 기본 bicubic은 기존 인코딩 결과와 같음
SCALE_ALGORITHMS: tuple[ScaleAlgorithm, ...] = (
    ScaleAlgorithm(
        "fast_bilinear",
        "빠른 쌍선형",
        "가장 빠르지만 계단 현상이 두드러집니다. 저해상도 미리 보기용입니다.",
    ),
    ScaleAlgorithm("bilinear", "쌍선형", "부드럽지만 세부가 흐려집니다. 매우 빠릅니다."),
    ScaleAlgorithm(
        "bicubic", "쌍삼차 (기본)", "속도와 선명도의 균형이 좋아 FFmpeg 기본값으로 쓰입니다."
    ),
    ScaleAlgorithm(
        "experimental", "실험적", "FFmpeg가 실험적으로 제공하는 방식입니다. 일반 용도로는 권장하지 않습니다."
    ),
    ScaleAlgorithm("neighbor", "최근접", "가장 빠르지만 픽셀이 각지고 계단 현상이 심합니다."),
    ScaleAlgorithm("area", "영역 평균", "축소에 강해 계단 현상이 적고 텍스트가 또렷합니다."),
    ScaleAlgorithm(
        "bicublin", "쌍삼차 (축소 전용)", "축소에는 쌍삼차, 확대에는 쌍선형을 적용하는 조합입니다."
    ),
    ScaleAlgorithm("gauss", "가우시안", "흐림이 강해 부드럽지만 세부가 뭉개집니다."),
    ScaleAlgorithm("sinc", "싱크", "링잉이 적은 고품질 방식이지만 처리 속도가 느립니다."),
    ScaleAlgorithm(
        "lanczos", "란초스", "가장 선명하지만 링잉과 파일 크기 증가가 조금 있습니다."
    ),
    ScaleAlgorithm("spline", "스플라인", "부드럽고 자연스러운 결과를 내지만 처리 속도가 느립니다."),
)
SCALE_ALGORITHM_VALUES: tuple[str, ...] = tuple(algorithm.value for algorithm in SCALE_ALGORITHMS)
SCALE_ALGORITHM_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (algorithm.label, algorithm.value) for algorithm in SCALE_ALGORITHMS
)
DEFAULT_SCALE_ALGORITHM: str = "bicubic"
SCALE_ALGORITHM_BY_VALUE: dict[str, ScaleAlgorithm] = {
    algorithm.value: algorithm for algorithm in SCALE_ALGORITHMS
}


def normalize_scale_algorithm(value: str) -> str:
    """지원하는 보간 방식 값이면 그대로, 모르는 값이면 기본 방식 반환"""
    return value if value in SCALE_ALGORITHM_BY_VALUE else DEFAULT_SCALE_ALGORITHM


def scale_filter(width_expr: str, height_expr: str, algorithm: str) -> str:
    """지정 크기 식과 보간 방식으로 FFmpeg scale 필터 문자열 생성

    - 인코딩과 화질 분석이 같은 보간 방식을 쓰도록 문자열 조립을 한 곳에 둠
    """
    return f"scale={width_expr}:{height_expr}:flags={normalize_scale_algorithm(algorithm)}"


def build_scale_expression(tier: ResolutionTier, algorithm: str = DEFAULT_SCALE_ALGORITHM) -> str:
    """규격과 보간 방식의 FFmpeg scale 필터 식 생성

    - `calc_scaled_dimensions`와 같은 규칙을 FFmpeg 식으로 옮긴 것
    - 가로형, 세로형 박스 선택은 `gte(iw,ih)`로 판별
    - trunc 전에 0.000001을 더해 부동소수 오차로 4의 배수가 한 단계 내려가지 않게 함
    """
    long_side, short_side = tier.long_side, tier.short_side
    fit = (
        f"min(1,min(if(gte(iw,ih),{long_side},{short_side})/iw,"
        f"if(gte(iw,ih),{short_side},{long_side})/ih))"
    )
    epsilon = f"{FFMPEG_TRUNC_EPSILON:.6f}"
    return scale_filter(
        f"'trunc(iw*{fit}/{OUTPUT_DIM_ALIGN}+{epsilon})*{OUTPUT_DIM_ALIGN}'",
        f"'trunc(ih*{fit}/{OUTPUT_DIM_ALIGN}+{epsilon})*{OUTPUT_DIM_ALIGN}'",
        algorithm,
    )
