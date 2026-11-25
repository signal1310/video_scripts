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


def is_valid_manual_target(value: str) -> bool:
    """영상별 직접 지정 값이 축소 안 함 또는 규격 키인지 판별"""
    return value == KEEP_RESOLUTION_KEY or value in RESOLUTION_TIER_BY_KEY


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


def build_scale_expression(tier: ResolutionTier) -> str:
    """규격의 FFmpeg scale 필터 식 생성

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
    return (
        f"scale='trunc(iw*{fit}/{OUTPUT_DIM_ALIGN}+{epsilon})*{OUTPUT_DIM_ALIGN}'"
        f":'trunc(ih*{fit}/{OUTPUT_DIM_ALIGN}+{epsilon})*{OUTPUT_DIM_ALIGN}'"
    )
