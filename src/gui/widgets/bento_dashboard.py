
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.core.models import MB_PER_GB, FootageStrategy, SummaryReport


class BentoCard(QFrame):
    """
    - 상단 대시보드의 개별 지표를 표시하는 카드 위젯.
    """

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
        self.value_label.setText(value)
        self.sub_label.setText(subtext)
        if tooltip:
            self.setToolTip(tooltip)


class BentoDashboard(QWidget):
    """
    - 핵심 지표 카드를 수평으로 배치하고 SummaryReport 데이터를 표시하는 대시보드 위젯.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QHBoxLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        self.card_files = BentoCard("전체 대상 영상", "0개", "분석 대기 중")
        self.card_orig_size = BentoCard("현재 총 용량", "0.00 GB", "0.0 MB")
        self.card_after_size = BentoCard(
            "인코딩 후 예상 총 용량", "0.00 GB", "과화질 감축 및 KF 교정 합산"
        )
        self.card_net_saved = BentoCard(
            "최종 순수 절감 예상 (Net)",
            "0.00 GB",
            "절감률 0.0%",
            highlight=True,
        )
        self.card_strategy = BentoCard(
            "소형 영상 최적화 전략", "대기 중", "분석 후 자동 판정"
        )

        for card in (
            self.card_files,
            self.card_orig_size,
            self.card_after_size,
            self.card_net_saved,
            self.card_strategy,
        ):
            grid.addWidget(card, stretch=1)

    def reset(self) -> None:
        """
        - 대시보드 5개 KPI 카드를 초기 대기 상태로 초기화.
        """
        self.card_files.update_content("0개", "분석 대기 중")
        self.card_files.setToolTip("")
        self.card_orig_size.update_content("0.00 GB", "0.0 MB")
        self.card_orig_size.setToolTip("")
        self.card_after_size.update_content(
            "0.00 GB", "과화질 감축 및 KF 교정 합산"
        )
        self.card_after_size.setToolTip("")
        self.card_net_saved.update_content("0.00 GB", "절감률 0.0%")
        self.card_net_saved.setToolTip("")
        self.card_strategy.update_content("대기 중", "분석 후 자동 판정")
        self.card_strategy.setToolTip("")

    def update_from_report(self, report: SummaryReport) -> None:
        """
        - SummaryReport 평가 결과를 바탕으로 5개 KPI 카드를 일괄 갱신.
        """
        self.card_files.update_content(
            f"총 {report.total_count:,}개",
            f"인코딩 대상 {report.encoded_count:,}개 · 원본 보존 {report.passed_count:,}개",
        )

        orig_gb = report.total_orig_mb / MB_PER_GB
        self.card_orig_size.update_content(
            f"{orig_gb:,.2f} GB",
            f"{report.total_orig_mb:,.1f} MB",
        )

        after_gb = report.total_expected_after_mb / MB_PER_GB
        self.card_after_size.update_content(
            f"{after_gb:,.2f} GB",
            f"과화질 -{report.gross_saved_mb:,.1f} MB / KF교정 +{report.keyframe_increase_mb:,.1f} MB",
        )

        net_gb = report.net_saved_mb / MB_PER_GB
        pct = (
            (report.net_saved_mb / report.total_orig_mb) * 100.0
            if report.total_orig_mb > 0
            else 0.0
        )
        self.card_net_saved.update_content(
            f"{net_gb:,.2f} GB 절약",
            f"{report.net_saved_mb:,.1f} MB 순수 절감 ({pct:.1f}%)",
        )

        dist = report.small_distribution
        strat_title = (
            "누적 최적화 (ACCUMULATE)"
            if dist.strategy == FootageStrategy.ACCUMULATE
            else "대용량 집중 (IGNORE_SMALL)"
        )
        strat_sub = (
            f"소형(<{dist.small_threshold_mb:.0f}MB) {dist.small_over_count}개 · "
            f"절감 비중 {dist.small_share_ratio * 100:.1f}% ({dist.small_potential_saved_mb:.1f}MB)"
        )
        self.card_strategy.update_content(
            strat_title,
            strat_sub,
            tooltip=dist.reason,
        )
