"""선택 영상 미리 보기 요청을 내장 창으로 전달하고 표 포커스, 창 영상, 미리 보기 설정을 맞추는 컨트롤러 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QWidget

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.settings.ui_state import UiState
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.preview.video_preview_window import PREVIEW_WINDOW_FLAGS, VideoPreviewWindow
from src.gui.table.video_table_view import VideoTableView

_NO_SELECTION_TEXT: str = "영상을 먼저 선택해 주세요."
_SETTINGS_POPUP_GAP_PX: int = 8


class PreviewController:
    """선택 영상 미리 보기 요청을 내장 창으로 처리하고 창 수명, 표와의 동기화, 설정 팝업을 관리"""

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
        # - 재평가로 표 모델이 초기화되면 포커스 행이 사라지므로, 보던 영상을 파일명으로 다시 찾기 위해 기억
        self._info_filename: str | None = None
        # - 확인 창에서 고른 영상과 그 확인 창. 확인 창이 열려 있는 동안 표 포커스와 무관하게 이 영상을 보여 줌
        self._pinned_ev: VideoEvaluation | None = None
        self._dialog_owner: QWidget | None = None
        self._compare_handler: Callable[[str, float], None] | None = None

    @property
    def builtin_window(self) -> VideoPreviewWindow | None:
        """내장 창 인스턴스, 한 번도 열지 않았으면 None"""
        return self._window

    @property
    def seek_seconds(self) -> int:
        """미리 보기 구간 이동 간격(초). 화질 비교 창이 같은 간격을 씀"""
        return self._get_ui().preview_seek_seconds

    @property
    def shortcuts(self) -> Mapping[str, str]:
        """지금 적용 중인 유효 단축키 사본, 화질 비교 창이 같은 설정을 씀"""
        return dict(self._get_shortcuts())

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """단축키 변경을 열린 내장 미리 보기 창에 전달, 창이 없으면 아무 것도 하지 않음"""
        if self._window is not None:
            self._window.sync_shortcuts(shortcuts)

    def get_audio(self) -> tuple[int, bool]:
        """내장 미리 보기와 화질 비교 창이 함께 쓰는 음량과 음소거 상태"""
        ui = self._get_ui()
        return ui.preview_volume, ui.preview_muted

    def save_audio(self, volume_percent: int, muted: bool) -> None:
        """화질 비교 창에서 바꾼 음량과 음소거를 미리 보기와 같은 저장 경로로 반영"""
        self._set_audio(volume_percent, muted)

    def get_playback_rate(self) -> float:
        """내장 미리 보기와 화질 비교 창이 함께 쓰는 재생 배속"""
        return self._get_ui().preview_playback_rate

    def save_playback_rate(self, rate: float) -> None:
        """화질 비교 창에서 바꾼 재생 배속을 미리 보기와 같은 저장 경로로 반영"""
        self._set_playback_rate(rate)

    def set_compare_handler(self, handler: Callable[[str, float], None]) -> None:
        """미리 보기 창의 화질 비교 버튼 요청을 받을 함수 등록"""
        self._compare_handler = handler

    def _on_compare_requested(self, path: str, position_sec: float) -> None:
        if self._compare_handler is not None:
            self._compare_handler(path, position_sec)

    def _on_fullscreen_changed(self, fullscreen: bool) -> None:
        """미리 보기 창의 전체 화면 여부를 UI 상태에 저장 예약, 재시작 뒤에도 같은 상태로 엶"""
        self._get_ui().preview_fullscreen = fullscreen
        self._persist()

    def preview_selected(self) -> None:
        """내장 창이 보이면 닫고, 아니면 선택한 영상 가운데 가장 위의 한 편을 미리 봄"""
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
        self.preview_path(path)

    def preview_path(self, path: str) -> None:
        """영상 한 편을 내장 창으로 미리 봄. 표 밖의 대화상자에서도 같은 경로를 씀"""
        self._preview_builtin(path)

    def preview_path_at(
        self,
        path: str,
        position_sec: float,
        geometry: QRect | None = None,
        *,
        was_playing: bool = True,
    ) -> None:
        """지정 시점과 창 위치로 미리 봄, 화질 비교 창에서 돌아올 때 씀

        - was_playing이 거짓이면 비교 창이 일시중지 상태였으므로 멈춘 채로 이어감
        """
        self._preview_builtin(
            path, position_sec=position_sec, geometry=geometry, auto_play=was_playing
        )

    def hide_builtin(self) -> None:
        """내장 미리 보기 창이 보이면 재생을 멈추고 숨김. 화질 비교 창으로 전환할 때 씀"""
        window = self._window
        if window is not None and window.isVisible():
            window.hide()

    def close_windows(self) -> None:
        """독립 최상위 창인 내장 미리 보기 창과 설정 팝업을 닫음. 메인 창 종료 정리에서 호출"""
        for popup in (self._window_popup, self._main_popup):
            if popup is not None:
                popup.close()
        if self._window is not None:
            self._window.close()

    def preview_for_dialog(self, ev: VideoEvaluation, path: str, owner: QWidget) -> None:
        """재인코딩 확인 창에서 고른 영상을 미리 봄. 내장 창은 확인 창의 자식으로 옮겨 모달 동안에도 조작하게 함"""
        self._pinned_ev = ev
        self._dialog_owner = owner
        self._preview_builtin(path, owner)

    def release_dialog_owner(self) -> None:
        """확인 창이 닫히면 내장 창을 다시 독립 창으로 되돌려 숨기고, 고른 영상 지정을 푼다

        - 항상 맨 위 플래그를 함께 넘겨 독립 창으로 돌아간 뒤에도 다른 창 뒤로 숨지 않게 함
        """
        if self._dialog_owner is None:
            return
        self._dialog_owner = None
        self._pinned_ev = None
        window = self._window
        if window is not None:
            window.hide()
            window.setParent(None, PREVIEW_WINDOW_FLAGS)

    def on_table_current_changed(self) -> None:
        """내장 창이 보이는 동안 표의 포커스 행이 바뀌면 그 영상으로 교체. 확인 창이 열려 있으면 고른 영상을 유지"""
        window = self._window
        if window is None or not window.isVisible() or self._dialog_owner is not None:
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
        ev = self._pinned_ev
        if ev is None:
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
        """설정 패널의 변경을 저장 예약하고 열린 창의 정보 표시를 갱신"""
        self._persist()
        if self._window is not None:
            self._window.refresh_info()

    def _preview_builtin(
        self,
        path: str,
        owner: QWidget | None = None,
        *,
        position_sec: float = 0.0,
        geometry: QRect | None = None,
        auto_play: bool = True,
    ) -> None:
        """내장 창을 처음 요청할 때만 만들고 선택한 영상을 재생. owner가 있으면 로드 전에 그 창의 자식으로 옮김

        - 부모 변경은 창을 숨기므로 로드 뒤에 하면 숨김 처리가 영상 소스를 비워 검은 화면이 됨
        - 항상 맨 위 플래그를 함께 넘겨 확인 창 아래로 옮긴 뒤에도 미리 보기가 뒤로 가리지 않게 함
        - auto_play가 거짓이면 비교 창에서 멈춘 시점 그대로 이어감
        """
        if self._window is None:
            self._window = VideoPreviewWindow(
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
            self._window.compare_requested.connect(self._on_compare_requested)
        if owner is not None:
            self._window.setParent(owner, PREVIEW_WINDOW_FLAGS)
        self._window.show_path_at(path, position_sec, geometry, auto_play=auto_play)
        self.refresh_video_info()
