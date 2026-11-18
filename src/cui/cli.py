"""스탠드얼론 CLI 인자 파서 및 CUI 실행 진입점 모듈"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import get_args

from src.classifier import VideoClassifier
from src.core.app_state import ensure_default_config_files
from src.core.config import VALID_SIZE_UNITS
from src.core.eta import format_estimate_label
from src.core.exceptions import VideoScriptsError, format_user_error
from src.core.logging_setup import ensure_console_logging
from src.core.messages import MSG_WORK_CANCELLED
from src.cui.sorting import SortBy

_SORT_BY_CHOICES: tuple[str, ...] = tuple(get_args(SortBy))


def build_cli_parser(
    *,
    prog: str = "python -m src.cui",
    include_dispatch_flags: bool = False,
) -> argparse.ArgumentParser:
    """스탠드얼론 CUI 실행용 argparse 파서를 생성"""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="FFmpeg/FFprobe 기반 비디오 화질 분석 및 자동 판정 CLI",
    )
    if include_dispatch_flags:
        mode_group = parser.add_mutually_exclusive_group()
        mode_group.add_argument(
            "--gui",
            action="store_true",
            help="데스크톱 GUI 실행 (인자 없이 python -m src 실행 시 기본 동작)",
        )
        mode_group.add_argument(
            "--cli",
            action="store_true",
            help="스탠드얼론 CLI 모드 강제 (다른 CLI 옵션과 함께 지정 시 생략 가능)",
        )
    parser.add_argument(
        "root_dir",
        nargs="?",
        default=None,
        help="분석할 작업 폴더 경로 (생략 시 data/rootdir.txt 경로 사용)",
    )

    parser.add_argument(
        "-k",
        "--keyframe",
        action="store_true",
        help="키프레임 간격 실측 포함 (생략 시 고속 분석)",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="하위 폴더 포함 분석 (숨김 폴더 제외)",
    )
    parser.add_argument(
        "-e",
        "--exception",
        action="append",
        default=[],
        metavar="TOKEN",
        help="재인코딩 대상에서 제외할 키워드 또는 조건식(예: 'v.fps < 30') 추가",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="그룹별 표 최대 출력 행 수",
    )
    parser.add_argument(
        "-s",
        "--sort",
        choices=_SORT_BY_CHOICES,
        default=None,
        help="테이블 정렬 기준 열",
    )

    order_group = parser.add_mutually_exclusive_group()
    order_group.add_argument(
        "--desc",
        dest="descending",
        action="store_true",
        default=None,
        help="정렬 방향을 내림차순으로 강제",
    )
    order_group.add_argument(
        "--asc",
        dest="descending",
        action="store_false",
        default=None,
        help="정렬 방향을 오름차순으로 강제",
    )

    parser.add_argument(
        "-u",
        "--unit",
        choices=VALID_SIZE_UNITS,
        default=None,
        help="용량 표기 단위 (mb, gb, auto)",
    )
    parser.add_argument(
        "--maxlen",
        type=int,
        default=None,
        help="표 출력 시 파일명 열 최대 표시 너비",
    )

    summary_group = parser.add_mutually_exclusive_group()
    summary_group.add_argument(
        "--summary-only",
        action="store_true",
        help="상세 표 없이 3단 용량 시뮬레이션 요약만 출력",
    )
    summary_group.add_argument(
        "--no-summary",
        action="store_true",
        help="요약 리포트 없이 그룹별 표만 출력",
    )

    parser.add_argument(
        "--no-emoji-sanitize",
        action="store_true",
        help="파일명의 이모지, 비표준 문자 치환을 비활성화",
    )
    return parser


def run_cli(
    args: argparse.Namespace,
    *,
    classifier_factory: type[VideoClassifier] = VideoClassifier,
) -> int:
    """파싱된 CLI 인자에 따라 분류기 조회와 판정 결과 출력을 실행"""
    classifier = classifier_factory(root_dir=args.root_dir)
    if args.unit is not None:
        classifier.config.display.size_unit = args.unit
    if args.maxlen is not None:
        classifier.set_filename_max_length(args.maxlen)
    if getattr(args, "recursive", False):
        classifier.include_subdirectories(True)
    if args.keyframe:
        classifier.include_keyframe_interval(True, eager_prepare=False)
    if args.exception:
        classifier.set_exception_tokens(
            [*classifier.exception_tokens, *args.exception]
        )

    estimate = classifier.estimate_analysis(classifier.include_keyframe)
    print(f"분석 예상: {format_estimate_label(estimate.pending_count, estimate.expected_sec)}")

    classifier.print(
        limit=args.limit,
        summary_only=bool(args.summary_only),
        include_summary=not bool(args.no_summary),
        sort_by=args.sort,
        descending=args.descending,
        sanitize_emoji=not bool(args.no_emoji_sanitize),
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    prog: str = "python -m src.cui",
    include_dispatch_flags: bool = False,
) -> int:
    """CLI 인자 검증, 실행 및 도메인 예외 종료 코드 매핑 진입점"""
    parser = build_cli_parser(
        prog=prog,
        include_dispatch_flags=include_dispatch_flags,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "gui", False):
        parser.error("--gui 옵션은 단독으로만 사용할 수 있습니다.")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit 값은 0 이상이어야 합니다.")
    if args.maxlen is not None and args.maxlen <= 0:
        parser.error("--maxlen 값은 1 이상이어야 합니다.")

    ensure_console_logging()
    ensure_default_config_files()
    try:
        return run_cli(args)
    except KeyboardInterrupt:
        print(f"\n{MSG_WORK_CANCELLED}", file=sys.stderr)
        return 130
    except VideoScriptsError as exc:
        print(format_user_error(exc), file=sys.stderr)
        return 1
