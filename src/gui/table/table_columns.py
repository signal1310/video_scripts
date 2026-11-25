"""테이블 열 스키마, 셀 포맷터, 배지 매핑 및 행 툴팁 포맷터 모듈"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt

from src.core.config import ClassifierConfig, DisplayConfig
from src.core.encode.encode_batch_book import (
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    STATUS_SKIPPED,
)
from src.core.formatting import (
    format_fps,
    format_ratio,
    format_size_header,
    format_size_mb,
)
from src.core.messages import (
    COLUMN_LABELS,
    LABEL_UNMEASURED,
    ColumnId,
    format_action_tooltip,
    format_keyframe_tooltip,
    format_quality_zone_tooltip,
    format_reason_tooltip,
)
from src.core.models import QUALITY_ZONE_SORT_RANK, QualityZone, VideoEvaluation
from src.core.smart_action import (
    SMART_ACTION_LABELS,
    SMART_ACTION_REASONS,
)
from src.core.sorting import WindowsNameOrder, natural_sort_key
from src.core.workspace_paths import to_display_filename
from src.gui.styles.palettes import ThemePalette
from src.gui.table.column_filters import (
    ColumnDataType,
    ColumnSpec,
)
from src.gui.table.numeric_buckets import NumericBucketKind
from src.gui.table.table_cell_tooltips import (
    action_cell_tip,
    after_size_cell_tip,
    bitrate_cell_tip,
    codec_cell_tip,
    duration_cell_tip,
    encode_state_cell_tip,
    est_encode_time_cell_tip,
    fps_cell_tip,
    height_cell_tip,
    keyframe_cell_tip,
    optimal_bitrate_cell_tip,
    out_bitrate_cell_tip,
    ratio_cell_tip,
    reason_cell_tip,
    rotate_cell_tip,
    saved_per_min_cell_tip,
    saved_size_cell_tip,
    size_cell_tip,
    target_ratio_cell_tip,
    width_cell_tip,
    zone_cell_tip,
)
from src.gui.table.table_column_formats import (
    BADGE_BY_ZONE,
    OUTPUT_RES_FILTER_LABELS,
    action_badge,
    action_foreground,
    fg_output_res,
    fg_rotate,
    format_duration,
    format_keyframe_display,
    format_output_res_display,
    format_rotate_text,
    format_saved_per_min_display,
    keyframe_filter_value,
    keyframe_icon_name,
    output_res_filter_value,
    output_res_sort_value,
    reason_foreground,
    rotate_icon_name,
    size_unit_desc,
)
from src.gui.table.table_column_styles import (
    FONT_MONO,
    FONT_MONO_BOLD,
    FONT_PATH,
    fg_after_size,
    fg_est_encode_time,
    fg_keyframe,
    fg_saved_per_min,
    fg_saved_size,
    font_saved_per_min,
    font_saved_size,
)
from src.gui.table.table_tooltips import (
    format_est_encode_time_tooltip,
    format_saved_per_min_tooltip,
)

DEFAULT_HEADER_CONFIG: ClassifierConfig = ClassifierConfig()

ALIGN_LEFT: int = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
ALIGN_CENTER: int = int(Qt.AlignmentFlag.AlignCenter)
ALIGN_RIGHT: int = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

ENCODE_STATE_LABELS: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    STATUS_DONE,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_REPLACE_PENDING,
)

_ZONE_SORT_RANK: dict[QualityZone, int] = QUALITY_ZONE_SORT_RANK
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
    align_factory: Callable[[ClassifierConfig], int] | None = None
    font_kind: str | None = None
    font_kind_getter: Callable[[VideoEvaluation], str | None] | None = None
    numeric_format: str | None = None
    value_sort_key: Callable[[str], Any] = natural_sort_key
    full_header: str | None = None
    cell_tooltip: Callable[[VideoEvaluation], str] | None = None

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
    cell_tooltip: Callable[[VideoEvaluation], str],
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
            f"{tooltip_prefix}단위: {size_unit_desc(cfg.display.size_unit)})"
        ),
        cell_tooltip=cell_tooltip,
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
        display_value=lambda ev, _cfg: to_display_filename(ev.video.filename),
        filter_value=lambda ev: ev.video.filename,
        sort_value=lambda ev: WindowsNameOrder(ev.video.filename),
        align=ALIGN_LEFT,
        align_factory=lambda cfg: (
            ALIGN_RIGHT if cfg.display.filename_align == "right" else ALIGN_LEFT
        ),
        font_kind=FONT_PATH,
    ),
    TableColumnDef(
        col_id=1,
        header=COLUMN_LABELS[ColumnId.WIDTH].full,
        spec=_num_spec(1, "px", 0, 10.0, NumericBucketKind.WIDTH),
        tooltip_factory=_static_tip("회전 보정이 반영된 너비입니다. (px)"),
        cell_tooltip=width_cell_tip,
        filter_value=lambda ev: float(ev.video.width),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=2,
        header=COLUMN_LABELS[ColumnId.HEIGHT].full,
        spec=_num_spec(2, "px", 0, 10.0, NumericBucketKind.HEIGHT),
        tooltip_factory=_static_tip("회전 보정이 반영된 높이입니다. (px)"),
        cell_tooltip=height_cell_tip,
        filter_value=lambda ev: float(ev.video.height),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=3,
        header=COLUMN_LABELS[ColumnId.ROTATE].full,
        spec=ColumnSpec(3, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "컨테이너 무관 시계 방향 기준으로 정규화된 회전 각도입니다. (°)"
        ),
        cell_tooltip=rotate_cell_tip,
        filter_value=format_rotate_text,
        sort_value=lambda ev: ev.video.rotate_type,
        icon_getter=rotate_icon_name,
        foreground_getter=fg_rotate,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=4,
        header=COLUMN_LABELS[ColumnId.CODEC].full,
        spec=ColumnSpec(4, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip("영상의 인코딩 코덱입니다."),
        cell_tooltip=codec_cell_tip,
        filter_value=lambda ev: ev.video.codec,
        sort_value=lambda ev: ev.video.codec.lower(),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=5,
        header=COLUMN_LABELS[ColumnId.FPS].full,
        spec=_num_spec(5, "fps", 2, 1.0, NumericBucketKind.FPS),
        tooltip_factory=_static_tip("영상의 초당 프레임 수입니다."),
        cell_tooltip=fps_cell_tip,
        filter_value=lambda ev: float(ev.video.fps),
        display_value=lambda ev, cfg: format_fps(ev.video.fps, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=6,
        header=COLUMN_LABELS[ColumnId.DURATION].full,
        spec=_num_spec(6, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=_static_tip("영상의 재생 시간입니다. (HH:MM:SS)"),
        cell_tooltip=duration_cell_tip,
        filter_value=lambda ev: float(ev.video.duration),
        display_value=lambda ev, _cfg: format_duration(ev.video.duration),
    ),
    TableColumnDef(
        col_id=7,
        header=COLUMN_LABELS[ColumnId.BITRATE].short,
        full_header=COLUMN_LABELS[ColumnId.BITRATE].full,
        spec=_num_spec(7, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("BR(비트레이트)는 현재 영상 비트레이트입니다. (kbps)"),
        cell_tooltip=bitrate_cell_tip,
        filter_value=lambda ev: float(ev.video.vid_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=8,
        header=COLUMN_LABELS[ColumnId.OPT_BITRATE].short,
        full_header=COLUMN_LABELS[ColumnId.OPT_BITRATE].full,
        spec=_num_spec(8, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip(
            "최적 BR(최적 비트레이트)은 해상도, FPS, 코덱을 반영한 화질 기준값 수준의 기준 비트레이트입니다. (kbps)"
        ),
        cell_tooltip=optimal_bitrate_cell_tip,
        filter_value=lambda ev: float(ev.optimal_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=9,
        header=COLUMN_LABELS[ColumnId.RATIO].short,
        full_header=COLUMN_LABELS[ColumnId.RATIO].full,
        spec=_num_spec(9, "배", 2, 0.1, NumericBucketKind.RATIO),
        tooltip_factory=_static_tip("BR 비율(비트레이트 비율)은 최적 비트레이트 대비 현재 비트레이트 비율입니다. (배)"),
        cell_tooltip=ratio_cell_tip,
        filter_value=lambda ev: float(ev.bitrate_ratio),
        display_value=lambda ev, cfg: format_ratio(ev.bitrate_ratio, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=10,
        header=COLUMN_LABELS[ColumnId.TARGET_RATIO].short,
        full_header=COLUMN_LABELS[ColumnId.TARGET_RATIO].full,
        spec=_num_spec(10, "배", 2, 0.1, NumericBucketKind.TARGET_RATIO),
        tooltip_factory=_static_tip(
            "출력 BR 비율은 목표 인코딩 코덱 기준 최적 비트레이트 대비 현재 비트레이트 비율입니다. 과화질 판정과 예상 용량에 쓰입니다. (배)"
        ),
        cell_tooltip=target_ratio_cell_tip,
        filter_value=lambda ev: float(ev.target_bitrate_ratio),
        display_value=lambda ev, cfg: format_ratio(ev.target_bitrate_ratio, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=11,
        header=COLUMN_LABELS[ColumnId.ZONE].full,
        spec=ColumnSpec(
            11, ColumnDataType.ENUM, preset_values=tuple(z.value for z in reversed(QualityZone))
        ),
        tooltip_factory=lambda cfg: format_quality_zone_tooltip(cfg.thresholds),
        cell_tooltip=zone_cell_tip,
        filter_value=lambda ev: ev.quality_zone.value,
        sort_value=lambda ev: _ZONE_SORT_RANK.get(ev.quality_zone, 0),
        badge_getter=lambda ev, p: BADGE_BY_ZONE[ev.quality_zone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=12,
        header=COLUMN_LABELS[ColumnId.KEYFRAME].short,
        full_header=COLUMN_LABELS[ColumnId.KEYFRAME].full,
        spec=_num_spec(
            12, "초", 2, 0.5, NumericBucketKind.KEYFRAME, nullable_label=LABEL_UNMEASURED
        ),
        tooltip_factory=lambda cfg: format_keyframe_tooltip(cfg.thresholds),
        cell_tooltip=keyframe_cell_tip,
        filter_value=keyframe_filter_value,
        display_value=format_keyframe_display,
        sort_value=lambda ev: (
            ev.video.keyframe_interval if ev.video.keyframe_interval is not None else -1.0
        ),
        icon_getter=keyframe_icon_name,
        foreground_getter=fg_keyframe,
        align=ALIGN_CENTER,
    ),
    _make_size_column(
        13,
        ColumnId.SIZE,
        NumericBucketKind.SIZE,
        "현재 용량 (",
        lambda ev: ev.video.vid_size_MB,
        lambda d: d.size_decimals,
        cell_tooltip=size_cell_tip,
    ),
    _make_size_column(
        14,
        ColumnId.AFTER_SIZE,
        NumericBucketKind.SIZE,
        "인코딩 후 예상 용량 (원본 보존 행은 인코딩을 가정한 참고값, ",
        lambda ev: ev.preview_after_mb,
        lambda d: d.after_size_decimals,
        cell_tooltip=after_size_cell_tip,
        foreground_getter=fg_after_size,
    ),
    _make_size_column(
        15,
        ColumnId.SAVED_SIZE,
        NumericBucketKind.SAVED_SIZE,
        "인코딩 후 예상 절약 (+: 절약, -: 키프레임 인코딩에 따른 용량 증가, 원본 보존 행 회색 일반체는 인코딩을 가정한 참고값, ",
        lambda ev: ev.preview_delta_mb,
        lambda d: d.saved_size_decimals,
        cell_tooltip=saved_size_cell_tip,
        signed=True,
        foreground_getter=fg_saved_size,
        font_kind_getter=font_saved_size,
    ),
    TableColumnDef(
        col_id=16,
        header=COLUMN_LABELS[ColumnId.SAVED_PER_MIN].full,
        spec=_num_spec(16, "MB/분", 2, 1.0, NumericBucketKind.SAVED_PER_MIN),
        tooltip_factory=format_saved_per_min_tooltip,
        cell_tooltip=saved_per_min_cell_tip,
        filter_value=lambda ev: float(ev.preview_mb_per_min),
        display_value=format_saved_per_min_display,
        foreground_getter=fg_saved_per_min,
        font_kind=FONT_MONO,
        font_kind_getter=font_saved_per_min,
    ),
    TableColumnDef(
        col_id=17,
        header=COLUMN_LABELS[ColumnId.EST_ENCODE_TIME].full,
        spec=_num_spec(17, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=format_est_encode_time_tooltip,
        cell_tooltip=est_encode_time_cell_tip,
        filter_value=lambda ev: float(ev.estimated_encode_sec),
        display_value=lambda ev, _cfg: format_duration(ev.estimated_encode_sec),
        foreground_getter=fg_est_encode_time,
    ),
    TableColumnDef(
        col_id=18,
        header=COLUMN_LABELS[ColumnId.OUTPUT_RES].full,
        spec=ColumnSpec(
            18, ColumnDataType.ENUM, preset_values=OUTPUT_RES_FILTER_LABELS
        ),
        tooltip_factory=_static_tip(
            "축소 인코딩 영상의 결과 해상도입니다. 축소 대상이 아니면 -로 표시합니다."
        ),
        filter_value=output_res_filter_value,
        sort_value=output_res_sort_value,
        display_value=format_output_res_display,
        foreground_getter=fg_output_res,
        font_kind=FONT_MONO_BOLD,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=19,
        header=COLUMN_LABELS[ColumnId.ACTIVE_FRAME].full,
        spec=ColumnSpec(19, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "화질 분석으로 찾은 검은 띠를 뺀 활성 화면입니다. 검은 띠가 없으면 -로 표시합니다."
        ),
        filter_value=lambda ev: None,
        align=ALIGN_CENTER,
        font_kind=FONT_MONO_BOLD,
    ),
    TableColumnDef(
        col_id=20,
        header=COLUMN_LABELS[ColumnId.DOWNSCALE_CANDIDATE].short,
        full_header=COLUMN_LABELS[ColumnId.DOWNSCALE_CANDIDATE].full,
        spec=ColumnSpec(20, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "화질 분석에서 화질 차이 기준을 넘는 가장 낮은 해상도 규격입니다. 후보가 없으면 유지, 유효 구간이 없으면 측정 불가입니다."
        ),
        filter_value=lambda ev: None,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=21,
        header=COLUMN_LABELS[ColumnId.QUALITY_METRIC].full,
        spec=ColumnSpec(21, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "축소 후보 판정에 쓴 측정 지표입니다. VMAF를 쓸 수 없으면 SSIM (대체)로 표시합니다."
        ),
        filter_value=lambda ev: None,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=22,
        header=COLUMN_LABELS[ColumnId.ACTION].full,
        spec=ColumnSpec(22, ColumnDataType.ENUM, preset_values=SMART_ACTION_LABELS),
        tooltip_factory=format_action_tooltip,
        cell_tooltip=action_cell_tip,
        filter_value=lambda ev: ev.action_label,
        badge_getter=action_badge,
        foreground_getter=action_foreground,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=23,
        header=COLUMN_LABELS[ColumnId.REASON].full,
        spec=ColumnSpec(23, ColumnDataType.ENUM, preset_values=SMART_ACTION_REASONS),
        tooltip_factory=format_reason_tooltip,
        cell_tooltip=reason_cell_tip,
        filter_value=lambda ev: ev.reason_label,
        foreground_getter=reason_foreground,
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=24,
        header=COLUMN_LABELS[ColumnId.OUT_BITRATE].short,
        full_header=COLUMN_LABELS[ColumnId.OUT_BITRATE].full,
        spec=_num_spec(24, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip(
            "출력 BR(출력 비트레이트)은 인코딩 후 출력 해상도와 목표 코덱 기준 예상 평균 비트레이트입니다. (kbps)"
        ),
        cell_tooltip=out_bitrate_cell_tip,
        filter_value=lambda ev: float(ev.target_optimal_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=25,
        header=COLUMN_LABELS[ColumnId.ENCODE_STATE].short,
        full_header=COLUMN_LABELS[ColumnId.ENCODE_STATE].full,
        spec=ColumnSpec(25, ColumnDataType.ENUM, preset_values=ENCODE_STATE_LABELS),
        tooltip_factory=_static_tip(
            "인코딩 배치에서 이 영상의 상태입니다. 배치 대상이 아니면 비어 있습니다."
        ),
        # - 상태는 배치 진행에 따라 바뀌므로 정렬과 필터 값은 표 모델이 따로 계산하지 않음
        cell_tooltip=encode_state_cell_tip,
        filter_value=lambda ev: None,
        align=ALIGN_CENTER,
    ),
)

COL_DEF_MAP: dict[int, TableColumnDef] = {d.col_id: d for d in COL_DEFS}
