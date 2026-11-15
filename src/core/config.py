"""작업 경로(data/rootdir.txt) 입출력 및 계층형 메타데이터 기반 설정 스키마 정의 모듈"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, TypeVar

from src.core.exceptions import (
    DomainFileNotFoundError,
    DomainRuntimeError,
    ErrorCode,
)

# - H.264(1.00)와 동등한 체감 화질을 유지하기 위해 각 코덱이 필요로 하는 상대 비트레이트 계수(C_codec)
# - 계수 값이 작을수록(예: AV1=0.65, HEVC=0.80) 압축 효율이 높아 적은 비트레이트로 동일 화질을 구현함
# - 계수 값이 클수록(예: MPEG-4=1.30, MPEG-2=1.60) 압축 효율이 낮아 더 높은 비트레이트를 요구함
DEFAULT_CODEC_WEIGHT_FALLBACK: float = 1.00

CODEC_WEIGHT_GROUPS: dict[str, tuple[str, ...]] = {
    "av1": ("av1", "av01"),
    "hevc_vp9": ("hevc", "h265", "hev1", "hvc1", "vp9"),
    "h264": ("h264", "avc", "avc1"),
    "legacy": (
        "vp8",
        "wmv3",
        "vc1",
        "theora",
        "rv40",
        "mpeg4",
        "msmpeg4v3",
        "msmpeg4v2",
        "flv1",
        "h263",
    ),
}

_CODEC_GROUP_BY_ALIAS: dict[str, str] = {
    alias: group
    for group, aliases in CODEC_WEIGHT_GROUPS.items()
    for alias in aliases
}

_STATIC_CODEC_WEIGHTS: dict[str, float] = {
    "mpeg2video": 1.60,
    "mpeg1video": 1.60,
    "wmv2": 1.60,
    "wmv1": 1.60,
    "mjpeg": 2.50,
    "prores": 2.50,
    "dnxhd": 2.50,
}

DATA_DIR_NAME: str = "data"
ROOTDIR_FILENAME: str = "rootdir.txt"

DEFAULT_ROOT_DIR: str = ""
DEFAULT_ROOTDIR_TEMPLATE: str = (
    "# Video Classifier 기본 작업 대상 비디오 폴더 경로 설정 (data/rootdir.txt)\n"
    "# - 첫 번째 비주석 줄에 작업 폴더 경로를 입력하세요 (따옴표·~·환경변수 사용 가능, # 뒤는 주석)\n"
    "# - 경로를 수정·저장하면 Jupyter 커널이나 앱 재시작 없이 즉시 반영됩니다\n"
    "# - 비워 두면 GUI 실행 시 폴더를 직접 선택해 작업할 수 있습니다\n"
    "# 예시:\n"
    "#   D:\\Videos\\Clips\n"  # hygiene: ignore
    '#   "D:\\Videos\\내 동영상"  # 공백 포함 경로·인라인 주석 가능\n'  # hygiene: ignore
)
DEFAULT_DIR_STANDARD_CQ22: str = "_1_표준인코딩_CQ22"
DEFAULT_DIR_LOW_QUALITY_KEYFRAME: str = "_2_저화질_키프레임교정"

DEFAULT_SCAN_MODE: str = "fast"
VALID_SCAN_MODES: tuple[str, ...] = ("fast", "full")

DEFAULT_FILTER_SEARCH: str = ""

DEFAULT_THEME_MODE: str = "system"
VALID_THEME_MODES: tuple[str, ...] = ("system", "dark", "light")

DEFAULT_FPS_TRIM_WHOLE: bool = True
DEFAULT_SIZE_UNIT: str = "mb"
VALID_SIZE_UNITS: tuple[str, ...] = ("mb", "gb", "auto")

MAX_RECENT_DIRS: int = 10


def _meta(
    comment: str,
    bounds: tuple[float, float] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"comment": comment}
    if bounds is not None:
        data["bounds"] = bounds
    return data


@dataclass(slots=True)
class PathsConfig:
    """최근 작업 폴더 이력 및 분류 대상 폴더명 설정"""

    recent_dirs: list[str] = field(
        default_factory=list,
        metadata=_meta(f"최근 작업 폴더 목록 (최대 {MAX_RECENT_DIRS}개)"),
    )
    dir_standard_cq22: str = field(
        default=DEFAULT_DIR_STANDARD_CQ22,
        metadata=_meta("과화질·키프레임 교정 표준 인코딩 폴더명"),
    )
    dir_low_quality_keyframe: str = field(
        default=DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
        metadata=_meta("저화질 키프레임 불량 교정 이동 폴더명"),
    )


@dataclass(slots=True)
class ThresholdsConfig:
    """화질 등급 및 키프레임 허용 상한 판별 임계값 설정"""

    over_bitrate_ratio: float = field(
        default=1.2,
        metadata=_meta("최적 비트레이트 대비 과화질 판별 배수", (1.01, 5.00)),
    )
    low_bitrate_ratio: float = field(
        default=0.8,
        metadata=_meta("최적 비트레이트 대비 저화질 판별 배수", (0.10, 0.99)),
    )
    keyframe_interval_sec: float = field(
        default=2.2,
        metadata=_meta("허용 최대 키프레임 간격 (초)", (0.5, 30.0)),
    )
    keyframe_1s_overhead_rate: float = field(
        default=0.06,
        metadata=_meta("1초 키프레임 교정 시 예상 용량 증가율", (0.00, 0.50)),
    )


DEFAULT_ENCODE_CODEC: str = "hevc_nvenc"
DEFAULT_ENCODE_SPEED_FACTOR: float = 1.0
ENCODE_CODEC_CHOICES: tuple[tuple[str, str], ...] = (
    ("HEVC (NVENC GPU · 샤나 기본)", "hevc_nvenc"),
    ("HEVC (CPU x265 · 샤나 기본)", "libx265"),
    ("H.264 (NVENC GPU)", "h264_nvenc"),
    ("AV1 (NVENC GPU · RTX 40+)", "av1_nvenc"),
    ("HEVC (Intel Quick Sync)", "hevc_qsv"),
    ("HEVC (AMD AMF)", "hevc_amf"),
    ("H.264 (CPU x264)", "libx264"),
    ("AV1 (CPU SVT-AV1)", "libsvtav1"),
)
VALID_ENCODE_CODECS: tuple[str, ...] = tuple(code for _, code in ENCODE_CODEC_CHOICES)
_ENCODE_CODEC_LABELS: dict[str, str] = {code: label for label, code in ENCODE_CODEC_CHOICES}


def get_encode_codec_label(codec: str) -> str:
    """목표 인코딩 코덱 코드에 대응하는 사용자 표시 라벨 반환"""
    return _ENCODE_CODEC_LABELS.get(codec, codec)


@dataclass(slots=True)
class EfficiencyConfig:
    """과화질 영상 인코딩 실익·시간 효율 및 예상 작업 속도 기준 설정"""

    min_saved_mb: float = field(
        default=5.0,
        metadata=_meta("개별 영상 최소 절감량 하한 (MB)", (0.0, 100.0)),
    )
    min_mb_per_min: float = field(
        default=3.0,
        metadata=_meta("재생 1분당 최소 용량 절감 효율 (MB/분)", (0.0, 50.0)),
    )
    exempt_saved_mb: float = field(
        default=50.0,
        metadata=_meta("시간 효율 면제 최소 절대 절감량 (MB, 0=비활성)", (0.0, 1000.0)),
    )
    encode_codec: str = field(
        default=DEFAULT_ENCODE_CODEC,
        metadata=_meta("목표 인코딩 코덱·방식 (추천 배속 산출 및 소요 시간 안내 기준)"),
    )
    encode_speed_factor: float = field(
        default=DEFAULT_ENCODE_SPEED_FACTOR,
        metadata=_meta(
            "1080p 30fps 기준 예상 인코딩 배속 (0.1~50.0, NVENC 3.0~8.0 / CPU 0.8~2.0)",
            (0.1, 50.0),
        ),
    )


@dataclass(slots=True)
class CodecWeightsConfig:
    """H.264(1.0) 대비 코덱 그룹별 상대 비트레이트 가중치 설정"""

    av1: float = field(
        default=0.65,
        metadata=_meta("AV1 상대 비트레이트 비율 (H.264=1.0 기준)", (0.10, 3.00)),
    )
    hevc_vp9: float = field(
        default=0.80,
        metadata=_meta("HEVC / VP9 상대 비트레이트 비율", (0.10, 3.00)),
    )
    h264: float = field(
        default=1.00,
        metadata=_meta("H.264 표준 기준 가중치", (0.10, 3.00)),
    )
    legacy: float = field(
        default=1.30,
        metadata=_meta("구형 레거시 코덱 상대 비트레이트 비율", (0.10, 5.00)),
    )

    def get_weight(self, codec: str) -> float:
        """코덱 명칭을 정규화해 상대 비트레이트 가중치를 O(1)로 반환

        미등록 코덱에는 기본 가중치 1.00 적용
        """
        key = (codec or "h264").lower().strip()
        group = _CODEC_GROUP_BY_ALIAS.get(key)
        if group == "av1":
            return self.av1
        if group == "hevc_vp9":
            return self.hevc_vp9
        if group == "h264":
            return self.h264
        if group == "legacy":
            return self.legacy
        return _STATIC_CODEC_WEIGHTS.get(key, DEFAULT_CODEC_WEIGHT_FALLBACK)


@dataclass(slots=True)
class BitrateModelConfig:
    """해상도·FPS 비선형 스케일링 기준 비트레이트 모델 설정"""

    base_width: int = field(
        default=1280,
        metadata=_meta("기준 가로 해상도 (px)", (320, 7680)),
    )
    base_height: int = field(
        default=720,
        metadata=_meta("기준 세로 해상도 (px)", (240, 4320)),
    )
    base_target_kbps: int = field(
        default=1500,
        metadata=_meta("기준 해상도·FPS 목표 비트레이트 (kbps)", (100, 50000)),
    )
    base_fps: float = field(
        default=30.0,
        metadata=_meta("기준 프레임레이트 (fps)", (1.0, 240.0)),
    )
    area_scaling_exponent: float = field(
        default=0.82,
        metadata=_meta("해상도 면적 스케일링 지수", (0.10, 1.50)),
    )
    fps_scaling_exponent: float = field(
        default=0.60,
        metadata=_meta("프레임레이트 보정 지수", (0.10, 1.50)),
    )


@dataclass(slots=True)
class AnalysisConfig:
    """화질·인코딩 효율·코덱·비트레이트 분석 기준 통합 컨테이너"""

    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    efficiency: EfficiencyConfig = field(default_factory=EfficiencyConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)

    def copy(self) -> AnalysisConfig:
        """모든 하위 분석 설정의 독립 복사본 반환"""
        return AnalysisConfig(
            thresholds=replace(self.thresholds),
            efficiency=replace(self.efficiency),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
        )


@dataclass(slots=True)
class DisplayConfig:
    """CUI·GUI 공통 테이블 열별 소수점 정밀도 및 단위 표기 설정"""

    fps_decimals: int = field(
        default=2,
        metadata=_meta("FPS 소수점 자릿수", (0, 4)),
    )
    fps_trim_whole: bool = field(
        default=DEFAULT_FPS_TRIM_WHOLE,
        metadata=_meta("FPS 값이 정수(.00)일 때 소수점 생략 여부"),
    )
    duration_decimals: int = field(
        default=1,
        metadata=_meta("길이(초) 소수점 자릿수 (CUI)", (0, 3)),
    )
    ratio_decimals: int = field(
        default=2,
        metadata=_meta("BR 비율 소수점 자릿수", (0, 4)),
    )
    keyframe_decimals: int = field(
        default=2,
        metadata=_meta("키프레임 간격 소수점 자릿수", (0, 4)),
    )
    size_decimals: int = field(
        default=2,
        metadata=_meta("원본 용량 소수점 자릿수", (0, 4)),
    )
    after_size_decimals: int = field(
        default=2,
        metadata=_meta("예상 용량 소수점 자릿수", (0, 4)),
    )
    saved_size_decimals: int = field(
        default=2,
        metadata=_meta("예상 절감량 소수점 자릿수", (0, 4)),
    )
    size_unit: str = field(
        default=DEFAULT_SIZE_UNIT,
        metadata=_meta('용량 표기 단위: "mb" | "gb" | "auto"'),
    )


@dataclass(slots=True)
class CuiConfig:
    """CUI(노트북·터미널) 표 출력 서식 설정"""

    filename_maxlen: int = field(
        default=80,
        metadata=_meta("콘솔 표 파일명 최대 표시 길이 (자)", (20, 300)),
    )


@dataclass(slots=True)
class UiFilterState:
    """GUI 테이블 검색 필터 상태"""

    search: str = field(
        default=DEFAULT_FILTER_SEARCH,
        metadata=_meta("파일명·코덱 검색어"),
    )


@dataclass(slots=True)
class UiLayoutState:
    """GUI 창 기하 배치 및 테이블 열 상태(Base64)"""

    window_geometry: str = field(
        default="",
        metadata=_meta("창 크기·위치 상태 (Base64)"),
    )
    table_header_state: str = field(
        default="",
        metadata=_meta("테이블 열 배치 상태 (Base64)"),
    )
    settings_tab: int = field(
        default=0,
        metadata=_meta("마지막으로 연 설정 탭 인덱스 (0=환경·세부 설정, 1=화질·효율 기준)"),
    )
    help_tab: int = field(
        default=0,
        metadata=_meta("마지막으로 연 도움말 탭 인덱스 (0=프로그램 안내, 1=핵심 개념·KPI, 2=예외 조건식·단축키)"),
    )


@dataclass(slots=True)
class UiState:
    """GUI 화면 테마, 스캔 모드, 예외 규칙, 필터, 레이아웃 통합 상태"""

    theme_mode: str = field(
        default=DEFAULT_THEME_MODE,
        metadata=_meta('테마: "system" | "dark" | "light"'),
    )
    scan_mode: str = field(
        default=DEFAULT_SCAN_MODE,
        metadata=_meta('스캔 모드: "fast" | "full"'),
    )
    show_setting_hints: bool = field(
        default=True,
        metadata=_meta("설정 창 내 쉬운 설명·예시 표시 여부"),
    )
    exception_tokens: list[str] = field(
        default_factory=list,
        metadata=_meta("인코딩 제외 키워드·AST 조건식 목록"),
    )
    disabled_exception_tokens: list[str] = field(
        default_factory=list,
        metadata=_meta("비활성화된 예외 키워드·AST 조건식 목록"),
    )
    filters: UiFilterState = field(default_factory=UiFilterState)
    layout: UiLayoutState = field(default_factory=UiLayoutState)

    def copy(self) -> UiState:
        """중첩 컬렉션과 하위 상태를 포함한 독립 복사본 반환"""
        return UiState(
            theme_mode=self.theme_mode,
            scan_mode=self.scan_mode,
            show_setting_hints=self.show_setting_hints,
            exception_tokens=list(self.exception_tokens),
            disabled_exception_tokens=list(self.disabled_exception_tokens),
            filters=replace(self.filters),
            layout=replace(self.layout),
        )


DEFAULT_PATHS = PathsConfig()
DEFAULT_THRESHOLDS = ThresholdsConfig()
DEFAULT_EFFICIENCY = EfficiencyConfig()
DEFAULT_CODEC_WEIGHTS_CONFIG = CodecWeightsConfig()
DEFAULT_BITRATE_MODEL = BitrateModelConfig()
DEFAULT_DISPLAY = DisplayConfig()
DEFAULT_CUI = CuiConfig()

# 분류기 핵심 세부 설정 항목 총 개수 (paths 폴더명 2 + analysis 19 + display 9 + cui 1)
CORE_SETTING_COUNT: int = (
    2
    + len(fields(ThresholdsConfig))
    + len(fields(EfficiencyConfig))
    + len(fields(CodecWeightsConfig))
    + len(fields(BitrateModelConfig))
    + len(fields(DisplayConfig))
    + len(fields(CuiConfig))
)

_SETTING_BOUND_CLASSES: tuple[type[Any], ...] = (
    ThresholdsConfig,
    EfficiencyConfig,
    CodecWeightsConfig,
    BitrateModelConfig,
    DisplayConfig,
    CuiConfig,
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


def _parse_efficiency_section(raw: Mapping[str, Any]) -> EfficiencyConfig:
    parsed = parse_typed_section(raw, DEFAULT_EFFICIENCY, EfficiencyConfig)
    if parsed.encode_codec not in VALID_ENCODE_CODECS:
        parsed.encode_codec = DEFAULT_ENCODE_CODEC
    return parsed


def _parse_display_section(raw: Mapping[str, Any]) -> DisplayConfig:
    parsed = parse_typed_section(raw, DEFAULT_DISPLAY, DisplayConfig)
    if parsed.size_unit not in VALID_SIZE_UNITS:
        parsed.size_unit = DEFAULT_SIZE_UNIT
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
    """분석 기준·분류 폴더명·공통 표 서식·CUI 출력을 계층 구조로 보관하는 분류기 런타임 설정"""

    dir_standard_cq22: str = DEFAULT_DIR_STANDARD_CQ22
    dir_low_quality_keyframe: str = DEFAULT_DIR_LOW_QUALITY_KEYFRAME
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    efficiency: EfficiencyConfig = field(default_factory=EfficiencyConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    cui: CuiConfig = field(default_factory=CuiConfig)

    def get_codec_weight(self, codec: str) -> float:
        """코덱 명칭을 정규화해 H.264 대비 상대 비트레이트 가중치 반환"""
        return self.codec_weights.get_weight(codec)

    def copy(self) -> ClassifierConfig:
        """하위 설정 데이터클래스를 모두 복제한 독립 사본 반환"""
        return ClassifierConfig(
            dir_standard_cq22=self.dir_standard_cq22,
            dir_low_quality_keyframe=self.dir_low_quality_keyframe,
            thresholds=replace(self.thresholds),
            efficiency=replace(self.efficiency),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
            display=replace(self.display),
            cui=replace(self.cui),
        )

    def to_dict(self) -> dict[str, Any]:
        """통합 JSON 스키마(paths 폴더명·analysis·display·cui)와 동일한 계층 사전으로 변환"""
        return {
            "paths": {
                "dir_standard_cq22": self.dir_standard_cq22,
                "dir_low_quality_keyframe": self.dir_low_quality_keyframe,
            },
            "analysis": {
                "thresholds": asdict(self.thresholds),
                "efficiency": asdict(self.efficiency),
                "codec_weights": asdict(self.codec_weights),
                "bitrate_model": asdict(self.bitrate_model),
            },
            "display": asdict(self.display),
            "cui": asdict(self.cui),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ClassifierConfig:
        """계층형 설정 사전으로부터 기본값을 보완해 ClassifierConfig 생성"""
        paths_raw = values.get("paths")
        analysis_raw = values.get("analysis")
        display_raw = values.get("display")
        cui_raw = values.get("cui")

        paths = paths_raw if isinstance(paths_raw, Mapping) else {}
        analysis = analysis_raw if isinstance(analysis_raw, Mapping) else {}
        display_map = display_raw if isinstance(display_raw, Mapping) else {}
        cui_map = cui_raw if isinstance(cui_raw, Mapping) else {}

        th_raw = analysis.get("thresholds")
        eff_raw = analysis.get("efficiency")
        cw_raw = analysis.get("codec_weights")
        bm_raw = analysis.get("bitrate_model")

        return cls(
            dir_standard_cq22=_coerce_typed_field(
                paths, "dir_standard_cq22", DEFAULT_DIR_STANDARD_CQ22
            ),
            dir_low_quality_keyframe=_coerce_typed_field(
                paths, "dir_low_quality_keyframe", DEFAULT_DIR_LOW_QUALITY_KEYFRAME
            ),
            thresholds=parse_typed_section(
                th_raw if isinstance(th_raw, Mapping) else {},
                DEFAULT_THRESHOLDS,
                ThresholdsConfig,
            ),
            efficiency=_parse_efficiency_section(
                eff_raw if isinstance(eff_raw, Mapping) else {}
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
        )


def merge_tuner_criteria(
    base: ClassifierConfig,
    thresholds: ThresholdsConfig,
    efficiency: EfficiencyConfig,
) -> ClassifierConfig:
    """판별 기준 튜너가 편집하는 6개 화질·효율 기준만 기존 설정에 병합"""
    return replace(
        base,
        thresholds=replace(
            base.thresholds,
            over_bitrate_ratio=thresholds.over_bitrate_ratio,
            low_bitrate_ratio=thresholds.low_bitrate_ratio,
            keyframe_interval_sec=thresholds.keyframe_interval_sec,
        ),
        efficiency=replace(
            base.efficiency,
            min_saved_mb=efficiency.min_saved_mb,
            min_mb_per_min=efficiency.min_mb_per_min,
            exempt_saved_mb=efficiency.exempt_saved_mb,
        ),
    )


def get_app_base_dir() -> str:
    """실행 파일(.exe) 또는 소스 프로젝트 루트 디렉터리 절대 경로 반환"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_data_dir() -> str:
    """포터블 백업 대상 데이터 디렉터리(실행 경로/data) 절대 경로 반환"""
    return os.path.join(get_app_base_dir(), DATA_DIR_NAME)


def get_rootdir_path() -> str:
    """data/rootdir.txt 파일의 절대 경로 반환"""
    return os.path.join(get_data_dir(), ROOTDIR_FILENAME)


def write_utf8_file(path: str, content: str) -> None:
    """상위 디렉터리를 자동 생성한 뒤 UTF-8 텍스트 파일을 기록"""
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def ensure_utf8_file(path: str, content_factory: Callable[[], str]) -> str:
    """대상 파일이 없을 때만 기본 내용을 생성해 기록하고 경로 반환"""
    if not os.path.exists(path):
        write_utf8_file(path, content_factory())
    return path


def _render_rootdir_content(path: str = "") -> str:
    """CUI 사용자 안내 주석을 포함한 data/rootdir.txt 파일 내용 생성"""
    cleaned = path.strip()
    if cleaned:
        return f"{DEFAULT_ROOTDIR_TEMPLATE}{cleaned}\n"
    return DEFAULT_ROOTDIR_TEMPLATE


def ensure_default_rootdir_file() -> str:
    """data/rootdir.txt 파일이 없으면 CUI 안내 주석 템플릿으로 자동 생성하고 경로 반환"""
    return ensure_utf8_file(
        get_rootdir_path(),
        lambda: _render_rootdir_content(DEFAULT_ROOT_DIR),
    )


def set_root_dir(path: str) -> None:
    """data/rootdir.txt 파일에 CUI 안내 주석과 함께 분석 대상 작업 경로를 저장"""
    write_utf8_file(get_rootdir_path(), _render_rootdir_content(path))


def _strip_inline_comment(line: str) -> str:
    """따옴표 바깥에 위치한 주석(#) 이후 문자열을 제거"""
    quote_char: str | None = None
    for idx, ch in enumerate(line):
        if quote_char is None:
            if ch in ('"', "'"):
                quote_char = ch
            elif ch == "#" and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] in ('"', "'")):
                return line[:idx]
        elif ch == quote_char:
            quote_char = None
    return line


def _parse_rootdir_line(raw_line: str) -> str:
    """data/rootdir.txt 단일 줄에서 주석·따옴표를 정리하고 사용자 홈(~) 및 환경변수를 확장"""
    cleaned = _strip_inline_comment(raw_line).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return ""
    return os.path.expanduser(os.path.expandvars(cleaned))


def get_root_dir() -> str:
    """data/rootdir.txt 파일에서 분석 대상 작업 경로 조회

    프로세스나 Jupyter 커널 재시작 없이 파일 변경 내용 즉시 반영
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, encoding="utf-8-sig") as f:
            for raw_line in f:
                parsed = _parse_rootdir_line(raw_line)
                if parsed:
                    return parsed
            return ""
    except FileNotFoundError as e:
        raise DomainFileNotFoundError(
            f"data/rootdir.txt 파일을 찾을 수 없습니다: {rootdir_path}",
            code=ErrorCode.ROOT_DIR_NOT_FOUND,
        ) from e
    except Exception as e:
        raise DomainRuntimeError(
            f"data/rootdir.txt 파일을 읽는 중 오류가 발생했습니다: {e}",
            code=ErrorCode.ROOT_DIR_READ_ERROR,
        ) from e
