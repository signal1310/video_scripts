"""실행 표식(session.json)과 실행 환경 정보 모듈

- 시작 때 표식을 남기고 정상 종료에서 닫힘 표시를 채워, 다음 시작이 비정상 종료를 알아채게 함
- 이전 실행 표식은 읽은 뒤 지우지 않고 새 표식으로 교체하므로 사후 보고서가 그대로 인용할 수 있음
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime

from src.core.app_version import APP_VERSION
from src.core.diagnostics.paths import SESSION_FILENAME, diagnostics_file
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

    @property
    def is_clean(self) -> bool:
        """정상 종료로 닫힌 표식인지 여부"""
        return self.state == STATE_CLEAN

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
        }


def session_path(directory: str | None = None) -> str:
    """실행 표식 파일 경로 반환"""
    return diagnostics_file(SESSION_FILENAME, directory)


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
    return SessionInfo(
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else 0,
        started_at=started_at if isinstance(started_at, str) else "",
        version=version if isinstance(version, str) else "",
        developer_mode=raw.get("developer_mode") is True,
        state=state if isinstance(state, str) and state else STATE_RUNNING,
        ended_at=ended_at if isinstance(ended_at, str) else "",
        native_log_offset=offset if isinstance(offset, int) and not isinstance(offset, bool) else 0,
    )


def write_session(path: str, info: SessionInfo) -> None:
    """표식 파일을 UTF-8 JSON으로 원자적 교체 기록"""
    write_utf8_file(path, json.dumps(info.to_dict(), ensure_ascii=False, indent=1) + "\n")


def start_session(developer_mode: bool, *, now: datetime | None = None) -> SessionInfo:
    """진행 중 상태의 새 표식 생성"""
    started = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return SessionInfo(
        pid=os.getpid(),
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
    )


def with_native_log_offset(info: SessionInfo, offset: int) -> SessionInfo:
    """표식에 이번 실행이 네이티브 로그에서 시작한 위치를 기록한 사본 반환"""
    return SessionInfo(
        pid=info.pid,
        started_at=info.started_at,
        version=info.version,
        developer_mode=info.developer_mode,
        state=info.state,
        ended_at=info.ended_at,
        native_log_offset=max(0, offset),
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
