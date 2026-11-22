"""Windows 공유 모드 파일 열기 도우미

- 읽는 동안에도 다른 프로그램이 이름 바꾸기와 삭제를 할 수 있도록 CreateFileW에 세 가지 공유를 모두 요청
- GUI 재생기(QFile)와 서비스 프레임 디코더(이진 파일 객체)가 같은 핸들 생성 규칙을 재사용
- Windows가 아니거나 열기에 실패하면 None을 돌려 호출부가 일반 열기로 대체
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes
from typing import BinaryIO

logger = logging.getLogger(__name__)

GENERIC_READ: int = 0x80000000
FILE_SHARE_READ: int = 0x00000001
FILE_SHARE_WRITE: int = 0x00000002
FILE_SHARE_DELETE: int = 0x00000004
OPEN_EXISTING: int = 3
FILE_ATTRIBUTE_NORMAL: int = 0x00000080
SHARE_MODE_ALL: int = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
# - 플랫폼 검사를 모듈 상수로 둬 정적 분석이 반대쪽 분기를 도달 불가능으로 보지 않게 함
IS_WINDOWS: bool = sys.platform == "win32"


def create_shared_read_handle(path: str) -> int | None:
    """읽기 전용이고 읽기, 쓰기, 삭제 공유를 허용하는 Windows 파일 핸들, 실패하면 None"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(path, GENERIC_READ, SHARE_MODE_ALL, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        logger.warning("공유 모드로 파일을 열지 못했습니다(오류 코드 %d)", ctypes.get_last_error())
        return None
    return int(handle)


def close_shared_handle(handle: int) -> None:
    """공유 모드 파일 핸들을 해제"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def open_shared_binary(path: str) -> BinaryIO | None:
    """삭제와 이름 바꾸기를 허용하는 핸들로 연 이진 파일 객체, Windows가 아니거나 실패하면 None"""
    if not IS_WINDOWS:
        return None
    handle = create_shared_read_handle(path)
    if handle is None:
        return None
    import msvcrt  # 다른 OS에서 임포트 오류가 나지 않도록 Windows 분기 안에서만 가져옴

    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        close_shared_handle(handle)
        logger.warning("공유 핸들을 파일 서술자로 바꾸지 못해 일반 열기로 대체")
        return None
    try:
        return os.fdopen(descriptor, "rb", closefd=True)
    except OSError:
        os.close(descriptor)
        return None
