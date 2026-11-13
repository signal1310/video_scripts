"""콘솔 및 Jupyter 노트북용 표 포맷터와 요약 리포트 출력 모듈"""

import unicodedata
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Protocol

from tabulate import tabulate

from src.core.config import ClassifierConfig
from src.core.file_manager import file_exists_in
from src.core.messages import LABEL_ORIGINAL_PRESERVED
from src.core.models import MB_PER_GB, SummaryReport

SortKey = Callable[[dict[str, Any]], tuple[Any, ...] | list[Any]] | None
REPORT_SEPARATOR_WIDTH: int = 78


class VideoTableSource(Protocol):
    """리포트 출력 시 루트 작업 경로를 제공하는 테이블 소스 프로토콜"""

    @property
    def root_dir(self) -> str:
        """현재 작업 폴더 루트 경로 반환"""
        ...


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


def truncate_text(text: str, max_len: int) -> str:
    """동아시아 전각 문자의 2칸 너비를 계산에 반영하여 지정한 최대 표시 너비로 문자열을 자르고 말줄임표(...)를 붙임"""
    if not text:
        return ""
    if text.isascii():
        if len(text) <= max_len:
            return text
        cut = text[: max_len - 2] if max_len >= 2 else ""
        return cut + "..."

    curr_len: int = 0
    for idx, ch in enumerate(text):
        ch_len: int = (
            1
            if ch.isascii()
            else (2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1)
        )
        if curr_len + ch_len > max_len:
            cut: str = text[: idx - 2] if idx >= 2 else ""
            return cut + "..."
        curr_len += ch_len
    return text


class TablePrinter:
    """딕셔너리 리스트 형태의 테이블 데이터를 정렬하고 서식을 맞춰 콘솔에 정갈하게 출력하는 유틸리티 클래스"""

    @staticmethod
    def print(
        table: list[dict[str, Any]],
        sort_key: SortKey,
        filename_maxlen: int,
        sanitize_emoji: bool,
        floatfmt: str = ".3f",
    ) -> None:
        """테이블 행 목록 정렬 및 너비·이모지 정제 규칙 적용 후 콘솔 출력"""
        sorted_rows: list[dict[str, Any]] = (
            sorted(table, key=sort_key) if sort_key else table
        )
        data_to_print: list[dict[str, Any]] = [dict(r) for r in sorted_rows]

        for row in data_to_print:
            if "\n이름" in row and isinstance(row["\n이름"], str):
                name_val = row["\n이름"]
                if sanitize_emoji:
                    name_val = sanitize_text(name_val)
                row["\n이름"] = truncate_text(name_val, filename_maxlen)

        print(
            tabulate(
                tabular_data=data_to_print,
                headers="keys",
                floatfmt=floatfmt,
            )
        )


class CUIPrinter:
    """Jupyter Notebook 및 터미널 환경에 적합한 그룹별 테이블과 3단 요약 리포트를 출력하는 포매터 클래스"""

    @staticmethod
    def _print_grouped_tables(
        tables: dict[tuple[str, bool], list[dict[str, Any]]],
        sort_key: SortKey,
        filename_maxlen: int,
        sanitize_emoji: bool,
        floatfmt: str = ".3f",
    ) -> None:
        sorted_items = sorted(
            tables.items(), key=lambda i: (bool(i[0][0]), i[0][1], i[0][0])
        )
        for (dirname, pseudo_classified), table in sorted_items:
            if not dirname:
                print(
                    f"\n\n[ 분류되지 않은 비디오 목록 ({LABEL_ORIGINAL_PRESERVED}) - 총 {len(table)}개 ]"
                )
            elif pseudo_classified:
                print(
                    f"\n\n[ '{dirname}' 폴더로 모의 분류된 비디오 목록 (실제로 이동되지 않습니다) - 총 {len(table)}개 ]"
                )
            else:
                print(f"\n\n[ '{dirname}' 폴더로 분류된 비디오 목록 - 총 {len(table)}개 ]")
            TablePrinter.print(
                table, sort_key, filename_maxlen, sanitize_emoji, floatfmt=floatfmt
            )

    @classmethod
    def print_report(
        cls,
        cache: VideoTableSource | str | None,
        report: SummaryReport,
        config: ClassifierConfig,
        sort_key: SortKey = None,
        filename_maxlen: int | None = None,
        sanitize_emoji: bool = True,
    ) -> None:
        """분류 그룹별(원본 보존, 표준인코딩_CQ22, 저화질_키프레임교정) 테이블과 하단 종합 용량 시뮬레이션을 출력"""
        if isinstance(cache, str):
            root_dir = cache
        elif cache is not None:
            root_dir = cache.root_dir
        else:
            root_dir = report.root_dir

        maxlen = filename_maxlen or config.cui.filename_maxlen
        tables: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)

        for ev in report.evaluations:
            vid = ev.video
            pseudo = vid.moved_dirname is not None and file_exists_in(
                root_dir, vid.filename
            )
            tables[(vid.moved_dirname or "", pseudo)].append(
                {
                    "\n이름": vid.filename,
                    "\nW": vid.width,
                    "\nH": vid.height,
                    "\n코덱": vid.codec,
                    "\nFPS": round(vid.fps, 1),
                    "길이\n(초)": vid.duration,
                    "\n│": "│",
                    "용량\n(MB)": vid.vid_size_MB,
                    "\nb-rate": vid.vid_kbps,
                    "최적\nb-rate": ev.optimal_kbps,
                    "b-rate\n비율": ev.bitrate_ratio,
                    "화질\n등급": ev.quality_zone.value,
                    "\u200b\n│": "│",
                    "KF\n간격": (
                        vid.keyframe_interval
                        if vid.keyframe_interval is not None
                        else -1.0
                    ),
                    "예상후\n(MB)": ev.expected_after_mb,
                    "예상절감\n(MB)": ev.expected_delta_mb,
                    "인코딩\n처방": ev.smart_action.value,
                }
            )

        cls._print_grouped_tables(
            tables, sort_key, maxlen, sanitize_emoji, config.cui.float_format
        )
        cls.print_summary_report(report, config)

    @staticmethod
    def print_summary_report(report: SummaryReport, config: ClassifierConfig) -> None:
        """판별 기준 요약, 소형 영상 분포 분석 결과, 전체 용량 변화 시뮬레이션의 3단 리포트를 출력"""
        dist = report.small_distribution
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
            f"목표 b-rate : {bm.base_target_kbps} kbps (CQ22 수준)"
        )
        print(
            f"  - 과화질 판별 경계 : > {th.over_bitrate_ratio:.2f}배 (기준 조건 > {base_over_kbps:,} kbps) | "
            f"저화질 판별 경계 : < {th.low_bitrate_ratio:.2f}배 (기준 조건 < {base_low_kbps:,} kbps)"
        )
        print(
            f"  - 키프레임 허용 상한 : <= {th.keyframe_interval_sec:.1f}초 (초과 시 1초 키프레임 교정 대상)"
        )

        print(minor_sep)
        print(f"[2] 소형 영상(< {dist.small_threshold_mb:.0f}MB) 분포 및 누적 최적화 전략 판정")
        print(
            f"  - 과화질 대상 중 소형 영상 수량 : 총 {dist.total_over_count}개 중 {dist.small_over_count}개"
        )
        print(
            f"  - 소형 영상 예상 절감 합계 : {dist.small_potential_saved_mb:.2f} MB "
            f"(전체 절감 가능량 {dist.total_potential_saved_mb:.2f} MB 중 {dist.share_pct:.1f}%)"
        )
        print(f"  - 적용된 전략 모드 : {dist.strategy.value}")
        print(f"  - 자동 판정 사유   : {dist.reason}")

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
        print(major_sep)
