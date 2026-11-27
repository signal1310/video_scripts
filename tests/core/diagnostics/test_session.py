"""실행 표식 기록, 관대한 해석, 프로세스 생존 확인, git 짧은 해시 직접 읽기 검증"""

import ctypes
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from src.core.diagnostics import session as session_module
from src.core.diagnostics.session import (
    ERROR_ACCESS_DENIED,
    STATE_CLEAN,
    STATE_RUNNING,
    STILL_ACTIVE_EXIT_CODE,
    SessionInfo,
    close_session,
    environment_lines,
    existing_session_markers,
    git_short_hash,
    instance_session_path,
    is_pid_alive,
    read_session,
    remove_session_marker,
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
        updated = with_native_log_offset(info, -5, native_log_name="native_crash-10.log")
        self.assertEqual(updated.native_log_offset, 0)
        self.assertEqual(updated.pid, 10)
        self.assertTrue(updated.developer_mode)
        self.assertEqual(updated.state, STATE_RUNNING)
        self.assertEqual(updated.native_log_name, "native_crash-10.log")
        self.assertEqual(updated.to_dict()["version"], "1.2.3")


class TestInstanceMarkers(unittest.TestCase):
    """인스턴스별 표식 경로, 목록, 정리와 옛 단일 표식 호환 검증"""

    def test_instance_path_is_keyed_by_pid(self) -> None:
        """인스턴스 표식 경로가 pid를 파일 이름에 넣는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(instance_session_path(42, tmp), os.path.join(tmp, "session-42.json"))

    def test_existing_markers_cover_instances_and_legacy_file(self) -> None:
        """인스턴스 표식과 옛 단일 표식을 함께 찾고 다른 파일은 무시하는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(existing_session_markers(tmp), [])
            for pid in (2, 1):
                write_session(instance_session_path(pid, tmp), start_session(False, pid=pid))
            write_session(session_path(tmp), start_session(False, pid=9))
            for name in ("session-notes.txt", "session-notes.json", "session-.json", "session-x.json"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write('{"pid": 616161, "state": "running"}')
            found = existing_session_markers(tmp)
        self.assertEqual(
            [os.path.basename(path) for path in found],
            ["session-1.json", "session-2.json", "session.json"],
        )

    def test_remove_marker_tolerates_missing_file(self) -> None:
        """없는 표식을 지워도 예외가 없는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            remove_session_marker(os.path.join(tmp, "session-404.json"))

    def test_native_log_name_defaults_to_shared_log(self) -> None:
        """네이티브 로그 이름이 없는 옛 표식은 공용 로그 이름을 쓰는지 검증"""
        legacy = SessionInfo(
            pid=5, started_at="시작", version="1.0", developer_mode=False, state=STATE_RUNNING
        )
        self.assertEqual(legacy.native_log_file, "native_crash.log")
        closed = close_session(legacy)
        self.assertEqual(closed.native_log_file, "native_crash.log")


class TestPidLiveness(unittest.TestCase):
    """프로세스 생존 확인이 플랫폼별 실패를 구분하는지 검증"""

    def test_current_process_is_alive(self) -> None:
        """이 프로세스의 pid는 살아 있는 것으로 판단하는지 검증"""
        self.assertTrue(is_pid_alive(os.getpid()))
        self.assertFalse(is_pid_alive(0))
        self.assertFalse(is_pid_alive(-1))

    def test_windows_branch_uses_win32_lookup(self) -> None:
        """Windows에서는 Win32 조회 함수로 판단하는지 검증"""
        with (
            patch.object(session_module, "IS_WINDOWS", True),
            patch.object(session_module, "_is_pid_alive_windows", return_value=True) as lookup,
        ):
            self.assertTrue(is_pid_alive(4321))
        lookup.assert_called_once_with(4321)

    def test_posix_branch_maps_signals(self) -> None:
        """신호 0번 결과를 생존 여부로 매핑하는지 검증"""
        with patch.object(session_module, "IS_WINDOWS", False):
            with patch.object(session_module.os, "kill", side_effect=ProcessLookupError):
                self.assertFalse(is_pid_alive(99))
            with patch.object(session_module.os, "kill", side_effect=PermissionError):
                self.assertTrue(is_pid_alive(99))
            with patch.object(session_module.os, "kill", side_effect=OSError("실패")):
                self.assertFalse(is_pid_alive(99))
            with patch.object(session_module.os, "kill") as kill:
                self.assertTrue(is_pid_alive(99))
        kill.assert_called_once_with(99, 0)

    def test_own_process_is_alive_and_an_impossible_pid_is_not(self) -> None:
        """실제 OS 조회로 자기 자신은 살아 있고 존재할 수 없는 pid는 죽은 것으로 판단하는지 검증"""
        self.assertTrue(session_module.is_pid_alive(os.getpid()))
        self.assertFalse(session_module.is_pid_alive(4_000_000))

    def test_win32_signatures_are_declared_with_handle_sized_types(self) -> None:
        """핸들 반환 형식과 인자 형식을 선언하고, 선언할 수 없는 대역에서는 조용히 건너뛰는지 검증"""
        fake = SimpleNamespace(
            OpenProcess=SimpleNamespace(),
            GetExitCodeProcess=SimpleNamespace(),
            CloseHandle=SimpleNamespace(),
        )
        session_module._declare_win32_signatures(fake)
        self.assertIs(fake.OpenProcess.restype, session_module.wintypes.HANDLE)
        self.assertEqual(len(fake.OpenProcess.argtypes), 3)
        self.assertEqual(len(fake.CloseHandle.argtypes), 1)
        session_module._declare_win32_signatures(SimpleNamespace())

    def test_windows_lookup_reads_exit_code(self) -> None:
        """Win32 조회가 종료 코드와 열기 실패 사유를 구분하는지 검증"""
        active = _FakeKernel32(STILL_ACTIVE_EXIT_CODE)
        with patch.object(session_module.ctypes, "WinDLL", return_value=active):
            self.assertTrue(session_module._is_pid_alive_windows(10))
        self.assertTrue(active.closed)

        exited = _FakeKernel32(1)
        with patch.object(session_module.ctypes, "WinDLL", return_value=exited):
            self.assertFalse(session_module._is_pid_alive_windows(10))

        denied = _FakeKernel32(0, open_error=ERROR_ACCESS_DENIED)
        with patch.object(session_module.ctypes, "WinDLL", return_value=denied):
            self.assertTrue(session_module._is_pid_alive_windows(10))

        missing = _FakeKernel32(0, open_error=2)
        with patch.object(session_module.ctypes, "WinDLL", return_value=missing):
            self.assertFalse(session_module._is_pid_alive_windows(10))

        broken = _FakeKernel32(STILL_ACTIVE_EXIT_CODE, exit_code_ok=False)
        with patch.object(session_module.ctypes, "WinDLL", return_value=broken):
            self.assertFalse(session_module._is_pid_alive_windows(10))


class _FakeKernel32:
    """kernel32 API 대역. OpenProcess와 GetExitCodeProcess 결과를 지정"""

    def __init__(self, exit_code: int, *, open_error: int = 0, exit_code_ok: bool = True) -> None:
        self._exit_code = exit_code
        self._open_error = open_error
        self._exit_code_ok = exit_code_ok
        self.closed = False

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        """열기 성공(가짜 핸들) 또는 실패(0)를 돌려주고 마지막 오류를 설정"""
        if self._open_error:
            ctypes.set_last_error(self._open_error)
            return 0
        return 1

    def GetExitCodeProcess(self, handle: int, code: Any) -> int:
        """종료 코드 기록 여부를 돌려줌"""
        if not self._exit_code_ok:
            return 0
        code._obj.value = self._exit_code
        return 1

    def CloseHandle(self, handle: int) -> int:
        """핸들 닫힘 표시"""
        self.closed = True
        return 1


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
