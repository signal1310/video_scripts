"""테이블 행 대상 파일 실행, 탐색기 표시, 클립보드 복사, 예외 키워드 분리 동작 모듈"""

import os
import subprocess

from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation


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
    subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
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
    """파일명 줄기를 예외 키워드 추가 대상과 제거 대상으로 분리"""
    registered = {t.lower() for t in current_tokens}
    stems = [os.path.splitext(e.video.filename)[0] for e in evals]
    to_add = [s for s in stems if s.lower() not in registered]
    to_remove = [s for s in stems if s.lower() in registered]
    return to_add, to_remove
