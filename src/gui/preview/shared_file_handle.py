"""재생 중인 영상 파일을 다른 프로그램이 이름 바꾸기와 삭제할 수 있는 공유 모드로 여는 도우미

- 파일 경로를 재생기에 직접 넘기면 Windows 기본 열기가 삭제 공유를 허용하지 않아 배치 중 이동이 WinError 32로 실패함
- Windows가 아니거나 열기에 실패하면 None을 반환하고, 호출부는 기존 경로 지정 방식으로 대체
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

from PyQt6.QtCore import QFile, QFileDevice, QIODevice

logger = logging.getLogger(__name__)

_GENERIC_READ: int = 0x80000000
_FILE_SHARE_READ: int = 0x00000001
_FILE_SHARE_WRITE: int = 0x00000002
_FILE_SHARE_DELETE: int = 0x00000004
_OPEN_EXISTING: int = 3
_FILE_ATTRIBUTE_NORMAL: int = 0x00000080
_SHARE_MODE_ALL: int = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
# - 플랫폼 검사를 모듈 상수로 둬 정적 분석이 반대쪽 분기를 도달 불가능으로 보지 않게 함
_IS_WINDOWS: bool = sys.platform == "win32"


def _create_shared_read_handle(path: str) -> int | None:
    """읽기 전용이고 읽기, 쓰기, 삭제 공유를 허용하는 Windows 파일 핸들을 만듦. 실패하면 None"""
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
    handle = create_file(path, _GENERIC_READ, _SHARE_MODE_ALL, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        logger.warning("공유 모드로 파일을 열지 못해 기존 방식으로 대체(오류 코드 %d)", ctypes.get_last_error())
        return None
    return int(handle)


def open_shared_read_file(path: str) -> QFile | None:
    """Windows에서 삭제와 이름 바꾸기를 허용하는 핸들로 파일을 열어 QFile을 반환

    - 반환된 QFile은 `close()`할 때 핸들을 해제하므로, 재생기가 파일을 놓은 뒤에만 닫아야 함
    - 다른 OS이거나 열기에 실패하면 None
    """
    if not _IS_WINDOWS:
        return None
    import msvcrt  # 다른 OS에서 임포트 오류가 나지 않도록 Windows 분기 안에서만 가져옴

    handle = _create_shared_read_handle(path)
    if handle is None:
        return None
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    qfile = QFile()
    if not qfile.open(fd, QIODevice.OpenModeFlag.ReadOnly, QFileDevice.FileHandleFlag.AutoCloseHandle):
        logger.warning("공유 핸들을 파일 객체로 감싸지 못해 기존 방식으로 대체")
        os.close(fd)
        return None
    return qfile
