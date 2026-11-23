"""계층형 메타데이터 기반 분석 설정 스키마와 분류기 런타임 설정 정의 모듈"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, TypeVar

from src.core.codec_groups import find_codec_group, get_static_codec_weight
from src.core.encode_tag import (
    DEFAULT_ENCODE_TAG_NAME,
    ENCODE_TAG_MODE_VISIBLE,
    VALID_ENCODE_TAG_MODES,
    normalize_tag_name,
)
from src.core.encoder_specs import DEFAULT_ENCODER_NAME, ENCODER_SPECS
from src.core.quality_target import (
    BASELINE_CQ_DEFAULT,
    BASELINE_CQ_MAX,
    BASELINE_CQ_MIN,
    clamp_baseline_cq,
)
from src.core.resolution_tiers import RESOLUTION_TIERS

# - 최대 해상도 설정의 "제한 없음" 값으로, 규격 키와 겹치지 않는 문자열
NO_MAX_RESOLUTION: str = "none"
VALID_MAX_RESOLUTION_TIERS: tuple[str, ...] = (NO_MAX_RESOLUTION, *(t.key for t in RESOLUTION_TIERS))

DEFAULT_FPS_TRIM_WHOLE: bool = True
DEFAULT_SIZE_UNIT: str = "auto"
VALID_SIZE_UNITS: tuple[str, ...] = ("mb", "gb", "auto")
DEFAULT_FILENAME_ALIGN: str = "left"
VALID_FILENAME_ALIGNS: tuple[str, ...] = ("left", "right")

MAX_RECENT_DIRS: int = 10

# - 화질 분석 방식: 구간 길이와 키프레임 시크 방식이 다르고, 방식마다 캐시 버전을 달리해 전환 즉시 다시 잼
FRAME_ANALYSIS_MODE_FAST: str = "fast"
FRAME_ANALYSIS_MODE_BALANCED: str = "balanced"
FRAME_ANALYSIS_MODE_PRECISE: str = "precise"
DEFAULT_FRAME_ANALYSIS_MODE: str = FRAME_ANALYSIS_MODE_BALANCED
VALID_FRAME_ANALYSIS_MODES: tuple[str, ...] = (
    FRAME_ANALYSIS_MODE_FAST,
    FRAME_ANALYSIS_MODE_BALANCED,
    FRAME_ANALYSIS_MODE_PRECISE,
)
FRAME_ANALYSIS_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("빠름", FRAME_ANALYSIS_MODE_FAST),
    ("균형 (기본)", FRAME_ANALYSIS_MODE_BALANCED),
    ("정밀", FRAME_ANALYSIS_MODE_PRECISE),
)


def setting_meta(
    comment: str,
    bounds: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """설정 필드 메타데이터 사전(comment, bounds) 생성"""
    data: dict[str, Any] = {"comment": comment}
    if bounds is not None:
        data["bounds"] = bounds
    return data


@dataclass(slots=True)
class PathsConfig:
    """최근 작업 폴더 이력 설정"""

    recent_dirs: list[str] = field(
        default_factory=list,
        metadata=setting_meta(f"최근 작업 폴더 목록 (최대 {MAX_RECENT_DIRS}개)"),
    )


@dataclass(slots=True)
class ThresholdsConfig:
    """화질 등급 및 키프레임 허용 상한 판별 임계값 설정"""

    over_bitrate_ratio: float = field(
        default=1.2,
        metadata=setting_meta("최적 비트레이트 대비 과화질 판별 배수", (1.01, 5.00)),
    )
    low_bitrate_ratio: float = field(
        default=0.8,
        metadata=setting_meta("최적 비트레이트 대비 저화질 판별 배수", (0.10, 0.99)),
    )
    keyframe_interval_sec: float = field(
        default=2.2,
        metadata=setting_meta("허용 최대 키프레임 간격 (초)", (0.5, 30.0)),
    )
    keyframe_1s_increase_rate: float = field(
        default=0.06,
        metadata=setting_meta("1초 키프레임 재인코딩 시 예상 용량 증가율", (0.00, 0.50)),
    )




DEFAULT_ENCODE_CODEC: str = DEFAULT_ENCODER_NAME
DEFAULT_ENCODE_SPEED_FACTOR: float = 1.0
# - 인코더 정의는 encoder_specs.ENCODER_SPECS가 단일 기준이며, 여기서는 사용 가능한 항목만 선택지로 파생
ENCODE_CODEC_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (spec.label, spec.name) for spec in ENCODER_SPECS if spec.available
)
VALID_ENCODE_CODECS: tuple[str, ...] = tuple(code for _, code in ENCODE_CODEC_CHOICES)


SAVINGS_BASIS_PLAYBACK: str = "playback"
SAVINGS_BASIS_ENCODE: str = "encode"
VALID_SAVINGS_BASES: tuple[str, ...] = (SAVINGS_BASIS_PLAYBACK, SAVINGS_BASIS_ENCODE)
SAVINGS_BASIS_CHOICES: tuple[tuple[str, str], ...] = (
    ("재생 시간 기준 (기본)", SAVINGS_BASIS_PLAYBACK),
    ("예상 인코딩 시간 기준", SAVINGS_BASIS_ENCODE),
)


@dataclass(slots=True)
class SavingsConfig:
    """과화질 재인코딩 대상의 절약량, 분당 절약량, 대용량 예외 기준 설정"""

    min_saved_mb: float = field(
        default=5.0,
        metadata=setting_meta("개별 영상 최소 절약량 하한 (MB)", (0.0, 100.0)),
    )
    min_mb_per_min: float = field(
        default=3.0,
        metadata=setting_meta("분당 최소 절약량 (MB/분)", (0.0, 50.0)),
    )
    exempt_saved_mb: float = field(
        default=50.0,
        metadata=setting_meta("대용량 절약 예외 허용 (MB, 0=비활성)", (0.0, 1000.0)),
    )
    selection_basis: str = field(
        default=SAVINGS_BASIS_PLAYBACK,
        metadata=setting_meta("절약 선별 기준 (playback: 재생 시간 기준, encode: 예상 인코딩 시간 기준)"),
    )


@dataclass(slots=True)
class EncodeConfig:
    """목표 인코딩 코덱과 기준 배속 설정, 예상 시간 산출에 사용"""

    baseline_cq: float = field(
        default=BASELINE_CQ_DEFAULT,
        metadata=setting_meta(
            "화질 기준값 (낮을수록 고화질, 기본 22.0)",
            (BASELINE_CQ_MIN, BASELINE_CQ_MAX),
        ),
    )
    encode_codec: str = field(
        default=DEFAULT_ENCODE_CODEC,
        metadata=setting_meta("목표 인코딩 코덱, 방식 (추천 배속 산출 및 예상 시간 안내 기준)"),
    )
    encode_speed_factor: float = field(
        default=DEFAULT_ENCODE_SPEED_FACTOR,
        metadata=setting_meta(
            "1080p 30fps 기준 예상 인코딩 배속 (0.1~50.0, NVENC 3.0~8.0 / CPU 0.8~2.0)",
            (0.1, 50.0),
        ),
    )
    force_legacy_compat: bool = field(
        default=False,
        metadata=setting_meta(
            "하드웨어 디코딩 미지원 구형 코덱을 절약 선별 없이 호환 표준 코덱으로 강제 재인코딩"
        ),
    )
    max_resolution_tier: str = field(
        default=NO_MAX_RESOLUTION,
        metadata=setting_meta("최대 해상도 (제한 없음 / 4K ... 360p)"),
    )
    auto_downscale_by_bitrate: bool = field(
        default=False,
        metadata=setting_meta("비트레이트 맞춤 축소 (저화질 고해상도 영상을 적정 해상도로 줄임)"),
    )
    frame_analysis_mode: str = field(
        default=DEFAULT_FRAME_ANALYSIS_MODE,
        metadata=setting_meta("화질 분석 방식 (fast: 빠름, balanced: 균형, precise: 정밀)"),
    )


@dataclass(slots=True)
class CodecWeightsConfig:
    """H.264(1.0) 대비 코덱 그룹별 상대 비트레이트 가중치 설정"""

    av1: float = field(
        default=0.65,
        metadata=setting_meta("AV1 상대 비트레이트 비율 (H.264=1.0 기준)", (0.10, 3.00)),
    )
    hevc_vp9: float = field(
        default=0.80,
        metadata=setting_meta("HEVC / VP9 상대 비트레이트 비율", (0.10, 3.00)),
    )
    h264: float = field(
        default=1.00,
        metadata=setting_meta("H.264 표준 기준 가중치", (0.10, 3.00)),
    )
    legacy: float = field(
        default=1.30,
        metadata=setting_meta("구형 코덱 상대 비트레이트 비율", (0.10, 5.00)),
    )

    def get_weight(self, codec: str) -> float:
        """코덱 명칭을 정규화해 상대 비트레이트 가중치를 O(1)로 반환

        미등록 코덱에는 기본 가중치 1.00 적용
        """
        key = (codec or "h264").lower().strip()
        group = find_codec_group(key)
        if group == "av1":
            return self.av1
        if group == "hevc_vp9":
            return self.hevc_vp9
        if group == "h264":
            return self.h264
        if group == "legacy":
            return self.legacy
        return get_static_codec_weight(key)


@dataclass(slots=True)
class BitrateModelConfig:
    """해상도, FPS 비선형 스케일링 기준 비트레이트 모델 설정"""

    base_width: int = field(
        default=1280,
        metadata=setting_meta("기준 해상도 너비 (px)", (320, 7680)),
    )
    base_height: int = field(
        default=720,
        metadata=setting_meta("기준 해상도 높이 (px)", (240, 4320)),
    )
    base_target_kbps: int = field(
        default=1500,
        metadata=setting_meta("기준 해상도, FPS 목표 비트레이트 (kbps)", (100, 50000)),
    )
    base_fps: float = field(
        default=30.0,
        metadata=setting_meta("기준 프레임레이트 (fps)", (1.0, 240.0)),
    )
    area_scaling_exponent: float = field(
        default=0.82,
        metadata=setting_meta("해상도 면적 스케일링 지수", (0.10, 1.50)),
    )
    fps_scaling_exponent: float = field(
        default=0.60,
        metadata=setting_meta("프레임레이트 보정 지수", (0.10, 1.50)),
    )


@dataclass(slots=True)
class AnalysisConfig:
    """화질, 절약, 인코딩, 코덱, 비트레이트 분석 기준 통합 컨테이너"""

    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    savings: SavingsConfig = field(default_factory=SavingsConfig)
    encode: EncodeConfig = field(default_factory=EncodeConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)

    def copy(self) -> AnalysisConfig:
        """모든 하위 분석 설정의 독립 복사본 반환"""
        return AnalysisConfig(
            thresholds=replace(self.thresholds),
            savings=replace(self.savings),
            encode=replace(self.encode),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
        )


@dataclass(slots=True)
class DisplayConfig:
    """CUI, GUI 공통 테이블 열별 소수점 정밀도 및 단위 표기 설정"""

    fps_decimals: int = field(
        default=2,
        metadata=setting_meta("FPS 소수점 자릿수", (0, 4)),
    )
    fps_trim_whole: bool = field(
        default=DEFAULT_FPS_TRIM_WHOLE,
        metadata=setting_meta("FPS 값이 정수(.00)일 때 소수점 생략 여부"),
    )
    duration_decimals: int = field(
        default=1,
        metadata=setting_meta("길이(초) 소수점 자릿수 (CUI)", (0, 3)),
    )
    ratio_decimals: int = field(
        default=2,
        metadata=setting_meta("BR 비율 소수점 자릿수", (0, 4)),
    )
    keyframe_decimals: int = field(
        default=2,
        metadata=setting_meta("키프레임 간격 소수점 자릿수", (0, 4)),
    )
    size_decimals: int = field(
        default=2,
        metadata=setting_meta("현재 용량 소수점 자릿수", (0, 4)),
    )
    after_size_decimals: int = field(
        default=2,
        metadata=setting_meta("예상 용량 소수점 자릿수", (0, 4)),
    )
    saved_size_decimals: int = field(
        default=2,
        metadata=setting_meta("예상 절약 소수점 자릿수", (0, 4)),
    )
    size_unit: str = field(
        default=DEFAULT_SIZE_UNIT,
        metadata=setting_meta('용량 표기 단위: "mb" | "gb" | "auto"'),
    )
    filename_align: str = field(
        default=DEFAULT_FILENAME_ALIGN,
        metadata=setting_meta('테이블 파일 이름 정렬: "left" | "right"'),
    )


@dataclass(slots=True)
class CuiConfig:
    """CUI(노트북, 터미널) 표 출력 서식 설정"""

    filename_maxlen: int = field(
        default=80,
        metadata=setting_meta("콘솔 표 파일명 최대 표시 길이 (자)", (20, 300)),
    )



RESULT_POLICY_ARCHIVE: str = "archive_original"
RESULT_POLICY_SEPARATE: str = "separate_output"
RESULT_POLICY_REPLACE: str = "replace_original"
VALID_RESULT_POLICIES: tuple[str, ...] = (
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_SEPARATE,
    RESULT_POLICY_REPLACE,
)
CONTAINER_POLICY_KEEP: str = "keep"
CONTAINER_POLICY_MP4: str = "mp4"
VALID_CONTAINER_POLICIES: tuple[str, ...] = (CONTAINER_POLICY_KEEP, CONTAINER_POLICY_MP4)
VALID_AFTER_COMPLETE_ACTIONS: tuple[str, ...] = ("none", "sleep", "shutdown", "exit")
VALID_PROCESS_PRIORITIES: tuple[str, ...] = (
    "high",
    "above_normal",
    "normal",
    "below_normal",
    "low",
)
NAME_CONFLICT_SKIP: str = "skip"
NAME_CONFLICT_NUMBER: str = "number"
VALID_NAME_CONFLICT_POLICIES: tuple[str, ...] = (NAME_CONFLICT_SKIP, NAME_CONFLICT_NUMBER)
DEFAULT_PROCESS_PRIORITY: str = "normal"
DEFAULT_NAME_CONFLICT_POLICY: str = NAME_CONFLICT_SKIP
# - 동시 재인코딩 수 설정 입력 상한. 장치 사양과 무관한 화면 범위 값이며 자동 규칙은 encode_concurrency 참조
CONCURRENT_JOBS_MAX: int = 4


@dataclass(slots=True)
class ExecutionConfig:
    """재인코딩 실행 기반 설정(FFmpeg 경로, 결과 파일 처리, 동시성, 절전, 완료 후 동작, 재인코딩 태그)"""

    ffmpeg_path: str = field(
        default="",
        metadata=setting_meta("FFmpeg 실행 파일 경로 (비우면 PATH에서 찾음)"),
    )
    ffprobe_path: str = field(
        default="",
        metadata=setting_meta("FFprobe 실행 파일 경로 (비우면 PATH에서 찾음)"),
    )
    result_policy: str = field(
        default=RESULT_POLICY_ARCHIVE,
        metadata=setting_meta(
            '결과 파일 처리: "archive_original" | "separate_output" | "replace_original"'
        ),
    )
    archive_dirname: str = field(
        default="_원본보관",
        metadata=setting_meta("원본 보관 폴더 이름"),
    )
    output_dirname: str = field(
        default="_재인코딩결과",
        metadata=setting_meta("별도 출력 폴더 이름"),
    )
    container_policy: str = field(
        default=CONTAINER_POLICY_KEEP,
        metadata=setting_meta('출력 형식: "keep"(원본 형식 유지) | "mp4"(항상 MP4)'),
    )
    concurrent_jobs: int = field(
        default=0,
        metadata=setting_meta(
            "동시 재인코딩 수 (0: 자동)", (0, CONCURRENT_JOBS_MAX)
        ),
    )
    prevent_sleep: bool = field(
        default=True,
        metadata=setting_meta("인코딩 중 시스템 절전 방지 (화면 끄기는 허용)"),
    )
    after_complete: str = field(
        default="none",
        metadata=setting_meta('인코딩 완료 후 동작: "none" | "sleep" | "shutdown" | "exit"'),
    )
    process_priority: str = field(
        default=DEFAULT_PROCESS_PRIORITY,
        metadata=setting_meta(
            '인코딩 프로세스 우선순위: "high" | "above_normal" | "normal" | "below_normal" | "low"'
        ),
    )
    name_conflict: str = field(
        default=DEFAULT_NAME_CONFLICT_POLICY,
        metadata=setting_meta('같은 이름의 파일이 있을 때: "skip"(건너뜀) | "number"(번호 붙여 저장)'),
    )
    encode_tag_mode: str = field(
        default=ENCODE_TAG_MODE_VISIBLE,
        metadata=setting_meta(
            '재인코딩 태그 기록: "visible"(태그와 상세 값) | "hidden"(상세 값만) | "off"(기록 안 함)'
        ),
    )
    encode_tag_name: str = field(
        default=DEFAULT_ENCODE_TAG_NAME,
        metadata=setting_meta("재인코딩 태그 이름 (64자 이하, 세미콜론 사용 불가)"),
    )


DEFAULT_PATHS = PathsConfig()
DEFAULT_THRESHOLDS = ThresholdsConfig()
DEFAULT_SAVINGS = SavingsConfig()
DEFAULT_ENCODE = EncodeConfig()
DEFAULT_CODEC_WEIGHTS_CONFIG = CodecWeightsConfig()
DEFAULT_BITRATE_MODEL = BitrateModelConfig()
DEFAULT_DISPLAY = DisplayConfig()
DEFAULT_CUI = CuiConfig()

# 분류기 핵심 세부 설정 항목 총 개수 (analysis 24 + display 10 + cui 1 + execution 13)
CORE_SETTING_COUNT: int = (
    len(fields(ThresholdsConfig))
    + len(fields(SavingsConfig))
    + len(fields(EncodeConfig))
    + len(fields(CodecWeightsConfig))
    + len(fields(BitrateModelConfig))
    + len(fields(DisplayConfig))
    + len(fields(CuiConfig))
    + len(fields(ExecutionConfig))
)

_SETTING_BOUND_CLASSES: tuple[type[Any], ...] = (
    ThresholdsConfig,
    SavingsConfig,
    EncodeConfig,
    CodecWeightsConfig,
    BitrateModelConfig,
    DisplayConfig,
    CuiConfig,
    ExecutionConfig,
)


def collect_setting_bounds() -> dict[str, tuple[float, float]]:
    """데이터클래스 필드 메타데이터에서 수치 입력 범위(bounds) 사전 수집"""
    bounds: dict[str, tuple[float, float]] = {}
    for cls in _SETTING_BOUND_CLASSES:
        for f in fields(cls):
            raw_bounds = f.metadata.get("bounds")
            if isinstance(raw_bounds, tuple) and len(raw_bounds) == 2:
                bounds[f.name] = (raw_bounds[0], raw_bounds[1])
    return bounds


_T = TypeVar("_T")


def _coerce_typed_field(mapping: Mapping[str, Any], key: str, default: Any) -> Any:
    raw = mapping.get(key)
    if raw is None:
        return default
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        text_val = str(raw).strip().lower()
        if text_val in ("true", "1", "yes", "on"):
            return True
        if text_val in ("false", "0", "no", "off"):
            return False
        return default
    if isinstance(default, int):
        try:
            return int(float(str(raw).strip()))
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return round(float(str(raw).strip()), 4)
        except ValueError:
            return default
    text = str(raw).strip().strip("'").strip('"')
    return text if text else default


def _parse_encode_section(raw: Mapping[str, Any]) -> EncodeConfig:
    parsed = parse_typed_section(raw, DEFAULT_ENCODE, EncodeConfig)
    parsed.baseline_cq = clamp_baseline_cq(parsed.baseline_cq)
    if parsed.encode_codec not in VALID_ENCODE_CODECS:
        parsed.encode_codec = DEFAULT_ENCODE_CODEC
    if parsed.max_resolution_tier not in VALID_MAX_RESOLUTION_TIERS:
        parsed.max_resolution_tier = NO_MAX_RESOLUTION
    if parsed.frame_analysis_mode not in VALID_FRAME_ANALYSIS_MODES:
        parsed.frame_analysis_mode = DEFAULT_FRAME_ANALYSIS_MODE
    return parsed


def _parse_savings_section(raw: Mapping[str, Any]) -> SavingsConfig:
    parsed = parse_typed_section(raw, DEFAULT_SAVINGS, SavingsConfig)
    if parsed.selection_basis not in VALID_SAVINGS_BASES:
        parsed.selection_basis = SAVINGS_BASIS_PLAYBACK
    return parsed


def _with_legacy_key(raw: Mapping[str, Any], key: str, legacy_key: str) -> dict[str, Any]:
    """이전 키 이름의 값을 새 키가 없을 때만 승계해 기존 설정 파일 보존"""
    merged = dict(raw)
    if key not in merged and legacy_key in merged:
        merged[key] = merged[legacy_key]
    return merged


def _parse_execution_section(raw: Mapping[str, Any]) -> ExecutionConfig:
    parsed = parse_typed_section(raw, ExecutionConfig(), ExecutionConfig)
    if parsed.result_policy not in VALID_RESULT_POLICIES:
        parsed.result_policy = RESULT_POLICY_ARCHIVE
    if parsed.container_policy not in VALID_CONTAINER_POLICIES:
        parsed.container_policy = CONTAINER_POLICY_KEEP
    if parsed.after_complete not in VALID_AFTER_COMPLETE_ACTIONS:
        parsed.after_complete = "none"
    if parsed.process_priority not in VALID_PROCESS_PRIORITIES:
        parsed.process_priority = DEFAULT_PROCESS_PRIORITY
    if parsed.name_conflict not in VALID_NAME_CONFLICT_POLICIES:
        parsed.name_conflict = DEFAULT_NAME_CONFLICT_POLICY
    if parsed.encode_tag_mode not in VALID_ENCODE_TAG_MODES:
        parsed.encode_tag_mode = ENCODE_TAG_MODE_VISIBLE
    try:
        parsed.encode_tag_name = normalize_tag_name(parsed.encode_tag_name)
    except ValueError:
        parsed.encode_tag_name = DEFAULT_ENCODE_TAG_NAME
    parsed.concurrent_jobs = min(max(parsed.concurrent_jobs, 0), CONCURRENT_JOBS_MAX)
    defaults = ExecutionConfig()
    for attr in ("archive_dirname", "output_dirname"):
        default = getattr(defaults, attr)
        name = getattr(parsed, attr).strip()
        if not name or any(sep in name for sep in ("/", "\\", ":")) or name in (".", ".."):
            setattr(parsed, attr, default)
    return parsed


def _parse_display_section(raw: Mapping[str, Any]) -> DisplayConfig:
    parsed = parse_typed_section(raw, DEFAULT_DISPLAY, DisplayConfig)
    if parsed.size_unit not in VALID_SIZE_UNITS:
        parsed.size_unit = DEFAULT_SIZE_UNIT
    if parsed.filename_align not in VALID_FILENAME_ALIGNS:
        parsed.filename_align = DEFAULT_FILENAME_ALIGN
    return parsed


def parse_typed_section(raw: Mapping[str, Any], default_obj: _T, cls: type[_T]) -> _T:
    """매핑 딕셔너리에서 필드 타입과 기본값을 맞춰 데이터클래스 섹션을 생성"""
    kwargs: dict[str, Any] = {
        f.name: _coerce_typed_field(raw, f.name, getattr(default_obj, f.name))
        for f in fields(cls)  # type: ignore[arg-type]
    }
    return cls(**kwargs)


@dataclass(slots=True)
class ClassifierConfig:
    """분석 기준, 공통 표 서식, CUI 출력을 계층 구조로 보관하는 분류기 런타임 설정"""

    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    savings: SavingsConfig = field(default_factory=SavingsConfig)
    encode: EncodeConfig = field(default_factory=EncodeConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    cui: CuiConfig = field(default_factory=CuiConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def get_codec_weight(self, codec: str) -> float:
        """코덱 명칭을 정규화해 H.264 대비 상대 비트레이트 가중치 반환"""
        return self.codec_weights.get_weight(codec)

    def copy(self) -> ClassifierConfig:
        """하위 설정 데이터클래스를 모두 복제한 독립 사본 반환"""
        return ClassifierConfig(
            thresholds=replace(self.thresholds),
            savings=replace(self.savings),
            encode=replace(self.encode),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
            display=replace(self.display),
            cui=replace(self.cui),
            execution=replace(self.execution),
        )

    def to_dict(self) -> dict[str, Any]:
        """통합 JSON 스키마(analysis, display, cui)와 동일한 계층 사전으로 변환"""
        return {
            "analysis": {
                "thresholds": asdict(self.thresholds),
                "savings": asdict(self.savings),
                "encode": asdict(self.encode),
                "codec_weights": asdict(self.codec_weights),
                "bitrate_model": asdict(self.bitrate_model),
            },
            "display": asdict(self.display),
            "cui": asdict(self.cui),
            "execution": asdict(self.execution),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ClassifierConfig:
        """계층형 설정 사전으로부터 기본값을 보완해 ClassifierConfig 생성"""
        analysis_raw = values.get("analysis")
        display_raw = values.get("display")
        cui_raw = values.get("cui")
        execution_raw = values.get("execution")

        analysis = analysis_raw if isinstance(analysis_raw, Mapping) else {}
        display_map = display_raw if isinstance(display_raw, Mapping) else {}
        cui_map = cui_raw if isinstance(cui_raw, Mapping) else {}

        th_raw = analysis.get("thresholds")
        # 이전 저장 형식의 efficiency 섹션은 신규 섹션이 없을 때만 승계
        legacy_eff_raw = analysis.get("efficiency")
        savings_raw = analysis.get("savings", legacy_eff_raw)
        encode_raw = analysis.get("encode", legacy_eff_raw)
        cw_raw = analysis.get("codec_weights")
        bm_raw = analysis.get("bitrate_model")

        return cls(
            thresholds=parse_typed_section(
                _with_legacy_key(
                    th_raw if isinstance(th_raw, Mapping) else {},
                    "keyframe_1s_increase_rate",
                    "keyframe_1s_overhead_rate",
                ),
                DEFAULT_THRESHOLDS,
                ThresholdsConfig,
            ),
            savings=_parse_savings_section(
                savings_raw if isinstance(savings_raw, Mapping) else {},
            ),
            encode=_parse_encode_section(
                encode_raw if isinstance(encode_raw, Mapping) else {}
            ),
            codec_weights=parse_typed_section(
                cw_raw if isinstance(cw_raw, Mapping) else {},
                DEFAULT_CODEC_WEIGHTS_CONFIG,
                CodecWeightsConfig,
            ),
            bitrate_model=parse_typed_section(
                bm_raw if isinstance(bm_raw, Mapping) else {},
                DEFAULT_BITRATE_MODEL,
                BitrateModelConfig,
            ),
            display=_parse_display_section(display_map),
            cui=parse_typed_section(cui_map, DEFAULT_CUI, CuiConfig),
            execution=_parse_execution_section(
                execution_raw if isinstance(execution_raw, Mapping) else {}
            ),
        )


def merge_tuner_criteria(
    base: ClassifierConfig,
    thresholds: ThresholdsConfig,
    savings: SavingsConfig,
) -> ClassifierConfig:
    """판별 기준 튜너가 편집하는 6개 판별 기준만 기존 설정에 병합"""
    return replace(
        base,
        thresholds=replace(
            base.thresholds,
            over_bitrate_ratio=thresholds.over_bitrate_ratio,
            low_bitrate_ratio=thresholds.low_bitrate_ratio,
            keyframe_interval_sec=thresholds.keyframe_interval_sec,
        ),
        savings=replace(
            base.savings,
            min_saved_mb=savings.min_saved_mb,
            min_mb_per_min=savings.min_mb_per_min,
            exempt_saved_mb=savings.exempt_saved_mb,
        ),
    )
