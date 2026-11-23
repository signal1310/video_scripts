"""재생 중인 영상 파일을 다른 프로그램이 이름 바꾸기와 삭제할 수 있는 공유 모드로 여는 도우미

- 파일 경로를 재생기에 직접 넘기면 Windows 기본 열기가 삭제 공유를 허용하지 않아 배치 중 이동이 WinError 32로 실패함
- Windows가 아니거나 열기에 실패하면 None을 반환하고, 호출부는 기존 경로 지정 방식으로 대체
"""

from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtCore import QFile, QFileDevice, QIODevice

from src.core.file_sharing import create_shared_read_handle

logger = logging.getLogger(__name__)

# - 플랫폼 검사를 모듈 상수로 둬 정적 분석이 반대쪽 분기를 도달 불가능으로 보지 않게 함
_IS_WINDOWS: bool = sys.platform == "win32"


def open_shared_read_file(path: str) -> QFile | None:
    """Windows에서 삭제와 이름 바꾸기를 허용하는 핸들로 파일을 열어 QFile을 반환

    - 반환된 QFile은 `close()`할 때 핸들을 해제하므로, 재생기가 파일을 놓은 뒤에만 닫아야 함
    - 다른 OS이거나 열기에 실패하면 None
    """
    if not _IS_WINDOWS:
        return None
    import msvcrt  # 다른 OS에서 임포트 오류가 나지 않도록 Windows 분기 안에서만 가져옴

    handle = create_shared_read_handle(path)
    if handle is None:
        return None
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    qfile = QFile()
    if not qfile.open(fd, QIODevice.OpenModeFlag.ReadOnly, QFileDevice.FileHandleFlag.AutoCloseHandle):
        logger.warning("공유 핸들을 파일 객체로 감싸지 못해 기존 방식으로 대체")
        os.close(fd)
        return None
    return qfile
