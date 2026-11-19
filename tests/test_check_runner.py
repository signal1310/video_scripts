"""검사 실행기의 샤드 배분, 종료 코드 표기, 제한 시간 구분, 실패 로그를 검증"""

import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.check_runner import (
    FAILURE_LOG_RE,
    LOG_DIR_NAME,
    LOG_FILE_NAME,
    LOG_KEEP_COUNT,
    RUFF_FIX_LABEL,
    TIMINGS_FILE_NAME,
    CommandResult,
    CommandSpec,
    apply_ruff_fixes,
    default_shard_count,
    describe_failure,
    discover_test_modules,
    failure_log_name,
    format_exit_code,
    format_failure,
    load_timings,
    main,
    module_name,
    parse_timings,
    plan_shards,
    prune_failure_logs,
    render_log,
    resolve_log_path,
    run_command,
    save_run_logs,
    save_timings,
    shard_command,
    write_log,
)

from tests._repo_root import REPO_ROOT as _REPO_ROOT

_SLOW_MODULE = "import time\nimport unittest\n\nclass SlowTest(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(3)\n"
_FAST_MODULE = "import unittest\n\nclass FastTest(unittest.TestCase):\n    def test_fast(self):\n        self.assertTrue(True)\n"
_PRINT_MODULE = (
    "import unittest\n\nclass PrintTest(unittest.TestCase):\n"
    "    def test_print(self):\n        print('PROBE_STDOUT_MARKER')\n"
)


def _result(
    *,
    returncode: int = 1,
    output: str = "",
    timed_out: bool = False,
    modules: tuple[str, ...] = (),
) -> CommandResult:
    spec = CommandSpec(label="shard 1/1", command=["python"], timeout_sec=None, modules=modules)
    return CommandResult(spec=spec, returncode=returncode, output=output, elapsed_sec=1.0, timed_out=timed_out)


class PlanShardsTests(unittest.TestCase):
    def test_modules_are_split_by_size_without_loss(self) -> None:
        """모듈이 하나의 샤드에만 들어가고, 크기 내림차순 탐욕 배분으로 나뉘는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            modules: dict[str, str] = {}
            for name, size in (("test_a", 300), ("test_b", 200), ("test_c", 200), ("test_d", 100)):
                path = os.path.join(tmp, name + ".py")
                with open(path, "wb") as f:
                    f.write(b"x" * size)
                modules["tests." + name] = path
            shards = plan_shards(modules, 2)
        self.assertEqual(
            shards,
            [["tests.test_a", "tests.test_d"], ["tests.test_b", "tests.test_c"]],
        )


class DiscoverTestModulesTests(unittest.TestCase):
    """하위 폴더의 테스트 모듈을 점 경로 이름으로 찾고, 도우미 모듈과 테스트가 아닌 파일은 제외하는지 검증"""

    def test_nested_test_modules_get_dotted_names_and_helpers_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for rel in (
                "tests/test_top.py",
                "tests/_helper.py",
                "tests/core/encode/test_x.py",
                "tests/core/helper.py",
                "tests/core/encode/notes.txt",
            ):
                path = os.path.join(root, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            found = discover_test_modules(root)
        self.assertEqual(sorted(found), ["tests.core.encode.test_x", "tests.test_top"])
        self.assertEqual(module_name(root, found["tests.core.encode.test_x"]), "tests.core.encode.test_x")


class ShardSizingTests(unittest.TestCase):
    """샤드 수 공식, 기록 우선 배분, 기록이 없을 때 파일 크기 추정, 구분 마커 파싱과 기록 병합을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _modules(self, *specs: tuple[str, int]) -> dict[str, str]:
        """이름과 크기로 임시 테스트 파일을 만들고 점 경로 이름에서 파일 경로로 매핑"""
        modules: dict[str, str] = {}
        for name, size in specs:
            path = os.path.join(self._tmp.name, name + ".py")
            with open(path, "wb") as f:
                f.write(b"x" * size)
            modules["tests." + name] = path
        return modules

    def test_shard_count_is_half_of_logical_cores_with_floor_of_one(self) -> None:
        """샤드 수가 논리 코어 수의 절반이고, 코어 수를 모르거나 작으면 최소 1인지 검증"""
        self.assertEqual(default_shard_count(20), 10)
        self.assertEqual(default_shard_count(3), 1)
        self.assertEqual(default_shard_count(None), 1)

    def test_recorded_times_override_file_size(self) -> None:
        """기록 시간이 있으면 파일 크기보다 기록을 우선해 긴 모듈부터 배분하는지 검증"""
        modules = self._modules(("test_a", 100), ("test_b", 300), ("test_c", 200))
        shards = plan_shards(modules, 2, {"tests.test_a": 9.0, "tests.test_b": 1.0, "tests.test_c": 1.0})
        self.assertEqual(shards, [["tests.test_a"], ["tests.test_b", "tests.test_c"]])

    def test_missing_records_are_estimated_from_size(self) -> None:
        """기록이 없는 모듈은 기록된 초당 비율로 파일 크기에서 추정해 배분하는지 검증"""
        modules = self._modules(("test_a", 100), ("test_b", 300), ("test_c", 200))
        shards = plan_shards(modules, 2, {"tests.test_a": 9.0})
        self.assertEqual(shards, [["tests.test_b"], ["tests.test_c", "tests.test_a"]])

    def test_marker_block_is_parsed_and_missing_marker_gives_empty(self) -> None:
        """구분 마커 사이 JSON만 읽고 마커가 없으면 빈 매핑을 반환하는지 검증"""
        output = 'Ran 1 test\nTIMINGS_BEGIN {"tests.test_a": 1.5} TIMINGS_END\n'
        self.assertEqual(parse_timings(output), {"tests.test_a": 1.5})
        self.assertEqual(parse_timings("Ran 1 test\nOK"), {})

    def test_broken_or_missing_timing_file_gives_empty_records(self) -> None:
        """기록 파일이 없거나 깨졌을 때 빈 기록으로 처리해 파일 크기 배분으로 되돌아가는지 검증"""
        self.assertEqual(load_timings(pathlib.Path(self._tmp.name, "missing.json")), {})
        broken = pathlib.Path(self._tmp.name, TIMINGS_FILE_NAME)
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_timings(broken), {})

    def test_save_timings_merges_with_previous_records(self) -> None:
        """이번 실행의 측정값이 이전 기록의 같은 모듈을 덮어쓰고 나머지 기록은 남기는지 검증"""
        last_path = pathlib.Path(self._tmp.name, LOG_DIR_NAME, LOG_FILE_NAME)
        measured = _result(returncode=0, output='TIMINGS_BEGIN {"tests.test_b": 2.0} TIMINGS_END')
        save_timings(last_path, {"tests.test_a": 1.0, "tests.test_b": 9.0}, [measured])
        saved = load_timings(last_path.parent / TIMINGS_FILE_NAME)
        self.assertEqual(saved, {"tests.test_a": 1.0, "tests.test_b": 2.0})

    def test_no_timing_file_without_measurements(self) -> None:
        """측정값이 없는 실행은 기록 파일을 만들지 않는지 검증"""
        last_path = pathlib.Path(self._tmp.name, LOG_DIR_NAME, LOG_FILE_NAME)
        save_timings(last_path, {}, [_result(returncode=0, output="Ran 1 test\nOK")])
        self.assertFalse((last_path.parent / TIMINGS_FILE_NAME).exists())


class FormatTests(unittest.TestCase):
    def test_exit_code_shows_unsigned_decimal_and_hex(self) -> None:
        """-1과 0xFFFFFFFF가 같은 부호 없는 10진수와 16진수로 표시되는지 검증"""
        self.assertEqual(format_exit_code(-1), "4294967295 (0xFFFFFFFF)")
        self.assertEqual(format_exit_code(0xC0000409), "3221226505 (0xC0000409)")

    def test_fault_timeout_marker_is_reported_as_time_limit(self) -> None:
        """faulthandler 제한 시간 출력이 있으면 종료 코드 1이어도 제한 시간 초과로 보고하는지 검증"""
        text = describe_failure(_result(returncode=1, output="Timeout (0:05:00)!\n"))
        self.assertIn("제한 시간 초과", text)
        self.assertNotIn("바깥", text)

    def test_outer_timeout_is_reported_separately(self) -> None:
        """바깥 예비 장치로 끝난 경우 샤드 안쪽 스택이 없다는 사실을 따로 보고하는지 검증"""
        text = describe_failure(_result(returncode=1, timed_out=True))
        self.assertIn("바깥 예비 장치", text)

    def test_empty_output_is_stated_in_failure_block(self) -> None:
        """출력 없는 실패는 강제 종료 가능성 표기와 출력 없음 문구, 로그 위치를 함께 보여 주는지 검증"""
        text = format_failure(_result(returncode=-1, output="", modules=("tests.test_a",)), "log-path")
        self.assertIn("강제 종료되었을 수 있음", text)
        self.assertIn("출력 없음", text)
        self.assertIn("모듈: tests.test_a", text)
        self.assertIn("전체 로그: log-path", text)

    def test_failed_test_names_are_listed(self) -> None:
        """실패한 테스트 이름을 실패 블록에 모으는지 검증"""
        output = "FAIL: test_x (tests.test_a.A)\nERROR: test_y (tests.test_a.A)\nRan 2 tests\n"
        text = format_failure(_result(output=output), "log-path")
        self.assertIn("FAIL: test_x (tests.test_a.A)", text)
        self.assertIn("ERROR: test_y (tests.test_a.A)", text)


class ShardProcessTests(unittest.TestCase):
    """실제 샤드 프로세스로 faulthandler 제한 시간, 통과, 바깥 제한 시간 경로를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, body in (
            ("slow_probe.py", _SLOW_MODULE),
            ("fast_probe.py", _FAST_MODULE),
            ("print_probe.py", _PRINT_MODULE),
        ):
            with open(os.path.join(self._tmp.name, name), "w", encoding="utf-8") as f:
                f.write(body)

    def test_fault_timeout_dumps_stack_inside_shard(self) -> None:
        """제한 시간을 넘긴 샤드가 안에서 스택을 남기고 종료하며, 바깥 제한 시간으로 끝나지 않는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=shard_command(sys.executable, ["slow_probe"], "-b", timeout_sec=1),
            timeout_sec=30,
            modules=("slow_probe",),
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        self.assertTrue(result.fault_timeout)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("slow_probe.py", result.output)
        self.assertLess(result.elapsed_sec, 3.0)

    def test_passing_shard_reports_ran_line(self) -> None:
        """통과하는 샤드는 종료 코드 0과 Ran 줄을 내고 제한 시간 표시가 없는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=shard_command(sys.executable, ["fast_probe"], "-b"),
            timeout_sec=30,
            modules=("fast_probe",),
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.failed)
        self.assertFalse(result.fault_timeout)
        self.assertIn("Ran 1 test", result.output)

    def test_verbose_shard_keeps_output_of_passing_tests(self) -> None:
        """상세 출력은 버퍼를 끄므로 통과한 테스트의 표준 출력이 샤드 출력에 남는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=shard_command(sys.executable, ["print_probe"], "-v"),
            timeout_sec=30,
            modules=("print_probe",),
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        self.assertEqual(result.returncode, 0)
        self.assertIn("PROBE_STDOUT_MARKER", result.output)

    def test_default_shard_drops_output_of_passing_tests(self) -> None:
        """기본 출력은 버퍼를 켜므로 통과한 테스트의 표준 출력이 샤드 출력에 남지 않는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=shard_command(sys.executable, ["print_probe"], "-b"),
            timeout_sec=30,
            modules=("print_probe",),
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("PROBE_STDOUT_MARKER", result.output)

    def test_passing_shard_reports_module_timings(self) -> None:
        """샤드가 모듈별 시간을 구분 마커 사이에 남겨 러너가 다음 배분에 쓸 수 있는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=shard_command(sys.executable, ["fast_probe"], "-b"),
            timeout_sec=30,
            modules=("fast_probe",),
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        timings = parse_timings(result.output)
        self.assertEqual(list(timings), ["fast_probe"])
        self.assertGreaterEqual(timings["fast_probe"], 0.0)

    def test_outer_timeout_kills_process_and_is_marked(self) -> None:
        """바깥 예비 제한 시간을 넘기면 강제 종료하고 바깥 시간 초과로 표시하는지 검증"""
        spec = CommandSpec(
            label="shard 1/1",
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_sec=0.5,
        )
        result = run_command(spec, self._tmp.name, dict(os.environ))
        self.assertTrue(result.timed_out)
        self.assertFalse(result.fault_timeout)
        self.assertLess(result.elapsed_sec, 10.0)


class LogTests(unittest.TestCase):
    def test_render_log_records_exit_code_and_empty_output(self) -> None:
        """전체 로그에 종료 코드와 출력 바이트 수, 빈 출력이 남는지 검증"""
        text = render_log([_result(returncode=-1, output="", modules=("tests.test_a",))], 1.5)
        self.assertIn("exit: 4294967295 (0xFFFFFFFF)", text)
        self.assertIn("output bytes: 0", text)
        self.assertIn("modules: tests.test_a", text)

    def test_write_log_overwrites_previous_run(self) -> None:
        """실행마다 이전 로그를 덮어쓰는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "nested", "last.log")
            write_log(path, "first")
            write_log(path, "second")
            self.assertEqual(path.read_text(encoding="utf-8"), "second")

    def test_log_path_is_under_git_common_directory(self) -> None:
        """저장소 안에서 로그 경로가 Git 공용 디렉터리 아래에 잡히는지 검증"""
        path = resolve_log_path(str(_REPO_ROOT))
        assert path is not None
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parts[-2:], (LOG_DIR_NAME, LOG_FILE_NAME))
        self.assertIn(".git", path.parts)

    def test_log_path_is_none_outside_repository(self) -> None:
        """Git 저장소 밖에서는 로그 경로가 없는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_log_path(tmp))

    def test_failure_log_name_matches_retention_pattern(self) -> None:
        """실패 로그 이름이 시각 형식으로 만들어지고 보존 정리 패턴에 맞는지 검증"""
        name = failure_log_name(datetime(2026, 11, 19, 10, 15, 30, 123456))
        self.assertEqual(name, "failure-20261119-101530-123456.log")
        self.assertIsNotNone(FAILURE_LOG_RE.fullmatch(name))

    def test_prune_removes_only_old_failure_logs_of_matching_format(self) -> None:
        """보존 개수를 넘는 오래된 실패 로그만 지우고 last.log와 형식이 다른 파일은 남기는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = pathlib.Path(tmp)
            total = LOG_KEEP_COUNT + 2
            failure_names = [f"failure-20261119-1015{index:02d}-000000.log" for index in range(total)]
            for name in failure_names:
                (log_dir / name).write_text("fail", encoding="utf-8")
            others = [LOG_FILE_NAME, "failure-note.txt", "failure-20261119.log", "other.log"]
            for name in others:
                (log_dir / name).write_text("keep", encoding="utf-8")

            prune_failure_logs(log_dir)

            remaining = sorted(entry.name for entry in log_dir.iterdir())
            self.assertEqual(remaining, sorted([*others, *failure_names[-LOG_KEEP_COUNT:]]))

    def test_failure_log_is_saved_only_when_a_step_fails(self) -> None:
        """실패가 있을 때만 시각 이름의 실패 로그를 남기고, 통과한 실행은 last.log만 갱신하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            last_path = pathlib.Path(tmp, LOG_DIR_NAME, LOG_FILE_NAME)
            passed = [_result(returncode=0, output="Ran 1 tests in 0.1s\nOK")]
            self.assertIsNone(save_run_logs(last_path, passed, 1.0, datetime(2026, 11, 19, 10, 0, 0)))
            self.assertTrue(last_path.exists())
            self.assertEqual(sorted(entry.name for entry in last_path.parent.iterdir()), [LOG_FILE_NAME])

            failed = [_result(returncode=1, output="FAIL: test_x", modules=("tests.test_a",))]
            failure_path = save_run_logs(last_path, failed, 2.0, datetime(2026, 11, 19, 10, 0, 1, 5))
            assert failure_path is not None
            self.assertIsNotNone(FAILURE_LOG_RE.fullmatch(failure_path.name))
            self.assertEqual(failure_path.read_text(encoding="utf-8"), last_path.read_text(encoding="utf-8"))
            self.assertIn("FAIL: test_x", last_path.read_text(encoding="utf-8"))

    def test_no_log_is_saved_outside_repository(self) -> None:
        """Git 저장소 밖에서는 실패가 있어도 실패 로그 경로를 만들지 않는지 검증"""
        failed = [_result(returncode=1, output="FAIL: test_x")]
        self.assertIsNone(save_run_logs(None, failed, 1.0, datetime(2026, 11, 19)))


def _ruff_exe() -> str:
    """테스트 실행 환경의 Ruff 실행 파일 경로"""
    return os.path.join(os.path.dirname(sys.executable), "ruff.exe" if os.name == "nt" else "ruff")


class RuffFixTests(unittest.TestCase):
    """임시 저장소에서 Ruff 안전 수정이 고친 파일만 보고하는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._root = self._tmp.name
        self._write("pyproject.toml", '[tool.ruff.lint]\nselect = ["I"]\n')

    def _write(self, name: str, text: str) -> None:
        path = pathlib.Path(self._root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _read(self, name: str) -> str:
        return pathlib.Path(self._root, name).read_text(encoding="utf-8")

    def test_safe_fix_sorts_imports_and_reports_only_changed_file(self) -> None:
        """import 순서(I001)를 고치고, 고친 파일만 저장소 기준 슬래시 경로로 보고하는지 검증"""
        self._write("pkg/a.py", "import sys\nimport os\n\nprint(os, sys)\n")
        self._write("pkg/b.py", "import os\n\nprint(os)\n")
        changed, result = apply_ruff_fixes(self._root, _ruff_exe(), dict(os.environ))
        self.assertEqual(result.spec.label, RUFF_FIX_LABEL)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(changed, ["pkg/a.py"])
        self.assertTrue(self._read("pkg/a.py").startswith("import os\nimport sys\n"))

    def test_clean_tree_reports_no_change(self) -> None:
        """고칠 것이 없으면 고친 파일 목록이 비어 있는지 검증"""
        self._write("pkg/a.py", "import os\n\nprint(os)\n")
        changed, result = apply_ruff_fixes(self._root, _ruff_exe(), dict(os.environ))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(changed, [])


class MainFixOrderTests(unittest.TestCase):
    """자동 수정이 검사 단계보다 먼저 순차로 실행되고 출력 줄은 고친 경우에만 나오는지 검증"""

    def _run_main(self, changed: list[str], fix_returncode: int = 0) -> tuple[int, list[str], str]:
        events: list[str] = []
        fix_spec = CommandSpec(label=RUFF_FIX_LABEL, command=["ruff"])

        def fake_fix(root: str, ruff_exe: str, env: dict[str, str]) -> tuple[list[str], CommandResult]:
            events.append("fix")
            return changed, CommandResult(
                spec=fix_spec, returncode=fix_returncode, output="", elapsed_sec=0.0, timed_out=False
            )

        def fake_run(spec: CommandSpec, cwd: str, env: dict[str, str]) -> CommandResult:
            events.append(spec.label)
            return CommandResult(spec=spec, returncode=0, output="Ran 1 test\nOK", elapsed_sec=0.0, timed_out=False)

        out = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("scripts.check_runner.apply_ruff_fixes", fake_fix),
            patch("scripts.check_runner.run_command", fake_run),
            patch("scripts.check_runner.resolve_log_path", return_value=None),
            contextlib.redirect_stdout(out),
        ):
            code = main([tmp])
        return code, events, out.getvalue()

    def test_auto_fix_runs_before_all_checks(self) -> None:
        """자동 수정이 첫 번째로 실행되고 나머지 검사 단계가 뒤따르는지 검증"""
        code, events, _ = self._run_main(changed=[])
        self.assertEqual(code, 0)
        self.assertEqual(events[0], "fix")
        self.assertEqual(len(events), 4)
        self.assertTrue(all(label.startswith("[") for label in events[1:]))

    def test_fixed_file_line_is_printed_only_when_changed(self) -> None:
        """고친 파일이 있을 때만 목록 줄을 출력하고 없으면 출력하지 않는지 검증"""
        _, _, fixed_out = self._run_main(changed=["src/a.py", "tests/b.py"])
        self.assertIn("Ruff 자동 수정: src/a.py, tests/b.py", fixed_out)
        _, _, clean_out = self._run_main(changed=[])
        self.assertNotIn("Ruff 자동 수정", clean_out)

    def test_fix_crash_stops_before_checks(self) -> None:
        """자동 수정이 종료 코드 1을 넘는 오류로 끝나면 검사를 시작하지 않고 실패하는지 검증"""
        code, events, out = self._run_main(changed=[], fix_returncode=2)
        self.assertEqual(code, 1)
        self.assertEqual(events, ["fix"])
        self.assertIn(f"FAILED: {RUFF_FIX_LABEL}", out)


if __name__ == "__main__":
    unittest.main()
