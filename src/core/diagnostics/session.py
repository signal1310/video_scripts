"""실행 표식(session-<pid>.json)과 실행 환경 정보 모듈

- 인스턴스마다 표식을 따로 남겨 동시 실행 중에도 서로 덮어쓰지 않게 함
- 시작 때 표식을 남기고 정상 종료에서 닫힘 표시를 채워, 다음 시작이 비정상 종료를 알아채게 함
- 이전 버전의 단일 `session.json`도 같은 규칙으로 읽어 사고를 놓치지 않음
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.app_version import APP_VERSION
from src.core.diagnostics.paths import (
    NATIVE_LOG_NAME,
    SESSION_FILENAME,
    SESSION_MARKER_PREFIX,
    SESSION_MARKER_SUFFIX,
    diagnostics_file,
)
from src.core.paths import get_app_base_dir, write_utf8_file

GIT_HEAD_NAME: str = "HEAD"
GIT_DIR_NAME: str = ".git"
GIT_COMMON_DIR_FILE: str = "commondir"
GIT_DIR_PREFIX: str = "gitdir:"
GIT_PACKED_REFS: str = "packed-refs"
GIT_REF_PREFIX: str = "ref:"
GIT_SHORT_HASH_LENGTH: int = 7
STATE_RUNNING: str = "running"
STATE_CLEAN: str = "clean"
# - OpenProcess 권한: 상태 조회에 필요한 최소 권한만 요청
PROCESS_QUERY_LIMITED_INFORMATION: int = 0x1000
STILL_ACTIVE_EXIT_CODE: int = 259
ERROR_ACCESS_DENIED: int = 5
IS_WINDOWS: bool = sys.platform == "win32"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """실행 표식 파일 한 건. state가 clean이 아니면 정상 종료되지 않은 실행"""

    pid: int
    started_at: str
    version: str
    developer_mode: bool
    state: str
    ended_at: str = ""
    native_log_offset: int = 0
    native_log_name: str = ""

    @property
    def is_clean(self) -> bool:
        """정상 종료로 닫힌 표식인지 여부"""
        return self.state == STATE_CLEAN

    @property
    def native_log_file(self) -> str:
        """이 실행이 쓴 네이티브 로그 파일 이름. 옛 표식은 공용 로그 이름"""
        return self.native_log_name or NATIVE_LOG_NAME

    def to_dict(self) -> dict[str, object]:
        """저장용 사전 표현"""
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "version": self.version,
            "developer_mode": self.developer_mode,
            "state": self.state,
            "ended_at": self.ended_at,
            "native_log_offset": self.native_log_offset,
            "native_log_name": self.native_log_name,
        }


def session_path(directory: str | None = None) -> str:
    """옛 단일 실행 표식 파일 경로 반환. 이전 버전이 남긴 표식을 읽을 때만 씀"""
    return diagnostics_file(SESSION_FILENAME, directory)


def instance_session_filename(pid: int) -> str:
    """인스턴스(pid)별 실행 표식 파일 이름 반환"""
    return f"{SESSION_MARKER_PREFIX}{pid}{SESSION_MARKER_SUFFIX}"


def instance_session_path(pid: int, directory: str | None = None) -> str:
    """인스턴스(pid)별 실행 표식 파일 경로 반환

    - 인스턴스마다 표식을 따로 두어 동시 실행 중에도 서로 덮어쓰지 않게 함
    """
    return diagnostics_file(instance_session_filename(pid), directory)


def existing_session_markers(directory: str) -> list[str]:
    """진단 폴더에 있는 실행 표식 파일 경로 목록 반환(옛 단일 표식 포함)

    - 인스턴스별 표식과 이전 버전이 남긴 `session.json`을 함께 읽어 사고를 놓치지 않게 함
    - session-<숫자>.json만 인정해, 이름만 비슷한 다른 파일을 표식으로 오인하지 않게 함
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = [
        os.path.join(directory, name)
        for name in names
        if _is_instance_marker_name(name)
    ]
    legacy = session_path(directory)
    if os.path.isfile(legacy):
        found.append(legacy)
    return sorted(found)


def _is_instance_marker_name(name: str) -> bool:
    """`session-<숫자>.json` 형식의 파일 이름인지 확인"""
    if not name.startswith(SESSION_MARKER_PREFIX) or not name.endswith(SESSION_MARKER_SUFFIX):
        return False
    digits = name[len(SESSION_MARKER_PREFIX) : -len(SESSION_MARKER_SUFFIX)]
    return digits.isdigit()


def remove_session_marker(path: str) -> None:
    """정상 종료로 처리한 표식 파일을 지움. 이미 없으면 무시"""
    try:
        os.remove(path)
    except FileNotFoundError:
        return


def is_pid_alive(pid: int) -> bool:
    """프로세스가 살아 있는지 확인

    - Windows는 OpenProcess와 종료 코드로, 그 밖의 플랫폼은 신호 0번 전송으로 확인
    - 접근 권한이 없어 열지 못한 프로세스는 살아 있는 것으로 봄
    """
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _is_pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _declare_win32_signatures(kernel32: Any) -> None:
    """핸들이 64비트 값이어도 잘리지 않도록 호출할 Win32 함수의 인자와 반환 형식을 선언

    - 선언할 수 없는 대역 객체에서는 건너뜀
    """
    try:
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    except (AttributeError, TypeError):
        return


def _is_pid_alive_windows(pid: int) -> bool:
    """Win32 API로 핸들을 열어 종료 코드가 STILL_ACTIVE인지 확인"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _declare_win32_signatures(kernel32)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # - 접근 거부는 존재하지만 열 수 없는 프로세스이므로 살아 있는 것으로 봄
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return bool(code.value == STILL_ACTIVE_EXIT_CODE)
    finally:
        kernel32.CloseHandle(handle)


def read_session(path: str) -> SessionInfo | None:
    """표식 파일을 읽어 SessionInfo 반환. 없거나 형식이 깨졌으면 None

    - 옛 표식에 없는 키는 기본값으로 채워 관대하게 해석
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    pid = raw.get("pid")
    started_at = raw.get("started_at")
    version = raw.get("version")
    state = raw.get("state")
    ended_at = raw.get("ended_at")
    offset = raw.get("native_log_offset")
    native_name = raw.get("native_log_name")
    return SessionInfo(
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else 0,
        started_at=started_at if isinstance(started_at, str) else "",
        version=version if isinstance(version, str) else "",
        developer_mode=raw.get("developer_mode") is True,
        state=state if isinstance(state, str) and state else STATE_RUNNING,
        ended_at=ended_at if isinstance(ended_at, str) else "",
        native_log_offset=offset if isinstance(offset, int) and not isinstance(offset, bool) else 0,
        native_log_name=native_name if isinstance(native_name, str) else "",
    )


def write_session(path: str, info: SessionInfo) -> None:
    """표식 파일을 UTF-8 JSON으로 원자적 교체 기록"""
    write_utf8_file(path, json.dumps(info.to_dict(), ensure_ascii=False, indent=1) + "\n")


def start_session(
    developer_mode: bool, *, now: datetime | None = None, pid: int | None = None
) -> SessionInfo:
    """진행 중 상태의 새 표식 생성"""
    started = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return SessionInfo(
        pid=os.getpid() if pid is None else pid,
        started_at=started,
        version=APP_VERSION,
        developer_mode=developer_mode,
        state=STATE_RUNNING,
    )


def close_session(info: SessionInfo, *, now: datetime | None = None) -> SessionInfo:
    """정상 종료 상태로 닫은 표식 반환"""
    ended = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return SessionInfo(
        pid=info.pid,
        started_at=info.started_at,
        version=info.version,
        developer_mode=info.developer_mode,
        state=STATE_CLEAN,
        ended_at=ended,
        native_log_offset=info.native_log_offset,
        native_log_name=info.native_log_name,
    )


def with_native_log_offset(
    info: SessionInfo, offset: int, *, native_log_name: str = ""
) -> SessionInfo:
    """표식에 이번 실행이 네이티브 로그에서 시작한 위치와 파일 이름을 기록한 사본 반환"""
    return SessionInfo(
        pid=info.pid,
        started_at=info.started_at,
        version=info.version,
        developer_mode=info.developer_mode,
        state=info.state,
        ended_at=info.ended_at,
        native_log_offset=max(0, offset),
        native_log_name=native_log_name or info.native_log_name,
    )


def git_short_hash(base_dir: str | None = None) -> str:
    """저장소 파일에서 짧은 커밋 해시를 직접 읽어 반환. 저장소가 아니면 빈 문자열

    - 외부 git 실행 없이 `.git/HEAD`를 읽어 시작 지연을 없앰
    - `.git`이 파일이면(워크트리, 서브모듈) `gitdir:`이 가리키는 디렉터리에서 찾음
    """
    root = base_dir or get_app_base_dir()
    git_dir = os.path.join(root, GIT_DIR_NAME)
    head_path = os.path.join(git_dir, GIT_HEAD_NAME)
    resolved_dir = _resolve_git_dir(git_dir)
    if resolved_dir is not None:
        head_path = os.path.join(resolved_dir, GIT_HEAD_NAME)
    for _ in range(2):
        head = _read_plain_text(head_path)
        if not head:
            return ""
        if head.startswith(GIT_REF_PREFIX):
            ref_name = head[len(GIT_REF_PREFIX) :].strip()
            resolved = _read_git_ref(resolved_dir or git_dir, ref_name)
            return resolved[:GIT_SHORT_HASH_LENGTH]
        if head.startswith(GIT_DIR_PREFIX):
            nested = head[len(GIT_DIR_PREFIX) :].strip()
            if not os.path.isabs(nested):
                nested = os.path.join(root, nested)
            resolved_dir = os.path.abspath(nested)
            head_path = os.path.join(resolved_dir, GIT_HEAD_NAME)
            continue
        return head[:GIT_SHORT_HASH_LENGTH]
    return ""


def _resolve_git_dir(git_dir: str) -> str | None:
    """`.git`이 파일인 워크트리의 실제 git 디렉터리 경로 반환"""
    if os.path.isdir(git_dir):
        return git_dir
    content = _read_plain_text(git_dir)
    if not content.startswith(GIT_DIR_PREFIX):
        return None
    target = content[len(GIT_DIR_PREFIX) :].strip()
    if not target:
        return None
    return os.path.abspath(target)


def _read_git_ref(git_dir: str, ref_name: str) -> str:
    """참조 이름의 커밋 해시를 느슨한 파일이나 packed-refs에서 찾아 반환"""
    loose = _read_plain_text(os.path.join(git_dir, *ref_name.split("/")))
    if loose:
        return loose
    common_dir = _read_plain_text(os.path.join(git_dir, GIT_COMMON_DIR_FILE))
    if common_dir:
        base = common_dir if os.path.isabs(common_dir) else os.path.join(git_dir, common_dir)
        loose = _read_plain_text(os.path.join(os.path.abspath(base), *ref_name.split("/")))
        if loose:
            return loose
        git_dir = os.path.abspath(base)
    # - packed-refs는 한 파일에 여러 참조가 줄 단위로 들어 있어 전체를 읽어야 함
    for line in _read_text_lines(os.path.join(git_dir, GIT_PACKED_REFS)):
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref_name:
            return parts[0]
    return ""


def _read_plain_text(path: str) -> str:
    """작은 UTF-8 텍스트 파일의 첫 줄을 공백 없이 반환. 읽지 못하면 빈 문자열"""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _read_text_lines(path: str) -> list[str]:
    """작은 UTF-8 텍스트 파일의 모든 줄을 공백 없이 반환. 읽지 못하면 빈 목록"""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [line.strip() for line in handle]
    except OSError:
        return []


def environment_lines() -> list[str]:
    """진단 머리글에 넣을 실행 환경 정보 줄 목록 반환

    - PyQt6와 Qt 버전은 이미 적재된 경우에만 읽어 임포트 지연을 만들지 않음
    """
    lines = [
        f"앱 버전: {APP_VERSION}",
        f"커밋: {git_short_hash() or '알 수 없음'}",
        f"Python: {sys.version.split()[0]} ({platform.python_implementation()})",
        f"운영체제: {platform.platform()}",
        f"명령줄: {' '.join(sys.argv) or '알 수 없음'}",
    ]
    lines.extend(_queued_qt_versions())
    return lines


def _queued_qt_versions() -> list[str]:
    """이미 임포트된 PyQt6 및 Qt 버전 문구 목록. 미적재면 빈 목록"""
    if "PyQt6.QtCore" not in sys.modules:
        return []
    from PyQt6 import QtCore

    pyqt_version = getattr(QtCore, "PYQT_VERSION_STR", "")
    qt_version = getattr(QtCore, "QT_VERSION_STR", "")
    if not pyqt_version and not qt_version:
        return []
    return [f"PyQt6: {pyqt_version or '알 수 없음'}, Qt: {qt_version or '알 수 없음'}"]
