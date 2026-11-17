"""테이블 행 대상 파일 실행, 탐색기 표시, 클립보드 복사, 예외 규칙 분리 동작 모듈"""

import ctypes
import os
import subprocess

from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation

_COINIT_APARTMENTTHREADED: int = 0x2
_S_OK: int = 0
_S_FALSE: int = 1


def _reveal_windows_shell(norm_path: str) -> bool:
    """Windows Shell API로 탐색기를 열어 대상 파일을 직접 선택"""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    shell32 = windll.shell32
    ole32 = windll.ole32
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.ILFree.restype = None
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    pidl = shell32.ILCreateFromPathW(norm_path)
    if not pidl:
        return False
    hr_com = int(ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
    try:
        hr = int(shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0))
        return hr >= 0
    finally:
        shell32.ILFree(pidl)
        if hr_com in (_S_OK, _S_FALSE):
            ole32.CoUninitialize()


def launch_video_file(root_dir: str, ev: VideoEvaluation) -> bool:
    """영상 파일을 기본 플레이어로 실행하고 실행 여부 반환"""
    filepath = resolve_video_filepath(root_dir, ev.video)
    if os.path.isfile(filepath) and hasattr(os, "startfile"):
        os.startfile(filepath)
        return True
    return False


def reveal_video_file(root_dir: str, ev: VideoEvaluation) -> bool:
    """탐색기에서 영상 파일 위치를 선택 표시하고 실행 여부 반환"""
    filepath = resolve_video_filepath(root_dir, ev.video)
    if not os.path.isfile(filepath):
        return False
    norm_path = os.path.abspath(os.path.normpath(filepath))
    # explorer /select 명령줄은 쉼표나 특수문자가 든 경로에서 홈 폴더로 빠질 수 있어 Shell API 우선 사용
    if os.name == "nt" and _reveal_windows_shell(norm_path):
        return True
    subprocess.Popen(f'explorer.exe /select,"{norm_path}"')
    return True


def copy_lines_to_clipboard(lines: list[str]) -> int:
    """줄 목록을 줄바꿈 구분으로 클립보드에 복사하고 복사 건수 반환"""
    if not lines:
        return 0
    cb: QClipboard | None = QApplication.clipboard()
    if cb is None:
        return 0
    cb.setText("\n".join(lines))
    return len(lines)


def split_exception_stems(
    evals: list[VideoEvaluation], current_tokens: list[str]
) -> tuple[list[str], list[str]]:
    """파일명 줄기를 예외 규칙 추가 대상과 제거 대상으로 분리"""
    registered = {t.lower() for t in current_tokens}
    stems = [os.path.splitext(e.video.filename)[0] for e in evals]
    to_add = [s for s in stems if s.lower() not in registered]
    to_remove = [s for s in stems if s.lower() in registered]
    return to_add, to_remove
