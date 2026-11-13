"""작업 경로(.rootdir) 입출력 및 계층형 메타데이터 기반 설정 스키마 정의 모듈"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
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

DEFAULT_ROOT_DIR: str = ""
DEFAULT_ROOTDIR_TEMPLATE: str = (
    "# 분석 대상 비디오 작업 폴더 경로를 첫 줄에 입력하세요\n"
)
DEFAULT_DIR_STANDARD_CQ22: str = "_1_표준인코딩_CQ22"
DEFAULT_DIR_LOW_QUALITY_KEYFRAME: str = "_2_저화질_키프레임교정"

DEFAULT_SCAN_MODE: str = "fast"
VALID_SCAN_MODES: tuple[str, ...] = ("fast", "full")

DEFAULT_FILTER_SEARCH: str = ""
DEFAULT_FILTER_ZONE: str = "ALL"
VALID_FILTER_ZONES: tuple[str, ...] = ("ALL", "OVER", "OPTIMAL", "LOW")

DEFAULT_FILTER_TARGET: str = "ALL"
VALID_FILTER_TARGETS: tuple[str, ...] = ("ALL", "ENCODE_ONLY", "PASS_ONLY", "DEFECT_KF")

DEFAULT_THEME_MODE: str = "system"
VALID_THEME_MODES: tuple[str, ...] = ("system", "dark", "light")

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
    """작업 폴더 경로, 최근 폴더 이력 및 분류 대상 폴더명 설정"""

    root_dir: str = field(
        default=DEFAULT_ROOT_DIR,
        metadata=_meta("기본 작업 폴더 (비어 있으면 .rootdir 또는 최근 선택 경로 사용)"),
    )
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


@dataclass(slots=True)
class SmallFootageConfig:
    """소형 영상 선별 및 누적 최적화 전략 기준 설정"""

    size_mb: float = field(
        default=30.0,
        metadata=_meta("소형 영상 기준 용량 (MB)", (0.0, 500.0)),
    )
    min_saved_mb: float = field(
        default=3.0,
        metadata=_meta("개별 소형 영상 최소 절감량 하한 (MB)", (0.1, 50.0)),
    )
    cumulative_ratio: float = field(
        default=0.15,
        metadata=_meta("전체 절감량 중 소형 영상 누적 비중 임계값", (0.01, 1.00)),
    )
    cumulative_mb: float = field(
        default=500.0,
        metadata=_meta("소형 영상 누적 절감 합계 임계값 (MB)", (10.0, 10000.0)),
    )
    keyframe_1s_overhead_rate: float = field(
        default=0.06,
        metadata=_meta("1초 키프레임 교정 시 예상 용량 증가율", (0.00, 0.50)),
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
    """화질·소형 영상·코덱·비트레이트 분석 기준 통합 컨테이너"""

    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    small_footage: SmallFootageConfig = field(default_factory=SmallFootageConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)

    def copy(self) -> AnalysisConfig:
        """모든 하위 분석 설정의 독립 복사본 반환"""
        return AnalysisConfig(
            thresholds=replace(self.thresholds),
            small_footage=replace(self.small_footage),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
        )


@dataclass(slots=True)
class CuiConfig:
    """CUI(노트북·터미널) 표 출력 서식 설정"""

    float_format: str = field(
        default=".3f",
        metadata=_meta("실수 소수점 출력 포맷"),
    )
    filename_maxlen: int = field(
        default=80,
        metadata=_meta("콘솔 표 파일명 최대 표시 길이 (자)", (20, 300)),
    )


@dataclass(slots=True)
class UiFilterState:
    """GUI 테이블 검색 및 등급·대상 필터 상태"""

    search: str = field(
        default=DEFAULT_FILTER_SEARCH,
        metadata=_meta("파일명·코덱 검색어"),
    )
    zone: str = field(
        default=DEFAULT_FILTER_ZONE,
        metadata=_meta('화질 필터: "ALL" | "OVER" | "OPTIMAL" | "LOW"'),
    )
    target: str = field(
        default=DEFAULT_FILTER_TARGET,
        metadata=_meta('대상 필터: "ALL" | "ENCODE_ONLY" | "PASS_ONLY" | "DEFECT_KF"'),
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
    exception_tokens: list[str] = field(
        default_factory=list,
        metadata=_meta("인코딩 제외 키워드·AST 조건식 목록"),
    )
    filters: UiFilterState = field(default_factory=UiFilterState)
    layout: UiLayoutState = field(default_factory=UiLayoutState)

    def copy(self) -> UiState:
        """중첩 컬렉션과 하위 상태를 포함한 독립 복사본 반환"""
        return UiState(
            theme_mode=self.theme_mode,
            scan_mode=self.scan_mode,
            exception_tokens=list(self.exception_tokens),
            filters=replace(self.filters),
            layout=replace(self.layout),
        )


DEFAULT_PATHS = PathsConfig()
DEFAULT_THRESHOLDS = ThresholdsConfig()
DEFAULT_SMALL_FOOTAGE = SmallFootageConfig()
DEFAULT_CODEC_WEIGHTS_CONFIG = CodecWeightsConfig()
DEFAULT_BITRATE_MODEL = BitrateModelConfig()
DEFAULT_CUI = CuiConfig()

# 분류기 핵심 세부 설정 항목 총 개수 (paths 폴더명 2 + analysis 18 + cui 2)
CORE_SETTING_COUNT: int = (
    2
    + len(fields(ThresholdsConfig))
    + len(fields(SmallFootageConfig))
    + len(fields(CodecWeightsConfig))
    + len(fields(BitrateModelConfig))
    + len(fields(CuiConfig))
)

_SETTING_BOUND_CLASSES: tuple[type[Any], ...] = (
    ThresholdsConfig,
    SmallFootageConfig,
    CodecWeightsConfig,
    BitrateModelConfig,
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
    if isinstance(default, int) and not isinstance(default, bool):
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


def parse_typed_section(raw: Mapping[str, Any], default_obj: _T, cls: type[_T]) -> _T:
    """매핑 딕셔너리에서 필드 타입과 기본값을 맞춰 데이터클래스 섹션을 생성"""
    kwargs: dict[str, Any] = {
        f.name: _coerce_typed_field(raw, f.name, getattr(default_obj, f.name))
        for f in fields(cls)  # type: ignore[arg-type]
    }
    return cls(**kwargs)


@dataclass(slots=True)
class ClassifierConfig:
    """분석 기준·분류 폴더명·CUI 출력을 계층 구조로 보관하는 분류기 런타임 설정"""

    dir_standard_cq22: str = DEFAULT_DIR_STANDARD_CQ22
    dir_low_quality_keyframe: str = DEFAULT_DIR_LOW_QUALITY_KEYFRAME
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    small_footage: SmallFootageConfig = field(default_factory=SmallFootageConfig)
    codec_weights: CodecWeightsConfig = field(default_factory=CodecWeightsConfig)
    bitrate_model: BitrateModelConfig = field(default_factory=BitrateModelConfig)
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
            small_footage=replace(self.small_footage),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
            cui=replace(self.cui),
        )

    def to_dict(self) -> dict[str, Any]:
        """통합 JSON 스키마(paths 폴더명·analysis·cui)와 동일한 계층 사전으로 변환"""
        th = self.thresholds
        sf = self.small_footage
        cw = self.codec_weights
        bm = self.bitrate_model
        cui = self.cui
        return {
            "paths": {
                "dir_standard_cq22": self.dir_standard_cq22,
                "dir_low_quality_keyframe": self.dir_low_quality_keyframe,
            },
            "analysis": {
                "thresholds": {
                    "over_bitrate_ratio": th.over_bitrate_ratio,
                    "low_bitrate_ratio": th.low_bitrate_ratio,
                    "keyframe_interval_sec": th.keyframe_interval_sec,
                },
                "small_footage": {
                    "size_mb": sf.size_mb,
                    "min_saved_mb": sf.min_saved_mb,
                    "cumulative_ratio": sf.cumulative_ratio,
                    "cumulative_mb": sf.cumulative_mb,
                    "keyframe_1s_overhead_rate": sf.keyframe_1s_overhead_rate,
                },
                "codec_weights": {
                    "av1": cw.av1,
                    "hevc_vp9": cw.hevc_vp9,
                    "h264": cw.h264,
                    "legacy": cw.legacy,
                },
                "bitrate_model": {
                    "base_width": bm.base_width,
                    "base_height": bm.base_height,
                    "base_target_kbps": bm.base_target_kbps,
                    "base_fps": bm.base_fps,
                    "area_scaling_exponent": bm.area_scaling_exponent,
                    "fps_scaling_exponent": bm.fps_scaling_exponent,
                },
            },
            "cui": {
                "float_format": cui.float_format,
                "filename_maxlen": cui.filename_maxlen,
            },
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ClassifierConfig:
        """계층형 설정 사전으로부터 기본값을 보완해 ClassifierConfig 생성"""
        paths_raw = values.get("paths")
        analysis_raw = values.get("analysis")
        cui_raw = values.get("cui")

        paths = paths_raw if isinstance(paths_raw, Mapping) else {}
        analysis = analysis_raw if isinstance(analysis_raw, Mapping) else {}
        cui_map = cui_raw if isinstance(cui_raw, Mapping) else {}

        th_raw = analysis.get("thresholds")
        sf_raw = analysis.get("small_footage")
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
            small_footage=parse_typed_section(
                sf_raw if isinstance(sf_raw, Mapping) else {},
                DEFAULT_SMALL_FOOTAGE,
                SmallFootageConfig,
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
            cui=parse_typed_section(cui_map, DEFAULT_CUI, CuiConfig),
        )


def get_app_base_dir() -> str:
    """실행 파일(.exe) 또는 소스 프로젝트 루트 디렉터리 절대 경로 반환"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_rootdir_path() -> str:
    """.rootdir 파일의 절대 경로 반환"""
    return os.path.join(get_app_base_dir(), ".rootdir")


def ensure_default_rootdir_file() -> str:
    """.rootdir 파일이 없으면 기본값 또는 안내 템플릿으로 자동 생성하고 경로를 반환"""
    rootdir_path = get_rootdir_path()
    if not os.path.exists(rootdir_path):
        parent_dir = os.path.dirname(rootdir_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        content = (
            f"{DEFAULT_ROOT_DIR.strip()}\n"
            if DEFAULT_ROOT_DIR.strip()
            else DEFAULT_ROOTDIR_TEMPLATE
        )
        with open(rootdir_path, "w", encoding="utf-8") as f:
            f.write(content)
    return rootdir_path


def set_root_dir(path: str) -> None:
    """앱 실행 루트의 .rootdir 파일에 지정된 분석 대상 작업 경로를 저장"""
    rootdir_path = get_rootdir_path()
    with open(rootdir_path, "w", encoding="utf-8") as f:
        f.write(path.strip() + "\n")


def get_root_dir() -> str:
    """프로젝트 루트의 .rootdir 파일에서 분석 대상 작업 경로 조회

    프로세스나 Jupyter 커널 재시작 없이 파일 변경 내용 즉시 반영
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
            return lines[0] if lines else ""
    except FileNotFoundError as e:
        raise DomainFileNotFoundError(
            f".rootdir 파일을 찾을 수 없습니다: {rootdir_path}",
            code=ErrorCode.ROOT_DIR_NOT_FOUND,
        ) from e
    except Exception as e:
        raise DomainRuntimeError(
            f".rootdir 파일을 읽는 중 오류가 발생했습니다: {e}",
            code=ErrorCode.ROOT_DIR_READ_ERROR,
        ) from e
