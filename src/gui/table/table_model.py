"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon

from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.encode.encode_batch_book import (
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    EncodeBatchBook,
)
from src.core.encode.encode_previous_discard import PREVIOUS_DISCARD_LABEL, PREVIOUS_DISCARD_TIP
from src.core.encoder_specs import get_encode_codec_label
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.resolution_tiers import ResolutionTier
from src.core.workspace_paths import resolve_source_filepath
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT, make_path_font
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.column_filters import (
    ColumnDataType,
    ColumnSpec,
)
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    DOWNSCALE_CANDIDATE_COLUMN,
    FRAME_ANALYSIS_COLUMNS,
    QUALITY_METRIC_COLUMN,
    FrameAnalysisResults,
    contiguous_row_ranges,
    is_frame_analysis_column,
)
from src.gui.table.table_cell_tooltips import ffmpeg_args_line
from src.gui.table.table_column_formats import (
    encode_state_badge,
    encode_state_icon_name,
    format_duration,
)
from src.gui.table.table_column_styles import (
    FONT_BOLD,
    FONT_MONO,
    FONT_MONO_BOLD,
    FONT_PATH,
)
from src.gui.table.table_columns import (
    ALIGN_RIGHT,
    COL_DEF_MAP,
    COL_DEFS,
    DEFAULT_HEADER_CONFIG,
)
from src.gui.table.table_display_values import (
    OUTPUT_RES_COLUMN_INDEX,
    comparable_sort_key,
    display_text_for,
    foreground_color,
)
from src.gui.table.table_tooltips import format_output_res_tooltip, format_row_tooltip
from src.gui.table.table_value_collection import (
    collect_numeric_bounds,
    collect_numeric_bucket_counts,
    collect_value_counts,
)
from src.gui.theme import ThemeManager
from src.service.encode.encode_preview import PlannedCommand, planned_command_for_evaluation

_EMPTY_INDEX = QModelIndex()
_CELL_ICON_SIZE_PX: int = 12
# - 인코딩 진행 행 강조: (행 배경색, 파일명 열 왼쪽 띠 색) 선택자
_ROW_TONE_BY_LABEL: dict[str, Callable[[ThemePalette], tuple[str, str]]] = {
    STATUS_RUNNING: lambda p: (p.row_running_bg, p.row_running_accent),
    STATUS_RUNNING_PAUSED: lambda p: (p.row_paused_bg, p.row_paused_accent),
}


class VideoTableModel(QAbstractTableModel):
    """VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하는 테이블 뷰모델

    표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3
    ACCENT_ROLE = int(Qt.ItemDataRole.UserRole) + 4

    config_changed = pyqtSignal()
    # - 데이터의 작업 폴더가 이전과 다를 때만 알림, 첫 적재는 폴더 변경으로 보지 않음
    root_changed = pyqtSignal()

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
    COL_OUTPUT_RES = OUTPUT_RES_COLUMN_INDEX
    COL_ACTIVE_FRAME = ACTIVE_FRAME_COLUMN
    COL_DOWNSCALE_CANDIDATE = DOWNSCALE_CANDIDATE_COLUMN
    COL_QUALITY_METRIC = QUALITY_METRIC_COLUMN
    COL_ACTION = 22
    COL_QUALITY_VALUE = 27
    COL_REASON = 23
    COL_OUT_BITRATE = 24
    COL_ENCODE_STATE = 25
    COL_FFMPEG_ARGS = 26

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
        # - 파일명 -> 표시 행 번호. 화질 분석 부분 갱신에서 행을 다시 훑지 않으려고 순서 적용 때 만듦
        self._row_of_filename: dict[str, int] = {}
        self._root_dir: str = ""
        # - 인코딩 배치 상태 저장소. 배치를 시작하기 전에는 상태 열이 비어 있음
        self._encode_book: EncodeBatchBook | None = None
        # - 이전 결과 폐기 기록과 원본이 같아 인코딩 대상에서 뺀 영상의 원본 경로. 데이터 설정 때마다 공급 함수로 다시 구함
        self._previous_discards: frozenset[str] = frozenset()
        self._previous_provider: Callable[[Sequence[VideoEvaluation], str], frozenset[str]] | None = None
        self._encode_label_cache: dict[str, str | None] = {}
        # - 예정 FFmpeg 명령은 행마다 한 번만 계산(열 표시와 툴팁이 함께 씀), 데이터, 설정 교체 시 비움
        self._planned_cache: dict[str, PlannedCommand | None] = {}
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._filter_summaries: dict[int, str] = {}
        self._color_cache: dict[str, QColor] = {}
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._frame_view: FrameAnalysisResults = FrameAnalysisResults()
        self._frame_candidate_handler: Callable[[list[VideoEvaluation]], None] | None = None
        self._frame_vr_handler: Callable[[list[VideoEvaluation], bool], None] | None = None
        self._frame_crop_handler: Callable[[list[VideoEvaluation], bool], None] | None = None
        self._frame_manual_handler: Callable[[list[VideoEvaluation], ActiveFrame | None], None] | None = None
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._mono_bold_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT, QFont.Weight.Bold)
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
        self._planned_cache.clear()
        self._emit_all_headers_changed()
        self.config_changed.emit()

    def set_data_source(
        self,
        evaluations: list[VideoEvaluation],
        root_dir: str = "",
        config: ClassifierConfig | None = None,
    ) -> None:
        """평가 결과 리스트와 현재 작업 폴더를 일괄 갱신"""
        if self._root_dir and self._root_dir != root_dir:
            # - 폴더가 바뀌면 이전 폴더 기준의 화면 기억(접힌 그룹 등)을 먼저 놓음
            self.root_changed.emit()
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
                f"{ev.option_label} {ev.planned_cq or ''}"
            ).lower()
            for ev in self._base_evaluations
        ]
        self._root_dir = root_dir
        self._previous_discards = (
            frozenset() if self._previous_provider is None else self._previous_provider(self._base_evaluations, root_dir)
        )
        self._encode_label_cache.clear()
        self._planned_cache.clear()
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
            comparable = [comparable_sort_key(value) for value in self._sort_keys_for(col)]
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
        self._row_of_filename = {ev.video.filename: row for row, ev in enumerate(self._evaluations)}

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
        return collect_value_counts(self._evaluations, col, self.get_column_spec(col), self.get_filter_value_for)

    def collect_numeric_bounds(
        self, col: int
    ) -> tuple[float | None, float | None, int]:
        """수치형 열의 현재 데이터셋 최솟값, 최댓값 및 미측정(None) 행 수 반환"""
        return collect_numeric_bounds(self._evaluations, col, self.get_filter_value_for)

    def collect_numeric_bucket_counts(
        self, col: int
    ) -> list[tuple[str, str, int]]:
        """수치형 열의 측정값을 도메인 구간 버킷별로 집계해 비어 있지 않은 목록 반환"""
        return collect_numeric_bucket_counts(
            self._evaluations, col, self.get_column_spec(col), self._config, self.get_filter_value_for
        )

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

    def set_frame_analysis_results(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """화질 분석 결과를 파일명별로 바꾸고 세 열을 다시 그림(정렬 캐시도 새로 계산)"""
        self._frame_view.set_results(results)
        self._sort_keys_by_col.clear()
        self._rank_by_col.clear()
        if self._evaluations:
            self.dataChanged.emit(
                self.index(0, self.COL_ACTIVE_FRAME),
                self.index(len(self._evaluations) - 1, self.COL_QUALITY_METRIC),
            )
        if any(col in FRAME_ANALYSIS_COLUMNS for col, _order in self._sort_stack):
            self.set_sort_stack(self._sort_stack)

    def update_frame_analysis_results(self, partial: Mapping[str, FrameAnalysisResult]) -> None:
        """도착한 화질 분석 결과만 병합하고 영향받은 행만 다시 그림

        - 화질 분석 열로 정렬 중이면 도착분마다 다시 정렬해 표시 순서와 값이 어긋나지 않게 함
        """
        if not partial:
            return
        self._frame_view.update_results(partial)
        for cache in (self._sort_keys_by_col, self._rank_by_col):
            for col in FRAME_ANALYSIS_COLUMNS:
                cache.pop(col, None)
        if any(col in FRAME_ANALYSIS_COLUMNS for col, _order in self._sort_stack):
            self.set_sort_stack(self._sort_stack)
        rows = {self._row_of_filename[name] for name in partial if name in self._row_of_filename}
        for first, last in contiguous_row_ranges(rows):
            self.dataChanged.emit(
                self.index(first, self.COL_ACTIVE_FRAME),
                self.index(last, self.COL_QUALITY_METRIC),
            )

    def set_manual_vr_overrides(self, filenames: Iterable[str]) -> None:
        """수동 VR 보호 파일명 집합을 바꿔 우클릭 체크 상태를 맞춤"""
        self._frame_view.set_manual_vr(filenames)

    def set_crop_exclusions(self, filenames: Iterable[str]) -> None:
        """검은 여백 잘라내기 제외 파일명 집합을 바꿔 우클릭 체크 상태를 맞춤"""
        self._frame_view.set_crop_excluded(filenames)

    def set_manual_active_frames(self, frames: Mapping[str, ActiveFrame]) -> None:
        """사용자 지정 활성 화면을 파일명별로 바꾸고 활성 화면 열을 다시 그림"""
        self._frame_view.set_manual_frames(frames)
        self._sort_keys_by_col.pop(self.COL_ACTIVE_FRAME, None)
        self._rank_by_col.pop(self.COL_ACTIVE_FRAME, None)
        if self._evaluations:
            self.dataChanged.emit(
                self.index(0, self.COL_ACTIVE_FRAME),
                self.index(len(self._evaluations) - 1, self.COL_ACTIVE_FRAME),
            )
        if any(col == self.COL_ACTIVE_FRAME for col, _order in self._sort_stack):
            self.set_sort_stack(self._sort_stack)

    def set_frame_action_handlers(
        self,
        *,
        apply_candidates: Callable[[list[VideoEvaluation]], None],
        toggle_vr: Callable[[list[VideoEvaluation], bool], None],
        toggle_crop_exclusion: Callable[[list[VideoEvaluation], bool], None] | None = None,
        set_manual_active_frame: Callable[[list[VideoEvaluation], ActiveFrame | None], None]
        | None = None,
    ) -> None:
        """화질 분석 후보 적용, VR 보호, 잘라내기 제외 전환, 사용자 지정 활성 화면 요청을 컨트롤러에 연결"""
        self._frame_candidate_handler = apply_candidates
        self._frame_vr_handler = toggle_vr
        self._frame_crop_handler = toggle_crop_exclusion
        self._frame_manual_handler = set_manual_active_frame

    def frame_candidates(self, evals: Sequence[VideoEvaluation]) -> dict[str, str]:
        """선택 영상 중 화질 분석 축소 후보가 있는 파일명과 규격 키"""
        return self._frame_view.candidates(evals)

    def is_manual_vr(self, filename: str) -> bool:
        """파일명이 수동 VR 보호 대상인지 여부"""
        return self._frame_view.is_manual_vr(filename)

    def is_crop_excluded(self, filename: str) -> bool:
        """파일명이 검은 여백 잘라내기 제외 대상인지 여부"""
        return self._frame_view.is_crop_excluded(filename)

    def has_frame_analysis(self, filename: str) -> bool:
        """파일명의 화질 분석 결과가 있는지 여부(검은 띠가 없어도 활성 화면을 직접 지정할 수 있음)"""
        return self._frame_view.result(filename) is not None

    def apply_frame_analysis_candidates(self, evals: list[VideoEvaluation]) -> None:
        """선택 영상에 화질 분석 축소 후보를 적용하도록 컨트롤러에 요청"""
        if self._frame_candidate_handler is not None:
            self._frame_candidate_handler(evals)

    def toggle_manual_vr(self, evals: list[VideoEvaluation], protected: bool) -> None:
        """선택 영상의 수동 VR 보호 전환을 컨트롤러에 요청"""
        if self._frame_vr_handler is not None:
            self._frame_vr_handler(evals, protected)

    def toggle_crop_exclusion(self, evals: list[VideoEvaluation], excluded: bool) -> None:
        """선택 영상의 검은 여백 잘라내기 제외 전환을 컨트롤러에 요청"""
        if self._frame_crop_handler is not None:
            self._frame_crop_handler(evals, excluded)

    def request_manual_active_frame(
        self, evals: list[VideoEvaluation], frame: ActiveFrame | None
    ) -> None:
        """선택 영상의 사용자 지정 활성 화면 저장을 컨트롤러에 요청"""
        if self._frame_manual_handler is not None:
            self._frame_manual_handler(evals, frame)

    def get_filter_value_for(self, ev: VideoEvaluation, col: int) -> str | float | None:
        """행과 열의 필터 값. 화질 분석 열은 파일명별 결과에서 읽음"""
        if is_frame_analysis_column(col):
            return self._frame_view.filter_value(ev.video.filename, col)
        return self.get_filter_value(ev, col)

    def set_previous_discard_provider(self, provider: Callable[[Sequence[VideoEvaluation], str], frozenset[str]]) -> None:
        """이전 결과 폐기 기록을 구하는 함수를 연결. 연결 뒤 데이터를 설정할 때 상태 표기가 이전 폐기를 보여 줌"""
        self._previous_provider = provider
        self._encode_label_cache.clear()

    def set_encode_book(self, book: EncodeBatchBook) -> None:
        """인코딩 배치의 항목 상태 저장소와 이전 폐기 목록을 갱신하고 상태 열을 다시 그림"""
        self._encode_book = book
        self._previous_discards = (
            frozenset() if self._previous_provider is None else self._previous_provider(self._base_evaluations, self._root_dir)
        )
        self._encode_label_cache.clear()
        if self._evaluations:
            # - 진행 중 -> 완료 전환은 행 배경도 함께 바꾸므로 상태 열만이 아니라 행 전체를 다시 그림
            last_row = len(self._evaluations) - 1
            self.dataChanged.emit(self.index(0, 0), self.index(last_row, self.columnCount() - 1))

    def _encode_label(self, ev: VideoEvaluation) -> str | None:
        """영상의 인코딩 상태 표기. 배치 상태가 있으면 그것을, 없고 이전 폐기 기록이 있으면 이전 폐기, 그 밖은 None"""
        filename = ev.video.filename
        if filename in self._encode_label_cache:
            return self._encode_label_cache[filename]
        path = resolve_source_filepath(self._root_dir, filename)
        label = self._encode_book.label_of(path) if self._encode_book is not None else None
        if label is None:
            label = PREVIOUS_DISCARD_LABEL if path in self._previous_discards else None
        self._encode_label_cache[filename] = label
        return label

    def _encode_tooltip(self, ev: VideoEvaluation) -> str | None:
        """영상의 인코딩 상태 툴팁 문장. 배치 상태가 있으면 그것을, 이전 폐기면 이유 문장, 그 밖은 None"""
        path = resolve_source_filepath(self._root_dir, ev.video.filename)
        if self._encode_book is not None:
            tooltip = self._encode_book.tooltip_of(path)
            if tooltip is not None:
                return tooltip
        return PREVIOUS_DISCARD_TIP if path in self._previous_discards else None

    def _planned_for(self, ev: VideoEvaluation) -> PlannedCommand | None:
        """영상의 예정 FFmpeg 명령. 행마다 한 번만 계산해 인자 열과 툴팁이 재사용"""
        cached = self._planned_cache.get(ev.video.filename)
        if cached is None and ev.video.filename not in self._planned_cache:
            cached = planned_command_for_evaluation(ev, self._config, root_dir=self._root_dir)
            self._planned_cache[ev.video.filename] = cached
        return cached

    def _with_ffmpeg_args(self, ev: VideoEvaluation, tip: str) -> str:
        """셀 툴팁 끝에 예정 FFmpeg 영상 인자 한 줄을 덧붙임. 명령을 만들 수 없으면 원래 문구만"""
        extra = ffmpeg_args_line(self._planned_for(ev))
        return f"{tip}\n{extra}" if extra else tip

    def ffmpeg_args_summary(self, ev: VideoEvaluation) -> str:
        """영상의 예정 FFmpeg 영상 인자 한 줄 요약, 명령을 만들 수 없으면 빈 문자열. CSV 내보내기가 같은 값을 씀"""
        planned = self._planned_for(ev)
        return "" if planned is None else planned.summary

    def planned_command_line(self, ev: VideoEvaluation) -> str | None:
        """영상의 예정 FFmpeg 명령줄, 만들 수 없으면 None. 우클릭 복사가 그대로 넘김"""
        planned = self._planned_for(ev)
        return None if planned is None else planned.command_line

    def _ffmpeg_command_tip(self, ev: VideoEvaluation) -> str:
        """인자 열 셀 툴팁: 붙여 넣어 실행할 전체 명령줄"""
        planned = self._planned_for(ev)
        if planned is None:
            return COL_DEF_MAP[self.COL_FFMPEG_ARGS].tooltip_factory(self._config)
        return planned.command_line

    def _display_data(self, ev: VideoEvaluation, col: int) -> Any:
        if col == self.COL_ENCODE_STATE:
            return self._encode_label(ev)
        if col == self.COL_FFMPEG_ARGS:
            planned = self._planned_for(ev)
            return planned.summary if planned is not None else None
        if is_frame_analysis_column(col):
            return self._frame_view.display(ev.video.filename, col)
        return display_text_for(ev, col, self._config, self._pending_output_tiers)

    def _sort_data(self, ev: VideoEvaluation, col: int) -> Any:
        if is_frame_analysis_column(col):
            return self._frame_view.sort_value(ev.video.filename, col)
        col_def = COL_DEF_MAP.get(col)
        if col_def is None:
            return None
        getter = col_def.sort_value or col_def.filter_value
        return getter(ev)

    def _badge_data(self, ev: VideoEvaluation, col: int) -> Any:
        if col == self.COL_ENCODE_STATE:
            return encode_state_badge(self._encode_label(ev), ThemeManager().current_palette)
        col_def = COL_DEF_MAP.get(col)
        if col_def is None or col_def.badge_getter is None:
            return None
        return col_def.badge_getter(ev, ThemeManager().current_palette)

    def _foreground_data(self, ev: VideoEvaluation, col: int) -> QColor | None:
        hex_color = foreground_color(
            ev, col, ThemeManager().current_palette, frame_view=self._frame_view,
            encode_label=self._encode_label(ev), encode_state_col=self.COL_ENCODE_STATE,
        )
        return self._get_cached_color(hex_color) if hex_color is not None else None

    def _row_tone(self, ev: VideoEvaluation) -> tuple[str, str] | None:
        """인코딩 상태에 따른 (행 배경색, 왼쪽 띠 색). 진행 중과 일시중지만 강조"""
        label = self._encode_label(ev)
        if label not in _ROW_TONE_BY_LABEL:
            return None
        pal = ThemeManager().current_palette
        return _ROW_TONE_BY_LABEL[label](pal)

    def _decoration_data(self, ev: VideoEvaluation, col: int) -> QIcon | None:
        if col == self.COL_ENCODE_STATE:
            icon_name = encode_state_icon_name(self._encode_label(ev))
        else:
            col_def = COL_DEF_MAP.get(col)
            if col_def is None or col_def.icon_getter is None:
                return None
            icon_name = col_def.icon_getter(ev)
        if not icon_name:
            return None
        pal = ThemeManager().current_palette
        col_def = COL_DEF_MAP.get(col)
        if col == self.COL_ENCODE_STATE:
            badge = encode_state_badge(self._encode_label(ev), pal)
            hex_color = badge[1] if badge is not None else pal.text_primary
        else:
            hex_color = (
                col_def.foreground_getter(ev, pal)
                if col_def is not None and col_def.foreground_getter is not None
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
            if font_kind == FONT_MONO_BOLD:
                return self._mono_bold_font
            if font_kind == FONT_MONO:
                return self._mono_font
            return None
        if role == self.BADGE_ROLE:
            return self._badge_data(ev, col)
        if role == int(Qt.ItemDataRole.BackgroundRole):
            tone = self._row_tone(ev)
            return self._get_cached_color(tone[0]) if tone is not None else None
        if role == self.ACCENT_ROLE:
            tone = self._row_tone(ev)
            return self._get_cached_color(tone[1]) if tone is not None else None
        if role == self.SORT_ROLE:
            return self._sort_data(ev, col)
        if role == self.EVAL_ROLE:
            return ev
        if role == int(Qt.ItemDataRole.ToolTipRole):
            if col == self.COL_FFMPEG_ARGS:
                return self._ffmpeg_command_tip(ev)
            if col == self.COL_OUTPUT_RES:
                return self._with_ffmpeg_args(ev, format_output_res_tooltip(ev, self._config))
            if is_frame_analysis_column(col):
                return self._frame_view.tooltip(ev.video.filename, col)
            if col == self.COL_ENCODE_STATE and (state_tip := self._encode_tooltip(ev)) is not None:
                return state_tip
            if col == self.COL_NAME:
                return format_row_tooltip(
                    ev, self._root_dir, get_encode_codec_label(self._config.encode.encode_codec)
                )
            if col == self.COL_ACTION:
                col_def = COL_DEF_MAP[col]
                assert col_def.cell_tooltip is not None
                return self._with_ffmpeg_args(ev, col_def.cell_tooltip(ev))
            cell_tooltip = COL_DEF_MAP[col].cell_tooltip
            if cell_tooltip is None:
                return COL_DEF_MAP[col].tooltip_factory(self._config)
            return cell_tooltip(ev)
        return None


__all__ = ["VideoTableModel", "format_duration"]
