"""테이블 열 스키마, 셀 포맷터, 배지 매핑 및 행 툴팁 포맷터 모듈"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt

from src.core.config import ClassifierConfig, DisplayConfig
from src.core.formatting import (
    format_duration_hms,
    format_float_precision,
    format_fps,
    format_keyframe_interval,
    format_ratio,
    format_size_header,
    format_size_mb,
)
from src.core.messages import (
    COLUMN_LABELS,
    LABEL_UNMEASURED,
    ColumnId,
    format_action_tooltip,
    format_classify_status_tooltip,
    format_keyframe_tooltip,
    format_quality_zone_tooltip,
    format_reason_tooltip,
)
from src.core.models import (
    QUALITY_ZONE_SORT_RANK,
    SMART_ACTION_LABELS,
    SMART_ACTION_REASONS,
    ActionBadgeTone,
    ClassifyStatus,
    DownscaleReason,
    QualityZone,
    SmartAction,
    VideoEvaluation,
)
from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.sorting import WindowsNameOrder, natural_sort_key
from src.gui.column_filters import (
    ColumnDataType,
    ColumnSpec,
)
from src.gui.numeric_buckets import NumericBucketKind
from src.gui.styles.palettes import ThemePalette
from src.gui.table_column_styles import (
    FONT_MONO,
    fg_after_size,
    fg_est_encode_time,
    fg_keyframe,
    fg_saved_per_min,
    fg_saved_size,
    font_saved_per_min,
    font_saved_size,
)
from src.gui.table_tooltips import (
    format_est_encode_time_tooltip,
    format_saved_per_min_tooltip,
)

DEFAULT_HEADER_CONFIG: ClassifierConfig = ClassifierConfig()
_HALF_TURN_DEG: int = 180
_SAVED_PER_MIN_DECIMALS: int = 1
_OUTPUT_RES_NOT_DOWNSCALED_LABEL: str = "축소 안 함"
_OUTPUT_RES_NOT_DOWNSCALED_CELL: str = "-"
_OUTPUT_RES_FILTER_LABELS: tuple[str, ...] = (
    _OUTPUT_RES_NOT_DOWNSCALED_LABEL,
    *(tier.display for tier in RESOLUTION_TIERS),
)

ALIGN_LEFT: int = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
ALIGN_CENTER: int = int(Qt.AlignmentFlag.AlignCenter)
ALIGN_RIGHT: int = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

_STATUS_SORT_RANK: dict[ClassifyStatus, int] = {
    ClassifyStatus.MOVED: 0,
    ClassifyStatus.PENDING: 1,
    ClassifyStatus.NOT_TARGET: 2,
}
_BADGE_BY_STATUS: dict[
    ClassifyStatus, Callable[[ThemePalette], tuple[str, str, str]]
] = {
    ClassifyStatus.MOVED: lambda p: p.badge_moved,
    ClassifyStatus.PENDING: lambda p: p.badge_pending,
    ClassifyStatus.NOT_TARGET: lambda p: p.badge_pass,
}
_ZONE_SORT_RANK: dict[QualityZone, int] = QUALITY_ZONE_SORT_RANK
_BADGE_BY_ZONE: dict[QualityZone, Callable[[ThemePalette], tuple[str, str, str]]] = {
    QualityZone.OVER: lambda p: p.badge_over,
    QualityZone.OPTIMAL: lambda p: p.badge_optimal,
    QualityZone.LOW: lambda p: p.badge_low,
}
_BADGE_BY_ACTION_TONE: dict[
    ActionBadgeTone, Callable[[ThemePalette], tuple[str, str, str]]
] = {
    ActionBadgeTone.STD_ENCODE: lambda p: p.badge_std_encode,
    ActionBadgeTone.LOW_KF_ENCODE: lambda p: p.badge_low_kf_encode,
    ActionBadgeTone.PASS: lambda p: p.badge_pass,
}
_BADGE_BY_REASON: dict[str, Callable[[ThemePalette], tuple[str, str, str]]] = {
    SmartAction.ENCODE_CQ22_OVER.reason_label: lambda p: p.badge_over,
    SmartAction.ENCODE_CQ22_OPT_KF.reason_label: lambda p: p.badge_exceeded,
    SmartAction.ENCODE_CODEC_UPGRADE.reason_label: lambda p: p.badge_std_encode,
    SmartAction.ENCODE_COMPAT.reason_label: lambda p: p.badge_pending,
    SmartAction.PASS_OPTIMAL.reason_label: lambda p: p.badge_optimal,
    SmartAction.PASS_LOW.reason_label: lambda p: p.badge_low,
    SmartAction.PASS_LOW_SAVED.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_LOW_PER_MIN.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_CODEC_SAVED.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_EXCEPTION.reason_label: lambda p: p.badge_exception,
    DownscaleReason.MAX_RESOLUTION.value: lambda p: p.badge_std_encode,
    DownscaleReason.BITRATE_FIT.value: lambda p: p.badge_std_encode,
    DownscaleReason.MANUAL.value: lambda p: p.badge_std_encode,
}


def _num_spec(
    col: int,
    unit: str,
    decimals: int,
    step: float,
    bucket_kind: NumericBucketKind,
    *,
    nullable_label: str = "",
) -> ColumnSpec:
    return ColumnSpec(
        col, ColumnDataType.NUMBER, unit, decimals, step, nullable_label, (), bucket_kind
    )


def _static_tip(text: str) -> Callable[[ClassifierConfig], str]:
    return lambda _cfg: text


def _format_rotate_text(ev: VideoEvaluation) -> str:
    return f"{ev.video.rotate_type}°"


def _rotate_icon_name(ev: VideoEvaluation) -> str | None:
    deg = ev.video.rotate_type
    if deg == 0:
        return None
    if deg == _HALF_TURN_DEG:
        return "rotate-180"
    return "rotate-ccw" if deg < 0 else "rotate-cw"


def _fg_rotate(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    return p.fg_cell_muted if ev.video.rotate_type == 0 else None


def _keyframe_filter_value(ev: VideoEvaluation) -> float | None:
    kf = ev.video.keyframe_interval
    return None if kf is None or kf < 0 else float(kf)


def _format_keyframe_display(ev: VideoEvaluation, cfg: ClassifierConfig) -> str:
    return format_keyframe_interval(
        ev.video.keyframe_interval,
        cfg.display,
        exceeded=ev.is_keyframe_exceeded,
        show_warn=True,
    )


_SIZE_UNIT_DESCRIPTIONS: dict[str, str] = {
    "mb": "MB",
    "gb": "GB",
    "auto": "자동 스케일(KB/MB/GB/TB)",
}


def _size_unit_desc(size_unit: str) -> str:
    return _SIZE_UNIT_DESCRIPTIONS.get(size_unit, "MB")


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    return format_duration_hms(seconds)


def _output_res_filter_value(ev: VideoEvaluation) -> str:
    if not ev.is_downscaled or ev.target_tier is None:
        return _OUTPUT_RES_NOT_DOWNSCALED_LABEL
    return ev.target_tier.display


def _output_res_sort_value(ev: VideoEvaluation) -> int:
    if not ev.is_downscaled:
        return 0
    return (ev.target_width or 0) * (ev.target_height or 0)


def _format_output_res_display(ev: VideoEvaluation, _cfg: ClassifierConfig) -> str:
    if not ev.is_downscaled:
        return _OUTPUT_RES_NOT_DOWNSCALED_CELL
    return f"{ev.target_width}x{ev.target_height}"


def _fg_output_res(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    return p.fg_maxrate_warn if ev.is_downscaled and ev.is_maxrate_near else None


def _format_saved_per_min_display(ev: VideoEvaluation, _cfg: ClassifierConfig) -> str:
    val_str = format_float_precision(
        ev.preview_mb_per_min, _SAVED_PER_MIN_DECIMALS, signed=True
    )
    return f"{val_str} MB/분"


@dataclass(frozen=True, slots=True)
class TableColumnDef:
    """단일 테이블 열의 헤더, 스펙, 렌더링, 정렬, 필터, 배지 동작 단일 명세"""

    col_id: int
    header: str
    spec: ColumnSpec
    tooltip_factory: Callable[[ClassifierConfig], str]
    filter_value: Callable[[VideoEvaluation], str | float | None]
    header_factory: Callable[[ClassifierConfig], str] | None = None
    display_value: Callable[[VideoEvaluation, ClassifierConfig], Any] | None = None
    sort_value: Callable[[VideoEvaluation], Any] | None = None
    badge_getter: Callable[[VideoEvaluation, ThemePalette], Any] | None = None
    icon_getter: Callable[[VideoEvaluation], str | None] | None = None
    foreground_getter: Callable[[VideoEvaluation, ThemePalette], str | None] | None = None
    align: int = ALIGN_RIGHT
    font_kind: str | None = None
    font_kind_getter: Callable[[VideoEvaluation], str | None] | None = None
    numeric_format: str | None = None
    value_sort_key: Callable[[str], Any] = natural_sort_key
    full_header: str | None = None

    @property
    def menu_title(self) -> str:
        """열 표시 메뉴, 필터 팝오버, CSV 헤더에 쓰는 풀네임 반환"""
        return self.full_header or self.header


def _make_size_column(
    col_id: int,
    label_id: ColumnId,
    bucket_kind: NumericBucketKind,
    tooltip_prefix: str,
    value_getter: Callable[[VideoEvaluation], float],
    decimals_getter: Callable[[DisplayConfig], int],
    *,
    signed: bool = False,
    foreground_getter: Callable[[VideoEvaluation, ThemePalette], str | None]
    | None = None,
    font_kind: str = FONT_MONO,
    font_kind_getter: Callable[[VideoEvaluation], str | None] | None = None,
) -> TableColumnDef:
    base_header = COLUMN_LABELS[label_id].full
    return TableColumnDef(
        col_id=col_id,
        header=base_header,
        spec=_num_spec(col_id, "MB", 2, 5.0, bucket_kind),
        tooltip_factory=lambda cfg: (
            f"{tooltip_prefix}단위: {_size_unit_desc(cfg.display.size_unit)})"
        ),
        filter_value=lambda ev: float(value_getter(ev)),
        header_factory=lambda cfg: format_size_header(
            base_header, cfg.display.size_unit
        ),
        display_value=lambda ev, cfg: format_size_mb(
            value_getter(ev),
            decimals_getter(cfg.display),
            unit=cfg.display.size_unit,
            signed=signed,
        ),
        foreground_getter=foreground_getter,
        font_kind=font_kind,
        font_kind_getter=font_kind_getter,
    )


COL_DEFS: tuple[TableColumnDef, ...] = (
    TableColumnDef(
        col_id=0,
        header=COLUMN_LABELS[ColumnId.NAME].full,
        spec=ColumnSpec(0, ColumnDataType.TEXT),
        tooltip_factory=_static_tip("파일 이름입니다."),
        filter_value=lambda ev: ev.video.filename,
        sort_value=lambda ev: WindowsNameOrder(ev.video.filename),
        align=ALIGN_LEFT,
    ),
    TableColumnDef(
        col_id=1,
        header=COLUMN_LABELS[ColumnId.STATUS].short,
        full_header=COLUMN_LABELS[ColumnId.STATUS].full,
        spec=ColumnSpec(
            1, ColumnDataType.ENUM, preset_values=tuple(s.value for s in ClassifyStatus)
        ),
        tooltip_factory=format_classify_status_tooltip,
        filter_value=lambda ev: ev.classify_status.value,
        sort_value=lambda ev: _STATUS_SORT_RANK.get(ev.classify_status, 0),
        badge_getter=lambda ev, p: _BADGE_BY_STATUS[ev.classify_status](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=2,
        header=COLUMN_LABELS[ColumnId.WIDTH].full,
        spec=_num_spec(2, "px", 0, 10.0, NumericBucketKind.WIDTH),
        tooltip_factory=_static_tip("회전 보정이 반영된 너비입니다. (px)"),
        filter_value=lambda ev: float(ev.video.width),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=3,
        header=COLUMN_LABELS[ColumnId.HEIGHT].full,
        spec=_num_spec(3, "px", 0, 10.0, NumericBucketKind.HEIGHT),
        tooltip_factory=_static_tip("회전 보정이 반영된 높이입니다. (px)"),
        filter_value=lambda ev: float(ev.video.height),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=4,
        header=COLUMN_LABELS[ColumnId.ROTATE].full,
        spec=ColumnSpec(4, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "컨테이너 무관 시계 방향 기준으로 정규화된 회전 각도입니다. (°)"
        ),
        filter_value=_format_rotate_text,
        sort_value=lambda ev: ev.video.rotate_type,
        icon_getter=_rotate_icon_name,
        foreground_getter=_fg_rotate,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=5,
        header=COLUMN_LABELS[ColumnId.CODEC].full,
        spec=ColumnSpec(5, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip("영상의 인코딩 코덱입니다."),
        filter_value=lambda ev: ev.video.codec,
        sort_value=lambda ev: ev.video.codec.lower(),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=6,
        header=COLUMN_LABELS[ColumnId.FPS].full,
        spec=_num_spec(6, "fps", 2, 1.0, NumericBucketKind.FPS),
        tooltip_factory=_static_tip("영상의 초당 프레임 수입니다."),
        filter_value=lambda ev: float(ev.video.fps),
        display_value=lambda ev, cfg: format_fps(ev.video.fps, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=7,
        header=COLUMN_LABELS[ColumnId.DURATION].full,
        spec=_num_spec(7, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=_static_tip("영상의 재생 시간입니다. (HH:MM:SS)"),
        filter_value=lambda ev: float(ev.video.duration),
        display_value=lambda ev, _cfg: format_duration(ev.video.duration),
    ),
    TableColumnDef(
        col_id=8,
        header=COLUMN_LABELS[ColumnId.BITRATE].short,
        full_header=COLUMN_LABELS[ColumnId.BITRATE].full,
        spec=_num_spec(8, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("BR(비트레이트)는 현재 영상 비트레이트입니다. (kbps)"),
        filter_value=lambda ev: float(ev.video.vid_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=9,
        header=COLUMN_LABELS[ColumnId.OPT_BITRATE].short,
        full_header=COLUMN_LABELS[ColumnId.OPT_BITRATE].full,
        spec=_num_spec(9, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip(
            "최적 BR(최적 비트레이트)은 해상도, 프레임레이트, 코덱을 반영한 CQ22(화질 기준값 22) 수준의 기준 비트레이트입니다. (kbps)"
        ),
        filter_value=lambda ev: float(ev.optimal_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=10,
        header=COLUMN_LABELS[ColumnId.RATIO].short,
        full_header=COLUMN_LABELS[ColumnId.RATIO].full,
        spec=_num_spec(10, "배", 2, 0.1, NumericBucketKind.RATIO),
        tooltip_factory=_static_tip("BR 비율(비트레이트 비율)은 최적 비트레이트 대비 현재 비트레이트 비율입니다. (배)"),
        filter_value=lambda ev: float(ev.bitrate_ratio),
        display_value=lambda ev, cfg: format_ratio(ev.bitrate_ratio, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=11,
        header=COLUMN_LABELS[ColumnId.TARGET_RATIO].short,
        full_header=COLUMN_LABELS[ColumnId.TARGET_RATIO].full,
        spec=_num_spec(11, "배", 2, 0.1, NumericBucketKind.TARGET_RATIO),
        tooltip_factory=_static_tip(
            "출력 BR 비율은 목표 인코딩 코덱 기준 최적 비트레이트 대비 현재 비트레이트 비율입니다. 과화질 판정과 예상 용량에 쓰입니다. (배)"
        ),
        filter_value=lambda ev: float(ev.target_bitrate_ratio),
        display_value=lambda ev, cfg: format_ratio(ev.target_bitrate_ratio, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=12,
        header=COLUMN_LABELS[ColumnId.ZONE].full,
        spec=ColumnSpec(
            12, ColumnDataType.ENUM, preset_values=tuple(z.value for z in reversed(QualityZone))
        ),
        tooltip_factory=lambda cfg: format_quality_zone_tooltip(cfg.thresholds),
        filter_value=lambda ev: ev.quality_zone.value,
        sort_value=lambda ev: _ZONE_SORT_RANK.get(ev.quality_zone, 0),
        badge_getter=lambda ev, p: _BADGE_BY_ZONE[ev.quality_zone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=13,
        header=COLUMN_LABELS[ColumnId.KEYFRAME].short,
        full_header=COLUMN_LABELS[ColumnId.KEYFRAME].full,
        spec=_num_spec(
            12, "초", 2, 0.5, NumericBucketKind.KEYFRAME, nullable_label=LABEL_UNMEASURED
        ),
        tooltip_factory=lambda cfg: format_keyframe_tooltip(cfg.thresholds),
        filter_value=_keyframe_filter_value,
        display_value=_format_keyframe_display,
        sort_value=lambda ev: (
            ev.video.keyframe_interval if ev.video.keyframe_interval is not None else -1.0
        ),
        foreground_getter=fg_keyframe,
        align=ALIGN_CENTER,
    ),
    _make_size_column(
        14,
        ColumnId.SIZE,
        NumericBucketKind.SIZE,
        "현재 용량 (",
        lambda ev: ev.video.vid_size_MB,
        lambda d: d.size_decimals,
    ),
    _make_size_column(
        15,
        ColumnId.AFTER_SIZE,
        NumericBucketKind.SIZE,
        "재인코딩 후 예상 용량 (원본 보존 행은 재인코딩을 가정한 참고값, ",
        lambda ev: ev.preview_after_mb,
        lambda d: d.after_size_decimals,
        foreground_getter=fg_after_size,
    ),
    _make_size_column(
        16,
        ColumnId.SAVED_SIZE,
        NumericBucketKind.SAVED_SIZE,
        "재인코딩 후 예상 절약 (+: 절약, -: 키프레임 재인코딩에 따른 용량 증가, 원본 보존 행 회색 일반체는 재인코딩을 가정한 참고값, ",
        lambda ev: ev.preview_delta_mb,
        lambda d: d.saved_size_decimals,
        signed=True,
        foreground_getter=fg_saved_size,
        font_kind_getter=font_saved_size,
    ),
    TableColumnDef(
        col_id=17,
        header=COLUMN_LABELS[ColumnId.SAVED_PER_MIN].full,
        spec=_num_spec(17, "MB/분", 2, 1.0, NumericBucketKind.SAVED_PER_MIN),
        tooltip_factory=format_saved_per_min_tooltip,
        filter_value=lambda ev: float(ev.preview_mb_per_min),
        display_value=_format_saved_per_min_display,
        foreground_getter=fg_saved_per_min,
        font_kind=FONT_MONO,
        font_kind_getter=font_saved_per_min,
    ),
    TableColumnDef(
        col_id=18,
        header=COLUMN_LABELS[ColumnId.EST_ENCODE_TIME].full,
        spec=_num_spec(18, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=format_est_encode_time_tooltip,
        filter_value=lambda ev: float(ev.estimated_encode_sec),
        display_value=lambda ev, _cfg: format_duration(ev.estimated_encode_sec),
        foreground_getter=fg_est_encode_time,
    ),
    TableColumnDef(
        col_id=19,
        header=COLUMN_LABELS[ColumnId.OUTPUT_RES].full,
        spec=ColumnSpec(
            19, ColumnDataType.ENUM, preset_values=_OUTPUT_RES_FILTER_LABELS
        ),
        tooltip_factory=_static_tip(
            "축소 재인코딩 영상의 결과 해상도입니다. 축소 대상이 아니면 -로 표시합니다."
        ),
        filter_value=_output_res_filter_value,
        sort_value=_output_res_sort_value,
        display_value=_format_output_res_display,
        foreground_getter=_fg_output_res,
        font_kind=FONT_MONO,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=20,
        header=COLUMN_LABELS[ColumnId.ACTION].full,
        spec=ColumnSpec(20, ColumnDataType.ENUM, preset_values=SMART_ACTION_LABELS),
        tooltip_factory=format_action_tooltip,
        filter_value=lambda ev: ev.action_label,
        badge_getter=lambda ev, p: _BADGE_BY_ACTION_TONE[ev.action_badge_tone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=21,
        header=COLUMN_LABELS[ColumnId.REASON].full,
        spec=ColumnSpec(21, ColumnDataType.ENUM, preset_values=SMART_ACTION_REASONS),
        tooltip_factory=format_reason_tooltip,
        filter_value=lambda ev: ev.reason_label,
        badge_getter=lambda ev, p: _BADGE_BY_REASON.get(
            ev.reason_label, lambda pal: pal.badge_pass
        )(p),
        align=ALIGN_CENTER,
    ),
)

COL_DEF_MAP: dict[int, TableColumnDef] = {d.col_id: d for d in COL_DEFS}
