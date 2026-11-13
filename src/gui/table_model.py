"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

from collections import Counter
from collections.abc import Callable
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
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_DEFAULT_HEADER_CONFIG = ClassifierConfig()
_SAVED_DISPLAY_ZERO_MB: float = 0.005
_SAVED_COLOR_THRESHOLD_MB: float = 0.05


def _num_spec(
    col: int, unit: str, decimals: int, step: float, bucket_kind: NumericBucketKind, *, nullable_label: str = ""
) -> ColumnSpec:
    return ColumnSpec(col, ColumnDataType.NUMBER, unit, decimals, step, nullable_label, (), bucket_kind)


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    total_sec = max(round(seconds), 0)
    mins, secs = divmod(total_sec, SECONDS_PER_MINUTE)
    hours, mins = divmod(mins, SECONDS_PER_MINUTE)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


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

    _ZONE_SORT_RANK: ClassVar[dict[QualityZone, int]] = {
        QualityZone.LOW: 0,
        QualityZone.OPTIMAL: 1,
        QualityZone.OVER: 2,
    }

    HEADERS: ClassVar[list[str]] = [
        "분류 상태", "이름", "해상도", "코덱", "FPS",
        "길이", "용량", "b-rate", "최적 b-rate", "b-rate 비율",
        "화질 등급", "KF 간격", "예상후", "예상 절감", "인코딩 처방",
    ]

    HEADER_TOOLTIPS: ClassVar[dict[int, str]] = {
        0: "분류 상태 (현재 작업 폴더 내 위치 및 모의 분류 예정 상태)",
        1: "비디오 파일명 (호버 시 전체 절대 경로 표시)",
        2: "가로×세로 정규화 해상도 (↻: 회전 메타데이터 적용됨, 단위: px)",
        3: "비디오 코덱 포맷 (H.264, HEVC, AV1 등)",
        4: "초당 프레임 수 (단위: fps)",
        5: "영상 재생 시간 (시:분:초 포맷, HH:MM:SS)",
        6: "현재 비디오 파일 크기 (단위: MB)",
        7: "현재 비디오 비트레이트 (단위: kbps)",
        8: "CQ22 기준 해상도·프레임레이트·코덱 최적 비트레이트 (단위: kbps)",
        9: "최적 비트레이트 대비 현재 비트레이트 비율 (단위: 배, x)",
        10: format_quality_zone_tooltip(_DEFAULT_HEADER_CONFIG.thresholds),
        11: format_keyframe_tooltip(_DEFAULT_HEADER_CONFIG.thresholds),
        12: "인코딩 및 키프레임 교정 후 예상 파일 크기 (단위: MB)",
        13: "인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 교정 오버헤드, 단위: MB)",
        14: "최종 분류 및 인코딩 처방 (과화질 감축, 키프레임 교정, 원본 보존 등)",
    }

    COLUMN_SPECS: ClassVar[dict[int, ColumnSpec]] = {
        COL_STATUS: ColumnSpec(COL_STATUS, ColumnDataType.CATEGORY),
        COL_NAME: ColumnSpec(COL_NAME, ColumnDataType.TEXT),
        COL_RES: ColumnSpec(COL_RES, ColumnDataType.CATEGORY),
        COL_CODEC: ColumnSpec(COL_CODEC, ColumnDataType.CATEGORY),
        COL_FPS: _num_spec(COL_FPS, "fps", 2, 1.0, NumericBucketKind.FPS),
        COL_DURATION: _num_spec(COL_DURATION, "초", 1, 10.0, NumericBucketKind.DURATION),
        COL_SIZE: _num_spec(COL_SIZE, "MB", 2, 5.0, NumericBucketKind.SIZE),
        COL_BITRATE: _num_spec(COL_BITRATE, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        COL_OPT_BITRATE: _num_spec(COL_OPT_BITRATE, "kbps", 0, 100.0, NumericBucketKind.BITRATE),
        COL_RATIO: _num_spec(COL_RATIO, "배", 2, 0.1, NumericBucketKind.RATIO),
        COL_ZONE: ColumnSpec(
            COL_ZONE, ColumnDataType.ENUM, preset_values=tuple(z.value for z in reversed(QualityZone))
        ),
        COL_KEYFRAME: _num_spec(
            COL_KEYFRAME, "초", 2, 0.5, NumericBucketKind.KEYFRAME, nullable_label=LABEL_UNMEASURED
        ),
        COL_AFTER_SIZE: _num_spec(COL_AFTER_SIZE, "MB", 2, 5.0, NumericBucketKind.SIZE),
        COL_SAVED_SIZE: _num_spec(COL_SAVED_SIZE, "MB", 2, 5.0, NumericBucketKind.SAVED_SIZE),
        COL_ACTION: ColumnSpec(
            COL_ACTION, ColumnDataType.ENUM, preset_values=tuple(a.value for a in SmartAction)
        ),
    }

    _CENTER_ALIGN_COLS: ClassVar[frozenset[int]] = frozenset(
        {COL_STATUS, COL_RES, COL_CODEC, COL_ZONE, COL_KEYFRAME, COL_ACTION}
    )
    _BOLD_FONT_COLS: ClassVar[frozenset[int]] = frozenset({COL_RATIO, COL_SAVED_SIZE})
    _MONO_FONT_COLS: ClassVar[frozenset[int]] = frozenset(
        {COL_FPS, COL_SIZE, COL_BITRATE, COL_OPT_BITRATE, COL_AFTER_SIZE}
    )
    _NUMERIC_DISPLAY_FORMATS: ClassVar[dict[int, str]] = {
        COL_FPS: ".2f",
        COL_SIZE: ",.2f",
        COL_BITRATE: ".0f",
        COL_OPT_BITRATE: ".0f",
        COL_RATIO: ".2f",
        COL_AFTER_SIZE: ",.2f",
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
        if orientation != Qt.Orientation.Horizontal or not (0 <= section < len(self.HEADERS)):
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            return self.HEADERS[section]
        if role == int(Qt.ItemDataRole.ToolTipRole):
            if section == self.COL_ZONE:
                base_tip = format_quality_zone_tooltip(self._config.thresholds)
            elif section == self.COL_KEYFRAME:
                base_tip = format_keyframe_tooltip(self._config.thresholds)
            else:
                base_tip = self.HEADER_TOOLTIPS.get(section, self.HEADERS[section])
            summary = self._filter_summaries.get(section)
            return f"{base_tip}\n[활성 필터] {summary}" if summary else base_tip
        return None

    @staticmethod
    def _format_status_label(ev: VideoEvaluation) -> str:
        if ev.video.moved_dirname:
            return f"{LABEL_MOVED} · {ev.video.moved_dirname}"
        if ev.target_dirname:
            return f"{LABEL_PSEUDO_CLASSIFY} · {ev.target_dirname}"
        return LABEL_ORIGINAL_LOCATION

    @classmethod
    def _numeric_column_value(cls, ev: VideoEvaluation, col: int) -> float | None:
        vid = ev.video
        mapping: dict[int, float] = {
            cls.COL_FPS: float(vid.fps),
            cls.COL_DURATION: float(vid.duration),
            cls.COL_SIZE: float(vid.vid_size_MB),
            cls.COL_BITRATE: float(vid.vid_kbps),
            cls.COL_OPT_BITRATE: float(ev.optimal_kbps),
            cls.COL_RATIO: float(ev.bitrate_ratio),
            cls.COL_AFTER_SIZE: float(ev.expected_after_mb),
            cls.COL_SAVED_SIZE: float(ev.expected_delta_mb),
        }
        return mapping.get(col)

    @classmethod
    def get_filter_value(cls, ev: VideoEvaluation, col: int) -> str | float | None:
        """지정 열의 동적 필터 평가용 정규화 값(문자열, 수치 또는 None) 반환"""
        num_val = cls._numeric_column_value(ev, col)
        if num_val is not None:
            return num_val
        vid = ev.video
        if col == cls.COL_STATUS:
            return cls._format_status_label(ev)
        if col == cls.COL_NAME:
            return vid.filename
        if col == cls.COL_RES:
            return f"{vid.width}×{vid.height}"
        if col == cls.COL_CODEC:
            return vid.codec
        if col == cls.COL_ZONE:
            return ev.quality_zone.value
        if col == cls.COL_KEYFRAME:
            if vid.keyframe_interval is None or vid.keyframe_interval < 0:
                return None
            return float(vid.keyframe_interval)
        if col == cls.COL_ACTION:
            return ev.smart_action.value
        return None

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

        if col == self.COL_RES:
            def _res_key(label: str) -> tuple[int, int, str]:
                parts = label.split("×", 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    w, h = int(parts[0]), int(parts[1])
                    return (-(w * h), -w, label)
                return (0, 0, label)

            return sorted(counts.items(), key=lambda item: _res_key(item[0]))

        return sorted(counts.items(), key=lambda item: natural_sort_key(item[0]))

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
        if col in (
            self.COL_STATUS,
            self.COL_NAME,
            self.COL_CODEC,
            self.COL_ZONE,
            self.COL_ACTION,
        ):
            return self.get_filter_value(ev, col)
        fmt = self._NUMERIC_DISPLAY_FORMATS.get(col)
        if fmt is not None:
            val = self._numeric_column_value(ev, col)
            return format(val, fmt) if val is not None else None
        vid = ev.video
        if col == self.COL_RES:
            rot_mark = " ↻" if vid.rotate_type in (90, 270) else ""
            return f"{vid.width}×{vid.height}{rot_mark}"
        if col == self.COL_DURATION:
            return format_duration(vid.duration)
        if col == self.COL_KEYFRAME:
            if vid.keyframe_interval is None or vid.keyframe_interval < 0:
                return LABEL_UNMEASURED
            warn = " ⚠" if ev.is_keyframe_defective else ""
            return f"{vid.keyframe_interval:.2f}{warn}"
        if col == self.COL_SAVED_SIZE:
            if abs(ev.expected_delta_mb) < _SAVED_DISPLAY_ZERO_MB:
                return "0.00"
            return (
                f"+{ev.expected_delta_mb:,.2f}"
                if ev.expected_delta_mb > 0
                else f"{ev.expected_delta_mb:,.2f}"
            )
        return None

    def _sort_data(self, ev: VideoEvaluation, col: int) -> Any:
        num_val = self._numeric_column_value(ev, col)
        if num_val is not None:
            return num_val
        if col in (self.COL_STATUS, self.COL_ACTION):
            return self.get_filter_value(ev, col)
        vid = ev.video
        if col == self.COL_NAME:
            return WindowsNameOrder(vid.filename)
        if col == self.COL_RES:
            return vid.width * vid.height
        if col == self.COL_CODEC:
            return vid.codec.lower()
        if col == self.COL_ZONE:
            return self._ZONE_SORT_RANK.get(ev.quality_zone, 0)
        if col == self.COL_KEYFRAME:
            return vid.keyframe_interval if vid.keyframe_interval is not None else -1.0
        return None

    def _badge_data(self, ev: VideoEvaluation, col: int) -> Any:
        p = ThemeManager().current_palette
        if col == self.COL_STATUS:
            if ev.video.moved_dirname:
                return p.badge_moved
            return p.badge_pseudo if ev.target_dirname else p.badge_pass
        if col == self.COL_ZONE:
            if ev.quality_zone == QualityZone.OVER:
                return p.badge_over
            return p.badge_optimal if ev.quality_zone == QualityZone.OPTIMAL else p.badge_low
        if col == self.COL_ACTION:
            if ev.smart_action in (SmartAction.ENCODE_CQ22_OVER, SmartAction.ENCODE_CQ22_OPT_KF):
                return p.badge_std_encode
            return p.badge_low_kf_encode if ev.smart_action == SmartAction.ENCODE_LOW_KF else p.badge_pass
        return None

    def _foreground_data(self, ev: VideoEvaluation, col: int) -> QColor | None:
        p = ThemeManager().current_palette
        hex_color: str | None = None
        if col == self.COL_RATIO:
            hex_color = {
                QualityZone.OVER: p.fg_ratio_over,
                QualityZone.OPTIMAL: p.fg_ratio_opt,
            }.get(ev.quality_zone, p.fg_ratio_low)
        elif col == self.COL_KEYFRAME:
            if ev.is_keyframe_defective:
                hex_color = p.fg_kf_defect
            else:
                hex_color = p.text_muted if ev.video.keyframe_interval is None else p.fg_kf_opt
        elif col == self.COL_SAVED_SIZE:
            if ev.expected_delta_mb > _SAVED_COLOR_THRESHOLD_MB:
                hex_color = p.fg_saved_pos
            else:
                hex_color = (
                    p.fg_saved_neg
                    if ev.expected_delta_mb < -_SAVED_COLOR_THRESHOLD_MB
                    else p.text_muted
                )
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
            if col == self.COL_NAME:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if col in self._CENTER_ALIGN_COLS:
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == int(Qt.ItemDataRole.FontRole):
            if col in self._BOLD_FONT_COLS:
                return self._bold_font
            if col in self._MONO_FONT_COLS:
                return self._mono_font
        return None
