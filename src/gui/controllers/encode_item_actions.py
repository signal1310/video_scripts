"""재인코딩 진행 창의 항목 조작: 영상 열기와 미리 보기, 교체 대기 재시도와 포기, 임시 파일 위치 열기와 옮기기

- 진행 창의 ProgressMedia 인터페이스를 구현하고, 현재 배치는 호출 시점에 받음
- 확인 창과 안내는 진행 창을 부모로 함
"""

import os
from collections.abc import Callable

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.gui.controllers.encode_cleanup import LeftoverCleanup
from src.gui.controllers.encode_controller import EncodeController
from src.gui.dialogs.encode_progress_window import WINDOW_TITLE, EncodeProgressWindow
from src.gui.dialogs.encode_resume_dialogs import ask_forfeit
from src.gui.preview.preview_controller import PreviewController
from src.gui.table.table_actions import missing_notice, open_media_path, reveal_path
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import EncodeSettings
from src.service.encode.encode_reasons import PART_LEFT
from src.service.encode.encode_resume import leftover_part_path

RETRY_BUSY_TEXT: str = "재인코딩이 끝난 뒤 다시 시도할 수 있습니다."
FORFEIT_BUSY_TEXT: str = "재인코딩이 끝난 뒤 포기할 수 있습니다."
LEFTOVER_MISSING_TEXT: str = "임시 파일을 찾을 수 없습니다. 이미 지워졌거나 옮겨졌을 수 있습니다."


def notify_text(parent: QWidget, text: str) -> None:
    """진행 창 제목으로 안내 창을 띄움"""
    QMessageBox.information(parent, WINDOW_TITLE, text)


class EncodeItemActions:
    """진행 창이 항목 조작을 넘기는 대상. 현재 배치가 없으면 조작은 아무 일도 하지 않음"""

    def __init__(
        self,
        controller: EncodeController,
        history: EncodeHistory,
        cleanup: LeftoverCleanup,
        preview: PreviewController,
        window: Callable[[], EncodeProgressWindow],
        active_batch: Callable[[], tuple[int, EncodeSettings] | None],
        refresh_batch: Callable[[int], None],
        compare_files: Callable[[str], None] | None = None,
    ) -> None:
        self._controller: EncodeController = controller
        self._history: EncodeHistory = history
        self._cleanup: LeftoverCleanup = cleanup
        self._preview: PreviewController = preview
        self._window: Callable[[], EncodeProgressWindow] = window
        self._active_batch: Callable[[], tuple[int, EncodeSettings] | None] = active_batch
        self._refresh_batch: Callable[[int], None] = refresh_batch
        self._compare_files: Callable[[str], None] | None = compare_files

    def compare_files(self, source: str) -> None:
        """완료, 교체 대기 항목의 원본과 결과 파일을 화질 비교 창으로 엶"""
        if self._compare_files is not None:
            self._compare_files(source)

    def open_file(self, path: str) -> None:
        """진행 창의 더블클릭: 영상 파일을 기본 플레이어로 연다. 파일이 없으면 안내"""
        if not open_media_path(path):
            self._notify_missing()

    def preview_file(self, path: str) -> None:
        """진행 창의 스페이스바: 영상 파일을 설정된 방식으로 미리 본다. 파일이 없으면 안내

        - 미리 보기 창의 소유자를 진행 창으로 넘겨, 미리 보기 활성화가 메인 창을 끌어올리지 않게 함
        """
        if not os.path.isfile(path):
            self._notify_missing()
            return
        self._preview.preview_path(path, owner=self._window())

    def retry_replace(self, source: str) -> None:
        """교체 대기 항목 하나를 다시 배치. 재인코딩이 실행 중이거나 다른 재시도 중이면 안내"""
        batch = self._active_batch()
        if batch is None:
            return
        batch_id, settings = batch
        if not self._controller.retry_replace(batch_id, settings, source):
            self._notify(RETRY_BUSY_TEXT)

    def forfeit_replace(self, source: str) -> None:
        """교체 대기 항목을 포기해 실패로 바꿈. 확인 창에서 교체 포기를 골랐을 때만 바꿈"""
        batch = self._active_batch()
        if batch is None:
            return
        batch_id, settings = batch
        if not ask_forfeit(self._window()):
            return
        if self._controller.forfeit_replace(batch_id, settings, source):
            self._refresh_batch(batch_id)
        else:
            self._notify(FORFEIT_BUSY_TEXT)

    def reveal_leftover(self, source: str) -> bool:
        """임시 파일이 남은 행(교체 대기, 임시 파일 때문에 실패)은 그 파일을 탐색기에서 선택하고 참을 돌려줌. 그 밖의 행은 거짓"""
        batch = self._active_batch()
        if batch is None:
            return False
        row = self._history.get_item(batch[0], source)
        part = None if row is None else leftover_part_path(row)
        if part is None:
            return False
        if not reveal_path(part):
            self._notify(LEFTOVER_MISSING_TEXT)
        return True

    def trash_leftover(self, source: str) -> None:
        """같은 자리 임시 파일 때문에 실패한 행의 임시 파일을 확인 뒤 휴지통으로 옮기고 행을 다시 읽음"""
        batch = self._active_batch()
        if batch is None:
            return
        batch_id = batch[0]
        row = self._history.get_item(batch_id, source)
        part = None if row is None or row.reason != PART_LEFT else leftover_part_path(row)
        if part is None:
            return
        self._cleanup.trash_part(part)
        self._refresh_batch(batch_id)

    def _notify_missing(self) -> None:
        self._notify(missing_notice(1, 1) or "")

    def _notify(self, text: str) -> None:
        notify_text(self._window(), text)
