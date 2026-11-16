"""테이블 표시 행, 선택 행 범위의 엑셀식 열 합계 계산 및 푸터 요약 포맷터 모듈"""

from collections.abc import Sequence
from dataclasses import dataclass

from src.core.formatting import format_duration_ko, format_size_mb
from src.core.models import MB_PER_GB, SECONDS_PER_MINUTE, VideoEvaluation

_SCOPE_VISIBLE: str = "표시"
_SCOPE_SELECTED: str = "선택"
_SCOPE_SEPARATOR: str = " / "
_TOOLTIP_CLEAR_SELECTION_LINE: str = "• 빈 여백, 카드 클릭으로 선택을 해제할 수 있습니다."
_TOOLTIP_EMPTY_VISIBLE: str = "현재 검색, 필터 조건과 일치하는 영상이 없습니다."


@dataclass(frozen=True, slots=True)
class FooterSummaryGroup:
    """한 범위(표시 행 또는 선택 행)의 라벨과 지표 쌍"""

    scope_label: str
    metrics: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FooterSummarySpec:
    """하단 푸터 요약 표시용 범위 그룹, 평문, 툴팁 명세

    - 필터와 선택이 함께 있으면 표시 행과 선택 행 그룹을 나란히 표시
    """

    groups: tuple[FooterSummaryGroup, ...]
    plain_text: str
    tooltip: str


EMPTY_FOOTER_SUMMARY: FooterSummarySpec = FooterSummarySpec((), "", "")


@dataclass(frozen=True, slots=True)
class TableRangeSummary:
    """한 범위의 행 수와 열 합계 스냅샷"""

    row_count: int
    orig_mb: float
    duration_sec: float
    column_saved_mb: float
    column_encode_sec: float
    encode_count: int
    pass_count: int
    target_saved_mb: float
    target_encode_sec: float


def summarize_rows(evals: Sequence[VideoEvaluation]) -> TableRangeSummary:
    """행 목록의 열 합계와 실제 인코딩 대상 소계 계산"""
    orig_mb = 0.0
    duration_sec = 0.0
    column_saved_mb = 0.0
    column_encode_sec = 0.0
    target_saved_mb = 0.0
    target_encode_sec = 0.0
    encode_count = 0

    for ev in evals:
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
        row_count=len(evals),
        orig_mb=orig_mb,
        duration_sec=duration_sec,
        column_saved_mb=column_saved_mb,
        column_encode_sec=column_encode_sec,
        encode_count=encode_count,
        pass_count=len(evals) - encode_count,
        target_saved_mb=target_saved_mb,
        target_encode_sec=target_encode_sec,
    )


def _format_summary_size(mb: float, *, signed: bool = False) -> str:
    decimals = 2 if abs(mb) >= MB_PER_GB else 1
    return format_size_mb(mb, decimals, unit="auto", signed=signed)


def _scope_desc(scope: str) -> str:
    return "선택 행" if scope == _SCOPE_SELECTED else "표시 행"


def _build_group(scope: str, summary: TableRangeSummary) -> FooterSummaryGroup:
    prefix = f"{scope} {summary.row_count:,}개"
    metrics: tuple[tuple[str, str], ...] = (
        ("용량", _format_summary_size(summary.orig_mb)),
        ("길이", format_duration_ko(summary.duration_sec)),
        ("예상 절약", _format_summary_size(summary.column_saved_mb, signed=True)),
        ("예상 시간", format_duration_ko(summary.column_encode_sec)),
    )
    return FooterSummaryGroup(scope_label=prefix, metrics=metrics)


def _group_tooltip_lines(scope: str, summary: TableRangeSummary) -> list[str]:
    prefix = f"{scope} {summary.row_count:,}개"
    lines = [
        f"{prefix} (인코딩 대상 {summary.encode_count:,}, 원본 보존 {summary.pass_count:,})"
    ]
    mins = summary.duration_sec / SECONDS_PER_MINUTE if summary.duration_sec > 0 else 0.0
    if mins > 0:
        lines.append(
            f"• 평균 분당 절약 ({_scope_desc(scope)} 재생 1분당 절약): "
            f"{summary.column_saved_mb / mins:+.1f} MB/분"
        )
    if summary.encode_count > 0 and summary.pass_count > 0:
        target_time_str = format_duration_ko(summary.target_encode_sec)
        lines.append(
            "• 실제 인코딩 대상 기준: "
            f"예상 절약 {_format_summary_size(summary.target_saved_mb, signed=True)}, "
            f"재인코딩 예상 시간 {target_time_str}"
        )
    else:
        lines.append("• 예상 시간: 설정된 목표 코덱, 1080p 배속 기준 예상 재인코딩 시간")
    return lines


def build_footer_summary_spec(
    *,
    total_count: int,
    visible: TableRangeSummary,
    selected: TableRangeSummary | None,
) -> FooterSummarySpec:
    """필터 또는 선택이 있을 때 표시 행과 선택 행 그룹, 평문, 상세 툴팁 명세 반환"""
    # 미분석 또는 미필터, 미선택 전체 상태는 상단 핵심 지표 카드와 중복되므로 푸터 요약을 비움
    if total_count <= 0:
        return EMPTY_FOOTER_SUMMARY
    is_filtered = visible.row_count < total_count
    is_selected = selected is not None and selected.row_count > 0
    if not is_filtered and not is_selected:
        return EMPTY_FOOTER_SUMMARY

    if not is_selected and visible.row_count == 0:
        prefix = f"{_SCOPE_VISIBLE} {visible.row_count:,}개"
        return FooterSummarySpec(
            groups=(FooterSummaryGroup(scope_label=prefix, metrics=()),),
            plain_text=prefix,
            tooltip=_TOOLTIP_EMPTY_VISIBLE,
        )

    scopes: list[tuple[str, TableRangeSummary]] = []
    if is_filtered:
        scopes.append((_SCOPE_VISIBLE, visible))
    if is_selected and selected is not None:
        scopes.append((_SCOPE_SELECTED, selected))

    groups = tuple(_build_group(scope, summary) for scope, summary in scopes)
    plain_text = _SCOPE_SEPARATOR.join(
        ", ".join([group.scope_label, *(f"{label} {value}" for label, value in group.metrics)])
        for group in groups
    )
    tooltip_lines: list[str] = []
    for scope, summary in scopes:
        tooltip_lines.extend(_group_tooltip_lines(scope, summary))
    if is_selected:
        tooltip_lines.append(_TOOLTIP_CLEAR_SELECTION_LINE)
    return FooterSummarySpec(
        groups=groups,
        plain_text=plain_text,
        tooltip="\n".join(tooltip_lines),
    )
