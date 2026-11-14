"""테이블 열 스키마, 셀 포맷터, 배지 매핑 및 행 툴팁 포맷터 모듈"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt

from src.core.config import ClassifierConfig
from src.core.file_manager import logical_video_filepath
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
    SECONDS_PER_MINUTE,
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
_SAVED_DISPLAY_ZERO_MB: float = 0.005
_SAVED_COLOR_THRESHOLD_MB: float = 0.05
_HALF_TURN_DEG: int = 180

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
_ZONE_SORT_RANK: dict[QualityZone, int] = {
    QualityZone.LOW: 0,
    QualityZone.OPTIMAL: 1,
    QualityZone.OVER: 2,
}
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
    SmartAction.PASS_SMALL_FOOTAGE.reason_label: lambda p: p.badge_pass,
    SmartAction.PASS_EXCEPTION.reason_label: lambda p: p.badge_exception,
}
_FG_BY_ZONE: dict[QualityZone, Callable[[ThemePalette], str]] = {
    QualityZone.OVER: lambda p: p.fg_ratio_over,
    QualityZone.OPTIMAL: lambda p: p.fg_ratio_opt,
    QualityZone.LOW: lambda p: p.fg_ratio_low,
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
    return p.text_muted if ev.video.rotate_type == 0 else None


def _keyframe_filter_value(ev: VideoEvaluation) -> float | None:
    kf = ev.video.keyframe_interval
    return None if kf is None or kf < 0 else float(kf)


def _format_keyframe_display(ev: VideoEvaluation) -> str:
    kf = ev.video.keyframe_interval
    if kf is None or kf < 0:
        return LABEL_UNMEASURED
    warn = " ⚠" if ev.is_keyframe_defective else ""
    return f"{kf:.2f}{warn}"


def _format_saved_display(ev: VideoEvaluation) -> str:
    delta = ev.expected_delta_mb
    if abs(delta) < _SAVED_DISPLAY_ZERO_MB:
        return "0.00"
    return f"+{delta:,.2f}" if delta > 0 else f"{delta:,.2f}"


def _fg_keyframe(ev: VideoEvaluation, p: ThemePalette) -> str:
    if ev.is_keyframe_defective:
        return p.fg_kf_defect
    return p.text_muted if ev.video.keyframe_interval is None else p.fg_kf_opt


def _fg_saved_size(ev: VideoEvaluation, p: ThemePalette) -> str:
    if ev.expected_delta_mb > _SAVED_COLOR_THRESHOLD_MB:
        return p.fg_saved_pos
    return (
        p.fg_saved_neg
        if ev.expected_delta_mb < -_SAVED_COLOR_THRESHOLD_MB
        else p.text_muted
    )


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    total_sec = max(round(seconds), 0)
    mins, secs = divmod(total_sec, SECONDS_PER_MINUTE)
    hours, mins = divmod(mins, SECONDS_PER_MINUTE)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_row_tooltip(ev: VideoEvaluation, root_dir: str) -> str:
    """테이블 행 호버 시 표시할 파일·회전 출처·품질·판정 사유 상세 툴팁 생성"""
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
    return (
        f"파일명: {vid.filename}\n"
        f"경로: {full_path}\n"
        f"해상도: {vid.width}x{vid.height} (비율 {vid.aspect_ratio:.3f}, 회전 {rotate_detail})\n"
        f"코덱 / FPS: {vid.codec} / {vid.fps:.3f} fps\n"
        f"비트레이트: 현재 {vid.vid_kbps:,} kbps / 최적 {ev.optimal_kbps:,} kbps ({ev.bitrate_ratio:.3f}배)\n"
        f"키프레임 간격: {kf_str}\n"
        f"용량 변화: {vid.vid_size_MB:.2f} MB → {ev.expected_after_mb:.2f} MB (절감 {ev.expected_delta_mb:+.2f} MB)\n"
        f"권장 작업: {ev.smart_action.action_label} ({ev.smart_action.option_label})\n"
        f"판정 사유: {ev.smart_action.reason_label} — {ev.smart_action.reason_description}\n"
        f"분류 상태: {_format_status_tooltip_detail(ev)}"
    )


@dataclass(frozen=True, slots=True)
class TableColumnDef:
    """단일 테이블 열의 헤더·스펙·렌더링·정렬·필터·배지 동작 단일 명세"""

    col_id: int
    header: str
    spec: ColumnSpec
    tooltip_factory: Callable[[ClassifierConfig], str]
    filter_value: Callable[[VideoEvaluation], str | float | None]
    display_value: Callable[[VideoEvaluation], Any] | None = None
    sort_value: Callable[[VideoEvaluation], Any] | None = None
    badge_getter: Callable[[VideoEvaluation, ThemePalette], Any] | None = None
    icon_getter: Callable[[VideoEvaluation], str | None] | None = None
    foreground_getter: Callable[[VideoEvaluation, ThemePalette], str | None] | None = None
    align: int = ALIGN_RIGHT
    font_kind: str | None = None
    numeric_format: str | None = None
    value_sort_key: Callable[[str], Any] = natural_sort_key


COL_DEFS: tuple[TableColumnDef, ...] = (
    TableColumnDef(
        col_id=0,
        header=COLUMN_LABELS[ColumnId.NAME].full,
        spec=ColumnSpec(0, ColumnDataType.TEXT),
        tooltip_factory=_static_tip("비디오 파일명 (호버 시 전체 절대 경로 표시)"),
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
        tooltip_factory=_static_tip("회전 보정이 반영된 가로 해상도 (단위: px)"),
        filter_value=lambda ev: float(ev.video.width),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=3,
        header=COLUMN_LABELS[ColumnId.HEIGHT].full,
        spec=_num_spec(3, "px", 0, 10.0, NumericBucketKind.HEIGHT),
        tooltip_factory=_static_tip("회전 보정이 반영된 세로 해상도 (단위: px)"),
        filter_value=lambda ev: float(ev.video.height),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=4,
        header=COLUMN_LABELS[ColumnId.ROTATE].full,
        spec=ColumnSpec(4, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip(
            "컨테이너 무관 시계 방향 기준으로 정규화된 회전 각도입니다. (0°·90°·180°·270°)"
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
        tooltip_factory=_static_tip("비디오 코덱 포맷 (H.264, HEVC, AV1 등)"),
        filter_value=lambda ev: ev.video.codec,
        sort_value=lambda ev: ev.video.codec.lower(),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=6,
        header=COLUMN_LABELS[ColumnId.FPS].full,
        spec=_num_spec(6, "fps", 2, 1.0, NumericBucketKind.FPS),
        tooltip_factory=_static_tip("초당 프레임 수 (단위: fps)"),
        filter_value=lambda ev: float(ev.video.fps),
        font_kind=FONT_MONO,
        numeric_format=".2f",
    ),
    TableColumnDef(
        col_id=7,
        header=COLUMN_LABELS[ColumnId.DURATION].full,
        spec=_num_spec(7, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=_static_tip("영상 재생 시간 (시:분:초 포맷, HH:MM:SS)"),
        filter_value=lambda ev: float(ev.video.duration),
        display_value=lambda ev: format_duration(ev.video.duration),
    ),
    TableColumnDef(
        col_id=8,
        header=COLUMN_LABELS[ColumnId.BITRATE].full,
        spec=_num_spec(8, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("현재 비디오 비트레이트 (단위: kbps)"),
        filter_value=lambda ev: float(ev.video.vid_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=9,
        header=COLUMN_LABELS[ColumnId.OPT_BITRATE].full,
        spec=_num_spec(9, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("CQ22 기준 해상도·프레임레이트·코덱 최적 비트레이트 (단위: kbps)"),
        filter_value=lambda ev: float(ev.optimal_kbps),
        font_kind=FONT_MONO,
        numeric_format=".0f",
    ),
    TableColumnDef(
        col_id=10,
        header=COLUMN_LABELS[ColumnId.RATIO].full,
        spec=_num_spec(10, "배", 2, 0.1, NumericBucketKind.RATIO),
        tooltip_factory=_static_tip("최적 비트레이트 대비 현재 비트레이트 비율 (단위: 배, x)"),
        filter_value=lambda ev: float(ev.bitrate_ratio),
        foreground_getter=lambda ev, p: _FG_BY_ZONE[ev.quality_zone](p),
        font_kind=FONT_BOLD,
        numeric_format=".2f",
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
        header=COLUMN_LABELS[ColumnId.KEYFRAME].full,
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
    TableColumnDef(
        col_id=13,
        header=COLUMN_LABELS[ColumnId.SIZE].full,
        spec=_num_spec(13, "MB", 2, 5.0, NumericBucketKind.SIZE),
        tooltip_factory=_static_tip("현재 비디오 파일 크기 (단위: MB)"),
        filter_value=lambda ev: float(ev.video.vid_size_MB),
        font_kind=FONT_MONO,
        numeric_format=",.2f",
    ),
    TableColumnDef(
        col_id=14,
        header=COLUMN_LABELS[ColumnId.AFTER_SIZE].full,
        spec=_num_spec(14, "MB", 2, 5.0, NumericBucketKind.SIZE),
        tooltip_factory=_static_tip("인코딩 및 키프레임 교정 후 예상 파일 크기 (단위: MB)"),
        filter_value=lambda ev: float(ev.expected_after_mb),
        font_kind=FONT_MONO,
        numeric_format=",.2f",
    ),
    TableColumnDef(
        col_id=15,
        header=COLUMN_LABELS[ColumnId.SAVED_SIZE].full,
        spec=_num_spec(15, "MB", 2, 5.0, NumericBucketKind.SAVED_SIZE),
        tooltip_factory=_static_tip("인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 교정 오버헤드, 단위: MB)"),
        filter_value=lambda ev: float(ev.expected_delta_mb),
        display_value=_format_saved_display,
        foreground_getter=_fg_saved_size,
        font_kind=FONT_BOLD,
    ),
    TableColumnDef(
        col_id=16,
        header=COLUMN_LABELS[ColumnId.ACTION].full,
        spec=ColumnSpec(16, ColumnDataType.ENUM, preset_values=SMART_ACTION_LABELS),
        tooltip_factory=format_action_tooltip,
        filter_value=lambda ev: ev.smart_action.action_label,
        badge_getter=lambda ev, p: _BADGE_BY_ACTION_TONE[ev.smart_action.badge_tone](p),
        align=ALIGN_CENTER,
    ),
    TableColumnDef(
        col_id=17,
        header=COLUMN_LABELS[ColumnId.REASON].full,
        spec=ColumnSpec(17, ColumnDataType.ENUM, preset_values=SMART_ACTION_REASONS),
        tooltip_factory=format_reason_tooltip,
        filter_value=lambda ev: ev.smart_action.reason_label,
        badge_getter=lambda ev, p: _BADGE_BY_REASON.get(
            ev.smart_action.reason_label, lambda pal: pal.badge_pass
        )(p),
        align=ALIGN_CENTER,
    ),
)

COL_DEF_MAP: dict[int, TableColumnDef] = {d.col_id: d for d in COL_DEFS}
