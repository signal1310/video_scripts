"""테이블 열 데이터 타입 스키마 및 동적 필터 조건 모델 모듈"""

from dataclasses import dataclass
from enum import Enum
from itertools import pairwise

from src.core.config import ClassifierConfig

TEXT_MODE_CONTAINS: str = "contains"
TEXT_MODE_NOT_CONTAINS: str = "not_contains"
TEXT_MODE_STARTS_WITH: str = "starts_with"
TEXT_MODE_ENDS_WITH: str = "ends_with"

TEXT_FILTER_MODES: tuple[tuple[str, str], ...] = (
    ("포함", TEXT_MODE_CONTAINS),
    ("제외", TEXT_MODE_NOT_CONTAINS),
    ("시작 문자", TEXT_MODE_STARTS_WITH),
    ("끝 문자", TEXT_MODE_ENDS_WITH),
)

_ROUND_COMPARE_TOLERANCE: float = 1e-6

_FPS_BUCKET_DEFS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("fps:lt_23.5", "저프레임 (< 23.5)", None, 23.5),
    ("fps:24", "~24 fps", 23.5, 24.5),
    ("fps:25", "~25 fps", 24.5, 25.5),
    ("fps:25.5_28.5", "가변/중간 (25.5~28.5)", 25.5, 28.5),
    ("fps:30", "~30 fps", 28.5, 31.5),
    ("fps:31.5_48.5", "가변/중간 (31.5~48.5)", 31.5, 48.5),
    ("fps:50", "~50 fps", 48.5, 51.5),
    ("fps:51.5_58.5", "가변/중간 (51.5~58.5)", 51.5, 58.5),
    ("fps:60", "~60 fps", 58.5, 61.5),
    ("fps:61.5_115", "고프레임 (61.5~115)", 61.5, 115.0),
    ("fps:120_plus", "~120+ fps (≥ 115)", 115.0, None),
)

_DURATION_BUCKET_DEFS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("dur:0_30", "30초 미만", None, 30.0),
    ("dur:30_60", "30초 ~ 1분", 30.0, 60.0),
    ("dur:60_180", "1 ~ 3분", 60.0, 180.0),
    ("dur:180_300", "3 ~ 5분", 180.0, 300.0),
    ("dur:300_600", "5 ~ 10분", 300.0, 600.0),
    ("dur:600_1200", "10 ~ 20분", 600.0, 1200.0),
    ("dur:1200_1800", "20 ~ 30분", 1200.0, 1800.0),
    ("dur:1800_3600", "30분 ~ 1시간", 1800.0, 3600.0),
    ("dur:3600_plus", "1시간 이상", 3600.0, None),
)

_SIZE_BASE_CUTPOINTS_MB: tuple[float, ...] = (30.0, 100.0, 250.0, 500.0, 1000.0)
_BITRATE_BASE_CUTPOINTS_KBPS: tuple[float, ...] = (
    500.0,
    1000.0,
    1500.0,
    2000.0,
    5000.0,
)

_RATIO_LOW_HALF_SCALE: float = 0.50
_RATIO_LOW_THREE_QUARTER_SCALE: float = 0.75
_RATIO_OPTIMAL_ANCHOR: float = 1.00
_RATIO_SUB_CUT_MIN_GAP: float = 0.05
_RATIO_OVER_MIN_GAP: float = 0.15
_RATIO_OVER_ANCHORS: tuple[float, ...] = (1.50, 2.00, 3.00)
_RATIO_FALLBACK_OVER_MULTIPLIERS: tuple[float, float] = (1.25, 1.67)
_RATIO_OVER_SUB_MIN_COUNT: int = 2

_KEYFRAME_DEFAULT_WARN_CAP_SEC: float = 5.0
_KEYFRAME_WARN_TRIGGER_CEIL_SEC: float = 4.5
_KEYFRAME_WARN_MULTIPLIER: float = 2.0

_DEFAULT_FILTER_CONFIG = ClassifierConfig()


class ColumnDataType(Enum):
    """테이블 열의 동적 필터링 방식과 팝오버 UI를 결정하는 데이터 타입"""

    ENUM = "enum"
    CATEGORY = "category"
    NUMBER = "number"
    TEXT = "text"

    @property
    def label(self) -> str:
        """팝오버 헤더에 표시할 한국어 데이터 타입 명칭"""
        if self is ColumnDataType.ENUM:
            return "열거형"
        if self is ColumnDataType.CATEGORY:
            return "범주형"
        if self is ColumnDataType.NUMBER:
            return "수치"
        return "문자열"


class NumericBucketKind(Enum):
    """수치형 열의 도메인 구간 버킷 생성 전략 구분"""

    NONE = "none"
    FPS = "fps"
    DURATION = "duration"
    SIZE = "size"
    BITRATE = "bitrate"
    RATIO = "ratio"
    KEYFRAME = "keyframe"
    SAVED_SIZE = "saved_size"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """개별 테이블 열의 데이터 타입, 단위, 소수점, 스텝 및 버킷 메타데이터"""

    col: int
    data_type: ColumnDataType
    unit: str = ""
    decimals: int = 2
    filter_step: float = 1.0
    nullable_label: str = ""
    preset_values: tuple[str, ...] = ()
    bucket_kind: NumericBucketKind = NumericBucketKind.NONE


@dataclass(frozen=True, slots=True)
class NumericBucketSpec:
    """수치형 열의 단일 구간 버킷 경계 및 표시 라벨 정의"""

    key: str
    label: str
    lower: float | None = None
    lower_inclusive: bool = True
    upper: float | None = None
    upper_inclusive: bool = False

    def contains(self, value: float) -> bool:
        """수치 값이 현재 버킷의 하한·상한 경계에 속하는지 판별"""
        if self.lower is not None:
            if self.lower_inclusive:
                if value < self.lower:
                    return False
            elif value <= self.lower:
                return False
        if self.upper is not None:
            if self.upper_inclusive:
                if value > self.upper:
                    return False
            elif value >= self.upper:
                return False
        return True


def _format_compact_number(value: float) -> str:
    if abs(value - round(value)) < _ROUND_COMPARE_TOLERANCE:
        return f"{round(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _format_key_number(value: float) -> str:
    if abs(value - round(value)) < _ROUND_COMPARE_TOLERANCE:
        return str(round(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _build_static_buckets(
    defs: tuple[tuple[str, str, float | None, float | None], ...],
) -> tuple[NumericBucketSpec, ...]:
    return tuple(
        NumericBucketSpec(
            key=key,
            label=label,
            lower=lower,
            lower_inclusive=True,
            upper=upper,
            upper_inclusive=False,
        )
        for key, label, lower, upper in defs
    )


def _build_cutpoint_buckets(
    prefix: str,
    unit: str,
    cutpoints: list[float],
    *,
    tag_map: dict[float, str] | None = None,
    first_lower: float | None = None,
    first_lower_inclusive: bool = True,
) -> tuple[NumericBucketSpec, ...]:
    if not cutpoints:
        return ()

    tags = tag_map or {}
    buckets: list[NumericBucketSpec] = []
    unit_suffix = f" {unit}" if unit else ""

    first_cut = cutpoints[0]
    first_tag = f" ({tags[first_cut]})" if first_cut in tags else ""
    if first_lower is None:
        first_key = f"{prefix}:lt_{_format_key_number(first_cut)}"
        first_label = f"{_format_compact_number(first_cut)}{unit_suffix} 미만{first_tag}"
    else:
        first_key = f"{prefix}:{_format_key_number(first_lower)}_{_format_key_number(first_cut)}"
        first_label = (
            f"{_format_compact_number(first_lower)} ~ "
            f"{_format_compact_number(first_cut)}{unit_suffix}{first_tag}"
        )
    buckets.append(
        NumericBucketSpec(
            key=first_key,
            label=first_label,
            lower=first_lower,
            lower_inclusive=first_lower_inclusive,
            upper=first_cut,
            upper_inclusive=False,
        )
    )

    for prev_cut, next_cut in pairwise(cutpoints):
        range_tag = f" ({tags[next_cut]})" if next_cut in tags else ""
        buckets.append(
            NumericBucketSpec(
                key=f"{prefix}:{_format_key_number(prev_cut)}_{_format_key_number(next_cut)}",
                label=(
                    f"{_format_compact_number(prev_cut)} ~ "
                    f"{_format_compact_number(next_cut)}{unit_suffix}{range_tag}"
                ),
                lower=prev_cut,
                lower_inclusive=True,
                upper=next_cut,
                upper_inclusive=False,
            )
        )

    last_cut = cutpoints[-1]
    buckets.append(
        NumericBucketSpec(
            key=f"{prefix}:ge_{_format_key_number(last_cut)}",
            label=f"{_format_compact_number(last_cut)}{unit_suffix} 이상",
            lower=last_cut,
            lower_inclusive=True,
            upper=None,
            upper_inclusive=False,
        )
    )
    return tuple(buckets)


def _build_ratio_buckets(config: ClassifierConfig) -> tuple[NumericBucketSpec, ...]:
    low = round(config.thresholds.low_bitrate_ratio, 2)
    over = round(config.thresholds.over_bitrate_ratio, 2)

    low_cuts: list[float] = []
    for candidate in (
        round(low * _RATIO_LOW_HALF_SCALE, 2),
        round(low * _RATIO_LOW_THREE_QUARTER_SCALE, 2),
    ):
        prev = low_cuts[-1] if low_cuts else 0.0
        if (
            candidate - prev >= _RATIO_SUB_CUT_MIN_GAP
            and low - candidate >= _RATIO_SUB_CUT_MIN_GAP
        ):
            low_cuts.append(candidate)
    low_cuts.append(low)

    buckets: list[NumericBucketSpec] = []
    for idx, cut in enumerate(low_cuts):
        prev = low_cuts[idx - 1] if idx > 0 else None
        if idx == 0:
            label = f"{cut:.2f}x 미만 (극저화질)" if len(low_cuts) > 1 else f"{cut:.2f}x 미만 (저화질)"
            key = f"ratio:lt_{cut:.2f}"
        elif idx == len(low_cuts) - 1:
            label = f"{prev:.2f} ~ {cut:.2f}x (저화질·경계)"
            key = f"ratio:{prev:.2f}_{cut:.2f}"
        else:
            label = f"{prev:.2f} ~ {cut:.2f}x (저화질)"
            key = f"ratio:{prev:.2f}_{cut:.2f}"
        buckets.append(
            NumericBucketSpec(
                key=key,
                label=label,
                lower=prev,
                lower_inclusive=True,
                upper=cut,
                upper_inclusive=False,
            )
        )

    if low < _RATIO_OPTIMAL_ANCHOR < over:
        buckets.append(
            NumericBucketSpec(
                key=f"ratio:{low:.2f}_{_RATIO_OPTIMAL_ANCHOR:.2f}",
                label=f"{low:.2f} ~ {_RATIO_OPTIMAL_ANCHOR:.2f}x (적정·이하)",
                lower=low,
                lower_inclusive=True,
                upper=_RATIO_OPTIMAL_ANCHOR,
                upper_inclusive=False,
            )
        )
        buckets.append(
            NumericBucketSpec(
                key=f"ratio:{_RATIO_OPTIMAL_ANCHOR:.2f}_{over:.2f}",
                label=f"{_RATIO_OPTIMAL_ANCHOR:.2f} ~ {over:.2f}x (적정·이상)",
                lower=_RATIO_OPTIMAL_ANCHOR,
                lower_inclusive=True,
                upper=over,
                upper_inclusive=True,
            )
        )
    else:
        buckets.append(
            NumericBucketSpec(
                key=f"ratio:{low:.2f}_{over:.2f}",
                label=f"{low:.2f} ~ {over:.2f}x (적정)",
                lower=low,
                lower_inclusive=True,
                upper=over,
                upper_inclusive=True,
            )
        )

    over_cuts = [a for a in _RATIO_OVER_ANCHORS if a >= over + _RATIO_OVER_MIN_GAP]
    if len(over_cuts) < _RATIO_OVER_SUB_MIN_COUNT:
        for mult in _RATIO_FALLBACK_OVER_MULTIPLIERS:
            scaled = round(over * mult, 2)
            prev_over = over_cuts[-1] if over_cuts else over
            if scaled >= prev_over + _RATIO_OVER_MIN_GAP:
                over_cuts.append(scaled)

    if not over_cuts:
        buckets.append(
            NumericBucketSpec(
                key=f"ratio:gt_{over:.2f}",
                label=f"{over:.2f}x 초과 (과화질)",
                lower=over,
                lower_inclusive=False,
                upper=None,
                upper_inclusive=False,
            )
        )
        return tuple(buckets)

    first_over = over_cuts[0]
    buckets.append(
        NumericBucketSpec(
            key=f"ratio:gt_{over:.2f}_{first_over:.2f}",
            label=f"{over:.2f} ~ {first_over:.2f}x (과화질)",
            lower=over,
            lower_inclusive=False,
            upper=first_over,
            upper_inclusive=False,
        )
    )
    for idx, (prev_cut, next_cut) in enumerate(pairwise(over_cuts)):
        desc = "고도 과화질" if idx == len(over_cuts) - 2 else "중과화질"
        buckets.append(
            NumericBucketSpec(
                key=f"ratio:{prev_cut:.2f}_{next_cut:.2f}",
                label=f"{prev_cut:.2f} ~ {next_cut:.2f}x ({desc})",
                lower=prev_cut,
                lower_inclusive=True,
                upper=next_cut,
                upper_inclusive=False,
            )
        )
    last_over = over_cuts[-1]
    buckets.append(
        NumericBucketSpec(
            key=f"ratio:ge_{last_over:.2f}",
            label=f"{last_over:.2f}x 이상 (초과화질)",
            lower=last_over,
            lower_inclusive=True,
            upper=None,
            upper_inclusive=False,
        )
    )
    return tuple(buckets)


def _build_keyframe_buckets(config: ClassifierConfig) -> tuple[NumericBucketSpec, ...]:
    kf_limit = round(config.thresholds.keyframe_interval_sec, 2)
    warn_cap = (
        _KEYFRAME_DEFAULT_WARN_CAP_SEC
        if kf_limit <= _KEYFRAME_WARN_TRIGGER_CEIL_SEC
        else round(kf_limit * _KEYFRAME_WARN_MULTIPLIER, 1)
    )
    return (
        NumericBucketSpec(
            key=f"kf:le_{_format_key_number(kf_limit)}",
            label=f"정상 (≤ {_format_compact_number(kf_limit)}초)",
            lower=None,
            lower_inclusive=True,
            upper=kf_limit,
            upper_inclusive=True,
        ),
        NumericBucketSpec(
            key=f"kf:{_format_key_number(kf_limit)}_{_format_key_number(warn_cap)}",
            label=f"주의 ({_format_compact_number(kf_limit)} ~ {_format_compact_number(warn_cap)}초)",
            lower=kf_limit,
            lower_inclusive=False,
            upper=warn_cap,
            upper_inclusive=True,
        ),
        NumericBucketSpec(
            key=f"kf:gt_{_format_key_number(warn_cap)}",
            label=f"불량 (> {_format_compact_number(warn_cap)}초)",
            lower=warn_cap,
            lower_inclusive=False,
            upper=None,
            upper_inclusive=False,
        ),
    )


def _build_saved_size_buckets(config: ClassifierConfig) -> tuple[NumericBucketSpec, ...]:
    min_saved = round(config.small_footage.min_saved_mb, 2)
    small_mb = round(config.small_footage.size_mb, 2)
    cuts = set(_SIZE_BASE_CUTPOINTS_MB)
    if min_saved > 0.0:
        cuts.add(min_saved)
    if small_mb > 0.0:
        cuts.add(small_mb)

    tag_map: dict[float, str] = {}
    if min_saved > 0.0:
        tag_map[min_saved] = "최소 미만"
    if small_mb > 0.0 and small_mb != min_saved:
        tag_map[small_mb] = "소형 기준"

    pos_buckets = _build_cutpoint_buckets(
        "saved",
        "MB",
        sorted(cuts),
        tag_map=tag_map,
        first_lower=0.0,
        first_lower_inclusive=False,
    )
    return (
        NumericBucketSpec(
            key="saved:neg",
            label="증가 (< 0 MB)",
            lower=None,
            lower_inclusive=True,
            upper=0.0,
            upper_inclusive=False,
        ),
        NumericBucketSpec(
            key="saved:zero",
            label="변화 없음 (0 MB)",
            lower=0.0,
            lower_inclusive=True,
            upper=0.0,
            upper_inclusive=True,
        ),
        *pos_buckets,
    )


def build_numeric_buckets(
    kind: NumericBucketKind,
    config: ClassifierConfig | None = None,
) -> tuple[NumericBucketSpec, ...]:
    """지정 수치 열 타입과 활성 설정에 맞는 순서 고정 도메인 버킷 목록 생성"""
    cfg = config or _DEFAULT_FILTER_CONFIG
    if kind is NumericBucketKind.FPS:
        return _build_static_buckets(_FPS_BUCKET_DEFS)
    if kind is NumericBucketKind.DURATION:
        return _build_static_buckets(_DURATION_BUCKET_DEFS)
    if kind is NumericBucketKind.SIZE:
        small_mb = round(cfg.small_footage.size_mb, 2)
        cuts = set(_SIZE_BASE_CUTPOINTS_MB)
        tag_map: dict[float, str] = {}
        if small_mb > 0.0:
            cuts.add(small_mb)
            tag_map[small_mb] = "소형 기준"
        return _build_cutpoint_buckets("size", "MB", sorted(cuts), tag_map=tag_map)
    if kind is NumericBucketKind.BITRATE:
        base_kbps = float(round(cfg.bitrate_model.base_target_kbps))
        cuts = set(_BITRATE_BASE_CUTPOINTS_KBPS)
        tag_map_kbps: dict[float, str] = {}
        if base_kbps > 0.0:
            cuts.add(base_kbps)
            tag_map_kbps[base_kbps] = "모델 기준"
        return _build_cutpoint_buckets("br", "kbps", sorted(cuts), tag_map=tag_map_kbps)
    if kind is NumericBucketKind.RATIO:
        return _build_ratio_buckets(cfg)
    if kind is NumericBucketKind.KEYFRAME:
        return _build_keyframe_buckets(cfg)
    if kind is NumericBucketKind.SAVED_SIZE:
        return _build_saved_size_buckets(cfg)
    return ()


def resolve_numeric_bucket(
    value: float,
    buckets: tuple[NumericBucketSpec, ...],
) -> NumericBucketSpec | None:
    """측정 수치 값이 속하는 단일 도메인 버킷 정의 반환"""
    for bucket in buckets:
        if bucket.contains(value):
            return bucket
    return None


@dataclass(frozen=True, slots=True)
class ColumnFilterRule:
    """개별 테이블 열에 적용되는 동적 필터 조건"""

    selected_values: frozenset[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    include_null: bool = True
    null_only: bool = False
    text_query: str = ""
    text_mode: str = TEXT_MODE_CONTAINS

    def is_active(self, data_type: ColumnDataType) -> bool:
        """해당 데이터 타입 기준으로 실제 필터링이 활성화되었는지 반환"""
        if data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            return self.selected_values is not None
        if data_type is ColumnDataType.NUMBER:
            return (
                self.null_only
                or self.selected_values is not None
                or self.min_value is not None
                or self.max_value is not None
                or not self.include_null
            )
        return bool(self.text_query.strip())

    def matches(
        self,
        value: str | float | None,
        spec: ColumnSpec,
        config: ClassifierConfig | None = None,
    ) -> bool:
        """셀 필터 값이 현재 필터 조건과 일치하는지 판별"""
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self.selected_values is None:
                return True
            return str(value or "") in self.selected_values

        if spec.data_type is ColumnDataType.NUMBER:
            if self.null_only:
                return value is None
            if value is None:
                return self.include_null
            if isinstance(value, str):
                return False
            num = float(value)
            if self.selected_values is not None:
                buckets = build_numeric_buckets(spec.bucket_kind, config)
                matched = resolve_numeric_bucket(num, buckets)
                if matched is None or matched.key not in self.selected_values:
                    return False
            if self.min_value is not None and num < self.min_value:
                return False
            return self.max_value is None or num <= self.max_value

        query = self.text_query.strip().lower()
        if not query:
            return True
        text = str(value or "").lower()
        if self.text_mode == TEXT_MODE_NOT_CONTAINS:
            return query not in text
        if self.text_mode == TEXT_MODE_STARTS_WITH:
            return text.startswith(query)
        if self.text_mode == TEXT_MODE_ENDS_WITH:
            return text.endswith(query)
        return query in text

    def describe(
        self,
        spec: ColumnSpec,
        config: ClassifierConfig | None = None,
    ) -> str:
        """헤더 툴팁 등에 표시할 필터 조건 한 줄 요약 문자열 반환"""
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self.selected_values is None:
                return ""
            if not self.selected_values:
                return "선택 없음 (0개)"
            ordered = sorted(self.selected_values)
            preview = ", ".join(ordered[:3])
            if len(ordered) > 3:
                preview += f" 외 {len(ordered) - 3}개"
            return f"선택: {preview}"

        if spec.data_type is ColumnDataType.NUMBER:
            if self.null_only and spec.nullable_label:
                return f"{spec.nullable_label}만 표시"
            unit = f" {spec.unit}" if spec.unit else ""
            parts: list[str] = []
            if self.min_value is not None and self.max_value is not None:
                parts.append(f"{self.min_value:g} ~ {self.max_value:g}{unit}")
            elif self.min_value is not None:
                parts.append(f">= {self.min_value:g}{unit}")
            elif self.max_value is not None:
                parts.append(f"<= {self.max_value:g}{unit}")
            if self.selected_values is not None:
                if not self.selected_values:
                    parts.append("구간 선택 없음")
                else:
                    buckets = build_numeric_buckets(spec.bucket_kind, config)
                    label_map = {b.key: b.label for b in buckets}
                    ordered_labels = [
                        b.label for b in buckets if b.key in self.selected_values
                    ]
                    unknown = sorted(k for k in self.selected_values if k not in label_map)
                    ordered_labels.extend(unknown)
                    preview = ", ".join(ordered_labels[:2])
                    if len(ordered_labels) > 2:
                        preview += f" 외 {len(ordered_labels) - 2}개"
                    parts.append(f"구간: {preview}")
            if not self.include_null and spec.nullable_label:
                parts.append(f"{spec.nullable_label} 제외")
            return ", ".join(parts)

        query = self.text_query.strip()
        if not query:
            return ""
        mode_labels = {mode: label for label, mode in TEXT_FILTER_MODES}
        mode_text = mode_labels.get(self.text_mode, "포함")
        return f"{mode_text}: '{query}'"
