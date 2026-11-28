"""화질 분석의 검은 띠 감지, 왕복 지표 명령 구성, 축소 후보 판정 순수 함수 모듈

- 검은 띠 합집합과 90% 경계, 구간별 여백 차이 감지, VR 보호 판정은 결정 20, 27의 확정값을 따름
- 축소 후보는 VMAF 98.0과 SSIM 0.996을 동시에 넘는 가장 낮은 규격이며, SSIM 선행 필터에 필요한 판정만 담당
- 프로세스 실행은 `frame_analysis_runner.py`, 지표 로그 파싱과 모델 선택은 `quality_probe.py`를 재사용
"""

import json
import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from src.core.analysis.hdr_tone_map import tonemap_graph_prefix
from src.core.config import (
    DEFAULT_FRAME_ANALYSIS_MODE,
    FRAME_ANALYSIS_MODE_BALANCED,
    FRAME_ANALYSIS_MODE_FAST,
    FRAME_ANALYSIS_MODE_PRECISE,
    VALID_FRAME_ANALYSIS_MODES,
    ClassifierConfig,
)
from src.core.models import ActiveFrame
from src.core.quality_probe import METRIC_INPUT_LABELS, build_metric_filter
from src.core.quality_target import (
    ROUNDTRIP_SSIM_THRESHOLD as _ROUNDTRIP_SSIM_THRESHOLD,
)
from src.core.quality_target import (
    ROUNDTRIP_VMAF_THRESHOLD as _ROUNDTRIP_VMAF_THRESHOLD,
)
from src.core.resolution_tiers import (
    DEFAULT_SCALE_ALGORITHM,
    RESOLUTION_TIERS,
    SCALE_ALGORITHM_VALUES,
    ResolutionTier,
    calc_scaled_dimensions,
    is_meaningful_downscale,
    normalize_scale_algorithm,
    scale_filter,
)

# - 활성 화면 면적이 전체의 90%를 넘으면 검은 띠가 없는 것으로 봄(결정 20 초기값 승인)
ACTIVE_AREA_NO_BARS_RATIO: float = 0.90
# - 각 면의 검은 여백 두께가 축 길이의 2%(또는 8px) 미만이면 어두운 가장자리 노이즈로 보고 프레임 끝으로 스냅
EDGE_SNAP_RATIO: float = 0.02
EDGE_SNAP_MIN_PX: int = 8
# - 구간별 여백 차이 판정: 스냅 기준을 넘는 차이가 축 길이의 5%를 더 넘어야 하고, 차이의 양쪽에 최소 2개 지점이 있어야 참
MARGIN_VARY_EXTRA_RATIO: float = 0.05
MARGIN_VARY_MIN_POINTS: int = 2
# - 합집합 확정에서 한 면 두께를 채택하는 지점 비율(3/4). 모자라면 전체 바깥 좌표를 씀
MARGIN_AGREE_NUMERATOR: int = 3
MARGIN_AGREE_DENOMINATOR: int = 4
# - 부가 정보가 없는 2:1 VR180 영상 보호 범위(결정 27, 12.17절 5항)
VR_ASPECT_RATIO_MIN: float = 1.95
VR_ASPECT_RATIO_MAX: float = 2.05
VR_MIN_LONG_SIDE: int = 2560
# - 4K 모델과 기본 모델 공통 기준(12.17절 승인). 품질값 정밀 결정과 공유하려고 `quality_target`에 정의
ROUNDTRIP_VMAF_THRESHOLD: float = _ROUNDTRIP_VMAF_THRESHOLD
ROUNDTRIP_SSIM_THRESHOLD: float = _ROUNDTRIP_SSIM_THRESHOLD
# - 12.19절 확정: 누적 중앙값이 이 값을 넘으면 통과 확정, 첫 탈락이 이 값 미만이면 탈락 확정
SSIM_CONFIRMED_PASS_SCORE: float = 0.998
SSIM_CONFIRMED_FAIL_SCORE: float = 0.995
VMAF_CONFIRMED_PASS_SCORE: float = 99.5
VMAF_CONFIRMED_FAIL_SCORE: float = 96.5
# - 설정한 기준에서 조기 확정 경계를 상대 파생하는 오프셋. 기준을 바꾸면 확정 경계도 함께 움직임
SSIM_CONFIRMED_PASS_OFFSET: float = 0.002
SSIM_CONFIRMED_FAIL_OFFSET: float = 0.001
SSIM_SCORE_MAX: float = 0.9999
SSIM_SCORE_MIN: float = 0.0
VMAF_CONFIRMED_OFFSET: float = 1.5
VMAF_SCORE_MAX: float = 100.0
VMAF_SCORE_MIN: float = 0.0
# - 서브샘플 2는 12.19절에서 판정 변화 0건, 1.71배 단축 확인
VMAF_SUBSAMPLE: int = 2
# - 적응형 라운드 순서: 가운데 50% -> 양옆 -> 양 끝 -> 나머지(누적 1, 3, 5, 9구간)
ROUND_POINT_INDICES: tuple[tuple[int, ...], ...] = ((4,), (2, 6), (0, 8), (1, 3, 5, 7))
# - 사용자 지정 활성 화면의 최소 크기와 짝수 정렬 단위(인코더 crop 필터는 짝수 크기를 요구)
MANUAL_FRAME_MIN_SIZE_PX: int = 32
MANUAL_FRAME_ALIGN: int = 2
# - 명령 필터 체인의 지표 입력 레이블, `build_metric_filter`의 기본 레이블을 이 값으로 바꿔 재사용
_DISTORTED_REFERENCE_LABELS: str = "[sc][ref]"
_ACTIVE_LABELS: str = "[0:v]"


@dataclass(frozen=True, slots=True)
class FrameAnalysisParams:
    """화질 분석 방식 한 가지의 구간 길이, 시크 방식, 캐시 버전"""

    mode: str
    segment_duration_sec: float
    fast_keyframe_seek: bool
    analysis_version: int


_MODE_PARAMS: dict[str, FrameAnalysisParams] = {
    FRAME_ANALYSIS_MODE_FAST: FrameAnalysisParams(
        mode=FRAME_ANALYSIS_MODE_FAST,
        segment_duration_sec=0.5,
        fast_keyframe_seek=True,
        analysis_version=557,
    ),
    FRAME_ANALYSIS_MODE_BALANCED: FrameAnalysisParams(
        mode=FRAME_ANALYSIS_MODE_BALANCED,
        segment_duration_sec=1.0,
        fast_keyframe_seek=True,
        analysis_version=558,
    ),
    FRAME_ANALYSIS_MODE_PRECISE: FrameAnalysisParams(
        mode=FRAME_ANALYSIS_MODE_PRECISE,
        segment_duration_sec=2.0,
        fast_keyframe_seek=False,
        analysis_version=559,
    ),
}
FRAME_ANALYSIS_VERSION: int = _MODE_PARAMS[DEFAULT_FRAME_ANALYSIS_MODE].analysis_version
# - 보간 방식별 캐시 버전 간격. 방식 이름 순서로 10씩 떨어뜨려 방식, 화질 분석 방식 조합마다 다른 버전을 씀
SCALE_ALGORITHM_VERSION_STRIDE: int = 10
# - 검은 여백만 감지한 캐시 행의 전용 버전과 측정 지표 자리. 화질 분석 방식과 무관하게 유효함
# - 스캔 규칙이나 실패 판정이 바뀌면 올려 이전 규칙으로 저장한 감지 전용 행을 다시 잼
# - `analysis_version_for_mode`는 방식 버전(557-559)에 보간 방식 오프셋(-20~80)을 더해 537-639 사이 10 간격의
#   세 연속 번호를 만든다. 방식 버전을 올릴 때는 새 최솟값(기준값 - 20)이 이전에 배포한 기준값 + 80보다 커야
#   캐시 행이 다른 측정 조건으로 재사용되지 않으며, 새 전용 버전도 그 집합에 들지 않는 번호(현재 337)인지 확인
CROP_SCAN_VERSION: int = 337
METRIC_CROP_ONLY: str = ""


class AnalysisVersioned(Protocol):
    """분석 버전을 가진 결과나 캐시 행"""

    @property
    def analysis_version(self) -> int:
        """캐시 행이나 결과를 만든 분석 버전"""
        ...


class CropScanOutcome(AnalysisVersioned, Protocol):
    """분석 버전과 감지 실패 표시, 측정 불가 여부를 가진 결과나 캐시 행"""

    @property
    def crop_scan_failed(self) -> bool:
        """검은 여백 감지 자체가 실패했는지"""
        ...

    @property
    def unmeasurable(self) -> bool:
        """유효 지점이 없어 측정하지 못했는지"""
        ...


def is_crop_only_result(result: AnalysisVersioned) -> bool:
    """검은 여백만 감지하고 축소 후보를 재지 않은 결과인지"""
    return result.analysis_version == CROP_SCAN_VERSION


def is_failed_crop_scan(result: CropScanOutcome) -> bool:
    """검은 여백 감지 자체가 실패해 활성 화면을 확정하지 못한 결과나 캐시 행인지

    - 새 결과는 전체 화질 분석과 감지 전용 실행 모두 저장한 `crop_scan_failed` 값을 그대로 씀
    - 이 플래그가 없는 감지 전용 행은 측정 불가가 곧 스캔 실패였으므로 그 값으로 판정
    - 측정 불가는 지표만 재지 못한 경우(모든 지점이 검은 화면 등)에도 서므로 전체 화질 분석에는 쓰지 않음
    """
    return result.crop_scan_failed or (result.unmeasurable and is_crop_only_result(result))


def count_failed_crop_scans(results: Iterable[CropScanOutcome]) -> int:
    """결과 목록에서 감지에 실패한 영상 수"""
    return sum(1 for result in results if is_failed_crop_scan(result))


def is_usable_analysis_version(record_version: int, active_version: int) -> bool:
    """캐시 행 버전이 활성 화면으로 쓸 수 있는지

    - 검은 여백 감지 전용 행은 화질 분석 방식을 바꿔도 그대로 유효
    - 전체 화질 분석 행은 지금 방식의 버전과 같을 때만 유효
    """
    return record_version in (CROP_SCAN_VERSION, active_version)


def normalize_frame_analysis_mode(mode: str) -> str:
    """모르는 방식 값은 기본 방식으로 대체"""
    return mode if mode in VALID_FRAME_ANALYSIS_MODES else DEFAULT_FRAME_ANALYSIS_MODE


def frame_analysis_params(mode: str) -> FrameAnalysisParams:
    """방식 코드에 해당하는 구간 길이, 시크 방식, 캐시 버전 반환"""
    return _MODE_PARAMS[normalize_frame_analysis_mode(mode)]


def analysis_version_for_mode(mode: str, algorithm: str = DEFAULT_SCALE_ALGORITHM) -> int:
    """화질 분석 방식과 보간 방식 조합의 캐시 버전

    - 두 값 중 하나만 바뀌어도 기존 캐시 행과 달라져 다시 잼
    - 기본 보간 방식은 기존 버전 값을 그대로 써서 기본 설정의 캐시를 그대로 재사용
    """
    base = frame_analysis_params(mode).analysis_version
    index = SCALE_ALGORITHM_VALUES.index(normalize_scale_algorithm(algorithm))
    offset = index - SCALE_ALGORITHM_VALUES.index(DEFAULT_SCALE_ALGORITHM)
    return base + offset * SCALE_ALGORITHM_VERSION_STRIDE


@dataclass(frozen=True, slots=True)
class CropBox:
    """키프레임 휘도 스캔이 찾은 활성 화면 사각형"""

    width: int
    height: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class EdgeDarkness:
    """한 지점에서 프레임 네 면 안쪽으로 이어진 어두운 줄, 열 두께(px)

    - 면마다 프레임 끝에서 안쪽으로 검은 띠 줄 조건(어둡고 평탄)을 만족하는 구간의 두께
    - 합집합에서 다수 두께를 채택할 때 그 지점이 더하는 띠에 그림이 있는지 판정하는 근거
    """

    top: int
    bottom: int
    left: int
    right: int


@dataclass(frozen=True, slots=True)
class ActiveRegion:
    """검은 띠를 뺀 활성 화면 영역과 검은 띠 존재 여부, 구간별 여백 차이 여부, 어두운 근거로 지점을 무시했는지"""

    width: int
    height: int
    x: int
    y: int
    has_black_bars: bool
    # - 구간마다 여백이 달라 합집합으로만 잡을 수 있었는지. 잘라내기 판정에는 쓰지 않고 안내에만 씀
    margins_vary: bool = False
    # - 어두운 근거로 지점을 무시해 안쪽 경계를 채택했는지. 전체 프레임이면 참이 될 수 없음
    dark_outliers_ignored: bool = False

    def to_active_frame(self) -> ActiveFrame:
        """판정, 인코딩 입력용 활성 화면 크기와 시작 좌표 반환"""
        return ActiveFrame(width=self.width, height=self.height, x=self.x, y=self.y)


def align_even(value: int) -> int:
    """짝수로 내림 정렬한 값 반환(인코더 crop 크기와 좌표는 짝수여야 함)"""
    return value - value % MANUAL_FRAME_ALIGN


def normalize_manual_active_frame(
    frame: ActiveFrame,
    frame_width: int,
    frame_height: int,
) -> ActiveFrame | None:
    """사용자 지정 활성 화면을 영상 프레임 안의 짝수 좌표와 크기로 맞춤

    - 크기와 좌표는 짝수로 내림 정렬(인코더 crop 필터는 짝수 값을 요구)
    - 영상 프레임을 벗어나거나 최소 크기 미만이면 `ValueError`. 저장 값과 실제 크롭이 어긋나지 않게 조용히 자르지 않음
    - 결과가 전체 프레임과 같으면 None(자동 결과로 되돌림)
    """
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("영상 크기가 올바르지 않습니다")
    left = align_even(frame.x)
    top = align_even(frame.y)
    width = align_even(frame.width)
    height = align_even(frame.height)
    if width < MANUAL_FRAME_MIN_SIZE_PX or height < MANUAL_FRAME_MIN_SIZE_PX:
        raise ValueError(f"활성 화면 크기는 {MANUAL_FRAME_MIN_SIZE_PX}픽셀 이상이어야 합니다")
    if (
        left < 0
        or top < 0
        or left + width > frame_width
        or top + height > frame_height
    ):
        raise ValueError("활성 화면이 영상 크기를 벗어났습니다")
    if left == 0 and top == 0 and width >= align_even(frame_width) and height >= align_even(frame_height):
        return None
    return ActiveFrame(width=width, height=height, x=left, y=top)


def resolve_crop_frame(
    region: ActiveRegion,
    *,
    crop_enabled: bool = True,
    excluded: bool = False,
    is_vr_protected: bool = False,
    manual_frame: ActiveFrame | None = None,
) -> ActiveFrame | None:
    """잘라내기 확정 조건을 모두 만족하면 활성 화면, 아니면 None(전체 프레임 유지)

    - 설정 끔과 VR 보호는 직접 지정보다 우선해 잘라내지 않음
    - 직접 지정은 자동 감지 결과와 영상별 제외를 덮어씀(제외는 "자르지 않음", 직접 지정은 확정 크롭)
    - 직접 지정은 `normalize_manual_active_frame`을 거친 값이며 전체 프레임과 같은 지정은 저장 전에 None이 됨
    """
    if not crop_enabled or is_vr_protected:
        return None
    if manual_frame is not None:
        if not region.has_black_bars and manual_frame == region.to_active_frame():
            return None
        return manual_frame
    if excluded or not region.has_black_bars:
        return None
    return region.to_active_frame()


def needs_crop_review(region: ActiveRegion) -> bool:
    """어두운 근거로 지점을 무시해 안쪽 경계를 채택한 영상인지(사람 확인 필요)

    - 전체 프레임이면 안쪽 경계를 채택하지 않았으므로 대상이 아님
    """
    return region.has_black_bars and region.dark_outliers_ignored


def needs_margins_vary_review(region: ActiveRegion) -> bool:
    """구간마다 여백이 달라 합집합으로만 잡힌 영상인지(사람 확인 권장)

    - 전체 프레임이면 잡은 여백 자체가 없으므로 대상이 아님
    """
    return region.has_black_bars and region.margins_vary


def review_holds_automatic_crop(
    region: ActiveRegion,
    *,
    crop_enabled: bool = True,
    is_vr_protected: bool = False,
    manual_frame: ActiveFrame | None = None,
    acknowledged: bool = False,
) -> bool:
    """어두운 근거로 지점을 무시한 자동 크롭을 보류할지

    - 어두운 근거로 안쪽 경계를 채택한 영상은 사람이 확인해야 하므로 출력 방식과 무관하게 자동 크롭을 쓰지 않음
    - 사용자 지정 활성 화면, 잘라내기 설정 끔, VR 보호는 기존 확정 규칙대로 보류 대상이 아님
    - 확인한 영상(직접 지정 저장, 자동 감지로 되돌리기, 전체 화면 유지)은 확인 표시로 보류를 풀어 자동 크롭을 그대로 씀
    """
    if manual_frame is not None or acknowledged:
        return False
    if not crop_enabled or is_vr_protected:
        return False
    return needs_crop_review(region)


@dataclass(frozen=True, slots=True)
class TierScore:
    """규격 한 개의 왕복 중앙값(SSIM 점수, VMAF 점수)"""

    tier: str
    ssim: float | None = None
    vmaf: float | None = None


@dataclass(frozen=True, slots=True)
class FrameAnalysisThresholds:
    """축소 후보 판정 기준과 검은 여백 무시 면적 상한

    - 설정 값이 없으면 12.17절, 12.19절 확정값을 그대로 씀
    - 조기 확정 경계는 기준에서 상대 오프셋으로 파생해 기준을 바꿔도 앞뒤 여유가 같은 비율로 유지됨
    """

    vmaf: float = ROUNDTRIP_VMAF_THRESHOLD
    ssim: float = ROUNDTRIP_SSIM_THRESHOLD
    active_area_no_bars_ratio: float = ACTIVE_AREA_NO_BARS_RATIO

    @property
    def ssim_pass_score(self) -> float:
        """SSIM 통과 조기 확정 경계"""
        return min(SSIM_SCORE_MAX, self.ssim + SSIM_CONFIRMED_PASS_OFFSET)

    @property
    def ssim_fail_score(self) -> float:
        """SSIM 탈락 조기 확정 경계"""
        return max(SSIM_SCORE_MIN, self.ssim - SSIM_CONFIRMED_FAIL_OFFSET)

    @property
    def vmaf_pass_score(self) -> float:
        """VMAF 통과 조기 확정 경계"""
        return min(VMAF_SCORE_MAX, self.vmaf + VMAF_CONFIRMED_OFFSET)

    @property
    def vmaf_fail_score(self) -> float:
        """VMAF 탈락 조기 확정 경계"""
        return max(VMAF_SCORE_MIN, self.vmaf - VMAF_CONFIRMED_OFFSET)


DEFAULT_FRAME_ANALYSIS_THRESHOLDS = FrameAnalysisThresholds()


def frame_thresholds_from_config(config: ClassifierConfig) -> FrameAnalysisThresholds:
    """설정의 축소 후보 기준과 검은 여백 무시 면적 상한으로 판정 기준 구성"""
    return FrameAnalysisThresholds(
        vmaf=config.encode.roundtrip_vmaf_threshold,
        ssim=config.encode.roundtrip_ssim_threshold,
        active_area_no_bars_ratio=config.encode.active_area_no_bars_ratio,
    )


def is_vr_protected(
    width: int,
    height: int,
    *,
    has_vr_metadata: bool = False,
    manual_vr: bool = False,
) -> bool:
    """VR로 보호해야 하는 영상인지 판정

    - 구면 투영, 입체 부가 정보나 사용자 지정이 있으면 참
    - 부가 정보가 없어도 2:1 종횡비이고 긴 변이 2560 이상이면 VR 추정으로 보호
    """
    if has_vr_metadata or manual_vr:
        return True
    long_side = max(width, height)
    short_side = min(width, height)
    if short_side <= 0:
        return False
    aspect = long_side / short_side
    return VR_ASPECT_RATIO_MIN <= aspect <= VR_ASPECT_RATIO_MAX and long_side >= VR_MIN_LONG_SIDE


def _even_floor(value: int) -> int:
    return value - value % 2


def _edge_is_noise(thickness: int, axis_length: int) -> bool:
    """면 여백 두께가 노이즈 스냅 기준(축 길이의 2% 또는 8px) 미만인지"""
    limit = max(EDGE_SNAP_MIN_PX, round(axis_length * EDGE_SNAP_RATIO))
    return thickness < limit


def _edge_margins_vary(values: Sequence[int], axis_length: int) -> bool:
    """한 면의 여백 두께 목록에 양쪽 지점이 충분한 큰 차이가 있는지 판정

    - 허용 오차는 스냅 기준에 축 길이의 5%를 더한 값이라 여백이 조금씩 다른 정도는 넘지 못함
    - 정렬한 목록에서 양쪽에 `MARGIN_VARY_MIN_POINTS`개 이상이 남는 인접 차이만 봄
    """
    tolerance = max(EDGE_SNAP_MIN_PX, round(axis_length * EDGE_SNAP_RATIO)) + round(
        axis_length * MARGIN_VARY_EXTRA_RATIO
    )
    ordered = sorted(values)
    return any(
        ordered[index] - ordered[index - 1] > tolerance
        for index in range(MARGIN_VARY_MIN_POINTS, len(ordered) - MARGIN_VARY_MIN_POINTS + 1)
    )


def margins_vary(boxes: Sequence[CropBox], frame_width: int, frame_height: int) -> bool:
    """샘플 지점의 검은 여백이 구간마다 달라지는지 판정

    - 프레임 안 양수 크기 사각형만 비교해 검은 화면, 무효 지점은 자연히 빠짐
    - 네 면을 독립 비교하고 한 면이라도 차이가 크면 참(합집합이 한쪽 구간만 덮는 상황을 알림)
    - 지점 하나의 노이즈나 여백이 조금씩 다른 정도로는 참이 되지 않음
    """
    if frame_width <= 0 or frame_height <= 0:
        return False
    valid = [
        box
        for box in boxes
        if box.width > 0
        and box.height > 0
        and 0 <= box.x < frame_width
        and 0 <= box.y < frame_height
    ]
    if len(valid) < MARGIN_VARY_MIN_POINTS * 2:
        return False
    edge_values: tuple[tuple[list[int], int], ...] = (
        ([box.x for box in valid], frame_width),
        ([box.y for box in valid], frame_height),
        ([max(0, frame_width - (box.x + box.width)) for box in valid], frame_width),
        ([max(0, frame_height - (box.y + box.height)) for box in valid], frame_height),
    )
    return any(_edge_margins_vary(values, axis_length) for values, axis_length in edge_values)


def _agreed_edge(
    values: Sequence[int],
    axis_length: int,
    *,
    from_low: bool,
    dark_depths: Sequence[int | None] | None = None,
) -> tuple[int, bool]:
    """한 면 좌표 목록에서 대부분 지점이 같은 두께를 보이면 그 두께의 바깥 좌표를 씀

    - 어두운 장면처럼 일부 지점에서 띠가 검출되지 않아 합집합이 프레임 끝으로 벌어지는 것을 막음
    - 같은 두께로 볼 지점이 3/4 이상일 때만 채택하고, 모자라면 전체 바깥 좌표를 씀(구간별 두께 변동 보존)
    - 바깥으로 벌어지는 지점은 그 지점의 어두운 구간이 채택 경계까지 닿을 때만 무시함. 근거가 없거나
      모자라면 그 구간에 그림이 있을 수 있으므로 전체 바깥 좌표로 넓혀 자르지 않음(열린 매트 보호)
    - 반환하는 두 번째 값은 어두운 근거로 무시한 지점이 있었는지(안내용)
    """
    outer = _outmost_low if from_low else _outmost_high
    if not values:
        return (outer(values), False)
    tolerance = max(EDGE_SNAP_MIN_PX, round(axis_length * EDGE_SNAP_RATIO))
    required = -(-len(values) * MARGIN_AGREE_NUMERATOR // MARGIN_AGREE_DENOMINATOR)
    best: list[int] = []
    for coordinate in values:
        cluster = [value for value in values if abs(value - coordinate) <= tolerance]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < required:
        return (outer(values), False)
    edge = outer(best)
    depths: Sequence[int | None] = dark_depths if dark_depths is not None else (None,) * len(values)
    ignored = False
    for value, depth in zip(values, depths, strict=True):
        inside = value >= edge if from_low else value <= edge
        if inside:
            continue
        reaches_edge = depth is not None and depth >= (edge if from_low else axis_length - edge)
        if not reaches_edge:
            return (outer(values), False)
        ignored = True
    return (edge, ignored)


def _outmost_low(values: Sequence[int]) -> int:
    """가장 작은 좌표"""
    return min(values)


def _outmost_high(values: Sequence[int]) -> int:
    """가장 큰 좌표"""
    return max(values)


def merge_crop_union(
    boxes: Sequence[CropBox],
    frame_width: int,
    frame_height: int,
    *,
    is_vr: bool = False,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    darkness: Sequence[EdgeDarkness | None] | None = None,
    flat_faces: Collection[str] = (),
) -> ActiveRegion:
    """검출 사각형 전체를 포함하는 합집합 영역을 활성 화면으로 확정

    - VR 영상이거나 쓸 수 있는 사각형이 없으면 전체 프레임
    - 상, 하, 좌, 우 경계를 독립 계산해 비대칭, 편측, 4면 액자형 여백과 구간별 두께 변동을 모두 보존
    - 면별 여백이 노이즈 기준 미만이면 프레임 끝으로 스냅하고, 시작 좌표와 크기를 짝수로 정렬
    - 합집합 면적이 기준 면적 비율을 넘거나 원본 전체 크기와 같으면 검은 띠 없음 처리
    - 합집합으로 확정한 영역에는 구간별 여백 차이 여부를 함께 기록(판정은 바꾸지 않음)
    - `darkness`는 `boxes`와 같은 순서의 지점별 어두운 면 두께. 다수 두께와 어긋나는 지점을 무시하려면
      그 지점의 어두운 구간이 채택 경계까지 이어져야 함
    - `flat_faces`는 평탄 여백 규칙이 합의로 자른 면 이름. 그 면은 노이즈 스냅을 건너뛰어 4K에서 2% 미만의
      합의된 여백도 버리지 않음
    - 어두운 근거로 지점을 무시한 사실은 채택한 영역의 `dark_outliers_ignored`에만 남기고 판정에는 쓰지 않음.
      전체 프레임으로 되돌리면 안쪽 경계를 채택하지 않았으므로 세우지 않음
    """
    if is_vr or not boxes or frame_width <= 0 or frame_height <= 0:
        return ActiveRegion(frame_width, frame_height, 0, 0, False)
    vary = margins_vary(boxes, frame_width, frame_height)
    evidence: Sequence[EdgeDarkness | None] = (
        darkness if darkness is not None and len(darkness) == len(boxes) else (None,) * len(boxes)
    )
    left, left_ignored = _agreed_edge(
        [max(0, box.x) for box in boxes],
        frame_width,
        from_low=True,
        dark_depths=[None if edge is None else edge.left for edge in evidence],
    )
    top, top_ignored = _agreed_edge(
        [max(0, box.y) for box in boxes],
        frame_height,
        from_low=True,
        dark_depths=[None if edge is None else edge.top for edge in evidence],
    )
    right, right_ignored = _agreed_edge(
        [min(frame_width, box.x + box.width) for box in boxes],
        frame_width,
        from_low=False,
        dark_depths=[None if edge is None else edge.right for edge in evidence],
    )
    bottom, bottom_ignored = _agreed_edge(
        [min(frame_height, box.y + box.height) for box in boxes],
        frame_height,
        from_low=False,
        dark_depths=[None if edge is None else edge.bottom for edge in evidence],
    )
    ignored = left_ignored or top_ignored or right_ignored or bottom_ignored
    full_frame = ActiveRegion(
        frame_width,
        frame_height,
        0,
        0,
        False,
        margins_vary=vary,
    )
    if "left" not in flat_faces and _edge_is_noise(left, frame_width):
        left = 0
    if "right" not in flat_faces and _edge_is_noise(frame_width - right, frame_width):
        right = frame_width
    if "top" not in flat_faces and _edge_is_noise(top, frame_height):
        top = 0
    if "bottom" not in flat_faces and _edge_is_noise(frame_height - bottom, frame_height):
        bottom = frame_height
    left = _even_floor(left)
    top = _even_floor(top)
    width = _even_floor(max(0, right - left))
    height = _even_floor(max(0, bottom - top))
    if width <= 0 or height <= 0:
        return full_frame
    if width >= frame_width and height >= frame_height:
        return full_frame
    if width * height > frame_width * frame_height * thresholds.active_area_no_bars_ratio:
        return full_frame
    return ActiveRegion(
        width,
        height,
        left,
        top,
        True,
        margins_vary=vary,
        dark_outliers_ignored=ignored,
    )


def candidate_tiers_for(width: int, height: int) -> tuple[ResolutionTier, ...]:
    """활성 화면 기준으로 의미 있는 축소가 되는 규격을 높은 규격부터 내림차순으로 반환"""
    return tuple(
        reversed([tier for tier in RESOLUTION_TIERS if is_meaningful_downscale(width, height, tier)])
    )


def _build_roundtrip_chain(
    active_region: ActiveRegion,
    tier: ResolutionTier,
    *,
    metric: str,
    model: str,
    log_name: str,
    n_subsample: int = 0,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
    tonemap: bool = False,
) -> str:
    """활성 화면 기준 축소 왕복과 지표 필터를 잇는 필터 그래프 문자열 생성

    - HDR이면 `split` 앞에서 SDR 톤 매핑을 거쳐 기준과 축소본이 같은 변환 결과로 비교되게 함
    - 검은 띠가 있으면 입력에 `crop`을 먼저 적용해 좌우 검은 띠가 SSIM을 부풀리지 않게 함(결정 20)
    - 왕복은 활성 화면 크기로 줄였다가 활성 화면 크기로 되돌린 뒤 원본과 비교
    - 보간 방식은 인코딩 축소와 같은 값을 받아 예측과 실제 결과를 맞춤
    """
    crop = (
        f"crop={active_region.width}:{active_region.height}:{active_region.x}:{active_region.y},"
        if active_region.has_black_bars
        else ""
    )
    prefix = f"{_ACTIVE_LABELS}{tonemap_graph_prefix(hdr=tonemap)}{crop}"
    out_width, out_height = calc_scaled_dimensions(active_region.width, active_region.height, tier)
    chain = (
        f"{prefix}split=2[ref][dist];"
        f"[dist]{scale_filter(str(out_width), str(out_height), algorithm)},"
        f"{scale_filter(str(active_region.width), str(active_region.height), algorithm)}[sc]"
    )
    metric_filter = build_metric_filter(metric, model=model, log_name=log_name, n_subsample=n_subsample)
    return f"{chain};{metric_filter.replace(METRIC_INPUT_LABELS, _DISTORTED_REFERENCE_LABELS, 1)}"


def build_roundtrip_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    duration_sec: float,
    active_region: ActiveRegion,
    tier: ResolutionTier,
    metric: str,
    model: str,
    log_name: str,
    fast_keyframe_seek: bool = False,
    n_subsample: int = 0,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
    tonemap: bool = False,
) -> list[str]:
    """구간을 규격으로 줄였다 되돌린 결과와 원본의 지표를 재는 FFmpeg 인자 목록"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        *(["-noaccurate_seek"] if fast_keyframe_seek else []),
        "-i",
        source_path,
        "-t",
        f"{duration_sec:.3f}",
        "-filter_complex",
        _build_roundtrip_chain(
            active_region,
            tier,
            metric=metric,
            model=model,
            log_name=log_name,
            n_subsample=n_subsample,
            algorithm=algorithm,
            tonemap=tonemap,
        ),
        "-f",
        "null",
        "-",
    ]


def score_passes(
    score: TierScore,
    *,
    use_vmaf: bool,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> bool:
    """규격 중앙값이 축소 후보 기준을 넘는지 판정

    - SSIM은 항상 기준을 넘어야 하고, VMAF가 있으면 VMAF도 함께 넘어야 함
    """
    if score.ssim is None or score.ssim < thresholds.ssim:
        return False
    if not use_vmaf:
        return True
    return score.vmaf is not None and score.vmaf >= thresholds.vmaf


def select_candidate_tier(
    tier_scores: Sequence[TierScore],
    *,
    use_vmaf: bool,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> str | None:
    """높은 규격부터 연속으로 통과한 규격 중 가장 낮은 규격 키 반환

    - 처음 실패한 규격 뒤로는 검사하지 않은 것으로 보고 그 위의 통과분만 후보로 인정
    """
    candidate: str | None = None
    for score in tier_scores:
        if not score_passes(score, use_vmaf=use_vmaf, thresholds=thresholds):
            break
        candidate = score.tier
    return candidate


def _nearest_unused(target: int, candidates: set[int]) -> int | None:
    """대상 지점에서 가장 가까운 미사용 지점. 같은 거리면 작은 번호"""
    if not candidates:
        return None
    return min(candidates, key=lambda point: (abs(point - target), point))


def choose_round_points(
    targets: Sequence[int],
    valid_points: set[int],
    used_points: set[int],
) -> tuple[int, ...]:
    """라운드의 목표 지점 중 아직 쓰지 않은 유효 지점을 고르고, 무효면 가까운 유효 지점으로 대체"""
    chosen: list[int] = []
    available = valid_points - used_points
    for target in targets:
        point = target if target in available else _nearest_unused(target, available)
        if point is None:
            continue
        chosen.append(point)
        available.discard(point)
    return tuple(chosen)


def ssim_stage_confirmed(
    medians: Sequence[float | None],
    *,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> bool:
    """규격 순서(높은 규격부터)별 누적 중앙값으로 SSIM 단계 조기 확정 여부 판정

    - 가장 낮은 통과 규격이 확정 통과값 이상이고 첫 탈락 규격이 확정 탈락값 미만이면 확정
    - 통과 규격이 없어도 첫 규격부터 확정 탈락값 미만이면 원본 유지로 확정
    - 모든 규격이 통과하면 가장 낮은 규격의 확정 통과 여부만 봄
    """
    lowest_pass: float | None = None
    first_fail: float | None = None
    for median in medians:
        if median is None:
            return False
        if median >= thresholds.ssim:
            lowest_pass = median
            continue
        first_fail = median
        break
    if lowest_pass is not None and lowest_pass < thresholds.ssim_pass_score:
        return False
    if first_fail is None:
        return lowest_pass is not None
    return first_fail < thresholds.ssim_fail_score


def next_round_tier_keys(
    tiers: Sequence[ResolutionTier],
    medians: Mapping[str, float | None],
    *,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> tuple[str, ...]:
    """다음 라운드에서 잴 규격 키. 확정 통과/탈락 규격은 빼고 경계 규격은 남김"""
    keys = [tier.key for tier in tiers]
    lowest_pass_key: str | None = None
    first_fail_key: str | None = None
    for key in keys:
        median = medians.get(key)
        if median is not None and median >= thresholds.ssim:
            lowest_pass_key = key
            continue
        first_fail_key = key
        break
    keep: list[str] = []
    for key in keys:
        median = medians.get(key)
        keep_current = (
            median is None
            or thresholds.ssim_fail_score <= median < thresholds.ssim_pass_score
            or key in (lowest_pass_key, first_fail_key)
        )
        if keep_current:
            keep.append(key)
    return tuple(keep)


def apply_no_bars_ratio(
    region: ActiveRegion,
    frame_width: int,
    frame_height: int,
    *,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> ActiveRegion:
    """저장된 활성 화면을 현재 면적 상한으로 다시 판정

    - 활성 화면 면적이 현재 상한을 넘으면 검은 여백이 없는 것으로 보고 전체 프레임으로 되돌림
    - 캐시에는 합집합 사각형만 남으므로 상한을 낮춘 경우의 즉시 재판정에 씀
    """
    if not region.has_black_bars or frame_width <= 0 or frame_height <= 0:
        return region
    if region.width * region.height > frame_width * frame_height * thresholds.active_area_no_bars_ratio:
        return ActiveRegion(
            frame_width, frame_height, 0, 0, False, margins_vary=region.margins_vary
        )
    return region


def tier_scores_to_json(tier_scores: Sequence[TierScore]) -> str:
    """규격별 중앙값 목록을 캐시 저장용 JSON 문자열로 변환"""
    return json.dumps(
        [{"tier": score.tier, "ssim": score.ssim, "vmaf": score.vmaf} for score in tier_scores],
        ensure_ascii=False,
    )


def can_reuse_tier_scores(
    tier_scores: Sequence[TierScore],
    *,
    use_vmaf: bool,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    all_tier_keys: Sequence[str] = (),
) -> bool:
    """저장된 규격 점수만으로 현재 기준의 축소 후보를 확정할 수 있는지

    - 측정된 규격 목록은 전체 규격의 앞부분과 같아야 함
    - SSIM 통과 연쇄가 저장 점수 안에서 끊기면(첫 탈락 규격의 점수가 있음) 확정 가능
    - VMAF는 통과 구간을 아래 규격부터 훑어 보며, 판정에 필요한 값이 비어 있으면 확정 불가
    - 저장 점수가 모두 통과하면 더 낮은 미측정 규격이 있을 수 있어 전체 규격을 쟀을 때만 확정
    """
    if not tier_scores:
        return False
    if all_tier_keys and tuple(score.tier for score in tier_scores) != tuple(all_tier_keys)[
        : len(tier_scores)
    ]:
        return False
    ssim_pass: list[TierScore] = []
    for score in tier_scores:
        if score.ssim is None or score.ssim < thresholds.ssim:
            break
        ssim_pass.append(score)
    if not ssim_pass:
        return tier_scores[0].ssim is not None
    all_ssim_pass = len(ssim_pass) == len(tier_scores)
    covered = bool(all_tier_keys) and len(tier_scores) == len(all_tier_keys)
    if not use_vmaf:
        return (not all_ssim_pass) or covered
    for index in range(len(ssim_pass) - 1, -1, -1):
        score = ssim_pass[index]
        if score.vmaf is None:
            return False
        if score.vmaf >= thresholds.vmaf:
            # - 가장 낮은 저장 규격이 통과했는데 전체 규격을 재지 않았으면 더 낮은 후보가 남을 수 있음
            lowest_stored = index == len(ssim_pass) - 1
            return not (all_ssim_pass and lowest_stored) or covered
    # - 통과 구간이 모두 VMAF 탈락이면 후보 없음 확정. 더 낮은 미측정 규격은 SSIM 탈락 뒤라 후보가 될 수 없음
    return True


def rejudge_candidate_tier(
    tier_scores: Sequence[TierScore],
    stored_candidate: str | None,
    *,
    use_vmaf: bool,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> str | None:
    """저장된 규격 점수로 현재 기준의 축소 후보를 다시 판정

    - 조기 확정으로 저장 후보 위쪽 규격은 VMAF를 재지 않았을 수 있으므로 후보 자체의 통과 여부만 먼저 봄
    - 후보가 기준을 넘지 못하면 None을 돌려 호출자가 다시 재도록 함
    - 기준이 완화되어 아래쪽 규격 점수도 넘으면 저장 점수가 있는 범위에서 후보를 확장
    - 저장 후보가 없으면 저장 점수만으로 판정해 기준 완화 뒤 후보가 생기는 행을 놓치지 않음
    """
    if not tier_scores:
        return None
    keys = [score.tier for score in tier_scores]
    if stored_candidate is None or stored_candidate not in keys:
        return select_candidate_tier(tier_scores, use_vmaf=use_vmaf, thresholds=thresholds)
    index = keys.index(stored_candidate)
    if not score_passes(tier_scores[index], use_vmaf=use_vmaf, thresholds=thresholds):
        for upper in reversed(tier_scores[:index]):
            if (
                use_vmaf
                and upper.ssim is not None
                and upper.ssim >= thresholds.ssim
                and upper.vmaf is None
            ):
                return None
            if score_passes(upper, use_vmaf=use_vmaf, thresholds=thresholds):
                return upper.tier
        return None
    candidate = stored_candidate
    for lower in tier_scores[index + 1 :]:
        if not score_passes(lower, use_vmaf=use_vmaf, thresholds=thresholds):
            break
        candidate = lower.tier
    return candidate


def _to_float(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


def tier_scores_from_json(text: str) -> tuple[TierScore, ...]:
    """캐시에 저장한 규격별 중앙값 JSON을 복원. 형식이 다르면 빈 튜플"""
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        return ()
    if not isinstance(raw, list):
        return ()
    scores: list[TierScore] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tier = item.get("tier")
        if not isinstance(tier, str) or not tier:
            continue
        scores.append(TierScore(tier=tier, ssim=_to_float(item.get("ssim")), vmaf=_to_float(item.get("vmaf"))))
    return tuple(scores)
