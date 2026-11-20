"""임시 파일의 휴지통 옮기기, 영구 삭제, 드라이브 종류 확인

- 휴지통 옮기기는 QFile.moveToTrash를 쓴다. 휴지통을 확인할 수 없는 이동식과 네트워크 드라이브에서는 휴지통을 거치지 않으므로 영구 삭제 확인을 따로 받게 함
- 이 모듈은 실제 파일을 건드리므로 테스트는 QFile과 드라이브 조회 함수를 대역으로 바꿈
"""

import ctypes
import os
from collections.abc import Iterable

from PyQt6.QtCore import QFile

DRIVE_UNKNOWN: int = 0
DRIVE_REMOVABLE: int = 2
DRIVE_REMOTE: int = 4
_UNCONFIRMED_DRIVE_KINDS: frozenset[int] = frozenset({DRIVE_UNKNOWN, DRIVE_REMOVABLE, DRIVE_REMOTE})


def move_to_trash(path: str) -> bool:
    """파일을 휴지통으로 옮김. 실패하면 거짓"""
    moved, _location = QFile.moveToTrash(path)
    return bool(moved)


def delete_permanently(path: str) -> bool:
    """휴지통을 거치지 않고 파일을 지움. 실패하면 거짓"""
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def drive_kind(path: str) -> int:
    """경로가 있는 드라이브의 종류(GetDriveTypeW 값). Windows가 아니거나 조회에 실패하면 DRIVE_UNKNOWN"""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return DRIVE_UNKNOWN
    root = os.path.splitdrive(os.path.abspath(path))[0]
    if not root:
        return DRIVE_UNKNOWN
    return int(windll.kernel32.GetDriveTypeW(root + os.sep))


def needs_permanent_confirm(path: str) -> bool:
    """휴지통이 있는지 확인할 수 없는 드라이브의 파일이면 참. 이 경우 옮기기는 영구 삭제가 될 수 있음"""
    return drive_kind(path) in _UNCONFIRMED_DRIVE_KINDS


def paths_needing_permanent_confirm(paths: Iterable[str]) -> list[str]:
    """영구 삭제 확인이 필요한 경로만 골라 돌려줌(입력 순서 유지)"""
    return [path for path in paths if needs_permanent_confirm(path)]
