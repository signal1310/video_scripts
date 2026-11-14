"""테이블 표시·선택 행 범위의 엑셀식 열 합계 계산 및 푸터 요약 포맷터 모듈"""

from collections.abc import Sequence
from dataclasses import dataclass

from src.core.formatting import format_duration_ko, format_size_mb
from src.core.models import MB_PER_GB, SECONDS_PER_MINUTE, VideoEvaluation


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


def format_footer_summary_texts(summary: TableRangeSummary) -> tuple[str, str]:
    """하단 푸터 바 좌측 요약 문구와 상세 툴팁 문자열 반환"""
    if summary.total_count <= 0:
        return (
            "표시: 0 / 0개",
            "폴더를 스캔하면 현재 표시되거나 선택한 영상의 용량·길이·예상 절감·예상 소요 합계가 표시됩니다.",
        )

    prefix = f"표시: {summary.visible_count:,} / {summary.total_count:,}개"
    if summary.selected_count > 0:
        prefix += f" · 선택 {summary.selected_count:,}개"
    elif summary.visible_count == 0:
        return (
            prefix,
            "현재 검색·필터 조건과 일치하는 영상이 없습니다.",
        )

    orig_str = _format_summary_size(summary.orig_mb)
    dur_str = format_duration_ko(summary.duration_sec)
    savings_str = f"예상 절감 {_format_summary_size(summary.column_saved_mb, signed=True)}"
    encode_str = f"예상 소요 {format_duration_ko(summary.column_encode_sec)}"
    line = (
        f"{prefix}  |  용량 {orig_str}  |  길이 {dur_str}  |  "
        f"{savings_str}  |  {encode_str}"
    )

    scope_title = (
        f"선택한 {summary.selected_count:,}개 영상 합계 (표시 {summary.visible_count:,} / {summary.total_count:,}개)"
        if summary.selected_count > 0
        else f"현재 표시된 {summary.visible_count:,}개 영상 합계 (전체 {summary.total_count:,}개)"
    )
    mins = summary.duration_sec / SECONDS_PER_MINUTE if summary.duration_sec > 0 else 0.0
    rate_suffix = (
        f" ({summary.column_saved_mb / mins:+.1f} MB/분)" if mins > 0 else ""
    )
    target_time_str = format_duration_ko(summary.target_encode_sec)
    col_time_str = format_duration_ko(summary.column_encode_sec)
    tooltip_lines = [
        scope_title,
        f"• 구성: 인코딩 대상 {summary.encode_count:,}개 · 원본 보존 {summary.pass_count:,}개",
        f"• 선택/표시 열 합계: 용량 {orig_str} · 길이 {dur_str} · {savings_str}{rate_suffix} · 예상 소요 {col_time_str}",
        f"• 실제 인코딩 대상 기준: 예상 절감 {_format_summary_size(summary.target_saved_mb, signed=True)} · 예상 소요 {target_time_str}",
    ]
    if summary.selected_count > 0:
        tooltip_lines.append("• 빈 여백·카드 클릭 또는 Esc 키로 선택을 해제할 수 있습니다.")
    return line, "\n".join(tooltip_lines)
