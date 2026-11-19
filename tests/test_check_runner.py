"""검사 실행기의 샤드 배분, 종료 코드 표기, 제한 시간 구분, 실패 로그를 검증"""

import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime

from scripts.check_runner import (
    FAILURE_LOG_RE,
    LOG_DIR_NAME,
    LOG_FILE_NAME,
    LOG_KEEP_COUNT,
    CommandResult,
    CommandSpec,
    describe_failure,
    failure_log_name,
    format_exit_code,
    format_failure,
    plan_shards,
    prune_failure_logs,
    render_log,
    resolve_log_path,
    run_command,
    save_run_logs,
    shard_command,
    write_log,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_SLOW_MODULE = "import time\nimport unittest\n\nclass SlowTest(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(3)\n"
_FAST_MODULE = "import unittest\n\nclass FastTest(unittest.TestCase):\n    def test_fast(self):\n        self.assertTrue(True)\n"


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
            paths: list[str] = []
            for name, size in (("test_a.py", 300), ("test_b.py", 200), ("test_c.py", 200), ("test_d.py", 100)):
                path = os.path.join(tmp, name)
                with open(path, "wb") as f:
                    f.write(b"x" * size)
                paths.append(path)
            shards = plan_shards(paths, 2)
        self.assertEqual(
            shards,
            [["tests.test_a", "tests.test_d"], ["tests.test_b", "tests.test_c"]],
        )


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
        for name, body in (("slow_probe.py", _SLOW_MODULE), ("fast_probe.py", _FAST_MODULE)):
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


if __name__ == "__main__":
    unittest.main()
