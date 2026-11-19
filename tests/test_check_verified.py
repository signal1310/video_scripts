"""검사 검증 기록의 작업 트리 지문, 환경 지문, 기록 보관과 실행 흐름을 임시 저장소에서 검증"""

import contextlib
import io
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.check_runner import (
    VERIFIED_DIR_NAME,
    VERIFIED_FILE_RE,
    VERIFIED_KEEP_COUNT,
    CommandResult,
    CommandSpec,
    Fingerprint,
    can_record,
    environment_hash,
    find_verified,
    installed_packages,
    main,
    save_verified,
    working_tree_hash,
)

_NOW = datetime(2026, 11, 19, 12, 0, 0)
# - 색인 항목 시각을 현재보다 과거로 두어, 시각 복사본이 항목보다 나중에 찍히는 조건을 만듦
_PAST_MTIME_OFFSET_SEC: float = 10.0


def _git(root: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return completed.stdout.strip()


def _init_repo(root: str) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "check-test")
    _git(root, "config", "user.email", "check-test@example.invalid")
    pathlib.Path(root, "a.txt").write_text("one", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _write(root: str, name: str, text: str) -> None:
    path = pathlib.Path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class WorkingTreeHashTests(unittest.TestCase):
    """작업 트리 지문이 커밋 여부와 무관하고 내용 변화에만 반응하는지, 실제 인덱스를 건드리지 않는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        _init_repo(self.root)

    def test_hash_matches_tree_after_commit(self) -> None:
        """새 파일을 둔 상태의 지문이 커밋 뒤 HEAD 트리와 같은지 검증"""
        _write(self.root, "b.txt", "two")
        before = working_tree_hash(self.root)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "add b")
        self.assertIsNotNone(before)
        self.assertEqual(working_tree_hash(self.root), before)
        self.assertEqual(before, _git(self.root, "rev-parse", "HEAD^{tree}"))

    def test_one_byte_change_changes_hash(self) -> None:
        """추적 파일의 한 글자 변경이 지문을 바꾸는지 검증"""
        before = working_tree_hash(self.root)
        _write(self.root, "a.txt", "onE")
        self.assertNotEqual(working_tree_hash(self.root), before)

    def test_same_size_edit_with_restored_mtime_changes_hash(self) -> None:
        """색인과 수정 시각, 크기가 같아도 내용이 바뀌면 지문이 바뀌는지 검증"""
        path = pathlib.Path(self.root, "a.txt")
        past = time.time() - _PAST_MTIME_OFFSET_SEC
        os.utime(path, (past, past))
        _git(self.root, "add", "-A")
        before = working_tree_hash(self.root)
        stat = os.stat(path)
        path.write_text("onE", encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(working_tree_hash(self.root), before)

    def test_new_unignored_file_changes_and_ignored_file_does_not(self) -> None:
        """무시되지 않은 새 파일은 지문을 바꾸고 무시된 파일은 바꾸지 않는지 검증"""
        _write(self.root, ".gitignore", "ignored.txt\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "ignore rule")
        base = working_tree_hash(self.root)
        _write(self.root, "ignored.txt", "x")
        self.assertEqual(working_tree_hash(self.root), base)
        _write(self.root, "new.txt", "y")
        self.assertNotEqual(working_tree_hash(self.root), base)

    def test_real_index_is_not_changed(self) -> None:
        """지문 계산 전후 실제 인덱스 파일과 스테이징 목록이 그대로인지 검증"""
        _write(self.root, "b.txt", "two")
        _git(self.root, "add", "b.txt")
        index_path = pathlib.Path(self.root, ".git", "index")
        index_before = index_path.read_bytes()
        staged_before = _git(self.root, "diff", "--cached", "--name-only")
        working_tree_hash(self.root)
        self.assertEqual(index_path.read_bytes(), index_before)
        self.assertEqual(_git(self.root, "diff", "--cached", "--name-only"), staged_before)

    def test_outside_repository_gives_none(self) -> None:
        """Git 저장소 밖에서는 지문이 없는지 검증"""
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(working_tree_hash(plain))


class EnvironmentFingerprintTests(unittest.TestCase):
    """환경 지문이 패키지 목록과 Python 버전에만 반응하는지 검증"""

    def test_package_version_change_changes_hash(self) -> None:
        """패키지 버전이 바뀌면 지문이 달라지고, 같은 목록은 순서와 무관하게 같은지 검증"""
        base = environment_hash("3.10.11", [("PyQt6", "6.7.1"), ("pip", "24.0")])
        self.assertEqual(base, environment_hash("3.10.11", [("pip", "24.0"), ("PyQt6", "6.7.1")]))
        self.assertNotEqual(base, environment_hash("3.10.11", [("PyQt6", "6.7.2"), ("pip", "24.0")]))
        self.assertNotEqual(base, environment_hash("3.11.0", [("PyQt6", "6.7.1"), ("pip", "24.0")]))

    def test_reads_names_and_versions_from_dist_info_directories(self) -> None:
        """dist-info 디렉터리 이름에서 패키지 이름과 버전을 pip 없이 읽는지 검증"""
        with tempfile.TemporaryDirectory() as site:
            for name in ("PyQt6_Qt6-6.7.3.dist-info", "pip-24.0.dist-info", "stray.txt"):
                os.makedirs(os.path.join(site, name)) if name.endswith(".dist-info") else pathlib.Path(site, name).touch()
            self.assertEqual(installed_packages(site), [("PyQt6_Qt6", "6.7.3"), ("pip", "24.0")])

    def test_missing_site_directory_gives_empty_list(self) -> None:
        """site-packages가 없으면 빈 목록을 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(installed_packages(os.path.join(tmp, "missing")), [])


class VerifiedRecordTests(unittest.TestCase):
    """검증 기록의 저장, 일치 조회, 최근 보관, 깨진 파일 무시, 기록 조건을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.verified_dir = pathlib.Path(self._tmp.name, VERIFIED_DIR_NAME)

    def test_saved_record_matches_only_same_fingerprint(self) -> None:
        """저장한 기록은 두 지문이 같을 때만 찾아지는지 검증"""
        fp = Fingerprint(tree="t" * 40, env="e" * 64)
        save_verified(self.verified_dir, fp, 1315, _NOW)
        found = find_verified(self.verified_dir, fp)
        assert found is not None
        self.assertEqual(found.tests, 1315)
        self.assertEqual(found.verified_at, "2026-11-19 12:00:00")
        self.assertIsNone(find_verified(self.verified_dir, Fingerprint(tree="u" * 40, env="e" * 64)))

    def test_only_recent_records_are_kept(self) -> None:
        """보관 개수를 넘는 오래된 기록은 지우고 최근 기록은 남기는지 검증"""
        fingerprints = [Fingerprint(tree=f"{index:040d}", env="e" * 64) for index in range(VERIFIED_KEEP_COUNT + 2)]
        for index, fp in enumerate(fingerprints):
            save_verified(self.verified_dir, fp, index, datetime(2026, 11, 19, 12, 0, index))
        names = sorted(entry.name for entry in self.verified_dir.iterdir())
        self.assertEqual(len(names), VERIFIED_KEEP_COUNT)
        self.assertTrue(all(VERIFIED_FILE_RE.fullmatch(name) for name in names))
        self.assertIsNone(find_verified(self.verified_dir, fingerprints[0]))
        self.assertIsNotNone(find_verified(self.verified_dir, fingerprints[-1]))

    def test_broken_record_is_ignored(self) -> None:
        """깨진 기록 파일이 있어도 조회가 실패하지 않고 무시하는지 검증"""
        self.verified_dir.mkdir(parents=True)
        pathlib.Path(self.verified_dir, "verified-20261119-120000-000001.json").write_text("{bad", encoding="utf-8")
        self.assertIsNone(find_verified(self.verified_dir, Fingerprint(tree="t", env="e")))

    def test_failed_or_skipped_run_is_not_recordable(self) -> None:
        """실패나 스킵이 있는 실행은 기록 대상이 아니고, 깨끗한 통과만 기록 대상인지 검증"""
        spec = CommandSpec(label="shard 1/1", command=["python"])
        passed = CommandResult(spec=spec, returncode=0, output="Ran 2 tests\nOK", elapsed_sec=0.1, timed_out=False)
        failed = CommandResult(spec=spec, returncode=1, output="FAILED", elapsed_sec=0.1, timed_out=False)
        skipped = CommandResult(
            spec=spec, returncode=0, output="Ran 2 tests\nOK (skipped=1)", elapsed_sec=0.1, timed_out=False
        )
        self.assertTrue(can_record([passed]))
        self.assertFalse(can_record([passed, failed]))
        self.assertFalse(can_record([skipped]))


class MainVerificationFlowTests(unittest.TestCase):
    """실행 흐름: 첫 통과 기록, 같은 상태 즉시 종료, 내용 변경과 환경 변경 재검사, 강제 실행, 실패 미기록"""

    def setUp(self) -> None:
        self._repo = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo.cleanup)
        self.root = self._repo.name
        _init_repo(self.root)
        _write(self.root, "tests/test_one.py", "print('placeholder')\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "tests")
        self._logs = tempfile.TemporaryDirectory()
        self.addCleanup(self._logs.cleanup)
        self.log_path = pathlib.Path(self._logs.name, "video-scripts-check", "last.log")
        self.shard_calls: list[str] = []
        self.shard_returncode = 0
        self.packages: list[tuple[str, str]] = [("PyQt6", "6.7.1")]

    def _fake_run(self, spec: CommandSpec, cwd: str, env: dict[str, str]) -> CommandResult:
        if spec.label.startswith("shard"):
            self.shard_calls.append(spec.label)
            output = "Ran 3 tests in 0.1s\nOK" if self.shard_returncode == 0 else "FAILED (failures=1)"
            return CommandResult(spec=spec, returncode=self.shard_returncode, output=output, elapsed_sec=0.1, timed_out=False)
        return CommandResult(spec=spec, returncode=0, output="", elapsed_sec=0.0, timed_out=False)

    def _run(self, *extra: str) -> tuple[int, str]:
        fix = CommandResult(
            spec=CommandSpec(label="Ruff 자동 수정", command=["ruff"]), returncode=0, output="", elapsed_sec=0.0, timed_out=False
        )
        out = io.StringIO()
        with (
            patch("scripts.check_runner.apply_ruff_fixes", return_value=([], fix)),
            patch("scripts.check_runner.run_command", side_effect=self._fake_run),
            patch("scripts.check_runner.resolve_log_path", return_value=self.log_path),
            patch("scripts.check_runner.installed_packages", side_effect=lambda _site: list(self.packages)),
            contextlib.redirect_stdout(out),
        ):
            code = main([self.root, *extra])
        return code, out.getvalue()

    def _verified_files(self) -> list[str]:
        verified = self.log_path.parent / VERIFIED_DIR_NAME
        return sorted(entry.name for entry in verified.iterdir()) if verified.is_dir() else []

    def test_second_run_in_same_state_skips_checks(self) -> None:
        """통과한 상태를 기록하고, 같은 상태의 두 번째 실행은 검사 없이 이미 검증됨으로 끝나는지 검증"""
        code, first = self._run()
        self.assertEqual(code, 0)
        self.assertIn("검증한 트리", first)
        self.assertEqual(len(self._verified_files()), 1)
        calls = len(self.shard_calls)
        code, second = self._run()
        self.assertEqual(code, 0)
        self.assertIn("이미 검증됨", second)
        self.assertEqual(len(self.shard_calls), calls)

    def test_content_change_and_new_file_force_full_check(self) -> None:
        """추적 파일을 바꾸거나 무시되지 않은 새 파일을 두면 다시 전체 검사하는지 검증"""
        self._run()
        calls = len(self.shard_calls)
        _write(self.root, "tests/test_one.py", "print('changed')\n")
        self._run()
        self.assertGreater(len(self.shard_calls), calls)
        calls = len(self.shard_calls)
        _write(self.root, "extra.txt", "new")
        self._run()
        self.assertGreater(len(self.shard_calls), calls)

    def test_commit_without_content_change_still_skips(self) -> None:
        """스테이징만 된 상태에서 검증한 뒤 내용을 바꾸지 않고 커밋하면 이미 검증됨이 되는지 검증"""
        _write(self.root, "extra.txt", "new")
        _git(self.root, "add", "extra.txt")
        self._run()
        _git(self.root, "commit", "-q", "-m", "snapshot")
        calls = len(self.shard_calls)
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("이미 검증됨", out)
        self.assertEqual(len(self.shard_calls), calls)

    def test_package_change_forces_full_check(self) -> None:
        """패키지 목록이 바뀌면 같은 내용이어도 다시 전체 검사하는지 검증"""
        self._run()
        calls = len(self.shard_calls)
        self.packages = [("PyQt6", "6.7.2")]
        self._run()
        self.assertGreater(len(self.shard_calls), calls)

    def test_force_runs_full_check_even_with_record(self) -> None:
        """--force는 기록이 있어도 전체 검사를 하고 기록을 갱신하는지 검증"""
        self._run()
        calls = len(self.shard_calls)
        code, out = self._run("--force")
        self.assertEqual(code, 0)
        self.assertGreater(len(self.shard_calls), calls)
        self.assertNotIn("이미 검증됨", out)

    def test_failed_run_is_not_recorded(self) -> None:
        """실패한 실행은 검증 기록을 남기지 않는지 검증"""
        self.shard_returncode = 1
        code, _out = self._run()
        self.assertEqual(code, 1)
        self.assertEqual(self._verified_files(), [])


if __name__ == "__main__":
    unittest.main()
