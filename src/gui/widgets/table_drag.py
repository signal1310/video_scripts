"""테이블 선택 행의 외부 파일 드래그 MIME 생성 및 복사 전용 OS 커서 드래그 실행 모듈"""

import os
from collections.abc import Sequence

from PyQt6.QtCore import QMimeData, Qt, QUrl
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import QWidget

from src.core.file_manager import resolve_video_filepath
from src.core.models import VideoEvaluation


def collect_draggable_file_paths(
    root_dir: str,
    evals: Sequence[VideoEvaluation],
) -> list[str]:
    """선택된 평가 항목에서 실제로 존재하는 로컬 영상 파일 절대 경로 목록 추출

    - 미분류 파일은 원본 경로, 분류 이동된 파일은 분류 폴더 내 물리 경로를 사용
    - 동일 물리 경로 중복은 제거하고 선택 순서를 유지
    """
    if not root_dir:
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for ev in evals:
        resolved = resolve_video_filepath(root_dir, ev.video)
        norm_path = os.path.normpath(os.path.abspath(resolved))
        norm_key = os.path.normcase(norm_path)
        if norm_key in seen or not os.path.isfile(norm_path):
            continue
        seen.add(norm_key)
        paths.append(norm_path)
    return paths


def build_file_drag_mime_data(file_paths: Sequence[str]) -> QMimeData:
    """로컬 파일 경로 목록을 외부 앱 드롭용 file:// URL MIME 데이터로 구성"""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(os.path.normpath(path)) for path in file_paths])
    return mime


def start_table_file_drag(
    widget: QWidget,
    file_paths: Sequence[str],
) -> Qt.DropAction:
    """선택된 파일 경로 목록으로 복사 전용 외부 드래그 세션을 시작하고 결과를 반환

    - 별도 드래그 픽스맵 창을 띄우지 않고 OS 커서만 사용해 원격 제어 환경 지연을 방지
    - MoveAction을 제외해 탐색기 드롭 시 원본 이동과 매니페스트 불일치를 방지
    """
    if not file_paths:
        return Qt.DropAction.IgnoreAction
    drag = QDrag(widget)
    drag.setMimeData(build_file_drag_mime_data(file_paths))
    return drag.exec(Qt.DropAction.CopyAction)
