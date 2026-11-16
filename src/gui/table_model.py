"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

from collections import Counter
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon

from src.core.config import ClassifierConfig, get_encode_codec_label
from src.core.models import VideoEvaluation
from src.core.sorting import natural_sort_key
from src.gui.column_filters import (
    ColumnDataType,
    ColumnSpec,
)
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT
from src.gui.numeric_buckets import build_numeric_buckets, resolve_numeric_bucket
from src.gui.styles.icons import get_icon
from src.gui.table_column_styles import FONT_BOLD, FONT_MONO
from src.gui.table_columns import (
    ALIGN_RIGHT,
    COL_DEF_MAP,
    COL_DEFS,
    DEFAULT_HEADER_CONFIG,
    format_duration,
)
from src.gui.table_tooltips import format_output_res_tooltip, format_row_tooltip
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_CELL_ICON_SIZE_PX: int = 12


class VideoTableModel(QAbstractTableModel):
    """VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하는 테이블 뷰모델

    표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    config_changed = pyqtSignal()

    COL_NAME = 0
    COL_STATUS = 1
    COL_WIDTH = 2
    COL_HEIGHT = 3
    COL_ROTATE = 4
    COL_CODEC = 5
    COL_FPS = 6
    COL_DURATION = 7
    COL_BITRATE = 8
    COL_OPT_BITRATE = 9
    COL_RATIO = 10
    COL_TARGET_RATIO = 11
    COL_ZONE = 12
    COL_KEYFRAME = 13
    COL_SIZE = 14
    COL_AFTER_SIZE = 15
    COL_SAVED_SIZE = 16
    COL_SAVED_PER_MIN = 17
    COL_EST_ENCODE_TIME = 18
    COL_OUTPUT_RES = 19
    COL_ACTION = 20
    COL_REASON = 21

    HEADERS: ClassVar[list[str]] = [d.header for d in COL_DEFS]
    MENU_HEADERS: ClassVar[list[str]] = [d.menu_title for d in COL_DEFS]
    HEADER_TOOLTIPS: ClassVar[dict[int, str]] = {
        d.col_id: d.tooltip_factory(DEFAULT_HEADER_CONFIG) for d in COL_DEFS
    }
    COLUMN_SPECS: ClassVar[dict[int, ColumnSpec]] = {
        d.col_id: d.spec for d in COL_DEFS
    }

    def __init__(
        self, parent: Any | None = None, config: ClassifierConfig | None = None
    ) -> None:
        super().__init__(parent)
        self._evaluations: list[VideoEvaluation] = []
        self._search_blobs: list[str] = []
        self._root_dir: str = ""
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._filter_summaries: dict[int, str] = {}
        self._color_cache: dict[str, QColor] = {}
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._bold_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)

    @classmethod
    def get_column_spec(cls, col: int) -> ColumnSpec:
        """지정 열의 데이터 타입, 단위, 선택지 스키마를 반환"""
        return cls.COLUMN_SPECS.get(col, ColumnSpec(col, ColumnDataType.TEXT))

    def _emit_all_headers_changed(self) -> None:
        if self.HEADERS:
            self.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 0, len(self.HEADERS) - 1
            )

    def set_filter_summaries(self, summaries: dict[int, str]) -> None:
        """헤더 툴팁에 표시할 활성 열 필터 요약 문구 맵을 갱신"""
        self._filter_summaries = dict(summaries)
        self._emit_all_headers_changed()

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
        """활성 임계치, 표시 서식 설정을 갱신해 헤더 툴팁과 표시 셀, 필터 버킷을 최신화"""
        self._config = config
        self._emit_all_headers_changed()
        if self._evaluations and self.HEADERS:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._evaluations) - 1, len(self.HEADERS) - 1),
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
        self._search_blobs = [
            (
                f"{ev.video.filename} {ev.video.codec} {ev.classify_status.value} "
                f"{ev.action_label} {ev.reason_label} "
                f"{ev.option_label} {ev.video.moved_dirname or ''} "
                f"{ev.target_dirname or ''}"
            ).lower()
            for ev in self._evaluations
        ]
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

    def get_evaluation_row(self, row: int) -> VideoEvaluation | None:
        """지정 행 인덱스의 VideoEvaluation 객체를 QModelIndex 생성 없이 반환"""
        if 0 <= row < len(self._evaluations):
            return self._evaluations[row]
        return None

    def get_sort_value(self, row: int, col: int) -> Any:
        """지정 행, 열의 정렬 기준 값을 QModelIndex 생성 없이 반환"""
        if 0 <= row < len(self._evaluations):
            return self._sort_data(self._evaluations[row], col)
        return None

    def get_search_blob(self, row: int) -> str:
        """지정 행의 사전 계산된 소문자 검색 대상 문자열 반환"""
        if 0 <= row < len(self._search_blobs):
            return self._search_blobs[row]
        return ""

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
        """수평 헤더 텍스트, 툴팁 및 정렬 방식 반환"""
        if orientation != Qt.Orientation.Horizontal:
            return None
        col_def = COL_DEF_MAP.get(section)
        if col_def is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            return (
                col_def.header_factory(self._config)
                if col_def.header_factory is not None
                else col_def.header
            )
        if role == int(Qt.ItemDataRole.ToolTipRole):
            base_tip = col_def.tooltip_factory(self._config)
            summary = self._filter_summaries.get(section)
            return f"{base_tip}\n[활성 필터] {summary}" if summary else base_tip
        return None

    def effective_headers(self) -> list[str]:
        """현재 표시 서식 설정(용량 단위 등)이 반영된 수평 헤더 문자열 목록 반환"""
        display_role = int(Qt.ItemDataRole.DisplayRole)
        return [
            str(self.headerData(col, Qt.Orientation.Horizontal, display_role) or "")
            for col in range(len(self.HEADERS))
        ]

    @classmethod
    def get_filter_value(cls, ev: VideoEvaluation, col: int) -> str | float | None:
        """지정 열의 동적 필터 평가용 정규화 값(문자열, 수치 또는 None) 반환"""
        col_def = COL_DEF_MAP.get(col)
        return col_def.filter_value(ev) if col_def is not None else None

    def collect_column_value_counts(self, col: int) -> list[tuple[str, int]]:
        """열거형, 범주형 열에 대해 현재 데이터셋의 고유 값별 행 수 목록 반환"""
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

        col_def = COL_DEF_MAP.get(col)
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

        col_def = COL_DEF_MAP.get(col)
        if col_def is None:
            return []
        counts: Counter[str] = Counter()
        get_val = col_def.filter_value
        for ev in self._evaluations:
            val = get_val(ev)
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
        col_def = COL_DEF_MAP.get(col)
        if col_def is None:
            return None
        if col_def.display_value is not None:
            return col_def.display_value(ev, self._config)
        val = col_def.filter_value(ev)
        if col_def.numeric_format is not None and isinstance(val, int | float):
            return format(val, col_def.numeric_format)
        return val

    def _sort_data(self, ev: VideoEvaluation, col: int) -> Any:
        col_def = COL_DEF_MAP.get(col)
        if col_def is None:
            return None
        getter = col_def.sort_value or col_def.filter_value
        return getter(ev)

    def _badge_data(self, ev: VideoEvaluation, col: int) -> Any:
        col_def = COL_DEF_MAP.get(col)
        if col_def is None or col_def.badge_getter is None:
            return None
        return col_def.badge_getter(ev, ThemeManager().current_palette)

    def _foreground_data(self, ev: VideoEvaluation, col: int) -> QColor | None:
        col_def = COL_DEF_MAP.get(col)
        if col_def is None or col_def.foreground_getter is None:
            return None
        hex_color = col_def.foreground_getter(ev, ThemeManager().current_palette)
        return self._get_cached_color(hex_color) if hex_color is not None else None

    def _decoration_data(self, ev: VideoEvaluation, col: int) -> QIcon | None:
        col_def = COL_DEF_MAP.get(col)
        if col_def is None or col_def.icon_getter is None:
            return None
        icon_name = col_def.icon_getter(ev)
        if not icon_name:
            return None
        pal = ThemeManager().current_palette
        hex_color = (
            col_def.foreground_getter(ev, pal)
            if col_def.foreground_getter is not None
            else None
        ) or pal.text_primary
        cache_key = (icon_name, hex_color)
        cached = self._icon_cache.get(cache_key)
        if cached is not None:
            return cached
        icon = get_icon(
            icon_name, color=hex_color, size=_CELL_ICON_SIZE_PX, palette=pal
        )
        self._icon_cache[cache_key] = icon
        return icon

    def data(
        self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> Any:
        """지정 셀의 표시 문자열, 정렬 값, 배지, 툴팁 및 폰트 등 역할별 데이터 반환"""
        if not index.isValid() or not (0 <= index.row() < len(self._evaluations)):
            return None

        ev: VideoEvaluation = self._evaluations[index.row()]
        col = index.column()
        if role == int(Qt.ItemDataRole.DisplayRole):
            return self._display_data(ev, col)
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._decoration_data(ev, col)
        if role == int(Qt.ItemDataRole.ForegroundRole):
            return self._foreground_data(ev, col)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            col_def = COL_DEF_MAP.get(col)
            return col_def.align if col_def is not None else ALIGN_RIGHT
        if role == int(Qt.ItemDataRole.FontRole):
            col_def = COL_DEF_MAP.get(col)
            if col_def is None:
                return None
            font_kind = (
                col_def.font_kind_getter(ev)
                if col_def.font_kind_getter is not None
                else col_def.font_kind
            )
            if font_kind == FONT_BOLD:
                return self._bold_font
            if font_kind == FONT_MONO:
                return self._mono_font
            return None
        if role == self.BADGE_ROLE:
            return self._badge_data(ev, col)
        if role == self.SORT_ROLE:
            return self._sort_data(ev, col)
        if role == self.EVAL_ROLE:
            return ev
        if role == int(Qt.ItemDataRole.ToolTipRole):
            if col == self.COL_OUTPUT_RES:
                return format_output_res_tooltip(ev, self._config)
            return format_row_tooltip(
                ev, self._root_dir, get_encode_codec_label(self._config.encode.encode_codec)
            )
        return None


__all__ = ["VideoTableModel", "format_duration"]
