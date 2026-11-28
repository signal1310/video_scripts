"""화질 분석 세 열의 셀 아이콘, 배경 강조, 접근성 이름과 툴팁 조합 모듈

- 표 모델이 파일명별 결과(`FrameAnalysisResults`)를 넘기고, 이 모듈이 셀 표시 상태를 판정
- 확인한 행은 칩 필터가 켜진 동안에만 흐린 확인 아이콘 하나를 그리고, 방법은 툴팁으로 알림
"""

from collections.abc import Callable, Mapping

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.models import VideoEvaluation
from src.gui.styles.palettes import ThemePalette
from src.gui.table.frame_analysis_columns import (
    ACK_ICON_NAME,
    ACTIVE_FRAME_KEEP_FULL_ICON,
    ACTIVE_FRAME_MANUAL_ICON,
    ACTIVE_FRAME_MANUAL_NAME,
    ACTIVE_FRAME_MARGINS_VARY_TEXT,
    CROP_REVIEW_TEXT,
    MARGINS_VARY_ICON_NAME,
    FrameAnalysisResults,
    acknowledged_tooltip,
    crop_review_tooltip,
    margins_vary_tooltip,
)

# - 셀 아이콘 그림의 한 변. 여러 아이콘을 나란히 담은 셀은 이 값의 배수만큼 넓힘
CELL_ICON_SIZE_PX: int = 12
# - 여백 확인 필요 경고 아이콘. 키프레임 초과 셀과 같은 아이콘을 씀
REVIEW_WARNING_ICON_NAME: str = "alert-triangle"


class FrameAnalysisCells:
    """화질 분석 셀의 표시 상태(확인, 여백 확인 필요, 구간별 여백 차이)를 판정하는 도우미

    - 이름 열과 활성 화면 열만 아이콘과 강조 배경을 가지며, 그 밖의 열은 빈 결과를 돌려줌
    - 여백 확인 필요가 구간별 여백 차이보다 우선해 배경색과 접근성 이름을 정함
    """

    def __init__(
        self,
        view: FrameAnalysisResults,
        *,
        crop_enabled: Callable[[], bool],
        name_column: int,
        active_frame_column: int,
    ) -> None:
        self._view = view
        self._crop_enabled = crop_enabled
        self._name_column = name_column
        self._active_frame_column = active_frame_column
        # - 칩 필터가 켜진 동안 표에 고정할 확인 행의 확인 방법(파일명 -> manual/reset/full)
        self._acknowledged: dict[str, str] = {}
        self._chip_filter_active: bool = False

    def review_region(self, ev: VideoEvaluation) -> ActiveRegion | None:
        """여백 확인 필요 표시를 보일 영상의 자동 감지 활성 화면, 아니면 None

        - 사용자 지정 활성 화면이 있으면 확인한 것으로 보아 경고를 보이지 않음(자동 크롭도 보류하지 않음)
        """
        if self.manual_applies(ev.video.filename):
            return None
        if not self._view.is_review_flagged(ev.video.filename):
            return None
        result = self._view.result(ev.video.filename)
        return None if result is None else result.active_region

    def vary_applies(self, ev: VideoEvaluation) -> bool:
        """구간별 여백 차이 표시를 붙일 행인지. 확인한 행과 직접 지정 행은 표시하지 않음"""
        filename = ev.video.filename
        return self._view.is_vary_flagged(filename) and not self.manual_applies(filename)

    def manual_applies(self, filename: str) -> bool:
        """사용자 지정 활성 화면이 셀 표시에 쓰이는지(VR 보호 영상은 VR 문구가 우선)"""
        if self._view.manual_frame(filename) is None:
            return False
        result = self._view.result(filename)
        return result is None or not result.is_vr_protected

    def keep_full_applies(self, ev: VideoEvaluation) -> bool:
        """영상별 잘라내기 제외로 전체 화면 유지 알약을 보일 행인지(잘라내기 설정이 켜져 있을 때)"""
        return self._crop_enabled() and self._view.is_keep_full(ev.video.filename)

    def set_acknowledged(self, kinds: Mapping[str, str], *, active: bool) -> bool:
        """확인한 행의 표시 방법과 칩 필터 활성 여부를 반영. 셀 다시 그리기가 필요하면 True"""
        changed = self._acknowledged != dict(kinds) or self._chip_filter_active != active
        self._acknowledged = dict(kinds)
        self._chip_filter_active = active
        return changed

    def acknowledged_kind(self, filename: str) -> str | None:
        """칩 필터가 켜진 동안 확인 아이콘을 보일 행의 확인 방법"""
        if not self._chip_filter_active:
            return None
        return self._acknowledged.get(filename)

    def accessible_name(self, ev: VideoEvaluation, col: int) -> str | None:
        """활성 화면 셀이나 여백 확인 필요 셀의 접근성 이름. 그 밖의 셀은 None

        - 사용자 지정 활성 화면은 연필 아이콘의 뜻을 접근성 이름으로 알림
        """
        if col != self._active_frame_column:
            return None
        if self.review_region(ev) is not None:
            return CROP_REVIEW_TEXT
        if self.vary_applies(ev):
            return ACTIVE_FRAME_MARGINS_VARY_TEXT
        if self.manual_applies(ev.video.filename):
            return ACTIVE_FRAME_MANUAL_NAME
        return None

    def icon_specs(
        self, ev: VideoEvaluation, col: int, *, palette: ThemePalette
    ) -> list[tuple[str, str]]:
        """이름, 활성 화면 셀에 왼쪽부터 나란히 그릴 (아이콘 이름, 색). 여백 확인 필요가 먼저 옴

        - 확인한 행은 칩 필터가 켜진 동안에만 흐린 확인 아이콘 하나를 그리며, 방법은 툴팁으로 알림
        - 이름 셀은 여백이 두 개 겹치면 여백 확인 필요 아이콘만 그리고, 활성 화면 셀은 둘 다 담음
        """
        if col not in (self._name_column, self._active_frame_column):
            return []
        if self.acknowledged_kind(ev.video.filename) is not None:
            return [(ACK_ICON_NAME, palette.text_muted)]
        specs: list[tuple[str, str]] = []
        if self.review_region(ev) is not None:
            specs.append((REVIEW_WARNING_ICON_NAME, palette.badge_pending[1]))
        if col == self._active_frame_column and self.vary_applies(ev):
            specs.append((MARGINS_VARY_ICON_NAME, palette.fg_vary_icon))
        if col == self._name_column or specs:
            return specs
        if self.manual_applies(ev.video.filename):
            return [(ACTIVE_FRAME_MANUAL_ICON, palette.accent_blue)]
        if self.keep_full_applies(ev):
            return [(ACTIVE_FRAME_KEEP_FULL_ICON, palette.badge_pass[1])]
        return []

    def icon_width(
        self, ev: VideoEvaluation, col: int, *, palette: ThemePalette
    ) -> int | None:
        """여러 아이콘을 나란히 그린 셀의 아이콘 그림 폭. 하나거나 없으면 None"""
        count = len(self.icon_specs(ev, col, palette=palette))
        return CELL_ICON_SIZE_PX * count if count > 1 else None

    def background(
        self, ev: VideoEvaluation, col: int, *, palette: ThemePalette
    ) -> str | None:
        """활성 화면 셀의 플래그 배경색. 확인한 행은 강조 없이, 여백 확인 필요가 구간별 여백 차이보다 우선"""
        if col != self._active_frame_column:
            return None
        if self.acknowledged_kind(ev.video.filename) is not None:
            return None
        if self.review_region(ev) is not None:
            return palette.badge_pending[0]
        if self.vary_applies(ev):
            return palette.badge_vary[0]
        return None

    def tooltip(
        self, ev: VideoEvaluation, col: int, *, row_tooltip: Callable[[], str]
    ) -> str | None:
        """이름, 활성 화면 셀의 확인, 여백 확인, 구간별 여백 차이 안내. 해당 없으면 None

        - 두 플래그가 함께면 기존 여백 확인 필요 안내 뒤에 구간별 차이 문장을 붙임
        - 확인한 행은 확인 방법을 알리고, 이름 열은 행 상세 정보를 지우지 않고 앞에 덧붙임
        """
        ack = self.acknowledged_kind(ev.video.filename)
        if ack is not None:
            if col == self._active_frame_column:
                return acknowledged_tooltip(ack)
            if col == self._name_column:
                return f"{acknowledged_tooltip(ack)}\n\n{row_tooltip()}"
        region = (
            self.review_region(ev)
            if col in (self._name_column, self._active_frame_column)
            else None
        )
        vary = col == self._active_frame_column and self.vary_applies(ev)
        if region is not None and col == self._active_frame_column:
            base = crop_review_tooltip(region)
            return f"{base}\n{margins_vary_tooltip()}" if vary else base
        if vary:
            return margins_vary_tooltip()
        if region is not None and col == self._name_column:
            return f"{crop_review_tooltip(region)}\n\n{row_tooltip()}"
        return None


__all__ = [
    "CELL_ICON_SIZE_PX",
    "REVIEW_WARNING_ICON_NAME",
    "FrameAnalysisCells",
]

