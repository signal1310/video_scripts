"""계층형 메타데이터 기반 분석 설정 스키마와 분류기 런타임 설정 정의 모듈"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, TypeVar

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

DEFAULT_DIR_STANDARD_CQ22: str = "_1_표준인코딩_CQ22"
DEFAULT_DIR_LOW_QUALITY_KEYFRAME: str = "_2_저화질_키프레임교정"

DEFAULT_FPS_TRIM_WHOLE: bool = True
DEFAULT_SIZE_UNIT: str = "auto"
VALID_SIZE_UNITS: tuple[str, ...] = ("mb", "gb", "auto")

MAX_RECENT_DIRS: int = 10


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
    """최근 작업 폴더 이력 및 분류 대상 폴더명 설정"""

    recent_dirs: list[str] = field(
        default_factory=list,
        metadata=setting_meta(f"최근 작업 폴더 목록 (최대 {MAX_RECENT_DIRS}개)"),
    )
    dir_standard_cq22: str = field(
        default=DEFAULT_DIR_STANDARD_CQ22,
        metadata=setting_meta("CQ22 재인코딩 분류 폴더명"),
    )
    dir_low_quality_keyframe: str = field(
        default=DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
        metadata=setting_meta("저화질 키프레임 간격 초과 재인코딩 분류 폴더명"),
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




DEFAULT_ENCODE_CODEC: str = "hevc_nvenc"
DEFAULT_ENCODE_SPEED_FACTOR: float = 1.0
ENCODE_CODEC_CHOICES: tuple[tuple[str, str], ...] = (
    ("HEVC (NVENC GPU, 샤나 기본)", "hevc_nvenc"),
    ("HEVC (CPU x265, 샤나 기본)", "libx265"),
    ("H.264 (NVENC GPU)", "h264_nvenc"),
    ("AV1 (NVENC GPU, RTX 40+)", "av1_nvenc"),
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


@dataclass(slots=True)
class EncodeConfig:
    """목표 인코딩 코덱과 기준 배속 설정, 예상 시간 산출에 사용"""

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
        metadata=setting_meta("구형 레거시 코덱 상대 비트레이트 비율", (0.10, 5.00)),
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


@dataclass(slots=True)
class CuiConfig:
    """CUI(노트북, 터미널) 표 출력 서식 설정"""

    filename_maxlen: int = field(
        default=80,
        metadata=setting_meta("콘솔 표 파일명 최대 표시 길이 (자)", (20, 300)),
    )


DEFAULT_PATHS = PathsConfig()
DEFAULT_THRESHOLDS = ThresholdsConfig()
DEFAULT_SAVINGS = SavingsConfig()
DEFAULT_ENCODE = EncodeConfig()
DEFAULT_CODEC_WEIGHTS_CONFIG = CodecWeightsConfig()
DEFAULT_BITRATE_MODEL = BitrateModelConfig()
DEFAULT_DISPLAY = DisplayConfig()
DEFAULT_CUI = CuiConfig()

# 분류기 핵심 세부 설정 항목 총 개수 (paths 폴더명 2 + analysis 19 + display 9 + cui 1)
CORE_SETTING_COUNT: int = (
    2
    + len(fields(ThresholdsConfig))
    + len(fields(SavingsConfig))
    + len(fields(EncodeConfig))
    + len(fields(CodecWeightsConfig))
    + len(fields(BitrateModelConfig))
    + len(fields(DisplayConfig))
    + len(fields(CuiConfig))
)

_SETTING_BOUND_CLASSES: tuple[type[Any], ...] = (
    ThresholdsConfig,
    SavingsConfig,
    EncodeConfig,
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


def _parse_encode_section(raw: Mapping[str, Any]) -> EncodeConfig:
    parsed = parse_typed_section(raw, DEFAULT_ENCODE, EncodeConfig)
    if parsed.encode_codec not in VALID_ENCODE_CODECS:
        parsed.encode_codec = DEFAULT_ENCODE_CODEC
    return parsed


def _with_legacy_key(raw: Mapping[str, Any], key: str, legacy_key: str) -> dict[str, Any]:
    """이전 키 이름의 값을 새 키가 없을 때만 승계해 기존 설정 파일 보존"""
    merged = dict(raw)
    if key not in merged and legacy_key in merged:
        merged[key] = merged[legacy_key]
    return merged


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
    """분석 기준, 분류 폴더명, 공통 표 서식, CUI 출력을 계층 구조로 보관하는 분류기 런타임 설정"""

    dir_standard_cq22: str = DEFAULT_DIR_STANDARD_CQ22
    dir_low_quality_keyframe: str = DEFAULT_DIR_LOW_QUALITY_KEYFRAME
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    savings: SavingsConfig = field(default_factory=SavingsConfig)
    encode: EncodeConfig = field(default_factory=EncodeConfig)
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
            savings=replace(self.savings),
            encode=replace(self.encode),
            codec_weights=replace(self.codec_weights),
            bitrate_model=replace(self.bitrate_model),
            display=replace(self.display),
            cui=replace(self.cui),
        )

    def to_dict(self) -> dict[str, Any]:
        """통합 JSON 스키마(paths 폴더명, analysis, display, cui)와 동일한 계층 사전으로 변환"""
        return {
            "paths": {
                "dir_standard_cq22": self.dir_standard_cq22,
                "dir_low_quality_keyframe": self.dir_low_quality_keyframe,
            },
            "analysis": {
                "thresholds": asdict(self.thresholds),
                "savings": asdict(self.savings),
                "encode": asdict(self.encode),
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
        # 이전 저장 형식의 efficiency 섹션은 신규 섹션이 없을 때만 승계
        legacy_eff_raw = analysis.get("efficiency")
        savings_raw = analysis.get("savings", legacy_eff_raw)
        encode_raw = analysis.get("encode", legacy_eff_raw)
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
                _with_legacy_key(
                    th_raw if isinstance(th_raw, Mapping) else {},
                    "keyframe_1s_increase_rate",
                    "keyframe_1s_overhead_rate",
                ),
                DEFAULT_THRESHOLDS,
                ThresholdsConfig,
            ),
            savings=parse_typed_section(
                savings_raw if isinstance(savings_raw, Mapping) else {},
                DEFAULT_SAVINGS,
                SavingsConfig,
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
