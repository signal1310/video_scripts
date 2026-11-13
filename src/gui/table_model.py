"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from src.core.config import ClassifierConfig
from src.core.file_manager import logical_video_filepath
from src.core.messages import (
    LABEL_MOVED,
    LABEL_NO_MOVE_TARGET,
    LABEL_ORIGINAL_LOCATION,
    LABEL_PSEUDO_CLASSIFY,
    LABEL_UNMEASURED,
    LABEL_UNMEASURED_DETAIL,
    format_keyframe_tooltip,
    format_quality_zone_tooltip,
)
from src.core.models import (
    SECONDS_PER_MINUTE,
    ActionBadgeTone,
    QualityZone,
    SmartAction,
    VideoEvaluation,
)
from src.core.sorting import WindowsNameOrder, natural_sort_key
from src.gui.column_filters import (
    ColumnDataType,
    ColumnSpec,
    NumericBucketKind,
    build_numeric_buckets,
    resolve_numeric_bucket,
)
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_DEFAULT_HEADER_CONFIG = ClassifierConfig()
_SAVED_DISPLAY_ZERO_MB: float = 0.005
_SAVED_COLOR_THRESHOLD_MB: float = 0.05

_ALIGN_LEFT: int = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_CENTER: int = int(Qt.AlignmentFlag.AlignCenter)
_ALIGN_RIGHT: int = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_FONT_MONO: str = "mono"
_FONT_BOLD: str = "bold"

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


def _format_status_label(ev: VideoEvaluation) -> str:
    if ev.video.moved_dirname:
        return f"{LABEL_MOVED} · {ev.video.moved_dirname}"
    if ev.target_dirname:
        return f"{LABEL_PSEUDO_CLASSIFY} · {ev.target_dirname}"
    return LABEL_ORIGINAL_LOCATION


def _res_value_sort_key(label: str) -> tuple[int, int, str]:
    parts = label.split("×", 1)
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        w, h = int(parts[0]), int(parts[1])
        return (-(w * h), -w, label)
    return (0, 0, label)


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


@dataclass(frozen=True, slots=True)
class _TableColumnDef:
    """단일 테이블 열의 헤더·스펙·렌더링·정렬·필터·배지 동작 단일 명세"""

    col_id: int
    header: str
    spec: ColumnSpec
    tooltip_factory: Callable[[ClassifierConfig], str]
    filter_value: Callable[[VideoEvaluation], str | float | None]
    display_value: Callable[[VideoEvaluation], Any] | None = None
    sort_value: Callable[[VideoEvaluation], Any] | None = None
    badge_getter: Callable[[VideoEvaluation, ThemePalette], Any] | None = None
    foreground_getter: Callable[[VideoEvaluation, ThemePalette], str | None] | None = None
    align: int = _ALIGN_RIGHT
    font_kind: str | None = None
    numeric_format: str | None = None
    value_sort_key: Callable[[str], Any] = natural_sort_key


_COL_DEFS: tuple[_TableColumnDef, ...] = (
    _TableColumnDef(
        col_id=0,
        header="분류 상태",
        spec=ColumnSpec(0, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip("분류 상태 (현재 작업 폴더 내 위치 및 모의 분류 예정 상태)"),
        filter_value=_format_status_label,
        badge_getter=lambda ev, p: (
            p.badge_moved
            if ev.video.moved_dirname
            else (p.badge_pseudo if ev.target_dirname else p.badge_pass)
        ),
        align=_ALIGN_CENTER,
    ),
    _TableColumnDef(
        col_id=1,
        header="이름",
        spec=ColumnSpec(1, ColumnDataType.TEXT),
        tooltip_factory=_static_tip("비디오 파일명 (호버 시 전체 절대 경로 표시)"),
        filter_value=lambda ev: ev.video.filename,
        sort_value=lambda ev: WindowsNameOrder(ev.video.filename),
        align=_ALIGN_LEFT,
    ),
    _TableColumnDef(
        col_id=2,
        header="해상도",
        spec=ColumnSpec(2, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip("가로×세로 정규화 해상도 (↻: 회전 메타데이터 적용됨, 단위: px)"),
        filter_value=lambda ev: f"{ev.video.width}×{ev.video.height}",
        display_value=lambda ev: (
            f"{ev.video.width}×{ev.video.height}"
            f"{' ↻' if ev.video.rotate_type in (90, 270) else ''}"
        ),
        sort_value=lambda ev: ev.video.width * ev.video.height,
        align=_ALIGN_CENTER,
        value_sort_key=_res_value_sort_key,
    ),
    _TableColumnDef(
        col_id=3,
        header="코덱",
        spec=ColumnSpec(3, ColumnDataType.CATEGORY),
        tooltip_factory=_static_tip("비디오 코덱 포맷 (H.264, HEVC, AV1 등)"),
        filter_value=lambda ev: ev.video.codec,
        sort_value=lambda ev: ev.video.codec.lower(),
        align=_ALIGN_CENTER,
    ),
    _TableColumnDef(
        col_id=4,
        header="FPS",
        spec=_num_spec(4, "fps", 2, 1.0, NumericBucketKind.FPS),
        tooltip_factory=_static_tip("초당 프레임 수 (단위: fps)"),
        filter_value=lambda ev: float(ev.video.fps),
        font_kind=_FONT_MONO,
        numeric_format=".2f",
    ),
    _TableColumnDef(
        col_id=5,
        header="길이",
        spec=_num_spec(5, "초", 1, 10.0, NumericBucketKind.DURATION),
        tooltip_factory=_static_tip("영상 재생 시간 (시:분:초 포맷, HH:MM:SS)"),
        filter_value=lambda ev: float(ev.video.duration),
        display_value=lambda ev: format_duration(ev.video.duration),
    ),
    _TableColumnDef(
        col_id=6,
        header="용량",
        spec=_num_spec(6, "MB", 2, 5.0, NumericBucketKind.SIZE),
        tooltip_factory=_static_tip("현재 비디오 파일 크기 (단위: MB)"),
        filter_value=lambda ev: float(ev.video.vid_size_MB),
        font_kind=_FONT_MONO,
        numeric_format=",.2f",
    ),
    _TableColumnDef(
        col_id=7,
        header="b-rate",
        spec=_num_spec(7, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("현재 비디오 비트레이트 (단위: kbps)"),
        filter_value=lambda ev: float(ev.video.vid_kbps),
        font_kind=_FONT_MONO,
        numeric_format=".0f",
    ),
    _TableColumnDef(
        col_id=8,
        header="최적 b-rate",
        spec=_num_spec(8, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        tooltip_factory=_static_tip("CQ22 기준 해상도·프레임레이트·코덱 최적 비트레이트 (단위: kbps)"),
        filter_value=lambda ev: float(ev.optimal_kbps),
        font_kind=_FONT_MONO,
        numeric_format=".0f",
    ),
    _TableColumnDef(
        col_id=9,
        header="b-rate 비율",
        spec=_num_spec(9, "배", 2, 0.1, NumericBucketKind.RATIO),
        tooltip_factory=_static_tip("최적 비트레이트 대비 현재 비트레이트 비율 (단위: 배, x)"),
        filter_value=lambda ev: float(ev.bitrate_ratio),
        foreground_getter=lambda ev, p: _FG_BY_ZONE[ev.quality_zone](p),
        font_kind=_FONT_BOLD,
        numeric_format=".2f",
    ),
    _TableColumnDef(
        col_id=10,
        header="화질 등급",
        spec=ColumnSpec(
            10, ColumnDataType.ENUM, preset_values=tuple(z.value for z in reversed(QualityZone))
        ),
        tooltip_factory=lambda cfg: format_quality_zone_tooltip(cfg.thresholds),
        filter_value=lambda ev: ev.quality_zone.value,
        sort_value=lambda ev: _ZONE_SORT_RANK.get(ev.quality_zone, 0),
        badge_getter=lambda ev, p: _BADGE_BY_ZONE[ev.quality_zone](p),
        align=_ALIGN_CENTER,
    ),
    _TableColumnDef(
        col_id=11,
        header="KF 간격",
        spec=_num_spec(
            11, "초", 2, 0.5, NumericBucketKind.KEYFRAME, nullable_label=LABEL_UNMEASURED
        ),
        tooltip_factory=lambda cfg: format_keyframe_tooltip(cfg.thresholds),
        filter_value=_keyframe_filter_value,
        display_value=_format_keyframe_display,
        sort_value=lambda ev: (
            ev.video.keyframe_interval if ev.video.keyframe_interval is not None else -1.0
        ),
        foreground_getter=_fg_keyframe,
        align=_ALIGN_CENTER,
    ),
    _TableColumnDef(
        col_id=12,
        header="예상후",
        spec=_num_spec(12, "MB", 2, 5.0, NumericBucketKind.SIZE),
        tooltip_factory=_static_tip("인코딩 및 키프레임 교정 후 예상 파일 크기 (단위: MB)"),
        filter_value=lambda ev: float(ev.expected_after_mb),
        font_kind=_FONT_MONO,
        numeric_format=",.2f",
    ),
    _TableColumnDef(
        col_id=13,
        header="예상 절감",
        spec=_num_spec(13, "MB", 2, 5.0, NumericBucketKind.SAVED_SIZE),
        tooltip_factory=_static_tip("인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 교정 오버헤드, 단위: MB)"),
        filter_value=lambda ev: float(ev.expected_delta_mb),
        display_value=_format_saved_display,
        foreground_getter=_fg_saved_size,
        font_kind=_FONT_BOLD,
    ),
    _TableColumnDef(
        col_id=14,
        header="인코딩 처방",
        spec=ColumnSpec(
            14, ColumnDataType.ENUM, preset_values=tuple(a.value for a in SmartAction)
        ),
        tooltip_factory=_static_tip("최종 분류 및 인코딩 처방 (과화질 감축, 키프레임 교정, 원본 보존 등)"),
        filter_value=lambda ev: ev.smart_action.value,
        badge_getter=lambda ev, p: _BADGE_BY_ACTION_TONE[ev.smart_action.badge_tone](p),
        align=_ALIGN_CENTER,
    ),
)
_COL_DEF_MAP: dict[int, _TableColumnDef] = {d.col_id: d for d in _COL_DEFS}


class VideoTableModel(QAbstractTableModel):
    """VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하는 테이블 뷰모델

    표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    config_changed = pyqtSignal()

    COL_STATUS = 0
    COL_NAME = 1
    COL_RES = 2
    COL_CODEC = 3
    COL_FPS = 4
    COL_DURATION = 5
    COL_SIZE = 6
    COL_BITRATE = 7
    COL_OPT_BITRATE = 8
    COL_RATIO = 9
    COL_ZONE = 10
    COL_KEYFRAME = 11
    COL_AFTER_SIZE = 12
    COL_SAVED_SIZE = 13
    COL_ACTION = 14

    HEADERS: ClassVar[list[str]] = [d.header for d in _COL_DEFS]
    HEADER_TOOLTIPS: ClassVar[dict[int, str]] = {
        d.col_id: d.tooltip_factory(_DEFAULT_HEADER_CONFIG) for d in _COL_DEFS
    }
    COLUMN_SPECS: ClassVar[dict[int, ColumnSpec]] = {
        d.col_id: d.spec for d in _COL_DEFS
    }

    def __init__(
        self, parent: Any | None = None, config: ClassifierConfig | None = None
    ) -> None:
        super().__init__(parent)
        self._evaluations: list[VideoEvaluation] = []
        self._root_dir: str = ""
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._filter_summaries: dict[int, str] = {}
        self._color_cache: dict[str, QColor] = {}
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._bold_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)

    @classmethod
    def get_column_spec(cls, col: int) -> ColumnSpec:
        """지정 열의 데이터 타입·단위·선택지 스키마를 반환"""
        return cls.COLUMN_SPECS.get(col, ColumnSpec(col, ColumnDataType.TEXT))

    def set_filter_summaries(self, summaries: dict[int, str]) -> None:
        """헤더 툴팁에 표시할 활성 열 필터 요약 문구 맵을 갱신"""
        self._filter_summaries = dict(summaries)
        if self.HEADERS:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.HEADERS) - 1)

    def _get_cached_color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

    @property
    def config(self) -> ClassifierConfig:
        """현재 테이블 모델에 연결된 분류 임계치 설정 반환"""
        return self._config

    def set_config(self, config: ClassifierConfig) -> None:
        """활성 임계치 설정을 갱신해 화질·키프레임 열 헤더 툴팁과 필터 버킷을 최신화"""
        self._config = config
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal, self.COL_ZONE, self.COL_KEYFRAME
        )
        self.config_changed.emit()

    def set_data_source(
        self,
        evaluations: list[VideoEvaluation],
        root_dir: str = "",
        config: ClassifierConfig | None = None,
    ) -> None:
        """평가 결과 리스트와 현재 작업 폴더를 일괄 갱신"""
        self.beginResetModel()
        self._evaluations = list(evaluations)
        self._root_dir = root_dir
        cfg_updated = config is not None
        if config is not None:
            self._config = config
        self.endResetModel()
        if cfg_updated:
            self.config_changed.emit()

    @property
    def evaluations(self) -> list[VideoEvaluation]:
        """현재 모델에 로드된 VideoEvaluation 목록 반환"""
        return self._evaluations

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        """모델의 총 비디오 행 수 반환"""
        return 0 if parent.isValid() else len(self._evaluations)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        """테이블 컬럼 개수 반환"""
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """수평 헤더 텍스트·툴팁 및 정렬 방식 반환"""
        if orientation != Qt.Orientation.Horizontal:
            return None
        col_def = _COL_DEF_MAP.get(section)
        if col_def is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            return col_def.header
        if role == int(Qt.ItemDataRole.ToolTipRole):
            base_tip = col_def.tooltip_factory(self._config)
            summary = self._filter_summaries.get(section)
            return f"{base_tip}\n[활성 필터] {summary}" if summary else base_tip
        return None

    @classmethod
    def get_filter_value(cls, ev: VideoEvaluation, col: int) -> str | float | None:
        """지정 열의 동적 필터 평가용 정규화 값(문자열, 수치 또는 None) 반환"""
        col_def = _COL_DEF_MAP.get(col)
        return col_def.filter_value(ev) if col_def is not None else None

    def collect_column_value_counts(self, col: int) -> list[tuple[str, int]]:
        """열거형·범주형 열에 대해 현재 데이터셋의 고유 값별 행 수 목록 반환"""
        spec = self.get_column_spec(col)
        counts: Counter[str] = Counter(
            str(self.get_filter_value(ev, col) or "") for ev in self._evaluations
        )
        if spec.data_type is ColumnDataType.ENUM and spec.preset_values:
            ordered = [(val, counts.get(val, 0)) for val in spec.preset_values]
            ordered.extend(
                (k, counts[k]) for k in sorted(counts) if k not in spec.preset_values
            )
            return ordered

        col_def = _COL_DEF_MAP.get(col)
        sort_key = col_def.value_sort_key if col_def is not None else natural_sort_key
        return sorted(counts.items(), key=lambda item: sort_key(item[0]))

    def collect_numeric_bounds(
        self, col: int
    ) -> tuple[float | None, float | None, int]:
        """수치형 열의 현재 데이터셋 최솟값, 최댓값 및 미측정(None) 행 수 반환"""
        values: list[float] = []
        null_count = 0
        for ev in self._evaluations:
            val = self.get_filter_value(ev, col)
            if val is None:
                null_count += 1
            elif isinstance(val, int | float):
                values.append(float(val))
        return (min(values), max(values), null_count) if values else (None, None, null_count)

    def collect_numeric_bucket_counts(
        self, col: int
    ) -> list[tuple[str, str, int]]:
        """수치형 열의 측정값을 도메인 구간 버킷별로 집계해 비어 있지 않은 목록 반환"""
        spec = self.get_column_spec(col)
        if spec.data_type is not ColumnDataType.NUMBER:
            return []
        buckets = build_numeric_buckets(spec.bucket_kind, self._config)
        if not buckets:
            return []

        counts: Counter[str] = Counter()
        for ev in self._evaluations:
            val = self.get_filter_value(ev, col)
            if isinstance(val, int | float):
                bucket = resolve_numeric_bucket(float(val), buckets)
                if bucket is not None:
                    counts[bucket.key] += 1

        return [
            (bucket.key, bucket.label, counts[bucket.key])
            for bucket in buckets
            if counts[bucket.key] > 0
        ]

    def _display_data(self, ev: VideoEvaluation, col: int) -> Any:
        col_def = _COL_DEF_MAP.get(col)
        if col_def is None:
            return None
        if col_def.display_value is not None:
            return col_def.display_value(ev)
        val = col_def.filter_value(ev)
        if col_def.numeric_format is not None and isinstance(val, int | float):
            return format(val, col_def.numeric_format)
        return val

    def _sort_data(self, ev: VideoEvaluation, col: int) -> Any:
        col_def = _COL_DEF_MAP.get(col)
        if col_def is None:
            return None
        getter = col_def.sort_value or col_def.filter_value
        return getter(ev)

    def _badge_data(self, ev: VideoEvaluation, col: int) -> Any:
        col_def = _COL_DEF_MAP.get(col)
        if col_def is None or col_def.badge_getter is None:
            return None
        return col_def.badge_getter(ev, ThemeManager().current_palette)

    def _foreground_data(self, ev: VideoEvaluation, col: int) -> QColor | None:
        col_def = _COL_DEF_MAP.get(col)
        if col_def is None or col_def.foreground_getter is None:
            return None
        hex_color = col_def.foreground_getter(ev, ThemeManager().current_palette)
        return self._get_cached_color(hex_color) if hex_color is not None else None

    def _tooltip_data(self, ev: VideoEvaluation) -> str:
        vid = ev.video
        full_path = logical_video_filepath(self._root_dir, vid)
        kf_str = (
            f"{vid.keyframe_interval:.3f}초"
            if vid.keyframe_interval is not None
            else LABEL_UNMEASURED_DETAIL
        )
        return (
            f"파일명: {vid.filename}\n"
            f"경로: {full_path}\n"
            f"해상도: {vid.width}x{vid.height} (비율 {vid.aspect_ratio:.3f}, 회전 {vid.rotate_type}°)\n"
            f"코덱 / FPS: {vid.codec} / {vid.fps:.3f} fps\n"
            f"비트레이트: 현재 {vid.vid_kbps:,} kbps / 최적 {ev.optimal_kbps:,} kbps ({ev.bitrate_ratio:.3f}배)\n"
            f"키프레임 간격: {kf_str}\n"
            f"용량 변화: {vid.vid_size_MB:.2f} MB → {ev.expected_after_mb:.2f} MB (절감 {ev.expected_delta_mb:+.2f} MB)\n"
            f"처방: {ev.smart_action.value}\n"
            f"분류 대상 폴더: {ev.target_dirname or LABEL_NO_MOVE_TARGET}"
        )

    def data(
        self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> Any:
        """지정 셀의 표시 문자열, 정렬 값, 배지, 툴팁 및 폰트 등 역할별 데이터 반환"""
        if not index.isValid() or not (0 <= index.row() < len(self._evaluations)):
            return None

        ev: VideoEvaluation = self._evaluations[index.row()]
        col = index.column()
        role_handlers: dict[int, Callable[[VideoEvaluation, int], Any]] = {
            self.EVAL_ROLE: lambda e, _c: e,
            int(Qt.ItemDataRole.DisplayRole): self._display_data,
            self.SORT_ROLE: self._sort_data,
            self.BADGE_ROLE: self._badge_data,
            int(Qt.ItemDataRole.ForegroundRole): self._foreground_data,
            int(Qt.ItemDataRole.ToolTipRole): lambda e, _c: self._tooltip_data(e),
        }
        if handler := role_handlers.get(role):
            return handler(ev, col)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            col_def = _COL_DEF_MAP.get(col)
            return col_def.align if col_def is not None else _ALIGN_RIGHT
        if role == int(Qt.ItemDataRole.FontRole):
            col_def = _COL_DEF_MAP.get(col)
            font_kind = col_def.font_kind if col_def is not None else None
            if font_kind == _FONT_BOLD:
                return self._bold_font
            if font_kind == _FONT_MONO:
                return self._mono_font
        return None
