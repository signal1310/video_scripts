"""네이티브 크래시(faulthandler) 로그 파일 준비와 세션 머리글 모듈

- 프로세스 수명 동안 닫지 않는 추가 모드 파일 객체를 만들어 치명 신호에서도 스택이 남게 함
- 시작 때 상한을 넘긴 로그를 줄이고, 이번 실행이 시작한 바이트 위치를 표식에 남겨 다음 시작이 새 구간만 인용
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
from collections.abc import Callable
from datetime import datetime
from typing import TextIO

from src.core.diagnostics.paths import (
    NATIVE_LOG_MAX_BYTES,
    NATIVE_LOG_NAME,
    diagnostics_file,
    ensure_diagnostics_dir,
    file_size,
    trim_file_to_tail,
)

SESSION_HEADER_RULE: str = "=" * 72
SESSION_HEADER_TEMPLATE: str = "=== 세션 시작 {stamp} (pid {pid}) ==="
STACK_DUMP_HEADER: str = "--- 치명 신호 스택 (faulthandler) ---"


class NativeLogSession:
    """이번 실행의 faulthandler 출력 파일과 세션 시작 위치 보관"""

    def __init__(self, path: str, offset: int, handle: TextIO | None = None) -> None:
        self._path = path
        self._offset = offset
        # - faulthandler는 파일 기술자만 쓰므로, 파일 객체 참조를 여기서 유지해야 닫히지 않음
        self._handle = handle

    @property
    def path(self) -> str:
        """열린 네이티브 로그 파일 경로"""
        return self._path

    @property
    def offset(self) -> int:
        """이번 세션 머리글이 시작한 파일 내 바이트 위치"""
        return self._offset

    @property
    def is_open(self) -> bool:
        """faulthandler 출력 파일 객체가 아직 열려 있는지"""
        return self._handle is not None and not self._handle.closed

    def close(self, disable: Callable[[], None] | None = None) -> None:
        """faulthandler 연결을 끊고 파일 객체를 닫음. 이미 닫혔으면 무시"""
        disabler = disable or faulthandler.disable
        with contextlib.suppress(RuntimeError, OSError, ValueError):
            disabler()
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            self._handle.close()
        self._handle = None


def build_session_header(stamp: datetime, pid: int, environment: list[str]) -> str:
    """세션 시작 시각, 프로세스, 실행 환경을 담은 머리글 문자열 생성"""
    lines = [SESSION_HEADER_TEMPLATE.format(stamp=stamp.strftime("%Y-%m-%d %H:%M:%S"), pid=pid)]
    lines.extend(f"- {line}" for line in environment)
    lines.append(STACK_DUMP_HEADER)
    lines.append(SESSION_HEADER_RULE)
    return "\n".join(lines) + "\n"


def open_native_log(
    path: str,
    *,
    environment: list[str],
    pid: int,
    now: datetime | None = None,
    enable: Callable[[TextIO], object] | None = None,
) -> NativeLogSession | None:
    """상한을 넘긴 로그를 줄인 뒤 세션 머리글을 덧붙이고 faulthandler를 연결

    - 파일 객체를 반환 값에 담아 참조가 끊기지 않게 유지
    - 파일을 열지 못하면 faulthandler를 켜지 않고 None 반환(앱 시작은 계속)
    """
    parent = os.path.dirname(path)
    if parent:
        ensure_diagnostics_dir(parent)
    trim_file_to_tail(path, NATIVE_LOG_MAX_BYTES)
    offset = file_size(path)
    try:
        # - 프로세스 수명 동안 닫지 않아야 하므로 with 없이 열고 NativeLogSession이 소유
        handle = os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND), "a", encoding="utf-8", buffering=1
        )
    except OSError:
        return None
    header = build_session_header(now or datetime.now(), pid, environment)
    try:
        handle.write(header)
        handle.flush()
        (enable or faulthandler.enable)(handle)
    except (OSError, ValueError, RuntimeError):
        # - 머리글을 남기지 못했거나 faulthandler를 켜지 못해도 앱 시작은 계속
        with contextlib.suppress(OSError):
            handle.close()
        return None
    return NativeLogSession(path, offset, handle)


def default_native_log_path() -> str:
    """데이터 폴더 아래 네이티브 로그 파일 경로 반환"""
    return diagnostics_file(NATIVE_LOG_NAME)
