"""검색어·화질·대상 필터링 및 자연 정렬 지원 프록시 모델 모듈"""

from typing import Any

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel

from src.core.models import QualityZone, VideoEvaluation
from src.core.sorting import compare_natural_tokens
from src.gui.table_model import VideoTableModel


class VideoSortFilterProxyModel(QSortFilterProxyModel):
    """검색어, 화질 등급, 인코딩 대상 조건 필터링 및 정렬을 수행하는 프록시 모델"""

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._search_text: str = ""
        self._zone_filter: str = "ALL"
        self._target_filter: str = "ALL"
        self.setSortRole(VideoTableModel.SORT_ROLE)
        self.setDynamicSortFilter(True)

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

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """검색어·화질 등급·분류 대상 필터 조건 일치 여부 판별"""
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
        return not (
            self._target_filter == "DEFECT_KF" and not ev.is_keyframe_defective
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """SORT_ROLE 값과 자연 정렬 토큰을 기준으로 행 순서 비교"""
        src_model = self.sourceModel()
        if src_model is None:
            return False
        l_val = src_model.data(left, VideoTableModel.SORT_ROLE)
        r_val = src_model.data(right, VideoTableModel.SORT_ROLE)

        if l_val is None and r_val is None:
            return False
        if l_val is None:
            return True
        if r_val is None:
            return False

        if isinstance(l_val, list) and isinstance(r_val, list):
            return compare_natural_tokens(l_val, r_val)

        # 동일 값일 경우 파일명 자연 정렬을 2차 정렬 기준으로 적용
        if l_val == r_val and left.column() != VideoTableModel.COL_NAME:
            l_name_idx = src_model.index(left.row(), VideoTableModel.COL_NAME)
            r_name_idx = src_model.index(right.row(), VideoTableModel.COL_NAME)
            l_tokens = src_model.data(l_name_idx, VideoTableModel.SORT_ROLE)
            r_tokens = src_model.data(r_name_idx, VideoTableModel.SORT_ROLE)
            if isinstance(l_tokens, list) and isinstance(r_tokens, list):
                return compare_natural_tokens(l_tokens, r_tokens)

        try:
            return bool(l_val < r_val)
        except TypeError:
            return str(l_val) < str(r_val)
