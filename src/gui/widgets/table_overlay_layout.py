"""테이블 뷰 위에 얹는 빈 상태 오버레이와 가로 끝 페이드 오버레이의 생성과 위치 동기화 모듈

- 오버레이의 그리기 로직은 각 위젯 모듈에 두고, 여기서는 뷰 크기와 스크롤 상태에 맞춘 배치만 담당
"""

from PyQt6.QtWidgets import QAbstractItemView, QWidget

from src.gui.widgets.edge_fade_overlay import HorizontalEdgeFadeOverlay, sync_edge_overlay_from_scrollbar
from src.gui.widgets.table_empty_overlay import TableEmptyOverlay


class TableOverlays:
    """뷰의 빈 상태 오버레이와 뷰포트, 헤더 뷰포트의 가로 끝 페이드 오버레이를 묶어 관리"""

    def __init__(self, view: QAbstractItemView, header_viewport: QWidget | None) -> None:
        self._view = view
        self._header_viewport = header_viewport
        vp = view.viewport()
        self.empty: TableEmptyOverlay = TableEmptyOverlay(vp)
        self.vp_fade: HorizontalEdgeFadeOverlay | None = (
            HorizontalEdgeFadeOverlay(vp, lambda p: p.bg_card) if vp is not None else None
        )
        self.header_fade: HorizontalEdgeFadeOverlay | None = (
            HorizontalEdgeFadeOverlay(header_viewport, lambda p: p.header_bg) if header_viewport is not None else None
        )
        h_bar = view.horizontalScrollBar()
        if h_bar is not None:
            h_bar.valueChanged.connect(lambda _value: self.sync_edge_fades())
            h_bar.rangeChanged.connect(lambda _lo, _hi: self.sync_edge_fades())

    def sync_edge_fades(self) -> None:
        """빈 상태 오버레이가 보이는 동안에는 뷰포트 페이드를 끄고, 스크롤바 상태에 맞춰 페이드를 갱신"""
        h_bar = self._view.horizontalScrollBar()
        show_vp_fade = not self.empty.isVisible()
        sync_edge_overlay_from_scrollbar(self.vp_fade, h_bar, enabled=show_vp_fade)
        sync_edge_overlay_from_scrollbar(self.header_fade, h_bar, enabled=True)

    def sync_geometry(self) -> None:
        """빈 상태 오버레이와 페이드 오버레이를 뷰포트 크기에 맞추고 페이드 상태를 갱신"""
        vp = self._view.viewport()
        if vp is not None:
            self.empty.setGeometry(vp.rect())
        for overlay, target_vp in ((self.vp_fade, vp), (self.header_fade, self._header_viewport)):
            if overlay is not None and target_vp is not None:
                overlay.setGeometry(target_vp.rect())
                overlay.raise_()
        self.sync_edge_fades()
