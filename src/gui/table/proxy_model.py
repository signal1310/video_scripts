"""검색어, 동적 열 필터링 및 자연 정렬 지원 프록시 모델 모듈"""

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
from src.gui.table.column_filters import ColumnFilterRule
from src.gui.table.table_model import VideoTableModel


class VideoSortFilterProxyModel(QSortFilterProxyModel):
    """검색어 및 열별 동적 조건 필터링 프록시 모델

    - 정렬 순서는 원본 모델이 소유하며, 프록시는 Qt 정렬을 쓰지 않고 필터만 담당
    """

    column_filters_changed = pyqtSignal()
    sort_keys_changed = pyqtSignal()
    # - 재평가 한 번의 시작과 끝, 그룹 프록시가 이 사이의 행 변경 신호를 모아 한 번만 다시 만듦
    filter_batch_started = pyqtSignal()
    filter_batch_finished = pyqtSignal()

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._search_text: str = DEFAULT_FILTER_SEARCH
        self._column_filters: dict[int, ColumnFilterRule] = {}
        self.setDynamicSortFilter(True)

    def is_any_filter_active(self) -> bool:
        """검색어, 열 헤더 필터 중 하나라도 활성화되었는지 반환"""
        return bool(self._search_text or self._column_filters)

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
        super().setSourceModel(sourceModel)
        if isinstance(sourceModel, VideoTableModel):
            sourceModel.config_changed.connect(self._on_source_config_changed)

    def _on_source_config_changed(self) -> None:
        self._refilter()
        if self._column_filters:
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
        """검색어 필터 문자열 갱신 및 필터 재평가"""
        self._search_text = text.strip().lower()
        self._refilter()

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

        # 2. 열 데이터 타입 기반 동적 필터 (AND 결합)
        cfg = model.config
        for col, rule in self._column_filters.items():
            spec = VideoTableModel.get_column_spec(col)
            val = model.get_filter_value_for(ev, col)
            if not rule.matches(val, spec, config=cfg):
                return False
        return True

