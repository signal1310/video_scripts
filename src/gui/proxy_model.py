"""검색어·화질·대상 필터링 및 자연 정렬 지원 프록시 모델 모듈"""

from contextlib import suppress
from typing import Any

from PyQt6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)

from src.core.config import (
    DEFAULT_FILTER_SEARCH,
    DEFAULT_FILTER_TARGET,
    DEFAULT_FILTER_ZONE,
    ClassifierConfig,
)
from src.core.models import QualityZone, VideoEvaluation
from src.core.sorting import compare_natural_tokens
from src.gui.column_filters import ColumnFilterRule
from src.gui.table_model import VideoTableModel


def _compare_sort_values(val_a: Any, val_b: Any) -> int:
    """SORT_ROLE 두 값을 오름차순 기준으로 비교해 -1·0·1 반환"""
    if val_a is None and val_b is None:
        return 0
    if val_a is None:
        return -1
    if val_b is None:
        return 1
    if isinstance(val_a, list) and isinstance(val_b, list):
        if compare_natural_tokens(val_a, val_b):
            return -1
        if compare_natural_tokens(val_b, val_a):
            return 1
        return 0
    if val_a == val_b:
        return 0
    try:
        return -1 if val_a < val_b else 1
    except TypeError:
        sa, sb = str(val_a), str(val_b)
        if sa == sb:
            return 0
        return -1 if sa < sb else 1


class VideoSortFilterProxyModel(QSortFilterProxyModel):
    """검색어, 화질 등급, 인코딩 대상 및 열별 동적 조건 필터링·정렬 프록시 모델"""

    column_filters_changed = pyqtSignal()

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._search_text: str = DEFAULT_FILTER_SEARCH
        self._zone_filter: str = DEFAULT_FILTER_ZONE
        self._target_filter: str = DEFAULT_FILTER_TARGET
        self._column_filters: dict[int, ColumnFilterRule] = {}
        self._sort_stack: list[tuple[int, Qt.SortOrder]] = []
        self.setSortRole(VideoTableModel.SORT_ROLE)
        self.setDynamicSortFilter(True)

    @property
    def sort_stack(self) -> list[tuple[int, Qt.SortOrder]]:
        """최근 클릭 순서로 누적된 정렬 우선순위 목록 사본 반환"""
        return list(self._sort_stack)

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """엑셀식 다중 열 안정 정렬 우선순위를 누적해 정렬 실행"""
        if column < 0:
            self._sort_stack.clear()
            super().sort(-1, order)
            return
        self._sort_stack = [(column, order)] + [
            (col, ord_) for col, ord_ in self._sort_stack if col != column
        ]
        super().sort(column, order)

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
        self.invalidateFilter()
        if self._column_filters:
            self.column_filters_changed.emit()

    def _source_config(self) -> ClassifierConfig | None:
        model = self.sourceModel()
        if isinstance(model, VideoTableModel):
            return model.config
        return None

    def set_search_text(self, text: str) -> None:
        """검색어 필터 문자열 갱신 및 필터 재평가"""
        self._search_text = text.strip().lower()
        self.invalidateFilter()

    def set_zone_filter(self, zone_key: str) -> None:
        """화질 등급 필터 설정 ('ALL', 'OVER', 'OPTIMAL', 'LOW')"""
        self._zone_filter = zone_key
        self.invalidateFilter()

    def set_target_filter(self, target_key: str) -> None:
        """분류 대상 필터 설정 ('ALL', 'ENCODE_ONLY', 'PASS_ONLY', 'DEFECT_KF')"""
        self._target_filter = target_key
        self.invalidateFilter()

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
        self.invalidateFilter()
        self.column_filters_changed.emit()

    def clear_column_filter(self, col: int) -> None:
        """지정 열의 동적 필터 조건을 해제"""
        self.set_column_filter(col, None)

    def clear_all_column_filters(self) -> None:
        """활성화된 모든 열별 동적 필터를 일괄 초기화"""
        if not self._column_filters:
            return
        self._column_filters.clear()
        self.invalidateFilter()
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

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """검색어·화질 등급·분류 대상 및 열별 동적 필터 조건 일치 여부 판별"""
        model = self.sourceModel()
        if not isinstance(model, VideoTableModel):
            return True

        idx = model.index(source_row, 0, source_parent)
        ev: VideoEvaluation | None = model.data(idx, VideoTableModel.EVAL_ROLE)
        if ev is None:
            return True

        # 1. 검색어 필터 (파일명, 코덱, 처방, 폴더명 매칭)
        if self._search_text:
            searchable = (
                f"{ev.video.filename} {ev.video.codec} "
                f"{ev.smart_action.value} {ev.target_dirname or ''}"
            ).lower()
            if self._search_text not in searchable:
                return False

        # 2. 화질 등급 필터
        if self._zone_filter == "OVER" and ev.quality_zone != QualityZone.OVER:
            return False
        if self._zone_filter == "OPTIMAL" and ev.quality_zone != QualityZone.OPTIMAL:
            return False
        if self._zone_filter == "LOW" and ev.quality_zone != QualityZone.LOW:
            return False

        # 3. 인코딩 대상 / 키프레임 불량 필터
        if self._target_filter == "ENCODE_ONLY" and ev.target_dirname is None:
            return False
        if self._target_filter == "PASS_ONLY" and ev.target_dirname is not None:
            return False
        if self._target_filter == "DEFECT_KF" and not ev.is_keyframe_defective:
            return False

        # 4. 열 데이터 타입 기반 동적 필터 (AND 결합)
        cfg = model.config
        for col, rule in self._column_filters.items():
            spec = VideoTableModel.get_column_spec(col)
            val = VideoTableModel.get_filter_value(ev, col)
            if not rule.matches(val, spec, config=cfg):
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """정렬 우선순위 스택과 원본 행 순서 기반의 엑셀식 순수 안정 비교 수행"""
        src_model = self.sourceModel()
        if src_model is None:
            return False

        # Qt 내림차순 래핑을 보정해 first/second가 항상 최종 뷰의 앞·뒤 후보 행을 가리키도록 정규화
        is_desc = self.sortOrder() == Qt.SortOrder.DescendingOrder
        first, second = (right, left) if is_desc else (left, right)

        stack = self._sort_stack or [(first.column(), self.sortOrder())]
        for col, order in stack:
            f_idx = src_model.index(first.row(), col)
            s_idx = src_model.index(second.row(), col)
            val_a = src_model.data(f_idx, VideoTableModel.SORT_ROLE)
            val_b = src_model.data(s_idx, VideoTableModel.SORT_ROLE)
            cmp = _compare_sort_values(val_a, val_b)
            if cmp != 0:
                return cmp < 0 if order == Qt.SortOrder.AscendingOrder else cmp > 0

        return first.row() < second.row()
