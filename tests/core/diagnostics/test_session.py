"""실행 표식 기록, 관대한 해석, git 짧은 해시 직접 읽기 검증"""

import json
import os
import tempfile
import unittest
from datetime import datetime

from src.core.diagnostics.session import (
    STATE_CLEAN,
    STATE_RUNNING,
    SessionInfo,
    close_session,
    environment_lines,
    git_short_hash,
    read_session,
    session_path,
    start_session,
    with_native_log_offset,
    write_session,
)


class TestSessionMarker(unittest.TestCase):
    """표식 생성, 기록, 해석, 정상 종료 표시 검증"""

    def test_start_and_close_session_round_trip(self) -> None:
        """진행 중 표식을 기록한 뒤 정상 종료 표식으로 다시 쓰는 흐름을 검증"""
        when = datetime(2026, 5, 6, 7, 8, 9)
        with tempfile.TemporaryDirectory() as tmp:
            path = session_path(tmp)
            self.assertEqual(path, os.path.join(tmp, "session.json"))
            started = start_session(False, now=when)
            self.assertEqual(started.state, STATE_RUNNING)
            self.assertFalse(started.is_clean)
            self.assertEqual(started.started_at, "2026-05-06 07:08:09")
            write_session(path, started)
            loaded = read_session(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.pid, started.pid)
            self.assertEqual(loaded.started_at, started.started_at)

            closed = close_session(loaded, now=when)
            write_session(path, closed)
            final = read_session(path)
            self.assertIsNotNone(final)
            assert final is not None
            self.assertTrue(final.is_clean)
            self.assertEqual(final.state, STATE_CLEAN)
            self.assertEqual(final.ended_at, "2026-05-06 07:08:09")

    def test_read_session_tolerates_missing_keys_and_broken_json(self) -> None:
        """옛 표식의 빠진 키를 기본값으로 채우고 깨진 JSON은 None으로 처리하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"pid": "잘못된 값"}, handle)
            loaded = read_session(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.pid, 0)
            self.assertEqual(loaded.state, STATE_RUNNING)
            self.assertFalse(loaded.developer_mode)
            self.assertEqual(loaded.native_log_offset, 0)

            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{깨진")
            self.assertIsNone(read_session(path))
            self.assertIsNone(read_session(os.path.join(tmp, "none.json")))

            with open(path, "w", encoding="utf-8") as handle:
                json.dump([1, 2, 3], handle)
            self.assertIsNone(read_session(path))

    def test_with_native_log_offset_keeps_other_fields(self) -> None:
        """오프셋을 붙인 사본이 나머지 표식 값을 그대로 두는지 검증"""
        info = SessionInfo(
            pid=10, started_at="시작", version="1.2.3", developer_mode=True, state=STATE_RUNNING
        )
        updated = with_native_log_offset(info, -5)
        self.assertEqual(updated.native_log_offset, 0)
        self.assertEqual(updated.pid, 10)
        self.assertTrue(updated.developer_mode)
        self.assertEqual(updated.state, STATE_RUNNING)
        self.assertEqual(updated.to_dict()["version"], "1.2.3")


class TestGitShortHash(unittest.TestCase):
    """외부 프로세스 없이 저장소 파일만으로 커밋 해시를 읽는지 검증"""

    def test_reads_loose_ref(self) -> None:
        """`.git/HEAD`가 가리키는 느슨한 참조 파일에서 짧은 해시를 읽는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = os.path.join(tmp, ".git")
            os.makedirs(os.path.join(git_dir, "refs", "heads"))
            with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as handle:
                handle.write("ref: refs/heads/main\n")
            ref_path = os.path.join(git_dir, "refs", "heads", "main")
            with open(ref_path, "w", encoding="utf-8") as handle:
                handle.write("abcdef1234567890\n")
            self.assertEqual(git_short_hash(tmp), "abcdef1")

    def test_reads_detached_head_and_packed_refs(self) -> None:
        """떼어낸 HEAD 해시와 packed-refs 항목을 읽는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = os.path.join(tmp, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as handle:
                handle.write("1234567890abcdef\n")
            self.assertEqual(git_short_hash(tmp), "1234567")

        with tempfile.TemporaryDirectory() as tmp:
            git_dir = os.path.join(tmp, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as handle:
                handle.write("ref: refs/heads/main\n")
            with open(os.path.join(git_dir, "packed-refs"), "w", encoding="utf-8") as handle:
                handle.write("# 주석\nfedcba9876543210 refs/heads/main\n")
            self.assertEqual(git_short_hash(tmp), "fedcba9")

    def test_reads_worktree_git_dir_file_and_missing_repo(self) -> None:
        """`.git` 파일이 가리키는 워크트리 디렉터리와 저장소 아님을 구분하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real-git")
            os.makedirs(real)
            with open(os.path.join(real, "HEAD"), "w", encoding="utf-8") as handle:
                handle.write("9876543210abcdef\n")
            work = os.path.join(tmp, "worktree")
            os.makedirs(work)
            with open(os.path.join(work, ".git"), "w", encoding="utf-8") as handle:
                handle.write(f"gitdir: {real}\n")
            self.assertEqual(git_short_hash(work), "9876543")

            empty = os.path.join(tmp, "empty")
            os.makedirs(empty)
            self.assertEqual(git_short_hash(empty), "")

    def test_environment_lines_include_version_and_platform(self) -> None:
        """환경 정보 줄에 앱 버전, Python, 운영체제, 명령줄이 포함되는지 검증"""
        lines = environment_lines()
        self.assertTrue(any(line.startswith("앱 버전: ") for line in lines))
        self.assertTrue(any(line.startswith("Python: ") for line in lines))
        self.assertTrue(any(line.startswith("운영체제: ") for line in lines))
        self.assertTrue(any(line.startswith("커밋: ") for line in lines))
        self.assertTrue(any(line.startswith("명령줄: ") for line in lines))


if __name__ == "__main__":
    unittest.main()
