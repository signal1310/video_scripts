"""화질 분석의 검은 띠 감지, 왕복 지표 명령 구성, 축소 후보 판정 순수 함수 모듈

- 검은 띠 합집합과 90% 경계, VR 보호 판정은 결정 20, 27의 확정값을 따름
- 축소 후보는 VMAF 98.0과 SSIM 0.996을 동시에 넘는 가장 낮은 규격이며, SSIM 선행 필터에 필요한 판정만 담당
- 프로세스 실행은 `frame_analysis_runner.py`, 지표 로그 파싱과 모델 선택은 `quality_probe.py`를 재사용
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from src.core.models import ActiveFrame
from src.core.quality_probe import build_metric_filter
from src.core.resolution_tiers import RESOLUTION_TIERS, ResolutionTier, calc_scaled_dimensions, is_meaningful_downscale

# - 활성 화면 면적이 전체의 90%를 넘으면 검은 띠가 없는 것으로 봄(결정 20 초기값 승인)
ACTIVE_AREA_NO_BARS_RATIO: float = 0.90
# - 부가 정보가 없는 2:1 VR180 영상 보호 범위(결정 27, 12.17절 5항)
VR_ASPECT_RATIO_MIN: float = 1.95
VR_ASPECT_RATIO_MAX: float = 2.05
VR_MIN_LONG_SIDE: int = 2560
# - 4K 모델과 기본 모델 공통 기준(12.17절 승인)
ROUNDTRIP_VMAF_THRESHOLD: float = 98.0
ROUNDTRIP_SSIM_THRESHOLD: float = 0.996
CROP_DETECT_LIMIT: int = 24
CROP_DETECT_ROUND: int = 2
BLACK_DETECT_MIN_DURATION: float = 0.1
BLACK_DETECT_PIX_TH: float = 0.10
# - 측정 규칙이 바뀌면 올려 캐시를 다시 재게 함
FRAME_ANALYSIS_VERSION: int = 1
CROP_MARKER_RE: re.Pattern[str] = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
BLACK_DURATION_RE: re.Pattern[str] = re.compile(r"black_duration:\s*([0-9.]+)")
# - 명령 필터 체인의 지표 입력 레이블, `build_metric_filter`의 기본 레이블을 이 값으로 바꿔 재사용
_METRIC_INPUT_LABELS: str = "[0:v][1:v]"
_DISTORTED_REFERENCE_LABELS: str = "[sc][ref]"
_ACTIVE_LABELS: str = "[0:v]"


@dataclass(frozen=True, slots=True)
class CropBox:
    """`cropdetect`가 제안한 활성 화면 사각형"""

    width: int
    height: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ActiveRegion:
    """검은 띠를 뺀 활성 화면 영역과 검은 띠 존재 여부"""

    width: int
    height: int
    x: int
    y: int
    has_black_bars: bool

    def to_active_frame(self) -> ActiveFrame:
        """판정 입력용 활성 화면 크기 반환"""
        return ActiveFrame(width=self.width, height=self.height)


@dataclass(frozen=True, slots=True)
class TierScore:
    """규격 한 개의 왕복 중앙값(SSIM 점수, VMAF 점수)"""

    tier: str
    ssim: float | None = None
    vmaf: float | None = None


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


def parse_cropdetect_boxes(stderr_text: str) -> list[CropBox]:
    """`cropdetect` 표준 오류에서 제안 사각형을 등장 순서대로 수집"""
    return [
        CropBox(width=int(width), height=int(height), x=int(x), y=int(y))
        for width, height, x, y in CROP_MARKER_RE.findall(stderr_text)
    ]


def _even_floor(value: int) -> int:
    return value - value % 2


def merge_crop_union(
    boxes: Sequence[CropBox],
    frame_width: int,
    frame_height: int,
    *,
    is_vr: bool = False,
) -> ActiveRegion:
    """검출 사각형 전체를 포함하는 합집합 영역을 활성 화면으로 확정

    - VR 영상이거나 쓸 수 있는 사각형이 없으면 전체 프레임
    - 프레임 경계로 자르고 모든 값을 짝수로 내림해 `crop` 필터에 그대로 쓸 수 있게 함
    - 합집합 면적이 `ACTIVE_AREA_NO_BARS_RATIO`를 넘으면 어두운 장면 오인으로 보고 검은 띠 없음 처리
    """
    full_frame = ActiveRegion(frame_width, frame_height, 0, 0, False)
    if is_vr or not boxes or frame_width <= 0 or frame_height <= 0:
        return full_frame
    left = _even_floor(max(0, min(box.x for box in boxes)))
    top = _even_floor(max(0, min(box.y for box in boxes)))
    right = min(frame_width, max(box.x + box.width for box in boxes))
    bottom = min(frame_height, max(box.y + box.height for box in boxes))
    width = _even_floor(max(0, right - left))
    height = _even_floor(max(0, bottom - top))
    if width <= 0 or height <= 0:
        return full_frame
    if width * height > frame_width * frame_height * ACTIVE_AREA_NO_BARS_RATIO:
        return full_frame
    return ActiveRegion(width, height, left, top, True)


def parse_blackdetect_ratio(stderr_text: str, segment_duration_sec: float) -> float:
    """`blackdetect`가 찾은 검은 구간 길이 합을 구간 길이로 나눈 비율(0.0 ~ 1.0)"""
    if segment_duration_sec <= 0:
        return 0.0
    total = 0.0
    for raw in BLACK_DURATION_RE.findall(stderr_text):
        try:
            total += float(raw)
        except ValueError:
            continue
    return min(1.0, max(0.0, total / segment_duration_sec))


def candidate_tiers_for(width: int, height: int) -> tuple[ResolutionTier, ...]:
    """활성 화면 기준으로 의미 있는 축소가 되는 규격을 높은 규격부터 내림차순으로 반환"""
    return tuple(
        reversed([tier for tier in RESOLUTION_TIERS if is_meaningful_downscale(width, height, tier)])
    )


def build_blackdetect_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    duration_sec: float,
) -> list[str]:
    """구간의 검은 화면 비율을 재는 `blackdetect` 명령 인자 목록"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        source_path,
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        f"blackdetect=d={BLACK_DETECT_MIN_DURATION}:pix_th={BLACK_DETECT_PIX_TH}",
        "-an",
        "-f",
        "null",
        "-",
    ]


def build_cropdetect_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    duration_sec: float,
) -> list[str]:
    """구간의 활성 화면 후보를 얻는 `cropdetect` 명령 인자 목록"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        source_path,
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        f"cropdetect={CROP_DETECT_LIMIT}:{CROP_DETECT_ROUND}:0",
        "-an",
        "-f",
        "null",
        "-",
    ]


def _build_roundtrip_chain(
    active_region: ActiveRegion,
    tier: ResolutionTier,
    *,
    metric: str,
    model: str,
    log_name: str,
) -> str:
    """활성 화면 기준 축소 왕복과 지표 필터를 잇는 필터 그래프 문자열 생성

    - 검은 띠가 있으면 입력에 `crop`을 먼저 적용해 좌우 검은 띠가 SSIM을 부풀리지 않게 함(결정 20)
    - 왕복은 활성 화면 크기로 줄였다가 활성 화면 크기로 되돌린 뒤 원본과 비교
    """
    crop = (
        f"crop={active_region.width}:{active_region.height}:{active_region.x}:{active_region.y},"
        if active_region.has_black_bars
        else ""
    )
    out_width, out_height = calc_scaled_dimensions(active_region.width, active_region.height, tier)
    chain = (
        f"{_ACTIVE_LABELS}{crop}split=2[ref][dist];"
        f"[dist]scale={out_width}:{out_height}:flags=bicubic,"
        f"scale={active_region.width}:{active_region.height}:flags=bicubic[sc]"
    )
    metric_filter = build_metric_filter(metric, model=model, log_name=log_name)
    return f"{chain};{metric_filter.replace(_METRIC_INPUT_LABELS, _DISTORTED_REFERENCE_LABELS, 1)}"


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
) -> list[str]:
    """구간을 규격으로 줄였다 되돌린 결과와 원본의 지표를 재는 FFmpeg 인자 목록"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        source_path,
        "-t",
        f"{duration_sec:.3f}",
        "-filter_complex",
        _build_roundtrip_chain(active_region, tier, metric=metric, model=model, log_name=log_name),
        "-f",
        "null",
        "-",
    ]


def score_passes(score: TierScore, *, use_vmaf: bool) -> bool:
    """규격 중앙값이 축소 후보 기준을 넘는지 판정

    - SSIM은 항상 기준을 넘어야 하고, VMAF가 있으면 VMAF도 함께 넘어야 함
    """
    if score.ssim is None or score.ssim < ROUNDTRIP_SSIM_THRESHOLD:
        return False
    if not use_vmaf:
        return True
    return score.vmaf is not None and score.vmaf >= ROUNDTRIP_VMAF_THRESHOLD


def select_candidate_tier(tier_scores: Sequence[TierScore], *, use_vmaf: bool) -> str | None:
    """높은 규격부터 연속으로 통과한 규격 중 가장 낮은 규격 키 반환

    - 처음 실패한 규격 뒤로는 검사하지 않은 것으로 보고 그 위의 통과분만 후보로 인정
    """
    candidate: str | None = None
    for score in tier_scores:
        if not score_passes(score, use_vmaf=use_vmaf):
            break
        candidate = score.tier
    return candidate


def tier_scores_to_json(tier_scores: Sequence[TierScore]) -> str:
    """규격별 중앙값 목록을 캐시 저장용 JSON 문자열로 변환"""
    return json.dumps(
        [{"tier": score.tier, "ssim": score.ssim, "vmaf": score.vmaf} for score in tier_scores],
        ensure_ascii=False,
    )


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
