"""테이블 행 대상 파일 실행, 탐색기 표시, 클립보드 복사, 예외 규칙 분리 동작 모듈"""

import ctypes
import os
import subprocess
from collections.abc import Sequence
from enum import Enum

from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.analysis.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation
from src.core.workspace_paths import primary_root_dir

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
    return open_media_path(resolve_video_filepath(root_dir, ev.video))


def open_media_path(filepath: str) -> bool:
    """경로의 영상 파일을 기본 플레이어로 실행하고 실행 여부 반환. 파일이 없으면 거짓

    - 표 행과 재인코딩 진행 창이 같은 동작을 쓰도록 경로 단위로 분리함
    """
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


MSG_MISSING_REFRESH: str = "선택한 영상 파일을 찾을 수 없어 목록을 다시 확인합니다."
MSG_MISSING_EXCLUDED: str = "찾을 수 없는 영상 파일 {count}개를 제외했습니다."


class RowFileState(Enum):
    """표 행의 영상 파일 상태: 행이 없음, 파일 있음, 행은 있으나 파일이 없음"""

    NO_ROW = "no_row"
    PRESENT = "present"
    MISSING = "missing"


def row_file_state(root_dir: str, ev: VideoEvaluation | None) -> tuple[RowFileState, str | None]:
    """평가 항목의 영상 파일 상태와 절대 경로(파일이 있을 때만)"""
    if ev is None or not root_dir:
        return RowFileState.NO_ROW, None
    path = resolve_video_filepath(root_dir, ev.video)
    if os.path.isfile(path):
        return RowFileState.PRESENT, path
    return RowFileState.MISSING, None


def count_missing_video_files(root_dir: str, evals: Sequence[VideoEvaluation]) -> int:
    """선택 항목 중 영상 파일이 없는 항목 수"""
    return sum(1 for ev in evals if row_file_state(root_dir, ev)[0] is RowFileState.MISSING)


def existing_video_paths(root_dir: str, evals: Sequence[VideoEvaluation]) -> list[str]:
    """선택 항목 중 파일이 있는 절대 경로 목록(선택 순서 유지, 중복 제외)"""
    paths: list[str] = []
    seen: set[str] = set()
    for ev in evals:
        _state, path = row_file_state(root_dir, ev)
        if path is None:
            continue
        norm = os.path.normpath(path)
        if norm not in seen:
            seen.add(norm)
            paths.append(norm)
    return paths


def missing_notice(missing: int, total: int) -> str | None:
    """대상 중 파일이 없는 건수에 맞는 안내 문구, 없으면 None

    - 대상 전부가 없으면 목록 확인 안내, 일부만 없으면 제외 안내
    """
    if missing <= 0:
        return None
    if missing >= total:
        return MSG_MISSING_REFRESH
    return MSG_MISSING_EXCLUDED.format(count=missing)


def confirm_bulk_play(parent: QWidget, count: int, threshold: int) -> bool:
    """대량 재생 전 확인: 임계 이하이면 묻지 않고 참을 반환"""
    if count <= threshold:
        return True
    answer = QMessageBox.question(
        parent,
        "영상 일괄 재생",
        f"선택한 {count:,}개 영상을 모두 재생합니다. 계속하시겠습니까?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def open_root_in_explorer(path_text: str) -> None:
    """작업 폴더 입력의 주 폴더를 탐색기로 엶. 폴더가 없으면 아무것도 하지 않음"""
    target = primary_root_dir(path_text)
    if target and os.path.isdir(target) and hasattr(os, "startfile"):
        os.startfile(target)
