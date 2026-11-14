"""상단 KPI 요약 카드와 대시보드 레이아웃 위젯 모듈"""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.core.formatting import format_duration_ko
from src.core.models import MB_PER_GB, SummaryReport


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
        grid = QHBoxLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        self.card_files = BentoCard("전체 대상 영상")
        self.card_net_saved = BentoCard("예상 용량 절감", highlight=True)
        self.card_workload = BentoCard("인코딩 작업 분량")
        self.card_skipped = BentoCard("제외된 과화질")

        for card in (
            self.card_files,
            self.card_net_saved,
            self.card_workload,
            self.card_skipped,
        ):
            grid.addWidget(card, stretch=1)
        self.reset()

    def reset(self) -> None:
        """대시보드 4개 KPI 카드를 초기 대기 상태로 초기화"""
        self.card_files.update_content("0개", "분석 대기 중")
        self.card_files.setToolTip("")
        self.card_net_saved.update_content("0.00 GB 절약", "0.00 → 0.00 GB (0.0%)")
        self.card_net_saved.setToolTip("")
        self.card_workload.update_content("0분", "분석 대기 중")
        self.card_workload.setToolTip("")
        self.card_skipped.update_content("0분 단축", "미반영 +0.0 MB · 미미 0 · 비효율 0")
        self.card_skipped.setToolTip("")

    def update_from_report(self, report: SummaryReport) -> None:
        """SummaryReport 평가 결과를 바탕으로 4개 KPI 카드를 일괄 갱신"""
        self.card_files.update_content(
            f"총 {report.total_count:,}개",
            f"인코딩 {report.encoded_count:,}개 · 보존 {report.passed_count:,}개",
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
                f"과화질 절감: -{report.gross_saved_mb:,.1f} MB / "
                f"키프레임 교정: +{report.keyframe_increase_mb:,.1f} MB\n"
                f"순수 절감(Net): {report.net_saved_mb:,.1f} MB ({report.net_saved_pct:.1f}%)"
            ),
        )

        eff = report.efficiency_summary
        workload_dur = format_duration_ko(eff.encode_duration_sec)
        exempt_note = (
            f"\n대용량 절감 예외: {eff.exempt_saved_mb:.0f} MB 이상"
            if eff.exempt_saved_mb > 0
            else "\n대용량 절감 예외: 사용 안 함"
        )
        self.card_workload.update_content(
            workload_dur,
            f"평균 효율 {report.encode_avg_mb_per_min:.1f} MB/분 · {report.encoded_count:,}개",
            tooltip=(
                f"인코딩 대상 영상 총 재생 시간: {workload_dur}\n"
                f"분당 평균 절감 효율: {report.encode_avg_mb_per_min:.1f} MB/분\n"
                f"적용 기준: 총 {eff.min_saved_mb:.1f} MB · "
                f"분당 {eff.min_mb_per_min:.1f} MB/분 이상{exempt_note}"
            ),
        )

        skipped_dur = format_duration_ko(eff.skipped_over_duration_sec)
        self.card_skipped.update_content(
            f"{skipped_dur} 단축",
            (
                f"미반영 +{eff.skipped_over_potential_saved_mb:,.1f} MB · "
                f"미미 {eff.low_savings_count:,} · 비효율 {eff.low_efficiency_count:,}"
            ),
            tooltip=(
                f"실익·효율 기준 미달로 보존 전환한 과화질 {eff.skipped_over_count:,}개\n"
                f"단축한 인코딩 대상 분량: {skipped_dur}\n"
                f"미반영 예상 절감량: {eff.skipped_over_potential_saved_mb:,.1f} MB\n"
                f"절감 미미: {eff.low_savings_count:,}개 (< {eff.min_saved_mb:.1f} MB)\n"
                f"시간 비효율: {eff.low_efficiency_count:,}개 (< {eff.min_mb_per_min:.1f} MB/분)"
            ),
        )
