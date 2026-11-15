"""테이블 열 스키마, 셀 포맷터, 배지 매핑 및 행 툴팁 포맷터 모듈"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt

from src.core.config import ClassifierConfig, DisplayConfig, get_encode_codec_label
from src.core.file_manager import logical_video_filepath
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
    LABEL_NO_MOVE_TARGET,
    LABEL_UNMEASURED,
    LABEL_UNMEASURED_DETAIL,
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
    QualityZone,
    SmartAction,
    VideoEvaluation,
)
from src.core.sorting import WindowsNameOrder, natural_sort_key
from src.gui.column_filters import (
    ColumnDataType,
    ColumnSpec,
    NumericBucketKind,
)
from src.gui.styles.palettes import ThemePalette

DEFAULT_HEADER_CONFIG: ClassifierConfig = ClassifierConfig()
_SAVED_COLOR_THRESHOLD_MB: float = 0.05
_HALF_TURN_DEG: int = 180
_SAVINGS_RATE_DECIMALS: int = 1
_SAVINGS_RATE_EXAMPLE_MINUTES: int = 10

ALIGN_LEFT: int = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
ALIGN_CENTER: int = int(Qt.AlignmentFlag.AlignCenter)
ALIGN_RIGHT: int = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
FONT_MONO: str = "mono"
FONT_BOLD: str = "bold"

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
    SmartAction.ENCODE_CQ22_OPT_KF.reason_label: lambda p: p.badge_defect,
    SmartAction.PASS_OPTIMAL.reason_label: lambda p: p.badge_optimal,
    SmartAction.PASS_LOW.reason_label: lambda p: p.badge_low,
    SmartAction.PASS_LOW_SAVINGS.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_LOW_EFFICIENCY.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_EXCEPTION.reason_label: lambda p: p.badge_exception,
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


def _format_status_tooltip_detail(ev: VideoEvaluation) -> str:
    status_label = ev.classify_status.value
    if ev.video.moved_dirname:
        return f"{status_label} (현재 위치: {ev.video.moved_dirname})"
    if ev.target_dirname:
        return f"{status_label} (대상 폴더: {ev.target_dirname})"
    return f"{status_label} ({LABEL_NO_MOVE_TARGET})"


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
        defective=ev.is_keyframe_defective,
        show_warn=True,
    )


_SIZE_UNIT_DESCRIPTIONS: dict[str, str] = {
    "mb": "MB",
    "gb": "GB",
    "auto": "자동 스케일(KB/MB/GB/TB)",
}


def _size_unit_desc(size_unit: str) -> str:
    return _SIZE_UNIT_DESCRIPTIONS.get(size_unit, "MB")


def _fg_keyframe(ev: VideoEvaluation, p: ThemePalette) -> str:
    if ev.is_keyframe_defective:
        return p.fg_kf_defect
    kf = ev.video.keyframe_interval
    return p.fg_cell_muted if kf is None or kf < 0 else p.fg_kf_opt


def _fg_after_size(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    if ev.is_encode_target:
        return None
    if abs(ev.preview_delta_mb) > _SAVED_COLOR_THRESHOLD_MB:
        return p.text_secondary
    return p.fg_cell_muted


def _fg_signed_metric(
    ev: VideoEvaluation, value: float, threshold: float, p: ThemePalette
) -> str:
    if not ev.is_encode_target:
        return p.text_secondary if abs(value) > threshold else p.fg_cell_muted
    if value > threshold:
        return p.fg_saved_pos
    return p.fg_saved_neg if value < -threshold else p.fg_cell_muted


def _font_signed_metric(ev: VideoEvaluation, value: float, threshold: float) -> str:
    return (
        FONT_BOLD
        if ev.is_encode_target and abs(value) > threshold
        else FONT_MONO
    )


def _fg_saved_size(ev: VideoEvaluation, p: ThemePalette) -> str:
    return _fg_signed_metric(ev, ev.preview_delta_mb, _SAVED_COLOR_THRESHOLD_MB, p)


def _font_saved_size(ev: VideoEvaluation) -> str:
    return _font_signed_metric(ev, ev.preview_delta_mb, _SAVED_COLOR_THRESHOLD_MB)


def _fg_savings_rate(ev: VideoEvaluation, p: ThemePalette) -> str:
    return _fg_signed_metric(ev, ev.preview_mb_per_min, _SAVED_COLOR_THRESHOLD_MB, p)


def _font_savings_rate(ev: VideoEvaluation) -> str:
    return _font_signed_metric(ev, ev.preview_mb_per_min, _SAVED_COLOR_THRESHOLD_MB)


def _fg_est_encode_time(ev: VideoEvaluation, p: ThemePalette) -> str | None:
    if ev.is_encode_target:
        return None
    return p.text_secondary if ev.estimated_encode_sec > 0 else p.fg_cell_muted


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    return format_duration_hms(seconds)


def _format_savings_rate_display(ev: VideoEvaluation, _cfg: ClassifierConfig) -> str:
    val_str = format_float_precision(
        ev.preview_mb_per_min, _SAVINGS_RATE_DECIMALS, signed=True
    )
    return f"{val_str} MB/분"


def _format_savings_rate_tooltip(cfg: ClassifierConfig) -> str:
    min_rate = cfg.efficiency.min_mb_per_min
    exempt_mb = cfg.efficiency.exempt_saved_mb
    if min_rate > 0:
        example_mb = min_rate * _SAVINGS_RATE_EXAMPLE_MINUTES
        exempt_note = (
            f" (단, 총 {exempt_mb:.0f} MB 이상 절감 시 예외 포함)"
            if exempt_mb > 0
            else ""
        )
        limit_line = (
            f"현재 효율 하한: {min_rate:.1f} MB/분 미만은 시간 대비 이득이 적어 원본 보존"
            f"{exempt_note} ({_SAVINGS_RATE_EXAMPLE_MINUTES}분 영상 기준 {example_mb:.1f} MB 미만 절감 시 제외)"
        )
    else:
        limit_line = "현재 효율 하한: 사용 안 함 (분당 절감량 제한 없이 통과)"
    return (
        "원본 영상 재생 길이 1분당 예상 용량 절감량(MB/분)으로, 인코딩 시간 대비 용량 절감 가성비를 나타냅니다.\n"
        "계산: 예상 절감 용량(MB) ÷ 영상 재생 길이(분)  (예: 10분 영상에서 50 MB 절감 시 +5.0 MB/분)\n"
        f"{limit_line}\n"
        "표기: +는 용량 절감, -는 1초 키프레임 교정 등으로 증가, 회색 일반체는 원본 보존 행을 만약 인코딩할 경우의 가상 참고치"
    )


def format_row_tooltip(ev: VideoEvaluation, root_dir: str) -> str:
    """테이블 행 호버 시 표시할 파일, 회전 출처, 품질, 판정 사유 상세 툴팁 생성"""
    vid = ev.video
    full_path = logical_video_filepath(root_dir, vid)
    kf_str = (
        f"{vid.keyframe_interval:.3f}초"
        if vid.keyframe_interval is not None
        else LABEL_UNMEASURED_DETAIL
    )
    rotate_detail = (
        f"{vid.rotate_type}° (원본: {vid.rotate_source})"
        if vid.rotate_source
        else f"{vid.rotate_type}°"
    )
    est_time_str = format_duration_hms(ev.estimated_encode_sec)
    if ev.is_encode_target:
        size_line = (
            f"용량 변화: {vid.vid_size_MB:.2f} MB → {ev.expected_after_mb:.2f} MB "
            f"(절감 {ev.expected_delta_mb:+.2f} MB, {ev.preview_mb_per_min:+.2f} MB/분)\n"
            f"예상 인코딩 소요: 약 {est_time_str}\n"
        )
    else:
        per_min = (
            f", {ev.preview_mb_per_min:+.2f} MB/분"
            if vid.duration > 0 and abs(ev.preview_delta_mb) > _SAVED_COLOR_THRESHOLD_MB
            else ""
        )
        size_line = (
            f"용량 변화: 원본 유지 ({vid.vid_size_MB:.2f} MB), "
            f"만약 인코딩 시 {ev.preview_after_mb:.2f} MB ({ev.preview_delta_mb:+.2f} MB{per_min}, "
            f"예상 인코딩 소요 약 {est_time_str})\n"
        )
    return (
        f"파일명: {vid.filename}\n"
        f"경로: {full_path}\n"
        f"해상도: {vid.width}x{vid.height} (비율 {vid.aspect_ratio:.3f}, 회전 {rotate_detail})\n"
        f"코덱 / FPS: {vid.codec} / {vid.fps:.3f} fps\n"
        f"비트레이트: 현재 {vid.vid_kbps:,} kbps / 최적 {ev.optimal_kbps:,} kbps ({ev.bitrate_ratio:.3f}배)\n"
        f"키프레임 간격: {kf_str}\n"
        f"{size_line}"
        f"권장 작업: {ev.smart_action.action_label} ({ev.smart_action.option_label})\n"
        f"판정 사유: {ev.smart_action.reason_label} — {ev.smart_action.reason_description}\n"
        f"분류 상태: {_format_status_tooltip_detail(ev)}"
    )


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
        header=COLUMN_LABELS[ColumnId.STATUS].full,
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
        tooltip_factory=_static_tip("회전 보정이 반영된 가로 해상도입니다. (px)"),
        filter_value=lambda ev: float(ev.video.width),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=3,
        header=COLUMN_LABELS[ColumnId.HEIGHT].full,
        spec=_num_spec(3, "px", 0, 10.0, NumericBucketKind.HEIGHT),
        tooltip_factory=_static_tip("회전 보정이 반영된 세로 해상도입니다. (px)"),
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
        spec=_num_spec(8, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("현재 영상 비트레이트입니다. (kbps)"),
        filter_value=lambda ev: float(ev.video.vid_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=9,
        header=COLUMN_LABELS[ColumnId.OPT_BITRATE].short,
        spec=_num_spec(9, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("CQ22 기준 해상도, 프레임레이트, 코덱 최적 영상 비트레이트입니다. (kbps)"),
        filter_value=lambda ev: float(ev.optimal_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=10,
        header=COLUMN_LABELS[ColumnId.RATIO].short,
        spec=_num_spec(10, "배", 2, 0.1, NumericBucketKind.RATIO),
        tooltip_factory=_static_tip("최적 비트레이트 대비 현재 비트레이트 비율입니다. (배)"),
        filter_value=lambda ev: float(ev.bitrate_ratio),
        display_value=lambda ev, cfg: format_ratio(ev.bitrate_ratio, cfg.display),
        font_kind=FONT_MONO,
    ),
    TableColumnDef(
        col_id=11,
        header=COLUMN_LABELS[ColumnId.ZONE].full,
        spec=ColumnSpec(
            11, ColumnDataType.ENUM, preset_values=tuple(z.value for z in reversed(QualityZone))
        ),
        tooltip_factory=lambda cfg: format_quality_zone_tooltip(cfg.thresholds),
        filter_value=lambda ev: ev.quality_zone.value,
        sort_value=lambda ev: _ZONE_SORT_RANK.get(ev.quality_zone, 0),
        badge_getter=lambda ev, p: _BADGE_BY_ZONE[ev.quality_zone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=12,
        header=COLUMN_LABELS[ColumnId.KEYFRAME].short,
        spec=_num_spec(
            12, "초", 2, 0.5, NumericBucketKind.KEYFRAME, nullable_label=LABEL_UNMEASURED
        ),
        tooltip_factory=lambda cfg: format_keyframe_tooltip(cfg.thresholds),
        filter_value=_keyframe_filter_value,
        display_value=_format_keyframe_display,
        sort_value=lambda ev: (
            ev.video.keyframe_interval if ev.video.keyframe_interval is not None else -1.0
        ),
        foreground_getter=_fg_keyframe,
        align=ALIGN_CENTER,
    ),
    _make_size_column(
        13,
        ColumnId.SIZE,
        NumericBucketKind.SIZE,
        "현재 비디오 파일 크기 (",
        lambda ev: ev.video.vid_size_MB,
        lambda d: d.size_decimals,
    ),
    _make_size_column(
        14,
        ColumnId.AFTER_SIZE,
        NumericBucketKind.SIZE,
        "인코딩 및 키프레임 교정 후 예상 파일 크기 (원본 보존은 가상 참고치, ",
        lambda ev: ev.preview_after_mb,
        lambda d: d.after_size_decimals,
        foreground_getter=_fg_after_size,
    ),
    _make_size_column(
        15,
        ColumnId.SAVED_SIZE,
        NumericBucketKind.SAVED_SIZE,
        "인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 오버헤드, 원본 보존 회색 일반체는 가상 참고치, ",
        lambda ev: ev.preview_delta_mb,
        lambda d: d.saved_size_decimals,
        signed=True,
        foreground_getter=_fg_saved_size,
        font_kind_getter=_font_saved_size,
    ),
    TableColumnDef(
        col_id=16,
        header=COLUMN_LABELS[ColumnId.SAVINGS_RATE].full,
        spec=_num_spec(16, "MB/분", 2, 1.0, NumericBucketKind.SAVINGS_RATE),
        tooltip_factory=_format_savings_rate_tooltip,
        filter_value=lambda ev: float(ev.preview_mb_per_min),
        display_value=_format_savings_rate_display,
        foreground_getter=_fg_savings_rate,
        font_kind=FONT_MONO,
        font_kind_getter=_font_savings_rate,
    ),
    TableColumnDef(
        col_id=17,
        header=COLUMN_LABELS[ColumnId.EST_ENCODE_TIME].full,
        spec=_num_spec(17, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=lambda cfg: (
            "분류 후 샤나인코더 등으로 실제로 재인코딩(트랜스코딩)할 때 걸리는 예상 인코딩 소요 시간입니다. "
            "(스캔, 파일 이동 시간이 아님)\n"
            f"계산: (영상 재생 길이 × 1080p {cfg.bitrate_model.base_fps:g}fps 대비 해상도, FPS 작업량 배수) ÷ "
            f"기준 배속({cfg.efficiency.encode_speed_factor:.1f}배속)\n"
            f"적용 기준: {get_encode_codec_label(cfg.efficiency.encode_codec)} (설정 > 환경/세부 설정에서 조정)\n"
            "표기: 일반 색상은 실제 인코딩 대상의 예상 시간, 회색은 원본 보존 행을 만약 인코딩할 경우의 참고치"
        ),
        filter_value=lambda ev: float(ev.estimated_encode_sec),
        display_value=lambda ev, _cfg: format_duration(ev.estimated_encode_sec),
        foreground_getter=_fg_est_encode_time,
    ),
    TableColumnDef(
        col_id=18,
        header=COLUMN_LABELS[ColumnId.ACTION].full,
        spec=ColumnSpec(18, ColumnDataType.ENUM, preset_values=SMART_ACTION_LABELS),
        tooltip_factory=format_action_tooltip,
        filter_value=lambda ev: ev.smart_action.action_label,
        badge_getter=lambda ev, p: _BADGE_BY_ACTION_TONE[ev.smart_action.badge_tone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=19,
        header=COLUMN_LABELS[ColumnId.REASON].full,
        spec=ColumnSpec(19, ColumnDataType.ENUM, preset_values=SMART_ACTION_REASONS),
        tooltip_factory=format_reason_tooltip,
        filter_value=lambda ev: ev.smart_action.reason_label,
        badge_getter=lambda ev, p: _BADGE_BY_REASON.get(
            ev.smart_action.reason_label, lambda pal: pal.badge_pass
        )(p),
        align=ALIGN_CENTER,
    ),
)

COL_DEF_MAP: dict[int, TableColumnDef] = {d.col_id: d for d in COL_DEFS}
