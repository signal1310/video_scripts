"""스탠드얼론 CLI 파서, 실행 흐름 및 루트 B-2 분기 단위 테스트 모듈"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

from src.__main__ import dispatch_main
from src.core.config import VALID_SIZE_UNITS, ClassifierConfig
from src.core.exceptions import ErrorCode, VideoScriptsError
from src.core.messages import MSG_WORK_CANCELLED
from src.cui import cli
from src.service.analysis_timing import AnalysisEstimate

if TYPE_CHECKING:
    from src.classifier import VideoClassifier


class _FakeClassifier:
    """CLI 동작 검증용 최소 VideoClassifier 대역"""

    def __init__(self, root_dir: str | None = None) -> None:
        self.root_dir = root_dir
        self.config = ClassifierConfig()
        self.exception_tokens: list[str] = ["existing_keep"]
        self.include_keyframe: bool = False
        self.calls: list[tuple[str, object]] = []

    def set_filename_max_length(self, maxlen: int) -> None:
        self.calls.append(("set_filename_max_length", maxlen))

    def include_keyframe_interval(
        self,
        include: bool = True,
        *,
        eager_prepare: bool = False,
    ) -> None:
        self.include_keyframe = include
        self.calls.append(
            ("include_keyframe_interval", (include, eager_prepare))
        )

    def estimate_analysis(self, include_keyframe: bool) -> AnalysisEstimate:
        return AnalysisEstimate(pending_count=0, expected_sec=0.0)

    def set_exception_tokens(self, tokens: list[str]) -> None:
        self.exception_tokens = list(tokens)
        self.calls.append(("set_exception_tokens", list(tokens)))

    def print(
        self,
        *,
        limit: int | None = None,
        summary_only: bool = False,
        include_summary: bool = True,
        sort_by: str | None = None,
        descending: bool | None = None,
        sanitize_emoji: bool = True,
    ) -> None:
        self.calls.append(
            (
                "print",
                {
                    "limit": limit,
                    "summary_only": summary_only,
                    "include_summary": include_summary,
                    "sort_by": sort_by,
                    "descending": descending,
                    "sanitize_emoji": sanitize_emoji,
                },
            )
        )


class TestStandaloneCli(unittest.TestCase):
    """스탠드얼론 CLI 파싱, 검증, 실행, 종료 코드 및 루트 패키지 분기 검증"""

    def test_build_cli_parser_defaults_and_choices(self) -> None:
        """기본 CLI 옵션 파싱 및 허용 단위, 정렬 키 제약을 검증"""
        parser = cli.build_cli_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.root_dir)
        self.assertFalse(args.keyframe)
        self.assertEqual(args.exception, [])
        self.assertIsNone(args.limit)
        self.assertIsNone(args.sort)
        self.assertIsNone(args.descending)
        self.assertIsNone(args.unit)
        self.assertIsNone(args.maxlen)
        self.assertFalse(args.summary_only)
        self.assertFalse(args.no_summary)
        self.assertFalse(args.no_emoji_sanitize)

        for unit in VALID_SIZE_UNITS:
            parsed = parser.parse_args(["--unit", unit])
            self.assertEqual(parsed.unit, unit)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx_order:
            parser.parse_args(["--desc", "--asc"])
        self.assertEqual(ctx_order.exception.code, 2)

    def test_main_rejects_negative_limit_and_non_positive_maxlen(self) -> None:
        """음수 --limit 및 0 이하 --maxlen 입력 시 종료 코드 2로 거부함을 검증"""
        for bad_args in (["--limit", "-1"], ["--maxlen", "0"], ["--maxlen", "-5"]):
            with (
                self.subTest(bad_args=bad_args),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as ctx,
            ):
                cli.main(bad_args)
            self.assertEqual(ctx.exception.code, 2)

    def test_run_cli_view_options_and_exception_merge(self) -> None:
        """조회 옵션, 단위, 예외 규칙 병합, 정렬 방향이 분류기 호출로 전달되는지 검증"""
        created: list[_FakeClassifier] = []

        def factory(root_dir: str | None = None) -> _FakeClassifier:
            inst = _FakeClassifier(root_dir=root_dir)
            created.append(inst)
            return inst

        parser = cli.build_cli_parser()
        args = parser.parse_args(
            [
                "sample_dir",
                "-k",
                "-e",
                "v.fps < 30",
                "-e",
                "sample",
                "-l",
                "15",
                "-s",
                "saved",
                "--asc",
                "-u",
                "gb",
                "--maxlen",
                "48",
                "--no-summary",
                "--no-emoji-sanitize",
            ]
        )

        exit_code = cli.run_cli(
            args,
            classifier_factory=cast("type[VideoClassifier]", factory),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(created), 1)
        inst = created[0]
        self.assertEqual(inst.root_dir, "sample_dir")
        self.assertEqual(inst.config.display.size_unit, "gb")
        self.assertEqual(
            inst.exception_tokens,
            ["existing_keep", "v.fps < 30", "sample"],
        )
        self.assertIn(("set_filename_max_length", 48), inst.calls)
        self.assertIn(("include_keyframe_interval", (True, False)), inst.calls)
        self.assertIn(
            (
                "print",
                {
                    "limit": 15,
                    "summary_only": False,
                    "include_summary": False,
                    "sort_by": "saved",
                    "descending": False,
                    "sanitize_emoji": False,
                },
            ),
            inst.calls,
        )

    def test_main_maps_domain_error_and_keyboard_interrupt_exit_codes(self) -> None:
        """도메인 예외(1)와 KeyboardInterrupt(130) 종료 코드 매핑을 검증"""
        err_stderr = io.StringIO()
        with (
            patch(
                "src.cui.cli.run_cli",
                side_effect=VideoScriptsError(
                    "테스트 경로 오류",
                    code=ErrorCode.ROOT_DIR_INVALID,
                    hint="경로를 확인하세요",
                ),
            ),
            redirect_stderr(err_stderr),
        ):
            code = cli.main([])
        self.assertEqual(code, 1)
        self.assertIn("테스트 경로 오류", err_stderr.getvalue())

        int_stderr = io.StringIO()
        with (
            patch("src.cui.cli.run_cli", side_effect=KeyboardInterrupt()),
            redirect_stderr(int_stderr),
        ):
            code = cli.main([])
        self.assertEqual(code, 130)
        self.assertIn(MSG_WORK_CANCELLED, int_stderr.getvalue())

    def test_root_dispatch_main_b2_routing_and_help_flags(self) -> None:
        """python -m src의 무인자/--gui -> GUI, 인자/--cli -> CLI 분기 및 통합 help 플래그 노출을 검증"""
        gui_run = MagicMock(return_value=0)
        cli_main = MagicMock(return_value=7)

        def fake_import(name: str) -> object:
            if name == "src.gui":
                return SimpleNamespace(run_gui=gui_run)
            if name == "src.cui.cli":
                return SimpleNamespace(main=cli_main)
            raise AssertionError(f"unexpected module import: {name}")

        with patch("src.__main__.importlib.import_module", side_effect=fake_import):
            self.assertEqual(dispatch_main([]), 0)
            self.assertEqual(dispatch_main(["--gui"]), 0)
            self.assertEqual(gui_run.call_count, 2)
            cli_main.assert_not_called()

            self.assertEqual(dispatch_main(["--cli"]), 7)
            cli_main.assert_called_with(
                ["--cli"],
                prog="python -m src",
                include_dispatch_flags=True,
            )

            self.assertEqual(dispatch_main(["--cli", "--sort", "saved"]), 7)
            cli_main.assert_called_with(
                ["--cli", "--sort", "saved"],
                prog="python -m src",
                include_dispatch_flags=True,
            )

            self.assertEqual(dispatch_main(["-n", "--unit", "gb"]), 7)
            cli_main.assert_called_with(
                ["-n", "--unit", "gb"],
                prog="python -m src",
                include_dispatch_flags=True,
            )

        root_help = cli.build_cli_parser(
            prog="python -m src",
            include_dispatch_flags=True,
        ).format_help()
        self.assertIn("--gui", root_help)
        self.assertIn("--cli", root_help)

        cui_help = cli.build_cli_parser().format_help()
        self.assertNotIn("--gui", cui_help)
        self.assertNotIn("--cli", cui_help)

        with (
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx_gui,
        ):
            cli.main(["--gui", "-n"], include_dispatch_flags=True)
        self.assertEqual(ctx_gui.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
