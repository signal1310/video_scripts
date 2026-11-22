"""화질 비교 뷰어의 비교 쌍 해석, 프레임 추출 인자, 시점과 배율 계산 순수 함수 모듈

- 비교 쌍과 하위 규격은 `frame_analysis.py`, `resolution_tiers.py`, `sample_points.py` 기준을 재사용
- 프로세스 실행과 파일 읽기는 `frame_compare_service.py`, 화면 표시는 GUI가 맡음
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from src.core.analysis.frame_analysis import candidate_tiers_for
from src.core.formatting import format_size_mb
from src.core.models import BYTES_PER_KB
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, ResolutionTier, calc_scaled_dimensions
from src.core.sample_points import SAMPLE_RANGE_END_RATIO, SAMPLE_RANGE_START_RATIO

RESULT_TARGET_KEY: str = "result"
RESULT_TARGET_LABEL: str = "결과"
ORIGINAL_LABEL: str = "원본"
DOWNSCALE_LABEL_TEMPLATE: str = "{tier} 축소 비교"
RESULT_TARGET_TIP: str = "원본과 재인코딩 결과를 같은 시점에서 비교합니다."
DOWNSCALE_TIP_TEMPLATE: str = (
    "원본 프레임을 {tier} 규격으로 줄였다가 원본 크기로 되돌린 비교 프레임입니다. "
    "축소 과정에서 생기는 화질 차이를 봅니다."
)
MISSING_ORIGINAL_REASON: str = "원본 파일이 없어 비교할 수 없습니다."
NO_TARGET_REASON: str = (
    "비교할 대상이 없습니다. 이 영상보다 낮은 축소 규격이나 재인코딩 결과가 없습니다."
)
REPRESENTATIVE_COUNT: int = 9
DEFAULT_FRAME_STEP_SEC: float = 1.0 / 30.0
BYTES_PER_MB: int = BYTES_PER_KB * BYTES_PER_KB
LARGE_SIZE_DECIMALS: int = 2
SMALL_SIZE_DECIMALS: int = 0
LARGE_SIZE_THRESHOLD_MB: float = 1024.0

ZOOM_FIT: str = "fit"
ZOOM_ONE_TO_ONE: str = "1"
ZOOM_TWO_HUNDRED: str = "200"
ZOOM_FOUR_HUNDRED: str = "400"
ZOOM_PRESETS: tuple[tuple[str, str], ...] = (
    (ZOOM_FIT, "맞춤"),
    (ZOOM_ONE_TO_ONE, "1:1"),
    (ZOOM_TWO_HUNDRED, "200%"),
    (ZOOM_FOUR_HUNDRED, "400%"),
)
ZOOM_PRESET_SCALES: dict[str, float] = {
    ZOOM_ONE_TO_ONE: 1.0,
    ZOOM_TWO_HUNDRED: 2.0,
    ZOOM_FOUR_HUNDRED: 4.0,
}
MIN_SCALE: float = 0.05
MAX_SCALE: float = 8.0
ZOOM_STEP_FACTOR: float = 1.25
SPLIT_MIN_RATIO: float = 0.05
SPLIT_MAX_RATIO: float = 0.95
# - 같은 프레임을 두 번 추출하지 않도록 요청 시각을 1ms 단위로 맞춤
TIME_DECIMALS: int = 3
_MS_PER_SECOND: int = 1000
_SECONDS_PER_MINUTE: int = 60
_SECONDS_PER_HOUR: int = 3600


@dataclass(frozen=True, slots=True)
class FrameSource:
    """비교에 쓰는 프레임 한 쪽의 식별 키, 경로와 표시 문구, 축소 왕복 규격

    - tier_key가 없으면 파일 프레임을 그대로 쓰고, 있으면 그 규격으로 줄였다 되돌린 프레임을 씀
    """

    key: str
    path: str
    label: str
    detail: str = ""
    tooltip: str = ""
    tier_key: str | None = None

    @property
    def tier(self) -> ResolutionTier | None:
        """축소 왕복에 쓰는 규격, 원본 그대로면 None"""
        if self.tier_key is None:
            return None
        return RESOLUTION_TIER_BY_KEY.get(self.tier_key)


@dataclass(frozen=True, slots=True)
class ComparePlan:
    """원본과 비교 대상 목록, 기본 선택, 비교 불가 사유"""

    original: FrameSource
    targets: tuple[FrameSource, ...]
    default_key: str | None
    reason: str = ""

    @property
    def can_compare(self) -> bool:
        """비교할 대상이 있고 사유가 없으면 참"""
        return bool(self.targets) and not self.reason

    def target(self, key: str | None) -> FrameSource | None:
        """키에 해당하는 비교 대상, 없으면 기본 대상, 그것도 없으면 None"""
        chosen = next((item for item in self.targets if item.key == key), None)
        if chosen is not None:
            return chosen
        default = next((item for item in self.targets if item.key == self.default_key), None)
        return default or (self.targets[0] if self.targets else None)


def is_readable_file(path: str | None) -> bool:
    """경로가 비어 있지 않고 실제 파일이면 참"""
    return bool(path) and os.path.isfile(path)


def choose_original_path(source_path: str, archive_path: str | None) -> str | None:
    """비교 기준이 되는 원본 파일 경로

    - 원본 교체 정책이면 보관 파일을, 별도 폴더 저장이면 원래 원본을 씀
    - 둘 다 없으면 None
    """
    if is_readable_file(archive_path):
        return archive_path
    if is_readable_file(source_path):
        return source_path
    return None


def is_same_file_path(first: str, second: str) -> bool:
    """대소문자와 구분자 차이를 무시하고 같은 파일 경로인지"""
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def resolve_result_paths(
    source_path: str, output_path: str | None, archive_path: str | None
) -> tuple[str, str] | None:
    """인코딩 완료 결과와 원본의 파일 경로 쌍. 원본이나 결과가 없으면 None

    - 원본 교체 정책으로 결과가 원본과 같은 파일이면 자기 자신과 비교가 되므로 None
    """
    original = choose_original_path(source_path, archive_path)
    if original is None or not is_readable_file(output_path):
        return None
    if is_same_file_path(original, str(output_path)):
        return None
    return original, str(output_path)


def resolve_pending_paths(source_path: str, part_path: str | None) -> tuple[str, str] | None:
    """교체 대기 중인 결과 임시 파일과 원본의 파일 경로 쌍. 둘 중 하나가 없으면 None"""
    if not is_readable_file(source_path) or not is_readable_file(part_path):
        return None
    return source_path, str(part_path)


def format_size_bytes(num_bytes: int) -> str:
    """파일 크기를 자동 단위로 표기, 1 GB 이상은 소수 두 자리"""
    size_mb = num_bytes / BYTES_PER_MB
    decimals = LARGE_SIZE_DECIMALS if size_mb >= LARGE_SIZE_THRESHOLD_MB else SMALL_SIZE_DECIMALS
    return format_size_mb(size_mb, decimals, unit="auto")


def resolution_text(width: int, height: int) -> str:
    """해상도 표기, ASCII x를 씀"""
    return f"{width}x{height}"


def source_detail(width: int, height: int, size_bytes: int) -> str:
    """비교 대상의 해상도와 파일 크기 표기"""
    return f"{resolution_text(width, height)} {format_size_bytes(size_bytes)}"


def result_target(output_path: str, *, width: int, height: int, size_bytes: int) -> FrameSource:
    """인코딩 완료 결과를 비교 대상으로 만듦"""
    return FrameSource(
        key=RESULT_TARGET_KEY,
        path=output_path,
        label=RESULT_TARGET_LABEL,
        detail=source_detail(width, height, size_bytes),
        tooltip=RESULT_TARGET_TIP,
    )


def build_downscale_targets(
    source_path: str, width: int, height: int
) -> tuple[FrameSource, ...]:
    """원본 해상도에서 의미 있게 줄일 수 있는 하위 규격을 높은 규격부터 만듦

    - 규격 표시는 규격 이름을, 상세 문구는 규격으로 줄인 크기를 씀
    """
    targets: list[FrameSource] = []
    for tier in candidate_tiers_for(width, height):
        out_width, out_height = calc_scaled_dimensions(width, height, tier)
        targets.append(
            FrameSource(
                key=tier.key,
                path=source_path,
                label=DOWNSCALE_LABEL_TEMPLATE.format(tier=tier.display),
                detail=resolution_text(out_width, out_height),
                tooltip=DOWNSCALE_TIP_TEMPLATE.format(tier=tier.display),
                tier_key=tier.key,
            )
        )
    return tuple(targets)


def default_target_key(targets: tuple[FrameSource, ...], candidate_tier: str | None) -> str | None:
    """기본으로 열 비교 대상 키. 결과가 있으면 결과, 없으면 화질 분석 축소 후보, 그다음 첫 규격"""
    if not targets:
        return None
    if targets[0].key == RESULT_TARGET_KEY:
        return RESULT_TARGET_KEY
    if candidate_tier is not None and any(item.key == candidate_tier for item in targets):
        return candidate_tier
    return targets[0].key


def resolve_compare_plan(
    *,
    source_path: str,
    width: int,
    height: int,
    size_bytes: int,
    result: FrameSource | None = None,
    candidate_tier: str | None = None,
) -> ComparePlan:
    """원본과 비교 대상 목록, 기본 선택, 비교 불가 사유를 계산

    - 원본 파일이 없으면 축소 비교도 할 수 없으므로 사유를 남기고 대상을 비움
    - 결과가 있으면 목록 맨 앞에 두고, 축소 비교 항목은 항상 뒤에 붙임
    """
    original = FrameSource(
        key=ORIGINAL_LABEL,
        path=source_path,
        label=ORIGINAL_LABEL,
        detail=source_detail(width, height, size_bytes),
    )
    if not is_readable_file(source_path):
        return ComparePlan(original=original, targets=(), default_key=None, reason=MISSING_ORIGINAL_REASON)
    downscale = build_downscale_targets(source_path, width, height)
    targets = ((result,) if result is not None else ()) + downscale
    if not targets:
        return ComparePlan(original=original, targets=(), default_key=None, reason=NO_TARGET_REASON)
    return ComparePlan(
        original=original,
        targets=targets,
        default_key=default_target_key(targets, candidate_tier),
    )


def analysis_score_text(tier_display: str, vmaf: float | None, ssim: float | None) -> str:
    """캐시된 화질 분석 점수 한 줄 문구. 잰 지표만 나열하고 없으면 빈 문자열"""
    parts: list[str] = []
    if vmaf is not None:
        parts.append(f"VMAF {vmaf:.1f}")
    if ssim is not None:
        parts.append(f"SSIM {ssim:.4f}")
    if not parts:
        return ""
    return f"화질 분석 {tier_display}: {', '.join(parts)}"


def roundtrip_filter(width: int, height: int, tier: ResolutionTier) -> str:
    """규격으로 줄였다가 원본 크기로 되돌리는 FFmpeg 필터 문자열

    - 회전은 FFmpeg가 자동 적용하므로 width, height는 회전 반영 뒤 크기를 받음
    """
    out_width, out_height = calc_scaled_dimensions(width, height, tier)
    return (
        f"scale={out_width}:{out_height}:flags=bicubic,"
        f"scale={width}:{height}:flags=bicubic"
    )


def _frame_input_args(ffmpeg_executable: str, source_path: str, time_sec: float) -> list[str]:
    """지정 시점부터 한 프레임을 읽는 공통 입력 인자"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(0.0, time_sec):.{TIME_DECIMALS}f}",
        "-i",
        source_path,
    ]


def _frame_output_args(output_path: str) -> list[str]:
    """무압축 BMP 한 장을 쓰는 공통 출력 인자"""
    return ["-frames:v", "1", "-an", "-sn", "-dn", "-c:v", "bmp", "-f", "image2", output_path]


def build_frame_extract_command(
    *,
    ffmpeg_executable: str,
    source: FrameSource,
    time_sec: float,
    output_path: str,
    frame_width: int,
    frame_height: int,
) -> list[str]:
    """지정 시점의 정확히 1프레임을 무압축 BMP 파일로 추출하는 FFmpeg 인자 목록

    - 축소 비교는 규격으로 줄였다 원본 크기로 되돌린 프레임을 씀
    - 무압축 BMP는 4K에서도 압축과 해제 지연이 없어 정지 화면이 빠르게 표시됨
    - 파이프 대신 임시 파일로 받아 큰 프레임에서도 교착이 생기지 않게 함
    """
    args = _frame_input_args(ffmpeg_executable, source.path, time_sec)
    tier = source.tier
    if tier is not None:
        args += ["-vf", roundtrip_filter(frame_width, frame_height, tier)]
    args += _frame_output_args(output_path)
    return args


def build_downscale_pair_command(
    *,
    ffmpeg_executable: str,
    source: FrameSource,
    tier: ResolutionTier,
    time_sec: float,
    original_output: str,
    compared_output: str,
    frame_width: int,
    frame_height: int,
) -> list[str]:
    """축소 비교의 원본과 왕복 축소 프레임을 한 번의 디코딩으로 쓰는 FFmpeg 인자 목록

    - split 필터로 같은 디코딩 프레임을 두 출력에 나눠 같은 파일을 두 번 읽지 않음
    """
    split_filter = (
        f"split=2[base][down];[down]{roundtrip_filter(frame_width, frame_height, tier)}[rt]"
    )
    args = _frame_input_args(ffmpeg_executable, source.path, time_sec)
    args += ["-filter_complex", split_filter]
    args += ["-map", "[base]", *_frame_output_args(original_output)]
    args += ["-map", "[rt]", *_frame_output_args(compared_output)]
    return args


def frame_step_seconds(fps: float) -> float:
    """한 프레임 이동 시간(초). fps를 모르면 1/30초"""
    return 1.0 / fps if fps > 0 else DEFAULT_FRAME_STEP_SEC


def clamp_time_sec(value: float, duration_sec: float) -> float:
    """시점을 [0, 영상 길이] 안으로 제한"""
    return min(max(value, 0.0), max(duration_sec, 0.0))


def shift_time_sec(position_sec: float, delta_sec: float, duration_sec: float) -> float:
    """현재 시점에서 delta_sec만큼 옮긴 시점, 영상 범위 밖으로는 가지 않음"""
    return clamp_time_sec(position_sec + delta_sec, duration_sec)


def representative_times(duration_sec: float, count: int = REPRESENTATIVE_COUNT) -> tuple[float, ...]:
    """영상의 10%부터 90%까지 count개 대표 시점(초)"""
    if duration_sec <= 0 or count <= 0:
        return ()
    if count == 1:
        return (clamp_time_sec(duration_sec * SAMPLE_RANGE_START_RATIO, duration_sec),)
    span = SAMPLE_RANGE_END_RATIO - SAMPLE_RANGE_START_RATIO
    return tuple(
        clamp_time_sec(
            duration_sec * (SAMPLE_RANGE_START_RATIO + span * index / (count - 1)),
            duration_sec,
        )
        for index in range(count)
    )


def format_timecode(seconds: float) -> str:
    """시점을 분:초.밀리초로 표기, 한 시간 이상이면 시를 앞에 붙임"""
    total_ms = max(0, round(seconds * _MS_PER_SECOND))
    hours, rest = divmod(total_ms, _SECONDS_PER_HOUR * _MS_PER_SECOND)
    minutes, rest = divmod(rest, _SECONDS_PER_MINUTE * _MS_PER_SECOND)
    secs, millis = divmod(rest, _MS_PER_SECOND)
    body = f"{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours}:{body}" if hours else body


def format_percent(scale: float) -> str:
    """배율을 퍼센트 정수 문구로 표기"""
    return f"{round(scale * 100)}%"


def fit_scale(view_width: float, view_height: float, image_width: int, image_height: int) -> float:
    """영상 전체가 보이는 맞춤 배율, 크기를 모르면 1"""
    if view_width <= 0 or view_height <= 0 or image_width <= 0 or image_height <= 0:
        return 1.0
    return min(view_width / image_width, view_height / image_height)


def fit_offset(view_size: float, scaled_size: float) -> float:
    """맞춤에서 영상을 가운데 두는 오프셋"""
    return (view_size - scaled_size) / 2.0


def clamp_offset(offset: float, scaled_size: float, view_size: float) -> float:
    """영상이 화면보다 작으면 가운데, 크면 여백이 남지 않게 오프셋 제한"""
    if scaled_size <= view_size:
        return fit_offset(view_size, scaled_size)
    return min(0.0, max(view_size - scaled_size, offset))


def clamp_scale(scale: float) -> float:
    """배율을 허용 범위 안으로 제한"""
    return min(max(scale, MIN_SCALE), MAX_SCALE)


def zoom_preset_scale(
    preset: str, view_width: float, view_height: float, image_width: int, image_height: int
) -> float:
    """배율 버튼(맞춤, 1:1, 200%, 400%)의 배율"""
    if preset == ZOOM_FIT:
        return clamp_scale(fit_scale(view_width, view_height, image_width, image_height))
    return clamp_scale(ZOOM_PRESET_SCALES.get(preset, 1.0))


def step_zoom(scale: float, direction: int) -> float:
    """+/- 단계 확대, 축소 배율. direction이 0보다 크면 확대"""
    factor = ZOOM_STEP_FACTOR if direction > 0 else 1.0 / ZOOM_STEP_FACTOR
    return clamp_scale(scale * factor)


@dataclass(frozen=True, slots=True)
class ViewTransform:
    """영상 표시 배율과 좌상단 오프셋"""

    scale: float
    offset_x: float
    offset_y: float


def clamp_transform(
    transform: ViewTransform,
    image_width: int,
    image_height: int,
    view_width: float,
    view_height: float,
) -> ViewTransform:
    """배율과 오프셋을 화면 범위에 맞춤"""
    scale = clamp_scale(transform.scale)
    return ViewTransform(
        scale=scale,
        offset_x=clamp_offset(transform.offset_x, image_width * scale, view_width),
        offset_y=clamp_offset(transform.offset_y, image_height * scale, view_height),
    )


def zoom_around(
    transform: ViewTransform,
    factor: float,
    *,
    cursor_x: float,
    cursor_y: float,
    image_width: int,
    image_height: int,
    view_width: float,
    view_height: float,
) -> ViewTransform:
    """커서 아래 영상 지점이 같은 자리에 남도록 배율을 바꿈"""
    scale = transform.scale if transform.scale > 0 else 1.0
    image_x = (cursor_x - transform.offset_x) / scale
    image_y = (cursor_y - transform.offset_y) / scale
    target = clamp_scale(scale * factor)
    moved = ViewTransform(
        scale=target,
        offset_x=cursor_x - image_x * target,
        offset_y=cursor_y - image_y * target,
    )
    return clamp_transform(moved, image_width, image_height, view_width, view_height)


def fit_transform(
    view_width: float, view_height: float, image_width: int, image_height: int
) -> ViewTransform:
    """영상 전체가 보이도록 가운데 맞춘 변환"""
    scale = clamp_scale(fit_scale(view_width, view_height, image_width, image_height))
    return ViewTransform(
        scale=scale,
        offset_x=fit_offset(view_width, image_width * scale),
        offset_y=fit_offset(view_height, image_height * scale),
    )


def split_ratio_for_x(x: float, width: float) -> float:
    """분할선 위치 비율을 0.05~0.95 안으로 제한"""
    if width <= 0:
        return (SPLIT_MIN_RATIO + SPLIT_MAX_RATIO) / 2.0
    return min(max(x / width, SPLIT_MIN_RATIO), SPLIT_MAX_RATIO)


def wheel_zoom_factor(delta: float) -> float:
    """마우스 휠 이동량을 배율 계수로 바꿈. 위로 굴리면(Qt 양수) 확대"""
    return math.exp(delta * 0.0015)
