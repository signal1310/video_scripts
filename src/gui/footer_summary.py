"""테이블 표시, 선택 행 범위의 엑셀식 열 합계 계산 및 푸터 요약 포맷터 모듈"""

from collections.abc import Sequence
from dataclasses import dataclass

from src.core.formatting import format_duration_ko, format_size_mb
from src.core.models import MB_PER_GB, SECONDS_PER_MINUTE, VideoEvaluation


@dataclass(frozen=True, slots=True)
class FooterSummarySpec:
    """하단 푸터 요약 표시용 범위, 지표 쌍, 평문, 툴팁 명세"""

    scope_label: str
    metrics: tuple[tuple[str, str], ...]
    plain_text: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class TableRangeSummary:
    """현재 표시 행 또는 선택 행 기준의 합계 스냅샷"""

    visible_count: int
    total_count: int
    selected_count: int
    orig_mb: float
    duration_sec: float
    column_saved_mb: float
    column_encode_sec: float
    encode_count: int
    pass_count: int
    target_saved_mb: float = 0.0
    target_encode_sec: float = 0.0


def compute_table_range_summary(
    *,
    visible_evals: Sequence[VideoEvaluation],
    selected_evals: Sequence[VideoEvaluation],
    total_count: int,
) -> TableRangeSummary:
    """선택 행(우선) 또는 현재 표시 행 목록으로 열 합계와 실제 인코딩 대상 소계 계산"""
    target_evals = selected_evals if selected_evals else visible_evals
    orig_mb = 0.0
    duration_sec = 0.0
    column_saved_mb = 0.0
    column_encode_sec = 0.0
    target_saved_mb = 0.0
    target_encode_sec = 0.0
    encode_count = 0

    for ev in target_evals:
        orig_mb += ev.video.vid_size_MB
        duration_sec += max(0.0, ev.video.duration)
        column_saved_mb += ev.preview_delta_mb
        est_sec = max(0.0, ev.estimated_encode_sec)
        column_encode_sec += est_sec
        target_saved_mb += ev.expected_delta_mb
        if ev.is_encode_target:
            encode_count += 1
            target_encode_sec += est_sec

    return TableRangeSummary(
        visible_count=len(visible_evals),
        total_count=total_count,
        selected_count=len(selected_evals),
        orig_mb=orig_mb,
        duration_sec=duration_sec,
        column_saved_mb=column_saved_mb,
        column_encode_sec=column_encode_sec,
        encode_count=encode_count,
        pass_count=len(target_evals) - encode_count,
        target_saved_mb=target_saved_mb,
        target_encode_sec=target_encode_sec,
    )


def _format_summary_size(mb: float, *, signed: bool = False) -> str:
    decimals = 2 if abs(mb) >= MB_PER_GB else 1
    return format_size_mb(mb, decimals, unit="auto", signed=signed)


def build_footer_summary_spec(summary: TableRangeSummary) -> FooterSummarySpec:
    """필터, 선택 범위가 있을 때 하단 푸터 바 요약 스펙(범위, 지표 쌍, 평문, 상세 툴팁) 반환"""
    # 미분석 또는 미필터, 미선택 전체 상태는 상단 KPI 카드와 중복되므로 푸터 요약을 비움
    if summary.total_count <= 0:
        return FooterSummarySpec("", (), "", "")
    is_filtered = summary.visible_count < summary.total_count
    is_selected = summary.selected_count > 0
    if not is_filtered and not is_selected:
        return FooterSummarySpec("", (), "", "")

    prefix = (
        f"선택 {summary.selected_count:,}개"
        if is_selected
        else f"표시 {summary.visible_count:,}개"
    )
    if not is_selected and summary.visible_count == 0:
        return FooterSummarySpec(
            scope_label=prefix,
            metrics=(),
            plain_text=prefix,
            tooltip="현재 검색, 필터 조건과 일치하는 영상이 없습니다.",
        )

    orig_str = _format_summary_size(summary.orig_mb)
    dur_str = format_duration_ko(summary.duration_sec)
    saved_val = _format_summary_size(summary.column_saved_mb, signed=True)
    encode_val = format_duration_ko(summary.column_encode_sec)
    metrics: tuple[tuple[str, str], ...] = (
        ("용량", orig_str),
        ("길이", dur_str),
        ("예상 절약", saved_val),
        ("예상 시간", encode_val),
    )
    plain_text = ", ".join([prefix, *(f"{label} {value}" for label, value in metrics)])

    mins = summary.duration_sec / SECONDS_PER_MINUTE if summary.duration_sec > 0 else 0.0
    scope_desc = "선택 구간" if is_selected else "표시 구간"
    tooltip_lines = [
        f"{prefix} (인코딩 대상 {summary.encode_count:,}, 원본 보존 {summary.pass_count:,})"
    ]
    if mins > 0:
        tooltip_lines.append(
            f"• 평균 분당 절약 ({scope_desc} 재생 1분당 절약): "
            f"{summary.column_saved_mb / mins:+.1f} MB/분"
        )
    if summary.encode_count > 0 and summary.pass_count > 0:
        target_time_str = format_duration_ko(summary.target_encode_sec)
        tooltip_lines.append(
            "• 실제 인코딩 대상 기준: "
            f"예상 절약 {_format_summary_size(summary.target_saved_mb, signed=True)}, "
            f"재인코딩 예상 시간 {target_time_str}"
        )
    else:
        tooltip_lines.append("• 예상 시간: 설정된 목표 코덱, 1080p 배속 기준 예상 재인코딩 시간")
    if is_selected:
        tooltip_lines.append("• 빈 여백, 카드 클릭으로 선택을 해제할 수 있습니다.")
    return FooterSummarySpec(
        scope_label=prefix,
        metrics=metrics,
        plain_text=plain_text,
        tooltip="\n".join(tooltip_lines),
    )
