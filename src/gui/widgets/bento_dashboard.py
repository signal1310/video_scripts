"""상단 KPI 요약 카드와 대시보드 레이아웃 위젯 모듈"""

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.formatting import format_duration_ko
from src.core.models import MB_PER_GB, SummaryReport
from src.gui.widgets.edge_fade_overlay import (
    HorizontalEdgeFadeOverlay,
    sync_edge_overlay_from_scrollbar,
)

_DASHBOARD_MIN_WIDTH_PX = 240
_CARD_SPACING_PX = 10
_WHEEL_ANGLE_DIVISOR = 120
_WHEEL_STEP_PX = 48


class BentoCard(QFrame):
    """상단 대시보드의 개별 지표를 표시하는 카드 위젯"""

    def __init__(
        self,
        title: str,
        initial_value: str = "-",
        initial_sub: str = "",
        highlight: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BentoCardHighlight" if highlight else "BentoCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")

        self.value_label = QLabel(initial_value)
        self.value_label.setObjectName(
            "CardValueHighlight" if highlight else "CardValue"
        )

        self.sub_label = QLabel(initial_sub)
        self.sub_label.setObjectName("CardSubtext")
        self.sub_label.setWordWrap(False)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)

    def update_content(
        self, value: str, subtext: str = "", tooltip: str = ""
    ) -> None:
        """카드의 주 수치, 보조 설명 및 툴팁 갱신"""
        self.value_label.setText(value)
        self.sub_label.setText(subtext)
        if tooltip:
            self.setToolTip(tooltip)


class BentoDashboard(QWidget):
    """핵심 지표 카드를 수평으로 배치하고 SummaryReport 데이터를 표시하는 대시보드 위젯"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget()
        grid = QHBoxLayout(self._content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_CARD_SPACING_PX)

        self.card_files = BentoCard("전체 대상 영상")
        self.card_net_saved = BentoCard("예상 순 용량 절약", highlight=True)
        self.card_workload = BentoCard("인코딩 작업 분량 (재생 길이)")
        self.card_skipped = BentoCard("제외된 과화질")

        for card in (
            self.card_files,
            self.card_net_saved,
            self.card_workload,
            self.card_skipped,
        ):
            grid.addWidget(card, stretch=1)

        self._scroll_area.setWidget(self._content)
        outer.addWidget(self._scroll_area)

        viewport = self._scroll_area.viewport()
        self._fade_overlay = (
            HorizontalEdgeFadeOverlay(viewport, lambda p: p.bg_base)
            if viewport is not None
            else None
        )
        self._drag_origin_x: int | None = None
        self._drag_scroll_start = 0
        self._dragging = False

        if viewport is not None:
            viewport.installEventFilter(self)
        h_bar = self._scroll_area.horizontalScrollBar()
        if h_bar is not None:
            h_bar.valueChanged.connect(self._sync_fade_overlay)
            h_bar.rangeChanged.connect(self._sync_fade_overlay)

        self.reset()
        self._sync_fade_overlay()

    def minimumSizeHint(self) -> QSize:
        """창 폭 축소 시 대시보드가 가로 최소 크기를 가로막지 않도록 축소된 최소 폭 반환"""
        return QSize(_DASHBOARD_MIN_WIDTH_PX, self._content.minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        """4개 카드가 모두 보이는 기본 권장 크기 반환"""
        return self._content.sizeHint()

    def _sync_fade_overlay(self) -> None:
        sync_edge_overlay_from_scrollbar(
            self._fade_overlay, self._scroll_area.horizontalScrollBar()
        )

    def _handle_wheel_scroll(self, event: QWheelEvent) -> bool:
        h_bar = self._scroll_area.horizontalScrollBar()
        if h_bar is None or h_bar.maximum() <= h_bar.minimum():
            return False
        px_delta = event.pixelDelta().x()
        ang_delta = event.angleDelta().x() or event.angleDelta().y()
        if px_delta != 0:
            step = -px_delta
        elif ang_delta != 0:
            step = round(-ang_delta * _WHEEL_STEP_PX / _WHEEL_ANGLE_DIVISOR)
        else:
            return False
        if step == 0:
            return False
        h_bar.setValue(h_bar.value() + step)
        event.accept()
        return True

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """뷰포트 크기 동기화, 휠 가로 스크롤, 드래그 패닝 처리"""
        viewport = self._scroll_area.viewport()
        if a0 is not viewport or a1 is None or viewport is None:
            return super().eventFilter(a0, a1)

        ev_type = a1.type()
        if ev_type in (QEvent.Type.Resize, QEvent.Type.Show):
            if self._fade_overlay is not None:
                self._fade_overlay.setGeometry(viewport.rect())
                self._fade_overlay.raise_()
            self._sync_fade_overlay()
            return False

        if ev_type == QEvent.Type.Wheel and isinstance(a1, QWheelEvent):
            return self._handle_wheel_scroll(a1)

        h_bar = self._scroll_area.horizontalScrollBar()
        if h_bar is None:
            return False

        if ev_type == QEvent.Type.MouseButtonPress and isinstance(a1, QMouseEvent):
            if a1.button() == Qt.MouseButton.LeftButton and h_bar.maximum() > h_bar.minimum():
                self._drag_origin_x = int(a1.position().x())
                self._drag_scroll_start = h_bar.value()
                self._dragging = False
            return False

        if ev_type == QEvent.Type.MouseMove and isinstance(a1, QMouseEvent):
            if self._drag_origin_x is not None:
                dx = int(a1.position().x()) - self._drag_origin_x
                if not self._dragging and abs(dx) >= QApplication.startDragDistance():
                    self._dragging = True
                    viewport.setCursor(Qt.CursorShape.ClosedHandCursor)
                if self._dragging:
                    h_bar.setValue(self._drag_scroll_start - dx)
                    a1.accept()
                    return True
            return False

        if ev_type == QEvent.Type.MouseButtonRelease and isinstance(a1, QMouseEvent):
            if self._drag_origin_x is not None:
                was_dragging = self._dragging
                self._drag_origin_x = None
                self._dragging = False
                viewport.unsetCursor()
                if was_dragging:
                    a1.accept()
                    return True
            return False

        return super().eventFilter(a0, a1)

    def reset(self) -> None:
        """대시보드 4개 KPI 카드를 초기 대기 상태로 초기화"""
        self.card_files.update_content(
            "0개",
            "폴더 분석 대기 중",
            tooltip=(
                "분석한 영상 중 재인코딩 대상(이동)과 원본 보존 파일 수입니다.\n"
                "• 과화질: 최적 비트레이트보다 용량이 과도해 화질 손실 없이 줄일 수 있는 영상\n"
                "• 키프레임 재인코딩: 구간 이동이 끊기지 않도록 키프레임 간격을 1초로 재인코딩하는 영상"
            ),
        )
        self.card_net_saved.update_content(
            "0.00 GB 절약",
            "과화질 절약 − 키프레임 재인코딩 증가",
            tooltip=(
                "과화질 재인코딩 절약(+)에서 키프레임 재인코딩 증가분(-)을 뺀 순 절약량입니다.\n"
                "• 과화질(-MB): CQ22 표준 인코딩으로 과도한 비트레이트를 낮춥니다.\n"
                "• 키프레임 재인코딩 증가(-MB): 비트레이트는 낮지만 구간 이동이 느린 영상은 키프레임 재인코딩으로 용량이 소폭 늘 수 있습니다."
            ),
        )
        self.card_workload.update_content(
            "0분",
            "실제 처리 길이 집계 대기",
            tooltip=(
                "파일 용량(MB)이 아니라, 실제로 재인코딩할 영상들의 총 재생 길이(작업량)입니다.\n"
                "재인코딩 예상 시간과 분당 절약(MB/분)을 가늠할 때 참고하세요."
            ),
        )
        self.card_skipped.update_content(
            "0분 절약",
            "제외분 절약 가능 +0.0 MB, 절약 미달 0, 분당 미달 0",
            tooltip=(
                "과화질이지만 절약 효과가 작아 재인코딩에서 자동으로 제외한 파일입니다.\n"
                "• 절약 미달: 예상 절약량이 '최소 총 절약량' 미만인 파일\n"
                "• 분당 미달: '분당 최소 절약량' 미만이라 재생 시간 대비 절약이 적은 파일\n"
                "• 상단 값(N분 절약)은 이 파일들을 건너뛰어 아낀 작업 길이입니다."
            ),
        )

    def update_from_report(self, report: SummaryReport) -> None:
        """SummaryReport 평가 결과를 바탕으로 4개 KPI 카드를 일괄 갱신"""
        self.card_files.update_content(
            f"총 {report.total_count:,}개",
            f"인코딩 {report.encoded_count:,}개, 보존 {report.passed_count:,}개",
            tooltip=(
                f"전체 분석 영상 {report.total_count:,}개 중 분류 대상 요약입니다.\n"
                f"• 재인코딩 대상: {report.encoded_count:,}개 (과화질 재인코딩 또는 키프레임 재인코딩)\n"
                f"• 원본 보존: {report.passed_count:,}개 (적정, 분당 미달, 예외 규칙 포함)"
            ),
        )

        orig_gb = report.total_orig_mb / MB_PER_GB
        after_gb = report.total_expected_after_mb / MB_PER_GB
        net_gb = report.net_saved_mb / MB_PER_GB
        self.card_net_saved.update_content(
            f"{net_gb:,.2f} GB 절약 ({report.net_saved_pct:.1f}%)",
            f"{orig_gb:,.2f} → {after_gb:,.2f} GB",
            tooltip=(
                f"현재 총 용량: {report.total_orig_mb:,.1f} MB ({orig_gb:,.2f} GB)\n"
                f"인코딩 후 예상: {report.total_expected_after_mb:,.1f} MB ({after_gb:,.2f} GB)\n"
                f"과화질 재인코딩 절약: +{report.gross_saved_mb:,.1f} MB / "
                f"키프레임 재인코딩 증가: -{report.keyframe_increase_mb:,.1f} MB\n"
                f"순 절약: {report.net_saved_mb:,.1f} MB ({report.net_saved_pct:.1f}%)\n"
                "※ 과화질은 CQ22 재인코딩으로 용량을 줄이고(+), 키프레임 재인코딩은 구간 이동 성능을 위해 소폭 증가(-)할 수 있습니다."
            ),
        )

        eff = report.efficiency_summary
        workload_dur = format_duration_ko(eff.encode_duration_sec)
        est_encode_str = format_duration_ko(report.total_estimated_encode_sec)
        exempt_note = (
            f"\n대용량 절약 예외: {eff.exempt_saved_mb:.0f} MB 이상"
            if eff.exempt_saved_mb > 0
            else "\n대용량 절약 예외: 사용 안 함"
        )
        workload_sub = (
            f"재인코딩 예상 시간 {est_encode_str}, {report.encode_avg_mb_per_min:.1f} MB/분"
            if report.total_estimated_encode_sec > 0
            else f"평균 분당 절약 {report.encode_avg_mb_per_min:.1f} MB/분, {report.encoded_count:,}개"
        )
        self.card_workload.update_content(
            workload_dur,
            workload_sub,
            tooltip=(
                f"실제로 재인코딩할 영상 {report.encoded_count:,}개의 총 재생 길이(작업량): {workload_dur}\n"
                f"• 예상 재인코딩 시간: 약 {est_encode_str} "
                "(설정된 목표 코덱, 1080p 기준 배속 및 해상도, FPS 작업량 배수 반영, 분석 시간이 아님)\n"
                f"• 평균 분당 절약: {report.encode_avg_mb_per_min:.1f} MB/분 "
                "(재생 길이 1분당 순 용량 절약량)\n"
                f"• 적용 기준: 총 {eff.min_saved_mb:.1f} MB, "
                f"분당 {eff.min_mb_per_min:.1f} MB/분 이상{exempt_note}"
            ),
        )

        skipped_dur = format_duration_ko(eff.skipped_over_duration_sec)
        self.card_skipped.update_content(
            f"{skipped_dur} 절약",
            (
                f"제외분 절약 가능 +{eff.skipped_over_potential_saved_mb:,.1f} MB, "
                f"절약 미달 {eff.low_savings_count:,}, 분당 미달 {eff.low_efficiency_count:,}"
            ),
            tooltip=(
                f"절약, 분당 절약 미달로 재인코딩에서 제외한 과화질 {eff.skipped_over_count:,}개입니다.\n"
                f"• 절약한 작업 길이: {skipped_dur} / 제외분 절약 가능: {eff.skipped_over_potential_saved_mb:,.1f} MB\n"
                f"• 절약 미달: {eff.low_savings_count:,}개 (< {eff.min_saved_mb:.1f} MB)\n"
                f"• 분당 미달: {eff.low_efficiency_count:,}개 (< {eff.min_mb_per_min:.1f} MB/분)"
            ),
        )
