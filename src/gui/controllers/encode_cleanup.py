"""인코딩 임시 파일 정리 흐름: 정리 창 열기, 옮기기 확인과 영구 삭제 분기, 이어 하지 않음과 실패 행의 옮기기, 작업 기록 줄

- 기록 DB와 부모 창을 받고, 화면 스레드에서만 쓴다. 판정과 옮기기 본체는 서비스 계층이 맡음
"""

import logging
import os
import time
from collections.abc import Sequence

from PyQt6.QtWidgets import QWidget

from src.core.workspace_paths import parse_root_dirs
from src.gui.controllers.encode_trash import (
    delete_permanently,
    move_to_trash,
    needs_permanent_confirm,
    paths_needing_permanent_confirm,
)
from src.gui.dialogs.encode_cleanup_window import CleanupWindow, ask_part_trash, ask_permanent
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_leftovers import (
    CleanupOutcome,
    LeftoverItem,
    discard_batch_parts,
    scan_leftovers,
    trash_leftovers,
    trash_path,
)

logger = logging.getLogger(__name__)

CLEANUP_MOVED_LOG: str = "임시 파일 휴지통 이동: {name}"
CLEANUP_DELETED_LOG: str = "임시 파일 영구 삭제: {name}"
CLEANUP_SKIPPED_LOG: str = "임시 파일 정리 건너뜀: {name}, 사유: {reason}"


def _movable_to_trash(path: str) -> bool:
    """휴지통으로 옮겨도 영구 삭제가 되지 않는 파일이면 참. 확인할 수 없는 드라이브는 거짓"""
    return not needs_permanent_confirm(path)


class LeftoverCleanup:
    """임시 파일 정리의 화면 쪽 흐름. 휴지통이 없거나 확인할 수 없는 드라이브의 파일은 영구 삭제 확인을 따로 받음"""

    def __init__(self, parent: QWidget, history: EncodeHistory) -> None:
        self._parent: QWidget = parent
        self._history: EncodeHistory = history

    def open(self, root_spec: str, output_dirname: str) -> None:
        """작업 폴더 전체의 .part를 모아 정리 창을 모달로 연다. 찾은 파일이 없어도 창은 열고 빈 상태 문구를 보임"""
        items = scan_leftovers(self._history, parse_root_dirs(root_spec), output_dirname, time.time_ns())
        CleanupWindow(items, self._trash_selected, self._parent).exec()

    def discard_batch(self, batch_id: int) -> None:
        """이어 하지 않음: 배치의 진행 중, 대기 임시 파일을 휴지통으로 옮기고 배치를 닫음"""
        for outcome in discard_batch_parts(self._history, batch_id, move_to_trash, _movable_to_trash):
            self._log(outcome, permanent=False)

    def trash_part(self, part_path: str) -> None:
        """임시 파일 때문에 실패한 항목이 쓰려던 임시 파일 하나를 확인 뒤 옮김. 영구 삭제가 될 드라이브면 영구 삭제 확인을 씀"""
        if needs_permanent_confirm(part_path):
            if ask_permanent(self._parent):
                self._log(trash_path(part_path, delete_permanently), permanent=True)
            return
        if ask_part_trash(self._parent, os.path.basename(part_path)):
            self._log(trash_path(part_path, move_to_trash), permanent=False)

    def _trash_selected(self, selected: Sequence[LeftoverItem]) -> list[CleanupOutcome]:
        """정리 창의 옮기기. 영구 삭제가 될 드라이브의 파일은 확인을 거절하면 건너뛰고 나머지만 옮김"""
        remote_paths = set(paths_needing_permanent_confirm(item.path for item in selected))
        local = [item for item in selected if item.path not in remote_paths]
        remote = [item for item in selected if item.path in remote_paths]
        if remote and not ask_permanent(self._parent):
            remote = []
        outcomes = trash_leftovers(local, move_to_trash) + trash_leftovers(remote, delete_permanently)
        for outcome in outcomes:
            self._log(outcome, permanent=outcome.path in remote_paths)
        return outcomes

    def _log(self, outcome: CleanupOutcome, *, permanent: bool) -> None:
        name = os.path.basename(outcome.path)
        if not outcome.moved:
            logger.info(CLEANUP_SKIPPED_LOG.format(name=name, reason=outcome.reason))
        elif permanent:
            logger.info(CLEANUP_DELETED_LOG.format(name=name))
        else:
            logger.info(CLEANUP_MOVED_LOG.format(name=name))
