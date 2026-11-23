"""선택 영상 미리 보기 요청을 내장 창으로 전달하고 표 포커스, 창 영상, 미리 보기 설정을 맞추는 컨트롤러 모듈"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QWidget

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.settings.ui_state import UiState
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.dialogs.frame_compare_worker import FrameExtractor
from src.gui.preview.preview_info_items import PreviewVideoInfo
from src.gui.preview.preview_settings_panel import PreviewSettingsPanel, PreviewSettingsPopup
from src.gui.preview.video_preview_window import VideoPreviewWindow
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
        self._build_compare_session: Callable[[str, float], CompareSession] | None = None
        self._compare_extractor: FrameExtractor | None = None

    @property
    def builtin_window(self) -> VideoPreviewWindow | None:
        """내장 창 인스턴스, 한 번도 열지 않았으면 None"""
        return self._window

    def sync_shortcuts(self, shortcuts: Mapping[str, str]) -> None:
        """단축키 변경을 열린 내장 미리 보기 창에 전달, 창이 없으면 아무 것도 하지 않음"""
        if self._window is not None:
            self._window.sync_shortcuts(shortcuts)

    def attach_compare(
        self, build_session: Callable[[str, float], CompareSession], extractor: FrameExtractor
    ) -> None:
        """비교 세션 생성기와 프레임 추출기를 연결, 이미 열린 창에도 바로 적용"""
        self._build_compare_session = build_session
        self._compare_extractor = extractor
        if self._window is not None:
            self._window.set_compare_extractor(extractor)

    def open_compare(
        self, path: str, position_sec: float = 0.0, *, auto_play: bool = False
    ) -> None:
        """같은 창 안에서 화질 비교 모드로 엶, 같은 시점과 재생 상태를 이어받음

        - 확인 창에서 띄운 미리 보기 창이면 그 창의 자식으로 옮겨 모달 동안에도 조작하게 함
        """
        if not path or self._build_compare_session is None:
            return
        session = self._build_compare_session(path, position_sec)
        window = self._ensure_window()
        owner = self._dialog_owner
        if window.parentWidget() is not owner:
            window.setParent(owner, window.windowFlags())
        window.show_compare_session(session, auto_play=auto_play)
        window.raise_()
        window.activateWindow()
        self.refresh_video_info()

    def toggle_compare(self) -> None:
        """지금 창이 보이면 비교 모드와 일반 모드를 오가고, 없으면 표의 선택 영상을 엶"""
        window = self._window
        if window is not None and window.isVisible():
            if window.compare_mode:
                window.leave_compare()
                return
            path = window.current_path
            if path:
                self.open_compare(path, window.position_sec, auto_play=window.is_playing)
            return
        row = self._table_view.topmost_selected_video_row()
        path = None if row is None else self._table_view.video_path_at_row(row)
        if path:
            self.open_compare(path)

    def _on_compare_requested(self, path: str, position_sec: float) -> None:
        """미리 보기 창의 화질 비교 버튼과 전환 단축키 요청을 같은 창의 비교 모드로 넘김"""
        if not path:
            return
        was_playing = self._window is not None and self._window.is_playing
        self.open_compare(path, position_sec, auto_play=was_playing)

    def _on_fullscreen_changed(self, fullscreen: bool) -> None:
        """미리 보기 창의 전체 화면 여부를 UI 상태에 저장 예약, 재시작 뒤에도 같은 상태로 엶"""
        self._get_ui().preview_fullscreen = fullscreen
        self._persist()

    def _on_always_on_top_changed(self, pinned: bool) -> None:
        """미리 보기 창의 항상 위에 표시 여부를 UI 상태에 저장 예약, 재시작 뒤에도 같은 상태로 엶"""
        self._get_ui().preview_always_on_top = pinned
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
        """확인 창이 닫히면 비교 뷰를 멈추고 내장 창을 독립 창으로 되돌려 숨김

        - 일반 영상을 다시 읽지 않고 비교 페이지와 하단 바만 되돌린 뒤 숨김
        - 항상 맨 위 플래그를 함께 넘겨 독립 창으로 돌아간 뒤에도 다른 창 뒤로 숨지 않게 함
        """
        if self._dialog_owner is None:
            return
        self._dialog_owner = None
        self._pinned_ev = None
        window = self._window
        if window is not None:
            if window.compare_mode:
                window.reset_compare_mode()
            window.hide()
            window.setParent(None, window.windowFlags())

    def on_table_current_changed(self) -> None:
        """내장 창이 보이는 동안 표의 포커스 행이 바뀌면 그 영상으로 교체, 확인 창이 열려 있으면 고른 영상을 유지

        - 비교 모드면 새 영상의 비교 세션을 처음 시점에서 같은 뷰에 바로 올림
        """
        window = self._window
        if window is None or not window.isVisible() or self._dialog_owner is not None:
            return
        path = self._table_view.current_video_path()
        if path is None:
            self._table_view.notify_current_missing()
        elif path != window.current_path:
            if window.compare_mode:
                self.open_compare(path, 0.0)
            else:
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

    def _ensure_window(self) -> VideoPreviewWindow:
        """내장 창을 처음 요청할 때만 만들어 표와 설정 요청을 연결"""
        window = self._window
        if window is not None:
            return window
        window = VideoPreviewWindow(
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
            lambda: self._get_ui().preview_always_on_top,
            self._on_always_on_top_changed,
        )
        window.step_requested.connect(self.on_step_requested)
        window.settings_requested.connect(self.show_settings)
        window.compare_requested.connect(self._on_compare_requested)
        if self._compare_extractor is not None:
            window.set_compare_extractor(self._compare_extractor)
        self._window = window
        return window

    def _preview_builtin(self, path: str, owner: QWidget | None = None) -> None:
        """내장 창을 처음 요청할 때만 만들고 선택한 영상을 처음부터 재생

        - owner와 부모가 다르면 로드 전에 부모를 옮김(부모 변경은 창을 숨김)
        - 이미 같은 부모 아래면 다시 옮기지 않아 일반 <-> 비교 전환에서 숨김 처리가 재생을 끊지 않게 함
        """
        window = self._ensure_window()
        if window.parentWidget() is not owner:
            window.setParent(owner, window.windowFlags())
        window.show_path_at(path, 0.0, None, auto_play=True)
        self.refresh_video_info()
