"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

from collections import Counter
from collections.abc import Mapping
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon

from src.core.config import ClassifierConfig, get_encode_codec_label
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import ResolutionTier
from src.core.sorting import natural_sort_key
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT, make_path_font
from src.gui.styles.icons import get_icon
from src.gui.table.column_filters import (
    ColumnDataType,
    ColumnSpec,
)
from src.gui.table.numeric_buckets import build_numeric_buckets, resolve_numeric_bucket
from src.gui.table.table_column_styles import FONT_BOLD, FONT_MONO, FONT_PATH
from src.gui.table.table_tooltips import format_output_res_tooltip, format_row_tooltip
from src.gui.table_columns import (
    ALIGN_RIGHT,
    COL_DEF_MAP,
    COL_DEFS,
    DEFAULT_HEADER_CONFIG,
    OUTPUT_RES_NOT_DOWNSCALED_CELL,
    format_duration,
)
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_CELL_ICON_SIZE_PX: int = 12


def display_text_for(
    ev: VideoEvaluation,
    col: int,
    config: ClassifierConfig,
    pending_tiers: Mapping[str, ResolutionTier | None],
) -> Any:
    """셀의 표시 값을 설정과 임시 출력 해상도 입력만으로 계산

    - 화면 표시와 CSV 저장이 같은 함수를 써서 값이 어긋나지 않게 하며, 워커 스레드에서도 호출 가능
    """
    col_def = COL_DEF_MAP.get(col)
    if col_def is None:
        return None
    if col == VideoTableModel.COL_OUTPUT_RES and ev.video.filename in pending_tiers:
        tier = pending_tiers[ev.video.filename]
        return tier.display if tier is not None else OUTPUT_RES_NOT_DOWNSCALED_CELL
    if col_def.display_value is not None:
        return col_def.display_value(ev, config)
    val = col_def.filter_value(ev)
    if col_def.numeric_format is not None and isinstance(val, int | float):
        return format(val, col_def.numeric_format)
    return val


def _comparable_sort_key(value: Any) -> tuple[Any, ...]:
    """정렬 값을 튜플 키로 바꿔 None을 가장 앞, 자연 정렬 토큰은 int < str 순서로 비교되게 함

    - 파일명 토큰 목록은 타입이 섞여도 비교가 되도록 토큰마다 (타입 순위, 값)으로 감쌈
    """
    if value is None:
        return (0,)
    if isinstance(value, list):
        return (1, tuple((0, tok) if isinstance(tok, int) else (1, tok) for tok in value))
    return (1, value)


class VideoTableModel(QAbstractTableModel):
    """VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하는 테이블 뷰모델

    표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    config_changed = pyqtSignal()

    COL_NAME = 0
    COL_WIDTH = 1
    COL_HEIGHT = 2
    COL_ROTATE = 3
    COL_CODEC = 4
    COL_FPS = 5
    COL_DURATION = 6
    COL_BITRATE = 7
    COL_OPT_BITRATE = 8
    COL_RATIO = 9
    COL_TARGET_RATIO = 10
    COL_ZONE = 11
    COL_KEYFRAME = 12
    COL_SIZE = 13
    COL_AFTER_SIZE = 14
    COL_SAVED_SIZE = 15
    COL_SAVED_PER_MIN = 16
    COL_EST_ENCODE_TIME = 17
    COL_OUTPUT_RES = 18
    COL_ACTION = 19
    COL_REASON = 20
    COL_OUT_BITRATE = 21

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
        # - 표시 순서 목록, 적재 순서 목록과 표시 행 -> 적재 인덱스 매핑을 함께 유지
        self._evaluations: list[VideoEvaluation] = []
        self._base_evaluations: list[VideoEvaluation] = []
        self._order: list[int] = []
        self._pending_output_tiers: dict[str, ResolutionTier | None] = {}
        self._search_blobs: list[str] = []
        self._base_blobs: list[str] = []
        self._sort_stack: list[tuple[int, Qt.SortOrder]] = []
        # - 열별 정렬 키 목록(적재 순서). 데이터 교체 시 한 번만 계산하고 정렬 순위 계산에 재사용
        self._sort_keys_by_col: dict[int, list[Any]] = {}
        # - 열별 정렬 순위(적재 순서). 정렬은 순위 정수로만 비교해 Python 콜백 없이 수행
        self._rank_by_col: dict[int, list[int]] = {}
        self._filenames: list[str] = []
        self._name_list_changed: bool = False
        self._root_dir: str = ""
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._filter_summaries: dict[int, str] = {}
        self._color_cache: dict[str, QColor] = {}
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._bold_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)
        self._path_font = make_path_font(FONT_UI_PT)

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

    @property
    def name_list_changed(self) -> bool:
        """마지막 set_data_source에서 파일명 목록이 바뀌었는지 여부(이름 열 폭은 파일명에만 의존)"""
        return self._name_list_changed

    def set_config(self, config: ClassifierConfig) -> None:
        """활성 임계치, 표시 서식 설정을 갱신해 헤더 툴팁과 필터 버킷을 최신화

        - 셀 값은 data()가 매번 현재 설정으로 계산하므로 전 행 dataChanged를 내지 않고, 보이는 영역 재그리기는 뷰가 config_changed로 처리
        - 정렬 키는 설정과 무관하게 캐시되므로 정렬은 다시 계산하지 않음
        """
        self._config = config
        self._emit_all_headers_changed()
        self.config_changed.emit()

    def set_data_source(
        self,
        evaluations: list[VideoEvaluation],
        root_dir: str = "",
        config: ClassifierConfig | None = None,
    ) -> None:
        """평가 결과 리스트와 현재 작업 폴더를 일괄 갱신"""
        self.beginResetModel()
        self._base_evaluations = list(evaluations)
        filenames = [ev.video.filename for ev in self._base_evaluations]
        self._name_list_changed = filenames != self._filenames
        self._filenames = filenames
        self._sort_keys_by_col = {}
        self._rank_by_col = {}
        self._pending_output_tiers = {}
        self._base_blobs = [
            (
                f"{ev.video.filename} {ev.video.codec} "
                f"{ev.action_label} {ev.reason_label} "
                f"{ev.option_label}"
            ).lower()
            for ev in self._base_evaluations
        ]
        self._root_dir = root_dir
        cfg_updated = config is not None
        if config is not None:
            self._config = config
        self._apply_order(self._compute_order())
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
        if not 0 <= row < len(self._order):
            return None
        return self._sort_keys_for(col)[self._order[row]]

    def _sort_keys_for(self, col: int) -> list[Any]:
        """열의 적재 순서 정렬 키 목록(데이터 교체 전까지 캐시)"""
        keys = self._sort_keys_by_col.get(col)
        if keys is None:
            keys = [self._sort_data(ev, col) for ev in self._base_evaluations]
            self._sort_keys_by_col[col] = keys
        return keys

    def _rank_for(self, col: int) -> list[int]:
        """열의 정렬 키를 순위 정수로 바꾼 목록(적재 순서), 같은 값은 같은 순위"""
        rank = self._rank_by_col.get(col)
        if rank is None:
            comparable = [_comparable_sort_key(value) for value in self._sort_keys_for(col)]
            position = {key: idx for idx, key in enumerate(sorted(set(comparable)))}
            rank = [position[key] for key in comparable]
            self._rank_by_col[col] = rank
        return rank

    def _compute_order(self) -> list[int]:
        """정렬 우선순위를 적용한 적재 인덱스 순서 계산

        - 우선순위의 역순으로 안정 정렬을 반복하므로 동률은 적재 순서를 유지
        """
        order = list(range(len(self._base_evaluations)))
        for col, sort_order in reversed(self._sort_stack):
            order.sort(
                key=self._rank_for(col).__getitem__,
                reverse=sort_order == Qt.SortOrder.DescendingOrder,
            )
        return order

    def _apply_order(self, order: list[int]) -> None:
        """표시 순서와 행 단위 캐시를 적재 인덱스 순서에 맞춰 교체(시그널 없음)"""
        self._order = order
        self._evaluations = [self._base_evaluations[idx] for idx in order]
        self._search_blobs = [self._base_blobs[idx] for idx in order]

    @property
    def sort_stack(self) -> list[tuple[int, Qt.SortOrder]]:
        """최근 클릭 순서로 누적된 정렬 우선순위 목록 사본 반환"""
        return list(self._sort_stack)

    def set_sort_stack(self, stack: list[tuple[int, Qt.SortOrder]]) -> None:
        """정렬 우선순위를 바꾸고 표시 순서를 파이썬 정렬 한 번으로 다시 계산

        - 선택 영역은 같은 영상을 따라가도록 영구 인덱스를 함께 옮김
        """
        self._sort_stack = list(stack)
        new_order = self._compute_order()
        self.layoutAboutToBeChanged.emit()
        old_persistent = self.persistentIndexList()
        new_row_of_base = [0] * len(new_order)
        for new_row, base_idx in enumerate(new_order):
            new_row_of_base[base_idx] = new_row
        moved = [
            self.index(new_row_of_base[self._order[index.row()]], index.column())
            for index in old_persistent
        ]
        self._apply_order(new_order)
        self.changePersistentIndexList(old_persistent, moved)
        self.layoutChanged.emit()

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

    def set_pending_output_tiers(self, tiers: Mapping[str, ResolutionTier | None]) -> None:
        """재평가 결과가 오기 전에 출력 해상도 열만 먼저 바꿔 보여 주는 임시 값, 새 평가 결과가 적용되면 사라짐"""
        self._pending_output_tiers = dict(tiers)
        if self._evaluations:
            col = self.COL_OUTPUT_RES
            self.dataChanged.emit(
                self.index(0, col),
                self.index(len(self._evaluations) - 1, col),
                [int(Qt.ItemDataRole.DisplayRole)],
            )

    @property
    def pending_output_tiers(self) -> dict[str, ResolutionTier | None]:
        """재평가 전 임시 출력 해상도 값의 사본(표시 값 계산 입력)"""
        return dict(self._pending_output_tiers)

    def _display_data(self, ev: VideoEvaluation, col: int) -> Any:
        return display_text_for(ev, col, self._config, self._pending_output_tiers)

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

    def column_font_varies(self, col: int) -> bool:
        """열 글꼴이 행 값에 따라 달라질 수 있는지, 값 기반 글꼴 결정 함수가 있는 열만 True"""
        col_def = COL_DEF_MAP.get(col)
        return col_def is not None and col_def.font_kind_getter is not None

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
            if col_def is None:
                return ALIGN_RIGHT
            if col_def.align_factory is not None:
                return col_def.align_factory(self._config)
            return col_def.align
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
            if font_kind == FONT_PATH:
                return self._path_font
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
