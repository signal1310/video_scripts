"""선택 영상 미리 보기 요청을 내장 창 또는 QuickLook으로 전달하고 표 포커스와 창 영상을 맞추는 컨트롤러 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.ui_state import PREVIEW_BACKEND_QUICKLOOK
from src.gui.widgets.quicklook_launcher import open_with_quicklook, warm_up_quicklook
from src.gui.widgets.video_preview_window import VideoPreviewWindow
from src.gui.widgets.video_table_view import VideoTableView

_PREVIEW_TITLE: str = "미리 보기"
_NO_SELECTION_TEXT: str = "영상을 먼저 선택해 주세요."
_QUICKLOOK_MISSING_TEXT: str = (
    "QuickLook을 찾을 수 없습니다. QuickLook을 설치한 뒤 다시 시도해 주세요."
)


class PreviewController:
    """선택 영상 미리 보기 요청을 설정된 방식에 따라 처리하고 내장 창 수명, 표와의 동기화를 관리"""

    def __init__(
        self,
        parent: QWidget,
        table_view: VideoTableView,
        get_backend: Callable[[], str],
        get_seek_seconds: Callable[[], int],
        get_audio: Callable[[], tuple[int, bool]],
        set_audio: Callable[[int, bool], None],
        get_shortcuts: Callable[[], Mapping[str, str]],
        forward_key: Callable[[str], bool],
        set_status: Callable[[str], None],
    ) -> None:
        self._get_shortcuts = get_shortcuts
        self._forward_key = forward_key
        self._parent = parent
        self._table_view = table_view
        self._get_backend = get_backend
        self._get_seek_seconds = get_seek_seconds
        self._get_audio = get_audio
        self._set_audio = set_audio
        self._set_status = set_status
        self._window: VideoPreviewWindow | None = None

    @property
    def builtin_window(self) -> VideoPreviewWindow | None:
        """내장 창 인스턴스, 한 번도 열지 않았으면 None"""
        return self._window

    def warm_up(self) -> None:
        """QuickLook 방식일 때 QuickLook을 미리 상주시켜 첫 미리 보기의 로딩 지연을 줄임"""
        if self._get_backend() == PREVIEW_BACKEND_QUICKLOOK:
            warm_up_quicklook()

    def preview_selected(self) -> None:
        """내장 창이 보이면 닫고, 아니면 선택한 영상 가운데 가장 위의 한 편을 설정된 방식으로 미리 봄"""
        window = self._window
        if window is not None and window.isVisible():
            window.hide()
            return
        row = self._table_view.topmost_selected_video_row()
        if row is None:
            self._set_status(_NO_SELECTION_TEXT)
            return
        self._table_view.set_current_row(row)
        path = self._table_view.video_path_at_row(row)
        if path is None:
            self._set_status(_NO_SELECTION_TEXT)
            return
        if self._get_backend() == PREVIEW_BACKEND_QUICKLOOK:
            self._preview_with_quicklook(path)
        else:
            self._preview_builtin(path)

    def on_table_current_changed(self) -> None:
        """내장 창이 보이는 동안 표의 포커스 행이 바뀌면 그 영상으로 교체"""
        window = self._window
        if window is None or not window.isVisible():
            return
        path = self._table_view.current_video_path()
        if path is not None and path != window.current_path:
            window.load_path(path)

    def on_step_requested(self, step: int) -> None:
        """내장 창의 위아래 방향키를 표의 이전, 다음 행 이동으로 전달, 영상 교체는 표 포커스 변경 신호가 담당"""
        self._table_view.move_current_row(step)

    def _preview_builtin(self, path: str) -> None:
        """내장 창을 처음 요청할 때만 만들고 선택한 영상을 재생"""
        if self._window is None:
            self._window = VideoPreviewWindow(
                self._parent,
                self._get_seek_seconds,
                self._get_audio,
                self._set_audio,
                self._get_shortcuts,
                self._forward_key,
            )
            self._window.step_requested.connect(self.on_step_requested)
        self._window.show_path(path)

    def _preview_with_quicklook(self, filepath: str) -> None:
        """QuickLook에 영상을 넘기고, 요청에 실패하면 설치 안내를 표시"""
        if not open_with_quicklook(filepath):
            QMessageBox.warning(self._parent, _PREVIEW_TITLE, _QUICKLOOK_MISSING_TEXT)
