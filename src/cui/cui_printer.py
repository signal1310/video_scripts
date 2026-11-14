"""콘솔 및 Jupyter 노트북용 표 포맷터와 요약 리포트 출력 모듈"""

import importlib
import io
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

from tabulate import tabulate

from src.core.config import DEFAULT_DISPLAY, ClassifierConfig, DisplayConfig
from src.core.formatting import (
    format_duration_ko,
    format_duration_seconds,
    format_fps,
    format_keyframe_interval,
    format_ratio,
    format_size_header,
    format_size_mb,
)
from src.core.messages import COLUMN_LABELS, LABEL_ORIGINAL_PRESERVED, ColumnId
from src.core.models import MB_PER_GB, ClassifyStatus, SummaryReport
from src.cui.sorting import SortTarget, build_sort_key

SortKey = Callable[[dict[str, Any]], tuple[Any, ...] | list[Any]] | None
REPORT_SEPARATOR_WIDTH: int = 78

_CUI_HDR_NAME: str = COLUMN_LABELS[ColumnId.NAME].compact_header
_CUI_HDR_WIDTH: str = COLUMN_LABELS[ColumnId.WIDTH].compact_header
_CUI_HDR_HEIGHT: str = COLUMN_LABELS[ColumnId.HEIGHT].compact_header
_CUI_HDR_ROTATE: str = COLUMN_LABELS[ColumnId.ROTATE].compact_header
_CUI_HDR_CODEC: str = COLUMN_LABELS[ColumnId.CODEC].compact_header
_CUI_HDR_FPS: str = COLUMN_LABELS[ColumnId.FPS].compact_header
_CUI_HDR_DURATION: str = COLUMN_LABELS[ColumnId.DURATION].compact_header
_CUI_HDR_BITRATE: str = COLUMN_LABELS[ColumnId.BITRATE].compact_header
_CUI_HDR_OPT_BITRATE: str = COLUMN_LABELS[ColumnId.OPT_BITRATE].compact_header
_CUI_HDR_RATIO: str = COLUMN_LABELS[ColumnId.RATIO].compact_header
_CUI_HDR_ZONE: str = COLUMN_LABELS[ColumnId.ZONE].compact_header
_CUI_HDR_KEYFRAME: str = COLUMN_LABELS[ColumnId.KEYFRAME].compact_header
_CUI_HDR_SIZE: str = COLUMN_LABELS[ColumnId.SIZE].compact_header
_CUI_HDR_AFTER_SIZE: str = COLUMN_LABELS[ColumnId.AFTER_SIZE].compact_header
_CUI_HDR_SAVED_SIZE: str = COLUMN_LABELS[ColumnId.SAVED_SIZE].compact_header
_CUI_HDR_ACTION: str = COLUMN_LABELS[ColumnId.ACTION].compact_header
_CUI_HDR_REASON: str = COLUMN_LABELS[ColumnId.REASON].compact_header

_CUI_SEP_LEFT_KEY: str = "_sep_1"
_CUI_SEP_RIGHT_KEY: str = "_sep_2"
_CUI_SEP_KEYS: frozenset[str] = frozenset({_CUI_SEP_LEFT_KEY, _CUI_SEP_RIGHT_KEY})
_CUI_SEP_HEADER: str = "\n│"

_CENTER_ALIGN_HEADERS: frozenset[str] = frozenset(
    {
        _CUI_HDR_ROTATE,
        _CUI_HDR_CODEC,
        _CUI_HDR_ZONE,
        _CUI_HDR_ACTION,
        _CUI_HDR_REASON,
        _CUI_SEP_HEADER,
        *_CUI_SEP_KEYS,
    }
)


_NUMERIC_CELL_FORMATTERS: dict[str, Callable[[float, DisplayConfig], str]] = {
    _CUI_HDR_FPS: format_fps,
    _CUI_HDR_DURATION: format_duration_seconds,
    _CUI_HDR_RATIO: format_ratio,
    _CUI_HDR_SIZE: lambda val, d: format_size_mb(val, d.size_decimals, unit=d.size_unit),
    _CUI_HDR_AFTER_SIZE: lambda val, d: format_size_mb(
        val, d.after_size_decimals, unit=d.size_unit
    ),
    _CUI_HDR_SAVED_SIZE: lambda val, d: format_size_mb(
        val, d.saved_size_decimals, unit=d.size_unit, signed=True
    ),
}

_SIZE_HEADER_TITLES: dict[str, str] = {
    _CUI_HDR_SIZE: COLUMN_LABELS[ColumnId.SIZE].short,
    _CUI_HDR_AFTER_SIZE: COLUMN_LABELS[ColumnId.AFTER_SIZE].short,
    _CUI_HDR_SAVED_SIZE: COLUMN_LABELS[ColumnId.SAVED_SIZE].short,
}

_ELLIPSIS: str = "..."
_ELLIPSIS_LEN: int = len(_ELLIPSIS)
_CUI_HEADER_LEGEND: str = "* 열 약어: BR = 비트레이트(kbps), KF = 키프레임 간격(초)"


def _format_cell_value(header: str, raw: Any, display: DisplayConfig) -> Any:
    if header == _CUI_HDR_KEYFRAME:
        kf_val = float(raw) if isinstance(raw, (int, float)) else None
        return format_keyframe_interval(kf_val, display)
    formatter = _NUMERIC_CELL_FORMATTERS.get(header)
    if formatter is not None and isinstance(raw, (int, float)):
        return formatter(float(raw), display)
    return raw


def _resolve_display_header(header: str, size_unit: str) -> str:
    if header in _CUI_SEP_KEYS:
        return _CUI_SEP_HEADER
    base_title = _SIZE_HEADER_TITLES.get(header)
    if base_title is not None:
        return format_size_header(base_title, size_unit, multiline=True)
    return header


def _resolve_col_align(header: str) -> str:
    if header == _CUI_HDR_NAME:
        return "left"
    if header in _CENTER_ALIGN_HEADERS:
        return "center"
    return "right"


def sanitize_text(text: str) -> str:
    """콘솔 열 정렬을 깨뜨리는 특수문자·이모지를 '?'로 치환

    ASCII 문자와 한글·한자·일본어 등 동아시아 전각 문자는 보존
    """
    if not text or text.isascii():
        return text

    clean_chars: list[str] = [
        ch
        if ch.isascii()
        or (
            unicodedata.category(ch).startswith("L")
            and unicodedata.east_asian_width(ch) == "W"
        )
        else "?"
        for ch in text
    ]
    return "".join(clean_chars)


def _char_display_width(ch: str) -> int:
    if ch.isascii():
        return 1
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1


def truncate_text(text: str, max_len: int) -> str:
    """동아시아 전각 문자의 2칸 너비를 계산에 반영하여 지정한 최대 표시 너비로 문자열을 자르고 말줄임표(...)를 붙임"""
    if not text or max_len <= 0:
        return ""
    if text.isascii():
        if len(text) <= max_len:
            return text
        if max_len <= _ELLIPSIS_LEN:
            return _ELLIPSIS[:max_len]
        return text[: max_len - _ELLIPSIS_LEN] + _ELLIPSIS

    total_len = 0
    cut_for_ellipsis = 0
    budget = max(0, max_len - _ELLIPSIS_LEN)
    for idx, ch in enumerate(text):
        ch_len = _char_display_width(ch)
        if total_len + ch_len > max_len:
            if max_len <= _ELLIPSIS_LEN:
                return _ELLIPSIS[:max_len]
            return text[:cut_for_ellipsis] + _ELLIPSIS
        total_len += ch_len
        if total_len <= budget:
            cut_for_ellipsis = idx + 1
    return text


def _resolve_notebook_display() -> Callable[..., Any] | None:
    """Jupyter/IPython 커널 환경이면 display 함수를 반환하고 일반 콘솔이면 None 반환"""
    ipython = sys.modules.get("IPython")
    if ipython is None:
        return None
    get_ipython_fn: Callable[[], Any] | None = getattr(ipython, "get_ipython", None)
    shell = get_ipython_fn() if callable(get_ipython_fn) else None
    if shell is None or not hasattr(shell, "kernel"):
        return None
    try:
        display_mod = importlib.import_module("IPython.display")
    except ImportError:
        return None
    display_fn: Callable[..., Any] | None = getattr(display_mod, "display", None)
    return display_fn if callable(display_fn) else None


def _capture_stdout(action: Callable[[], None]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        action()
    return buf.getvalue().strip("\n")


class TablePrinter:
    """딕셔너리 리스트 형태의 테이블 데이터를 정렬하고 서식을 맞춰 콘솔에 정갈하게 출력하는 유틸리티 클래스"""

    @staticmethod
    def print(
        table: list[dict[str, Any]],
        sort_key: SortKey,
        filename_maxlen: int,
        sanitize_emoji: bool,
        *,
        display: DisplayConfig | None = None,
        limit: int | None = None,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
    ) -> None:
        """지정 정렬 키·행 상한 및 열별 DisplayConfig 정밀도 규칙을 적용해 콘솔 표 출력"""
        if not table:
            return

        effective_display = display or DEFAULT_DISPLAY
        effective_sort_key = (
            sort_key if sort_key is not None else build_sort_key(sort_by, descending)
        )
        sorted_table = sorted(table, key=effective_sort_key)

        total_rows = len(sorted_table)
        shown_rows = (
            sorted_table[:limit]
            if limit is not None and limit >= 0
            else sorted_table
        )
        omitted_count = total_rows - len(shown_rows)
        if not shown_rows:
            print(f"... 전체 {total_rows}개 생략 (limit={limit})")
            return

        data_to_print: list[dict[str, Any]] = []
        for row in shown_rows:
            formatted_row: dict[str, Any] = {}
            for hdr, val in row.items():
                if hdr == _CUI_HDR_NAME:
                    name_val = str(val)
                    if sanitize_emoji:
                        name_val = sanitize_text(name_val)
                    formatted_row[hdr] = truncate_text(name_val, filename_maxlen)
                else:
                    formatted_row[hdr] = _format_cell_value(hdr, val, effective_display)
            data_to_print.append(formatted_row)

        raw_headers = tuple(data_to_print[0])
        colalign = tuple(_resolve_col_align(hdr) for hdr in raw_headers)
        display_headers = [
            _resolve_display_header(hdr, effective_display.size_unit)
            for hdr in raw_headers
        ]
        rows_as_lists = [
            [formatted_row[hdr] for hdr in raw_headers]
            for formatted_row in data_to_print
        ]
        print(
            tabulate(
                tabular_data=rows_as_lists,
                headers=display_headers,
                disable_numparse=True,
                colalign=colalign,
            )
        )
        if omitted_count > 0:
            print(
                f"... 외 {omitted_count:,}개 생략 (전체 {total_rows:,}개 중 상위 {len(shown_rows):,}개 표시)"
            )


class CUIPrinter:
    """평가 결과 리포트를 콘솔·Jupyter 환경에 맞춰 그룹별 표와 3단 요약 리포트로 출력하는 포맷터"""

    @staticmethod
    def _print_grouped_tables(
        tables: dict[tuple[str, bool], list[dict[str, Any]]],
        sort_key: SortKey,
        filename_maxlen: int,
        sanitize_emoji: bool,
        *,
        display: DisplayConfig,
        limit: int | None = None,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
    ) -> None:
        sorted_items = sorted(
            tables.items(), key=lambda i: (bool(i[0][0]), i[0][1], i[0][0])
        )
        for idx, ((dirname, is_pending), table) in enumerate(sorted_items):
            if not dirname:
                print(
                    f"\n\n[ {ClassifyStatus.NOT_TARGET.value} ({LABEL_ORIGINAL_PRESERVED}) - 총 {len(table)}개 ]"
                )
            elif is_pending:
                print(
                    f"\n\n[ {ClassifyStatus.PENDING.value}: '{dirname}' 폴더 (실제 이동 전) - 총 {len(table)}개 ]"
                )
            else:
                print(
                    f"\n\n[ {ClassifyStatus.MOVED.value}: '{dirname}' 폴더 - 총 {len(table)}개 ]"
                )
            if idx == 0 and (limit is None or limit > 0):
                print(_CUI_HEADER_LEGEND)
            TablePrinter.print(
                table,
                sort_key,
                filename_maxlen,
                sanitize_emoji,
                display=display,
                limit=limit,
                sort_by=sort_by,
                descending=descending,
            )

    @classmethod
    def print_tables(
        cls,
        report: SummaryReport,
        config: ClassifierConfig,
        sort_key: SortKey = None,
        filename_maxlen: int | None = None,
        sanitize_emoji: bool = True,
        *,
        limit: int | None = None,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
    ) -> None:
        """분류 상태 그룹별(이동 완료, 이동 예정, 이동 안 함) 상세 테이블만 콘솔에 출력"""
        maxlen = (
            filename_maxlen
            if filename_maxlen is not None
            else config.cui.filename_maxlen
        )
        tables: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)

        for ev in report.evaluations:
            vid = ev.video
            if vid.moved_dirname:
                group_key = (vid.moved_dirname, False)
            elif ev.target_dirname:
                group_key = (ev.target_dirname, True)
            else:
                group_key = ("", False)

            tables[group_key].append(
                {
                    _CUI_HDR_NAME: vid.filename,
                    _CUI_HDR_WIDTH: vid.width,
                    _CUI_HDR_HEIGHT: vid.height,
                    _CUI_HDR_ROTATE: f"{vid.rotate_type}°",
                    _CUI_HDR_CODEC: vid.codec,
                    _CUI_HDR_FPS: vid.fps,
                    _CUI_HDR_DURATION: vid.duration,
                    _CUI_SEP_LEFT_KEY: "│",
                    _CUI_HDR_BITRATE: vid.vid_kbps,
                    _CUI_HDR_OPT_BITRATE: ev.optimal_kbps,
                    _CUI_HDR_RATIO: ev.bitrate_ratio,
                    _CUI_HDR_ZONE: ev.quality_zone.value,
                    _CUI_HDR_KEYFRAME: vid.keyframe_interval,
                    _CUI_SEP_RIGHT_KEY: "│",
                    _CUI_HDR_SIZE: vid.vid_size_MB,
                    _CUI_HDR_AFTER_SIZE: ev.preview_after_mb,
                    _CUI_HDR_SAVED_SIZE: ev.preview_delta_mb,
                    _CUI_HDR_ACTION: ev.smart_action.action_label,
                    _CUI_HDR_REASON: ev.smart_action.reason_label,
                }
            )

        cls._print_grouped_tables(
            tables,
            sort_key,
            maxlen,
            sanitize_emoji,
            display=config.display,
            limit=limit,
            sort_by=sort_by,
            descending=descending,
        )

    @classmethod
    def print_report(
        cls,
        report: SummaryReport,
        config: ClassifierConfig,
        sort_key: SortKey = None,
        filename_maxlen: int | None = None,
        sanitize_emoji: bool = True,
        *,
        limit: int | None = None,
        summary_only: bool = False,
        include_summary: bool = True,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
    ) -> None:
        """분류 상태 그룹별 테이블과 하단 종합 용량 시뮬레이션 요약을 조합해 출력

        Jupyter 커널에서는 스크롤 편의를 위해 상세 표·요약 순으로 출력 블록을 분리
        """

        def _emit_tables() -> None:
            cls.print_tables(
                report=report,
                config=config,
                sort_key=sort_key,
                filename_maxlen=filename_maxlen,
                sanitize_emoji=sanitize_emoji,
                limit=limit,
                sort_by=sort_by,
                descending=descending,
            )

        if not summary_only and include_summary:
            display_fn = _resolve_notebook_display()
            if display_fn is not None:
                tables_text = _capture_stdout(_emit_tables)
                summary_text = _capture_stdout(
                    lambda: cls.print_summary_report(report, config)
                )
                if tables_text:
                    display_fn({"text/plain": tables_text}, raw=True)
                if summary_text:
                    display_fn({"text/plain": summary_text}, raw=True)
                return

        if not summary_only:
            _emit_tables()
        if summary_only or include_summary:
            cls.print_summary_report(report, config)

    @staticmethod
    def print_summary_report(report: SummaryReport, config: ClassifierConfig) -> None:
        """판별 기준 요약, 실익·효율 선별 결과, 전체 용량 변화 시뮬레이션의 3단 리포트를 출력"""
        eff = report.efficiency_summary
        bm = config.bitrate_model
        th = config.thresholds
        base_over_kbps = int(bm.base_target_kbps * th.over_bitrate_ratio)
        base_low_kbps = int(bm.base_target_kbps * th.low_bitrate_ratio)

        major_sep = "=" * REPORT_SEPARATOR_WIDTH
        minor_sep = "-" * REPORT_SEPARATOR_WIDTH
        print(f"\n{major_sep}")
        print("[1] 판별 기준 요약 (해상도 및 코덱별 비선형 픽셀밀도 스케일링 적용)")
        print(
            f"  - 기준 해상도({bm.base_width}x{bm.base_height}, {bm.base_fps:g}fps, H.264) "
            f"목표 비트레이트 : {bm.base_target_kbps} kbps (CQ22 수준)"
        )
        print(
            f"  - 과화질 판별 경계 : > {th.over_bitrate_ratio:.2f}배 (기준 조건 > {base_over_kbps:,} kbps) | "
            f"저화질 판별 경계 : < {th.low_bitrate_ratio:.2f}배 (기준 조건 < {base_low_kbps:,} kbps)"
        )
        print(
            f"  - 키프레임 허용 상한 : <= {th.keyframe_interval_sec:.1f}초 (초과 시 1초 키프레임 교정 대상)"
        )

        print(minor_sep)
        print("[2] 과화질 실익 · 시간 효율 선별 결과")
        exempt_txt = (
            f" | 대용량 절감 예외 >= {eff.exempt_saved_mb:.0f} MB"
            if eff.exempt_saved_mb > 0
            else ""
        )
        print(
            f"  - 선별 기준        : 최소 총 절감량 >= {eff.min_saved_mb:.1f} MB | "
            f"분당 최소 절감량 >= {eff.min_mb_per_min:.1f} MB/분{exempt_txt}"
        )
        print(
            f"  - 인코딩 작업 분량 : {format_duration_ko(eff.encode_duration_sec)} "
            f"(평균 효율 {report.encode_avg_mb_per_min:.1f} MB/분, {report.encoded_count}개)"
        )
        print(
            f"  - 제외된 과화질    : {eff.skipped_over_count}개 "
            f"(절감 미미 {eff.low_savings_count}개 / 시간 비효율 {eff.low_efficiency_count}개, "
            f"{format_duration_ko(eff.skipped_over_duration_sec)} 단축, "
            f"미반영 +{eff.skipped_over_potential_saved_mb:.1f} MB)"
        )

        print(minor_sep)
        print("[3] 전체 작업 폴더 용량 변화 시뮬레이션")
        print(
            f"  - 전체 대상 영상 수량         : 총 {report.total_count}개 "
            f"(인코딩 대상 {report.encoded_count}개 / {LABEL_ORIGINAL_PRESERVED} {report.passed_count}개)"
        )
        print(
            f"  - 현재 총 용량                : {report.total_orig_mb / MB_PER_GB:.2f} GB ({report.total_orig_mb:.1f} MB)"
        )
        print(
            f"  - 인코딩 완료 후 예상 총 용량 : {report.total_expected_after_mb / MB_PER_GB:.2f} GB ({report.total_expected_after_mb:.1f} MB)"
        )
        print(
            f"  - 과화질 감축 절감량(-)       : -{report.gross_saved_mb / MB_PER_GB:.2f} GB (-{report.gross_saved_mb:.1f} MB)"
        )
        print(
            f"  - 1초 키프레임 교정 증가분(+) : +{report.keyframe_increase_mb / MB_PER_GB:.2f} GB (+{report.keyframe_increase_mb:.1f} MB)"
        )
        print(
            f"  ★ 최종 순수 절감 용량(Net)    : {report.net_saved_mb / MB_PER_GB:.2f} GB "
            f"({report.net_saved_mb:.1f} MB, {report.net_saved_pct:.1f}% 절약 예상)"
        )
