"""검색어, 동적 열 필터링 및 자연 정렬 지원 프록시 모델 모듈"""

from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import Any

from PyQt6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.settings.ui_state import DEFAULT_FILTER_SEARCH
from src.gui.table.column_filter_values import prune_stale_filter_rules
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.table_model import VideoTableModel


class VideoSortFilterProxyModel(QSortFilterProxyModel):
    """검색어 및 열별 동적 조건 필터링 프록시 모델

    - 정렬 순서는 원본 모델이 소유하며, 프록시는 Qt 정렬을 쓰지 않고 필터만 담당
    """

    column_filters_changed = pyqtSignal()
    sort_keys_changed = pyqtSignal()
    # - 칩 필터 안에 고정한 확인 행이나 그 확인 방법이 바뀌면 알림(표가 흐린 확인 아이콘을 다시 그림)
    sticky_changed = pyqtSignal()
    # - 표 셀 검색 강조가 따라갈 지금 검색어, 강조를 그리는 델리게이트가 이 신호로 다시 그림
    search_text_changed = pyqtSignal(str)
    # - 재평가 한 번의 시작과 끝, 그룹 프록시가 이 사이의 행 변경 신호를 모아 한 번만 다시 만듦
    filter_batch_started = pyqtSignal()
    filter_batch_finished = pyqtSignal()

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._search_text: str = DEFAULT_FILTER_SEARCH
        self._search_display: str = ""
        self._column_filters: dict[int, ColumnFilterRule] = {}
        # - 칩 필터(여백 확인 필요, 구간별 여백 차이)는 OR로 합침. 열 필터와 별개로 저장하지 않음
        self._review_only: bool = False
        self._vary_only: bool = False
        # - 칩 필터가 켜진 동안 확인 처리해 표에 남긴 파일명과 확인 방법(manual/reset/full).
        #   칩을 끄거나 폴더를 다시 읽으면 비움
        self._sticky: dict[str, str] = {}
        # - 마지막으로 본 파일 이름 목록. 목록이 바뀌면 고정을 풀어 다른 폴더의 행이 남지 않게 함
        self._source_names: set[str] = set()
        self.setDynamicSortFilter(True)

    def is_any_filter_active(self) -> bool:
        """검색어, 열 헤더 필터, 칩 필터 중 하나라도 활성화되었는지 반환"""
        return bool(self._search_text or self._column_filters or self._review_only or self._vary_only)

    @property
    def chip_filter_active(self) -> bool:
        """칩 필터(여백 확인 필요, 구간별 여백 차이)가 하나라도 켜져 있는지"""
        return self._review_only or self._vary_only

    def set_chip_filters(self, *, review: bool, vary: bool) -> None:
        """두 칩 필터 상태를 적용하고, 모두 꺼지면 고정 행을 풂

        - 칩이 하나라도 켜져 있는 동안 확인한 행은 고정 목록에 남겨 표에서 사라지지 않게 함
        """
        if self._review_only == review and self._vary_only == vary:
            return
        self._review_only = review
        self._vary_only = vary
        had_sticky = bool(self._sticky)
        if not (review or vary):
            self._sticky.clear()
        self._refilter()
        if had_sticky and not self._sticky:
            self.sticky_changed.emit()

    def set_review_only(self, enabled: bool) -> None:
        """여백 확인 필요 영상만 보기 칩 상태를 적용하고 필터 재평가"""
        self.set_chip_filters(review=enabled, vary=self._vary_only)

    def sticky_acknowledged(self) -> dict[str, str]:
        """지금 칩 필터 안에 고정된 확인 행의 파일명과 확인 방법 사본"""
        return dict(self._sticky)

    def add_sticky(self, kinds: Mapping[str, str]) -> None:
        """칩 필터가 켜져 있는 동안 확인한 행을 그 확인 방법과 함께 표에 고정"""
        if not self.chip_filter_active:
            return
        before = dict(self._sticky)
        self._sticky.update({name: kind for name, kind in kinds.items() if name})
        if self._sticky == before:
            return
        self._refilter()
        self.sticky_changed.emit()

    def discard_sticky(self, names: Iterable[str], *, kind: str) -> None:
        """확인 방법이 kind인 고정 행만 풀어 표시 대상으로 되돌림

        - 확인 취소처럼 확인한 조치가 더 이상 유효하지 않을 때, 다른 방법으로 확인한 행은 그대로 둠
        """
        stale = {name for name in names if self._sticky.get(name) == kind}
        if not stale:
            return
        for name in stale:
            del self._sticky[name]
        self._refilter()
        self.sticky_changed.emit()

    def clear_sticky(self) -> None:
        """고정 목록을 비워 확인한 행을 표에서 다시 뺌"""
        if not self._sticky:
            return
        self._sticky.clear()
        self._refilter()
        self.sticky_changed.emit()

    def reset_chip_filters(self) -> None:
        """필터 초기화에서 두 칩 필터와 고정 목록을 함께 끔"""
        if not (self._review_only or self._vary_only or self._sticky):
            return
        had_sticky = bool(self._sticky)
        self._review_only = False
        self._vary_only = False
        self._sticky.clear()
        self._refilter()
        if had_sticky:
            self.sticky_changed.emit()

    def refilter(self) -> None:
        """표시 값이 바뀐 뒤 필터를 다시 평가"""
        self._refilter()

    @property
    def review_only(self) -> bool:
        """여백 확인 필요 영상만 보는 중인지"""
        return self._review_only

    @property
    def vary_only(self) -> bool:
        """구간별 여백 차이 영상만 보는 중인지"""
        return self._vary_only

    def iter_visible_evaluations(self) -> list[VideoEvaluation]:
        """현재 필터 조건을 통과해 화면에 표시 중인 VideoEvaluation 목록 반환"""
        model = self.sourceModel()
        if not isinstance(model, VideoTableModel):
            return []
        result: list[VideoEvaluation] = []
        for proxy_row in range(self.rowCount()):
            src_row = self.mapToSource(self.index(proxy_row, 0)).row()
            ev = model.get_evaluation_row(src_row)
            if ev is not None:
                result.append(ev)
        return result

    @property
    def sort_stack(self) -> list[tuple[int, Qt.SortOrder]]:
        """최근 클릭 순서로 누적된 정렬 우선순위 목록 사본 반환"""
        model = self.sourceModel()
        return model.sort_stack if isinstance(model, VideoTableModel) else []

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """엑셀식 다중 열 안정 정렬 우선순위를 누적해 원본 모델에 전달

        - column이 음수면 정렬을 해제하고 적재 순서로 복원
        """
        if column < 0:
            stack: list[tuple[int, Qt.SortOrder]] = []
        else:
            stack = [(column, order)] + [
                (col, ord_) for col, ord_ in self.sort_stack if col != column
            ]
        model = self.sourceModel()
        if isinstance(model, VideoTableModel):
            model.set_sort_stack(stack)
        self.sort_keys_changed.emit()

    def setSourceModel(self, sourceModel: QAbstractItemModel | None) -> None:
        """원본 모델 설정 시 분류 임계치 변경 시그널과 동적 필터 재평가를 연결"""
        prev = self.sourceModel()
        if isinstance(prev, VideoTableModel):
            with suppress(TypeError):
                prev.config_changed.disconnect(self._on_source_config_changed)
            with suppress(TypeError):
                prev.root_changed.disconnect(self._on_source_root_changed)
            with suppress(TypeError):
                prev.modelReset.disconnect(self._on_source_reset)
        super().setSourceModel(sourceModel)
        if isinstance(sourceModel, VideoTableModel):
            sourceModel.config_changed.connect(self._on_source_config_changed)
            # - 폴더를 다시 읽으면 이전 폴더에서 고정한 확인 행이 남지 않게 비움
            sourceModel.root_changed.connect(self._on_source_root_changed)
            sourceModel.modelReset.connect(self._on_source_reset)

    def _on_source_root_changed(self) -> None:
        """작업 폴더가 바뀌면 칩 필터 안 확인 행 고정을 풂"""
        self.clear_sticky()

    def _on_source_reset(self) -> None:
        """파일 목록을 다시 읽어 이름 목록이 바뀌면 칩 필터 안 확인 행 고정을 풂

        - 재평가처럼 같은 목록을 다시 반영할 때는 고정을 그대로 둠
        """
        model = self.sourceModel()
        if not isinstance(model, VideoTableModel):
            return
        names = {ev.video.filename for ev in model.evaluations}
        if names == self._source_names:
            return
        self._source_names = names
        self.clear_sticky()

    def _on_source_config_changed(self) -> None:
        # - 설정이 바뀌면 수치 구간 키가 새로 만들어지므로, 사라진 키를 먼저 버려야 필터가 표를 비우지 않음
        pruned = prune_stale_filter_rules(
            self.get_active_column_filters(), self._source_config()
        )
        filters_pruned = pruned != self._column_filters
        if filters_pruned:
            self._column_filters = pruned
        self._refilter()
        if self._column_filters or filters_pruned:
            self.column_filters_changed.emit()

    def _refilter(self) -> None:
        """필터 재평가를 하나의 묶음으로 알리고 실행

        - Qt는 재평가 결과를 연속 구간마다 rowsRemoved/rowsInserted로 나눠 알리므로
          그룹 프록시가 신호마다 다시 만들지 않도록 시작과 끝을 알림
        """
        self.filter_batch_started.emit()
        try:
            self.invalidateFilter()
        finally:
            self.filter_batch_finished.emit()

    def _source_config(self) -> ClassifierConfig | None:
        model = self.sourceModel()
        if isinstance(model, VideoTableModel):
            return model.config
        return None

    def set_search_text(self, text: str) -> None:
        """검색어 필터 문자열 갱신 및 필터 재평가

        - 표 셀 강조가 같은 값을 쓰도록 입력 표기 그대로를 신호로 알림(값이 그대로면 알리지 않음)
        """
        display = text.strip()
        previous = self._search_display
        self._search_display = display
        self._search_text = display.lower()
        if display != previous:
            self.search_text_changed.emit(display)
        self._refilter()

    @property
    def search_text(self) -> str:
        """지금 적용 중인 검색어(사용자가 입력한 표기 그대로)"""
        return self._search_display

    def set_column_filter(self, col: int, rule: ColumnFilterRule | None) -> None:
        """지정 열의 동적 필터 조건을 설정하거나 비활성 시 제거"""
        spec = VideoTableModel.get_column_spec(col)
        if rule is None or not rule.is_active(spec.data_type):
            if col not in self._column_filters:
                return
            del self._column_filters[col]
        else:
            if self._column_filters.get(col) == rule:
                return
            self._column_filters[col] = rule
        self._refilter()
        self.column_filters_changed.emit()

    def clear_column_filter(self, col: int) -> None:
        """지정 열의 동적 필터 조건을 해제"""
        self.set_column_filter(col, None)

    def clear_all_column_filters(self) -> None:
        """활성화된 모든 열별 동적 필터를 일괄 초기화"""
        if not self._column_filters:
            return
        self._column_filters.clear()
        self._refilter()
        self.column_filters_changed.emit()

    def get_column_filter(self, col: int) -> ColumnFilterRule | None:
        """지정 열에 적용 중인 동적 필터 조건 반환"""
        return self._column_filters.get(col)

    def get_active_column_filters(self) -> dict[int, ColumnFilterRule]:
        """현재 활성화된 모든 열별 동적 필터 맵 사본 반환"""
        return dict(self._column_filters)

    def active_column_filter_count(self) -> int:
        """활성화된 열별 동적 필터 개수 반환"""
        return len(self._column_filters)

    def get_column_filter_summaries(self) -> dict[int, str]:
        """활성 열별 필터 조건의 요약 문구 맵 반환"""
        cfg = self._source_config()
        return {
            col: rule.describe(VideoTableModel.get_column_spec(col), config=cfg)
            for col, rule in self._column_filters.items()
        }

    def filterAcceptsRow(self, source_row: int, _source_parent: QModelIndex) -> bool:
        """검색어 및 열별 동적 필터 조건 일치 여부 판별"""
        model = self.sourceModel()
        if not isinstance(model, VideoTableModel):
            return True

        ev = model.get_evaluation_row(source_row)
        if ev is None:
            return True

        # 1. 검색어 필터 (파일명, 코덱, 판정, 권장 작업, 폴더명 매칭)
        if (
            self._search_text
            and self._search_text not in model.get_search_blob(source_row)
        ):
            return False

        # 2. 칩 필터(여백 확인 필요, 구간별 여백 차이). 두 칩이 함께 켜지면 OR로 합침
        if self.chip_filter_active:
            filename = ev.video.filename
            # - 이번 칩 필터 세션에서 확인한 행은 문제가 풀렸어도 표에 남김
            if filename not in self._sticky:
                flagged = (self._review_only and model.is_review_flagged(filename)) or (
                    self._vary_only and model.is_vary_flagged(filename)
                )
                if not flagged:
                    return False

        # 3. 열 데이터 타입 기반 동적 필터 (AND 결합)
        cfg = model.config
        for col, rule in self._column_filters.items():
            spec = VideoTableModel.get_column_spec(col)
            # - 한 행이 값 두 개를 가질 수 있는 열(활성 화면의 크기와 여백 확인 필요)은 어느 하나만 맞아도 남김
            values = model.get_filter_values_for(ev, col)
            if not any(rule.matches(value, spec, config=cfg) for value in values):
                return False
        return True

