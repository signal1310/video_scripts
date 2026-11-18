"""선택 영상 미리 보기 요청을 내장 창 또는 QuickLook으로 전달하고 표 포커스, 창 영상, 미리 보기 설정을 맞추는 컨트롤러 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.config import ClassifierConfig
from src.core.ui_state import PREVIEW_BACKEND_QUICKLOOK, UiState
from src.gui.preview_info_items import PreviewVideoInfo
from src.gui.widgets.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.widgets.quicklook_launcher import open_with_quicklook, warm_up_quicklook
from src.gui.widgets.video_preview_window import VideoPreviewWindow
from src.gui.widgets.video_table_view import VideoTableView

_PREVIEW_TITLE: str = "미리 보기"
_NO_SELECTION_TEXT: str = "영상을 먼저 선택해 주세요."
_QUICKLOOK_MISSING_TEXT: str = (
    "QuickLook을 찾을 수 없습니다. QuickLook을 설치한 뒤 다시 시도해 주세요."
)
_SETTINGS_POPUP_GAP_PX: int = 8


class PreviewController:
    """선택 영상 미리 보기 요청을 설정된 방식에 따라 처리하고 내장 창 수명, 표와의 동기화, 설정 팝업을 관리"""

    def __init__(
        self,
        parent: QWidget,
        table_view: VideoTableView,
        get_ui: Callable[[], UiState],
        get_config: Callable[[], ClassifierConfig],
        persist: Callable[[], None],
        set_audio: Callable[[int, bool], None],
        set_playback_rate: Callable[[float], None],
        get_shortcuts: Callable[[], Mapping[str, str]],
        forward_key: Callable[[str], bool],
        set_status: Callable[[str], None],
    ) -> None:
        self._get_shortcuts = get_shortcuts
        self._forward_key = forward_key
        self._parent = parent
        self._table_view = table_view
        self._get_ui = get_ui
        self._get_config = get_config
        self._persist = persist
        self._set_audio = set_audio
        self._set_playback_rate = set_playback_rate
        self._set_status = set_status
        self._window: VideoPreviewWindow | None = None
        self._window_popup: PreviewSettingsPopup | None = None
        self._main_popup: PreviewSettingsPopup | None = None
        self._known_backend: str = get_ui().preview_backend
        # - 재평가로 표 모델이 초기화되면 포커스 행이 사라지므로, 보던 영상을 파일명으로 다시 찾기 위해 기억
        self._info_filename: str | None = None

    @property
    def builtin_window(self) -> VideoPreviewWindow | None:
        """내장 창 인스턴스, 한 번도 열지 않았으면 None"""
        return self._window

    def _on_fullscreen_changed(self, fullscreen: bool) -> None:
        """미리 보기 창의 전체 화면 여부를 UI 상태에 저장 예약, 재시작 뒤에도 같은 상태로 엶"""
        self._get_ui().preview_fullscreen = fullscreen
        self._persist()

    def set_backend(self, backend: str) -> None:
        """일반 설정 대화상자에서 고른 미리 보기 방식을 저장하고, QuickLook이면 상주시킴"""
        self._get_ui().preview_backend = backend
        self.warm_up()
        self._persist()

    def warm_up(self) -> None:
        """QuickLook 방식일 때 QuickLook을 미리 상주시켜 첫 미리 보기의 로딩 지연을 줄임"""
        if self._get_ui().preview_backend == PREVIEW_BACKEND_QUICKLOOK:
            warm_up_quicklook()

    def preview_selected(self) -> None:
        """내장 창이 보이면 닫고, 아니면 선택한 영상 가운데 가장 위의 한 편을 설정된 방식으로 미리 봄"""
        window = self._window
        if window is not None and window.isVisible():
            window.hide()
            return
        row = self._table_view.topmost_selected_video_row()
        if row is None:
            if not self._table_view.notify_missing_selected():
                self._set_status(_NO_SELECTION_TEXT)
            return
        self._table_view.set_current_row(row)
        path = self._table_view.video_path_at_row(row)
        if path is None:
            self._set_status(_NO_SELECTION_TEXT)
            return
        if self._get_ui().preview_backend == PREVIEW_BACKEND_QUICKLOOK:
            self._preview_with_quicklook(path)
        else:
            self._preview_builtin(path)

    def on_table_current_changed(self) -> None:
        """내장 창이 보이는 동안 표의 포커스 행이 바뀌면 그 영상으로 교체"""
        window = self._window
        if window is None or not window.isVisible():
            return
        path = self._table_view.current_video_path()
        if path is None:
            self._table_view.notify_current_missing()
        elif path != window.current_path:
            window.load_path(path)
        self.refresh_video_info()

    def on_step_requested(self, step: int) -> None:
        """내장 창의 위아래 방향키를 표의 이전, 다음 행 이동으로 전달, 영상 교체는 표 포커스 변경 신호가 담당"""
        self._table_view.move_current_row(step)

    def refresh_video_info(self) -> None:
        """내장 창이 보이는 동안 표의 포커스 행 평가 결과와 선택 수를 정보 표시에 전달"""
        window = self._window
        if window is None or not window.isVisible():
            return
        table = self._table_view
        ev = table.get_evaluation_at(table.currentIndex())
        if ev is not None:
            self._info_filename = ev.video.filename
        elif self._info_filename is not None:
            ev = next(
                (
                    item
                    for item in table.table_model.evaluations
                    if item.video.filename == self._info_filename
                ),
                None,
            )
        if ev is None:
            window.set_video_info(None)
            return
        model = table.table_model
        selection = table.selectionModel()
        window.set_video_info(
            PreviewVideoInfo(
                ev=ev,
                config=model.config,
                pending_tiers=model.pending_output_tiers,
                selected_count=len(selection.selectedRows()) if selection is not None else 0,
            )
        )

    def notify(self, text: str) -> None:
        """내장 창이 보이는 동안 실행 결과 문구를 영상 위에 잠깐 표시, 전체화면에서는 상태 표시줄이 가려지기 때문"""
        window = self._window
        if window is not None and window.isVisible():
            window.show_notice(text)

    def show_settings(self) -> None:
        """미리 보기 설정 팝업을 열고, 내장 창이 보이면 설정 버튼 위에, 아니면 메인 창 가운데에 띄움"""
        window = self._window
        if window is not None and window.isVisible():
            popup = self._window_popup
            if popup is None:
                popup = self._create_popup(window)
                self._window_popup = popup
            popup.adjustSize()
            button = window.settings_button_rect()
            popup.show_clamped_at(
                QPoint(
                    button.right() - popup.width(),
                    button.top() - popup.height() - _SETTINGS_POPUP_GAP_PX,
                )
            )
            return
        popup = self._main_popup
        if popup is None:
            popup = self._create_popup(self._parent)
            self._main_popup = popup
        popup.adjustSize()
        center = self._parent.mapToGlobal(self._parent.rect().center())
        popup.show_clamped_at(QPoint(center.x() - popup.width() // 2, center.y() - popup.height() // 2))

    def _create_popup(self, parent: QWidget) -> PreviewSettingsPopup:
        panel = PreviewSettingsPanel(self._get_ui, self._get_config)
        panel.changed.connect(self._on_settings_changed)
        return PreviewSettingsPopup(panel, parent)

    def _on_settings_changed(self) -> None:
        """설정 패널의 변경을 저장 예약하고 QuickLook 방식으로 바뀌면 웜업, 열린 창의 정보 표시를 갱신"""
        ui = self._get_ui()
        if ui.preview_backend != self._known_backend:
            self._known_backend = ui.preview_backend
            self.warm_up()
        self._persist()
        if self._window is not None:
            self._window.refresh_info()

    def _preview_builtin(self, path: str) -> None:
        """내장 창을 처음 요청할 때만 만들고 선택한 영상을 재생"""
        if self._window is None:
            self._window = VideoPreviewWindow(
                self._parent,
                lambda: self._get_ui().preview_seek_seconds,
                lambda: (self._get_ui().preview_volume, self._get_ui().preview_muted),
                self._set_audio,
                self._get_shortcuts,
                self._forward_key,
                lambda: self._get_ui().preview_info,
                self._persist,
                lambda: self._get_ui().preview_playback_rate,
                self._set_playback_rate,
                lambda: self._get_ui().preview_fullscreen,
                self._on_fullscreen_changed,
            )
            self._window.step_requested.connect(self.on_step_requested)
            self._window.settings_requested.connect(self.show_settings)
        self._window.show_path(path)
        self.refresh_video_info()

    def _preview_with_quicklook(self, filepath: str) -> None:
        """QuickLook에 영상을 넘기고, 요청에 실패하면 설치 안내를 표시"""
        if not open_with_quicklook(filepath):
            QMessageBox.warning(self._parent, _PREVIEW_TITLE, _QUICKLOOK_MISSING_TEXT)
