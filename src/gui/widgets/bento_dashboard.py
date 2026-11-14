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
        self.card_net_saved = BentoCard("예상 순 용량 절감", highlight=True)
        self.card_workload = BentoCard("인코딩 작업 분량 (재생 길이)")
        self.card_skipped = BentoCard("제외된 과화질 (가성비 절약)")

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
        self.card_files.update_content(
            "0개",
            "폴더 스캔 대기 중",
            tooltip=(
                "스캔한 영상 중 인코딩 대상(이동)과 원본 유지(보존) 파일 수입니다.\n"
                "• 과화질: 최적 비트레이트보다 용량이 과도해 화질 손실 없이 줄일 수 있는 영상\n"
                "• 키프레임 교정: 탐색이 끊기지 않도록 키프레임 간격을 1초로 교정하는 영상"
            ),
        )
        self.card_net_saved.update_content(
            "0.00 GB 절약",
            "과화질 절감 − 키프레임 교정 증가",
            tooltip=(
                "과화질 압축 절감량(-)에서 키프레임 교정 증가분(+)을 뺀 순수 절감량입니다.\n"
                "• 과화질(-MB): CQ22 표준 인코딩으로 과도한 비트레이트를 낮춥니다.\n"
                "• 키프레임(+MB): 비트레이트는 낮지만 탐색이 느린 영상은 키프레임 교정으로 용량이 소폭 늘 수 있습니다."
            ),
        )
        self.card_workload.update_content(
            "0분",
            "실제 처리 길이 집계 대기",
            tooltip=(
                "파일 용량(MB)이 아니라, 실제로 재인코딩할 영상들의 총 재생 길이(작업량)입니다.\n"
                "인코딩 소요 시간과 재생 1분당 절감 효율(MB/분)을 가늠할 때 참고하세요."
            ),
        )
        self.card_skipped.update_content(
            "0분 단축",
            "미반영 +0.0 MB · 미미 0 · 비효율 0",
            tooltip=(
                "과화질이지만 실제 절감 이득이 너무 작아 인코딩에서 자동으로 뺀 파일입니다.\n"
                "• 미미: 예상 절감량이 '최소 총 절감량' 미만인 파일\n"
                "• 비효율: '분당 최소 절감량' 미만이라 시간 대비 효율이 낮은 파일\n"
                "• 상단 값(N분 단축)은 이 파일들을 건너뛰어 아낀 작업 길이입니다."
            ),
        )

    def update_from_report(self, report: SummaryReport) -> None:
        """SummaryReport 평가 결과를 바탕으로 4개 KPI 카드를 일괄 갱신"""
        self.card_files.update_content(
            f"총 {report.total_count:,}개",
            f"인코딩 {report.encoded_count:,}개 · 보존 {report.passed_count:,}개",
            tooltip=(
                f"전체 스캔 영상 {report.total_count:,}개 중 분류 대상 요약입니다.\n"
                f"• 인코딩 대상: {report.encoded_count:,}개 (과화질 압축 또는 키프레임 교정)\n"
                f"• 원본 보존: {report.passed_count:,}개 (적정 화질 · 가성비 미달 · 예외 규칙 포함)"
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
                f"과화질 절감: -{report.gross_saved_mb:,.1f} MB / "
                f"키프레임 교정 증가: +{report.keyframe_increase_mb:,.1f} MB\n"
                f"순수 절감(Net): {report.net_saved_mb:,.1f} MB ({report.net_saved_pct:.1f}%)\n"
                "※ 과화질은 CQ22 인코딩으로 용량을 줄이고(-), 키프레임 교정은 탐색성을 위해 소폭 증가(+)할 수 있습니다."
            ),
        )

        eff = report.efficiency_summary
        workload_dur = format_duration_ko(eff.encode_duration_sec)
        est_encode_str = format_duration_ko(report.total_estimated_encode_sec)
        exempt_note = (
            f"\n대용량 절감 예외: {eff.exempt_saved_mb:.0f} MB 이상"
            if eff.exempt_saved_mb > 0
            else "\n대용량 절감 예외: 사용 안 함"
        )
        workload_sub = (
            f"예상 소요 {est_encode_str} · {report.encode_avg_mb_per_min:.1f} MB/분"
            if report.total_estimated_encode_sec > 0
            else f"평균 효율 {report.encode_avg_mb_per_min:.1f} MB/분 · {report.encoded_count:,}개"
        )
        self.card_workload.update_content(
            workload_dur,
            workload_sub,
            tooltip=(
                f"실제로 재인코딩할 영상 {report.encoded_count:,}개의 총 재생 길이(작업량): {workload_dur}\n"
                f"설정된 1080p 기준 배속 반영 예상 소요 시간: {est_encode_str}\n"
                f"재생 1분당 평균 절감 효율: {report.encode_avg_mb_per_min:.1f} MB/분\n"
                f"적용 효율 기준: 총 {eff.min_saved_mb:.1f} MB · "
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
                f"실익·시간 효율 미달로 인코딩에서 뺀 과화질 {eff.skipped_over_count:,}개입니다.\n"
                f"• 절약한 작업 길이: {skipped_dur} / 미반영 예상 절감량: {eff.skipped_over_potential_saved_mb:,.1f} MB\n"
                f"• 절감 미미: {eff.low_savings_count:,}개 (< {eff.min_saved_mb:.1f} MB)\n"
                f"• 시간 비효율: {eff.low_efficiency_count:,}개 (< {eff.min_mb_per_min:.1f} MB/분)"
            ),
        )
