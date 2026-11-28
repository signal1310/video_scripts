"""통합 데이터 정리 및 초기화 대화상자와 메인 창 상태를 잇는 컨트롤러

- 저장 데이터(범주 1, 2)는 서비스가 지우고, 화면 배치와 설정(범주 3, 4)은 이 컨트롤러가 메모리 상태와 config.json에 반영
- 미리보기는 저장 데이터를 서비스에서, 화면과 설정을 현재 화면 값과 AppState에서 읽어 실제 화면 상태와 맞춤
- 분석, 화질 분석, 인코딩 배치가 실행 중이면 대화상자를 열지 않고 실행 확정 때 한 번 더 확인
- 메인 창이 이 모듈을 임포트하므로 창 클래스를 직접 임포트하지 않고 프로토콜과 이름 조회로만 연결
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from src.core.config import (
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    EncodeConfig,
    ExecutionConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.core.encode.encode_progress_values import bytes_to_mb
from src.core.formatting import format_size_mb
from src.core.settings.app_state import get_state_path
from src.core.settings.state_codec import AppState
from src.core.settings.ui_state import (
    DEFAULT_ANALYZE_MODE,
    DEFAULT_THEME_MODE,
    GROUP_BY_NONE,
)
from src.gui.constants import PRESET_DEFAULT, WINDOW_DEFAULT_HEIGHT, WINDOW_DEFAULT_WIDTH
from src.gui.controllers.encode_coordinator import EncodeCoordinator
from src.gui.controllers.encode_trash import move_to_trash, needs_permanent_confirm
from src.gui.controllers.frame_analysis_controller import FrameAnalysisController
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.controllers.shortcuts_controller import ShortcutsController
from src.gui.controllers.task_runner import TaskRunner
from src.gui.dialogs.data_cleanup_dialog import DataCleanupDialog
from src.gui.menus.menu_bar import MainMenuController
from src.gui.table.column_presets import hidden_columns_for_preset, store_preset_layout
from src.gui.table.column_sizing import auto_fit_visible_columns
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_top_area import CollapsibleTopArea
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.header_bar import HeaderBar
from src.gui.window_layout import TableDisplayCoordinator
from src.service.analysis_store import open_default_store
from src.service.data_cleanup_service import (
    CLEANUP_ITEM_LABELS,
    STORAGE_CLEANUP_ITEM_KEYS,
    UI_RESET_CLEANUP_ITEM_KEYS,
    CleanupItemKey,
    CleanupItemPreview,
    CleanupPlanResult,
    CleanupScope,
    DataCleanupService,
    StoragePreviewFn,
    partition_cleanup_keys,
)
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_output_history import EncodeOutputHistory
from src.service.frame_analysis_store import FrameAnalysisStore

logger = logging.getLogger(__name__)

BUSY_TITLE: str = "데이터 정리 및 초기화"
RESULT_TITLE: str = "데이터 정리 결과"
ANALYSIS_BUSY_TEXT: str = "영상 분석이 실행 중입니다. 분석이 끝난 뒤 데이터 정리를 시작해 주세요."
FRAME_BUSY_TEXT: str = "화질 분석이 실행 중입니다. 화질 분석이 끝난 뒤 데이터 정리를 시작해 주세요."
ENCODE_BUSY_TEXT: str = "인코딩이 실행 중입니다. 인코딩이 끝난 뒤 데이터 정리를 시작해 주세요."
STORAGE_DONE_TEMPLATE: str = "기록 {records}건과 임시 파일 {size}를 정리했습니다."
UI_DONE_TEMPLATE: str = "화면 및 설정 {count}개 항목을 기본값으로 되돌렸습니다."
NOTHING_DONE_TEXT: str = "정리한 항목이 없습니다."
FAILURE_HEADER_TEXT: str = "정리하지 못한 항목이 있습니다."
FAILURE_LINE_TEMPLATE: str = "- {label}: {reason}"
SIZE_DECIMALS: int = 1
# - 메인 창이 이 모듈을 임포트하므로 창 클래스 대신 이름 상수로 비공개 구성 요소를 조회
SESSION_ATTR: str = "_session"
ENCODE_ATTR: str = "_encode"
THEME_ATTR: str = "_theme_mgr"
RE_EVALUATE_ATTR: str = "_re_evaluate_async"
SYNC_WIDGETS_ATTR: str = "_sync_widgets_from_session"
USER_RESIZED_NAME_ATTR: str = "_user_resized_name_col"
USER_RESIZED_COLS_ATTR: str = "_user_resized_cols"
# - 표시 데이터가 아니라 저장 기록과 캐시를 지우는 항목이라 실행 뒤 표와 캐시를 다시 읽어야 함
ANALYSIS_STORAGE_KEYS: frozenset[CleanupItemKey] = frozenset(
    {
        "probe_cache",
        "frame_cache",
        "manual_targets",
        "crop_exclusions",
        "crop_review_acks",
        "manual_active_frames",
        "vr_overrides",
    }
)
ENCODE_STORAGE_KEYS: frozenset[CleanupItemKey] = frozenset({"discarded_history", "encode_output_history"})


class CleanupWindowPort(Protocol):
    """정리 컨트롤러가 쓰는 메인 창의 공개 화면과 상태"""

    table_view: VideoTableView
    table_model: VideoTableModel
    proxy_model: VideoSortFilterProxyModel
    filter_bar: FilterTuningBar
    top_area: CollapsibleTopArea
    header_bar: HeaderBar
    menu: MainMenuController
    frame_analysis: FrameAnalysisController
    table_display: TableDisplayCoordinator
    shortcuts: ShortcutsController

    @property
    def task_runner(self) -> TaskRunner:
        """비동기 작업 실행 컨트롤러"""
        ...

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        """하단 상태 줄 문구 설정"""
        ...


@dataclass(frozen=True, slots=True)
class DataCleanupDeps:
    """컨트롤러가 메인 창에서 쓰는 의존성 묶음

    - 설정 상태는 설정 초기화 때 세션에서 새 인스턴스로 바뀌므로 여기에 담지 않고 `session.state`로 읽음
    """

    parent: QWidget
    session: SessionCoordinator
    table_view: VideoTableView
    table_model: VideoTableModel
    proxy_model: VideoSortFilterProxyModel
    filter_bar: FilterTuningBar
    top_area: CollapsibleTopArea
    header_bar: HeaderBar
    menu: MainMenuController
    frame_analysis: FrameAnalysisController
    encode: EncodeCoordinator
    table_display: TableDisplayCoordinator
    shortcuts: ShortcutsController
    theme: ThemeManager
    sync_widgets: Callable[[], None]
    re_evaluate: Callable[[], None]
    set_status: Callable[[str], None]
    is_analysis_busy: Callable[[], bool]


def _member(owner: object, name: str) -> Any:
    """이름으로 메인 창 구성 요소 조회. 창 모듈과의 임포트 순환을 피하려고 이름으로 받음"""
    return getattr(owner, name, None)


def _resolve_deps(window: CleanupWindowPort) -> DataCleanupDeps:
    """메인 창에서 정리 실행에 필요한 의존성을 모음"""
    session = cast("SessionCoordinator", _member(window, SESSION_ATTR))
    return DataCleanupDeps(
        parent=cast("QWidget", window),
        session=session,
        table_view=window.table_view,
        table_model=window.table_model,
        proxy_model=window.proxy_model,
        filter_bar=window.filter_bar,
        top_area=window.top_area,
        header_bar=window.header_bar,
        menu=window.menu,
        frame_analysis=window.frame_analysis,
        encode=cast("EncodeCoordinator", _member(window, ENCODE_ATTR)),
        table_display=window.table_display,
        shortcuts=window.shortcuts,
        theme=cast("ThemeManager", _member(window, THEME_ATTR)),
        sync_widgets=cast("Callable[[], None]", _member(window, SYNC_WIDGETS_ATTR)),
        re_evaluate=cast("Callable[[], None]", _member(window, RE_EVALUATE_ATTR)),
        set_status=window.set_status_text,
        is_analysis_busy=window.task_runner.is_running,
    )


def _format_size(size_bytes: int) -> str:
    """확보 용량을 단위에 맞춰 표시"""
    return format_size_mb(bytes_to_mb(size_bytes), SIZE_DECIMALS, unit="auto")


def _movable_to_trash(path: str) -> bool:
    """휴지통으로 옮겨도 영구 삭제가 되지 않는 파일이면 참. 확인할 수 없는 드라이브는 거짓

    - 정리 창은 영구 삭제 확인을 받지 않으므로 휴지통이 없는 드라이브의 임시 파일은 남김
    """
    return not needs_permanent_confirm(path)


def result_text(result: CleanupPlanResult, ui_count: int, freed_bytes: int) -> str:
    """정리 결과를 건수, 확보 용량, 초기화 항목 수, 실패 사유 순서로 안내"""
    parts: list[str] = []
    records = sum(result.cleared_counts.values())
    if records > 0 or freed_bytes > 0:
        parts.append(STORAGE_DONE_TEMPLATE.format(records=records, size=_format_size(freed_bytes)))
    if ui_count > 0:
        parts.append(UI_DONE_TEMPLATE.format(count=ui_count))
    if result.errors:
        details = "\n".join(
            FAILURE_LINE_TEMPLATE.format(label=CLEANUP_ITEM_LABELS[key], reason=reason)
            for key, reason in result.errors.items()
        )
        parts.append(f"{FAILURE_HEADER_TEXT}\n{details}")
    return "\n".join(parts) if parts else NOTHING_DONE_TEXT


class DataCleanupController:
    """데이터 정리 대화상자의 미리보기와 실행을 메인 창 상태에 연결"""

    def __init__(self, main_window: CleanupWindowPort) -> None:
        self._window: CleanupWindowPort = main_window
        self._deps_cache: DataCleanupDeps | None = None
        self._service_cache: DataCleanupService | None = None

    @property
    def _deps(self) -> DataCleanupDeps:
        """메인 창 구성 요소는 창 생성이 끝난 뒤 처음 쓸 때 모음"""
        if self._deps_cache is None:
            self._deps_cache = _resolve_deps(self._window)
        return self._deps_cache

    @contextmanager
    def _service(self) -> Generator[DataCleanupService, None, None]:
        """저장 데이터 정리 서비스

        - 연결을 오래 붙들지 않도록 호출마다 기본 저장소를 열고 닫음
        - 테스트가 `_service_cache`에 대역을 넣으면 그 대역을 그대로 넘김
        """
        if self._service_cache is not None:
            yield self._service_cache
            return
        store = open_default_store()
        frame_store = FrameAnalysisStore(store.db_path)
        history = EncodeHistory(store.db_path)
        output_history = EncodeOutputHistory(store.db_path)
        try:
            yield DataCleanupService(
                analysis_store=store,
                frame_store=frame_store,
                encode_history=history,
                output_history=output_history,
                state_files=self._state_files(),
                trash_fn=move_to_trash,
                movable_fn=_movable_to_trash,
            )
        finally:
            output_history.close()
            history.close()
            frame_store.close()
            store.close()

    def _state_files(self) -> dict[CleanupItemKey, str]:
        """화면과 설정 항목이 저장되는 config.json 경로"""
        path = get_state_path()
        return {key: path for key in UI_RESET_CLEANUP_ITEM_KEYS}

    def busy_reason(self) -> str:
        """정리를 시작할 수 없는 사유. 실행 중인 작업이 없으면 빈 문자열"""
        deps = self._deps
        if deps.is_analysis_busy():
            return ANALYSIS_BUSY_TEXT
        if deps.frame_analysis.is_running:
            return FRAME_BUSY_TEXT
        if deps.encode.is_running:
            return ENCODE_BUSY_TEXT
        return ""

    def open_dialog(self) -> None:
        """정리 대화상자를 열어 선택 항목을 실행하고, 실행 확정 때 실행 중 여부를 다시 확인"""
        deps = self._deps
        reason = self.busy_reason()
        if reason:
            QMessageBox.information(deps.parent, BUSY_TITLE, reason)
            return
        # - 작업 폴더는 위젯에서 읽어야 하므로 화면 스레드에서 한 번만 구하고, 건수 읽기는 워커에 맡김
        root_spec = self._root_spec()
        dialog = DataCleanupDialog(
            storage_preview_fn=self.storage_preview_fn(root_spec),
            ui_preview_fn=self.ui_previews,
            current_root=root_spec,
            parent=deps.parent,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        request = dialog.selected_request()
        if request is None:
            return
        reason = self.busy_reason()
        if reason:
            QMessageBox.information(deps.parent, BUSY_TITLE, reason)
            return
        text = self.execute_cleanup(request.scope, request.keys)
        QMessageBox.information(deps.parent, RESULT_TITLE, text)

    def storage_preview_fn(self, root_spec: str) -> StoragePreviewFn:
        """정리 창이 백그라운드에서 부를 저장 데이터 미리보기 함수

        - 작업 폴더 경로를 미리 받아 두어 워커가 Qt 위젯을 읽지 않게 함
        - 저장소 연결은 부르는 스레드에서 열고 닫음
        """

        def preview(scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
            return self._storage_previews(scope, root_spec)

        return preview

    def ui_previews(self) -> dict[CleanupItemKey, CleanupItemPreview]:
        """화면과 설정 항목의 미리보기. 기본값과 다른 항목만 1건으로 셈(화면 스레드에서만 호출)"""
        checks = self._ui_checks()
        return {key: CleanupItemPreview(key, 1 if checks[key]() else 0, 0, True) for key in UI_RESET_CLEANUP_ITEM_KEYS}

    def build_preview(self, scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
        """범위의 항목별 미리보기. 화면과 설정 항목은 기본값과 다른 항목만 1건으로 셈"""
        previews = self._storage_previews(scope, self._root_spec())
        previews.update(self.ui_previews())
        return previews

    def execute_cleanup(self, scope: CleanupScope, selected_keys: Sequence[CleanupItemKey]) -> str:
        """선택한 저장 데이터를 지우고 화면과 설정 항목을 기본값으로 되돌린 뒤 결과 문구 반환"""
        previews = self.build_preview(scope)
        storage_keys, ui_keys = partition_cleanup_keys(selected_keys)
        result = CleanupPlanResult({}, {})
        freed_bytes = 0
        if storage_keys:
            with self._service() as service:
                result = service.execute_storage_cleanup(storage_keys, scope, self._root_spec())
            freed_bytes = sum(previews[key].size_bytes for key in storage_keys if key in result.cleared_counts)
            self._refresh_after_storage_cleanup(storage_keys)
        resets = self._ui_resets()
        for key in ui_keys:
            resets[key]()
        if ui_keys:
            self._deps.session.persist()
        text = result_text(result, len(ui_keys), freed_bytes)
        self._deps.set_status(text.replace("\n", " "))
        return text

    def _root_spec(self) -> str:
        """현재 작업 폴더 경로 문자열"""
        deps = self._deps
        return deps.session.current_root_dir(deps.header_bar.path_edit.text())

    def _storage_previews(
        self, scope: CleanupScope, root_spec: str
    ) -> dict[CleanupItemKey, CleanupItemPreview]:
        """저장 데이터 항목의 미리보기. 범위가 현재인데 작업 폴더가 없으면 지울 것이 없는 것으로 봄

        - 작업 폴더 경로를 인자로 받아 저장 데이터 미리보기 워커가 Qt 위젯을 읽지 않게 함
        """
        try:
            with self._service() as service:
                previews = service.preview(scope, root_spec)
        except ValueError:
            logger.info("작업 폴더가 없어 현재 작업 폴더 범위의 저장 데이터 미리보기를 비웁니다")
            return {key: CleanupItemPreview(key, 0, 0, False) for key in STORAGE_CLEANUP_ITEM_KEYS}
        return {key: previews[key] for key in STORAGE_CLEANUP_ITEM_KEYS}

    def _refresh_after_storage_cleanup(self, keys: Sequence[CleanupItemKey]) -> None:
        """지운 항목에 영향받는 분석 뷰, 표, 배지, 메뉴 상태를 즉시 다시 읽음"""
        deps = self._deps
        cleared = set(keys)
        if cleared & ANALYSIS_STORAGE_KEYS:
            classifier = deps.session.classifier
            if classifier is not None:
                classifier.invalidate_cache()
            # - 지운 직접 지정이 메모리에 남거나 다음 저장 때 되살아나지 않도록 DB에서 다시 읽음
            if "manual_targets" in cleared:
                deps.session.reload_manual_targets()
            deps.session.attach_manual_targets()
            deps.frame_analysis.reload_cached()
            deps.re_evaluate()
        if cleared & ENCODE_STORAGE_KEYS:
            # - 폐기 표시는 폐기 기록을 지웠을 때만 메모리에서 비움(다른 항목만 지웠는데 표시가 사라지면 안 됨)
            if "discarded_history" in cleared:
                deps.encode.book.clear_discarded()
            deps.table_model.set_encode_book(deps.encode.book)
            deps.menu.refresh_encode_state()

    def _ui_checks(self) -> dict[CleanupItemKey, Callable[[], bool]]:
        """화면과 설정 항목별로 기본값과 다른지 확인하는 함수"""
        return {
            "column_order": self._column_order_changed,
            "column_widths": self._column_widths_changed,
            "column_visibility": self._column_visibility_changed,
            "table_sort_group": self._sort_group_changed,
            "window_geometry": self._window_geometry_changed,
            "recent_dirs_selection": self._recent_dirs_changed,
            "criteria_settings": self._criteria_changed,
            "encode_settings": self._encode_settings_changed,
            "general_display_settings": self._general_display_changed,
            "shortcuts_settings": self._shortcuts_changed,
            "exception_rules": self._exception_rules_changed,
        }

    def _ui_resets(self) -> dict[CleanupItemKey, Callable[[], None]]:
        """화면과 설정 항목별로 기본값으로 되돌리는 함수"""
        return {
            "column_order": self._reset_column_order,
            "column_widths": self._reset_column_widths,
            "column_visibility": self._reset_column_visibility,
            "table_sort_group": self._reset_sort_group,
            "window_geometry": self._reset_window_geometry,
            "recent_dirs_selection": self._reset_recent_dirs_selection,
            "criteria_settings": self._reset_criteria,
            "encode_settings": self._reset_encode_settings,
            "general_display_settings": self._reset_general_display,
            "shortcuts_settings": self._reset_shortcuts,
            "exception_rules": self._reset_exception_rules,
        }

    def _column_order_changed(self) -> bool:
        view = self._deps.table_view
        header = view.filter_header
        return any(header.visualIndex(col) != index for index, col in enumerate(view.DEFAULT_COL_ORDER))

    def _column_widths_changed(self) -> bool:
        # - 사용자가 직접 조절했거나 저장값을 복원한 열 폭만 기본값과 다른 것으로 봄
        view = self._deps.table_view
        name_resized = bool(_member(view, USER_RESIZED_NAME_ATTR))
        resized = cast("set[int] | None", _member(view, USER_RESIZED_COLS_ATTR))
        return name_resized or bool(resized)

    def _active_preset_hidden_columns(self) -> frozenset[int]:
        """활성 열 프리셋의 기본 숨김 열, 기본 프리셋은 표의 기본 숨김 열을 따름"""
        preset = self._deps.session.state.ui.layout.column_preset
        if preset == PRESET_DEFAULT:
            return self._deps.table_view.DEFAULT_HIDDEN_COLS
        return hidden_columns_for_preset(preset)

    def _column_visibility_changed(self) -> bool:
        view = self._deps.table_view
        hidden = {col for col in range(self._deps.table_model.columnCount()) if view.isColumnHidden(col)}
        return hidden != set(self._active_preset_hidden_columns())

    def _sort_group_changed(self) -> bool:
        deps = self._deps
        return (
            bool(deps.proxy_model.sort_stack)
            or deps.proxy_model.is_any_filter_active()
            or bool(deps.filter_bar.search_edit.text())
            or bool(deps.table_view.group_model.group_by)
            or bool(deps.session.state.ui.filters.collapsed_groups)
        )

    def _window_geometry_changed(self) -> bool:
        layout = self._deps.session.state.ui.layout
        return bool(layout.window_geometry) or layout.top_area_collapsed or layout.table_left_scroll > 0

    def _recent_dirs_changed(self) -> bool:
        state = self._deps.session.state
        return (
            bool(state.paths.recent_dirs)
            or bool(state.ui.layout.table_top_filename)
            or bool(state.ui.layout.table_current_filename)
        )

    def _criteria_changed(self) -> bool:
        analysis = self._deps.session.state.analysis
        return (
            analysis.thresholds != ThresholdsConfig()
            or analysis.savings != SavingsConfig()
            or analysis.codec_weights != CodecWeightsConfig()
            or analysis.bitrate_model != BitrateModelConfig()
        )

    def _encode_settings_changed(self) -> bool:
        state = self._deps.session.state
        return state.analysis.encode != EncodeConfig() or state.execution != ExecutionConfig()

    def _general_display_changed(self) -> bool:
        state = self._deps.session.state
        base = AppState()
        ui, base_ui = state.ui, base.ui
        return (
            state.display != base.display
            or state.cui != base.cui
            or ui.theme_mode != base_ui.theme_mode
            or ui.analyze_mode != base_ui.analyze_mode
            or ui.recursive_scan != base_ui.recursive_scan
            or ui.show_setting_hints != base_ui.show_setting_hints
            or ui.compact_table != base_ui.compact_table
            or ui.preview_info != base_ui.preview_info
            or ui.preview_seek_seconds != base_ui.preview_seek_seconds
            or ui.preview_volume != base_ui.preview_volume
            or ui.preview_muted != base_ui.preview_muted
            or ui.preview_playback_rate != base_ui.preview_playback_rate
            or ui.preview_fullscreen != base_ui.preview_fullscreen
            or ui.preview_always_on_top != base_ui.preview_always_on_top
            or ui.preview_background != base_ui.preview_background
            or ui.preview_frame_outline != base_ui.preview_frame_outline
            or ui.compare_top_bar_collapsed != base_ui.compare_top_bar_collapsed
        )

    def _shortcuts_changed(self) -> bool:
        return bool(self._deps.session.state.ui.shortcuts)

    def _exception_rules_changed(self) -> bool:
        ui = self._deps.session.state.ui
        return bool(ui.exception_tokens) or bool(ui.disabled_exception_tokens)

    def _reset_column_order(self) -> None:
        view = self._deps.table_view
        header = view.filter_header
        for index, col in enumerate(view.DEFAULT_COL_ORDER):
            current = header.visualIndex(col)
            if current != index:
                header.moveSection(current, index)
        self._persist_columns()

    def _reset_column_widths(self) -> None:
        deps = self._deps
        view = deps.table_view
        auto_fit_visible_columns(deps.table_model, view, compact=deps.filter_bar.is_compact())
        view.fit_name_column_to_contents(force=True)
        # - 초기화 뒤 저장값 복원이 다시 줄이지 않도록 사용자 조절 표시를 지움
        setattr(view, USER_RESIZED_NAME_ATTR, False)
        resized = cast("set[int] | None", _member(view, USER_RESIZED_COLS_ATTR))
        if resized is not None:
            resized.clear()
        self._persist_columns()

    def _reset_column_visibility(self) -> None:
        deps = self._deps
        # - 활성 프리셋의 기본 표시 열로 되돌려, 다른 프리셋 이름 아래에 기본 프리셋의 표시 열이 저장되지 않게 함
        hidden = self._active_preset_hidden_columns()
        for col in range(deps.table_model.columnCount()):
            deps.table_view.setColumnHidden(col, col in hidden)
        self._persist_columns()

    def _reset_sort_group(self) -> None:
        deps = self._deps
        deps.table_view.reset_table_sort()
        deps.table_view.group_model.expand_all_groups()
        deps.table_view.group_model.set_group_by(GROUP_BY_NONE)
        deps.filter_bar.set_row_group(GROUP_BY_NONE, label="", count=0)
        deps.proxy_model.clear_all_column_filters()
        # - 칩은 시그널 없이 끄므로 프록시의 보기 필터와 고정 행도 함께 풀어야 표가 걸러진 채 남지 않음
        deps.filter_bar.clear_chip_filters()
        deps.proxy_model.reset_chip_filters()
        deps.filter_bar.search_edit.clear()
        filters = deps.session.state.ui.filters
        filters.search = ""
        filters.columns = {}
        filters.sort_keys = []
        filters.group_by = GROUP_BY_NONE
        filters.collapsed_groups = {}

    def _reset_window_geometry(self) -> None:
        deps = self._deps
        deps.parent.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        deps.top_area.set_collapsed(False)
        layout = deps.session.state.ui.layout
        layout.window_geometry = ""
        layout.top_area_collapsed = False
        layout.table_left_scroll = 0

    def _reset_recent_dirs_selection(self) -> None:
        deps = self._deps
        deps.session.clear_recent_dirs(deps.menu, deps.header_bar.path_edit.text())
        layout = deps.session.state.ui.layout
        layout.table_top_filename = ""
        layout.table_top_row = 0
        layout.table_current_filename = ""
        store = open_default_store()
        try:
            store.clear_table_selections()
        finally:
            store.close()
        # - DB에서 지운 선택이 세션 메모리에서 되살아나 다시 저장되지 않도록 함께 비움
        deps.session.clear_saved_selections()

    def _reset_criteria(self) -> None:
        analysis = self._deps.session.state.analysis
        analysis.thresholds = ThresholdsConfig()
        analysis.savings = SavingsConfig()
        analysis.codec_weights = CodecWeightsConfig()
        analysis.bitrate_model = BitrateModelConfig()
        self._apply_config_reset()

    def _reset_encode_settings(self) -> None:
        state = self._deps.session.state
        state.analysis.encode = EncodeConfig()
        state.execution = ExecutionConfig()
        self._apply_config_reset()

    def _reset_general_display(self) -> None:
        deps = self._deps
        state = deps.session.state
        base = AppState()
        state.display = base.display
        state.cui = base.cui
        ui, base_ui = state.ui, base.ui
        ui.preview_seek_seconds = base_ui.preview_seek_seconds
        ui.preview_volume = base_ui.preview_volume
        ui.preview_muted = base_ui.preview_muted
        ui.preview_playback_rate = base_ui.preview_playback_rate
        ui.preview_fullscreen = base_ui.preview_fullscreen
        ui.preview_always_on_top = base_ui.preview_always_on_top
        ui.preview_background = base_ui.preview_background
        ui.preview_frame_outline = base_ui.preview_frame_outline
        ui.compare_top_bar_collapsed = base_ui.compare_top_bar_collapsed
        ui.preview_info = base_ui.preview_info
        ui.recursive_scan = base_ui.recursive_scan
        ui.show_setting_hints = base_ui.show_setting_hints
        ui.analyze_mode = DEFAULT_ANALYZE_MODE
        deps.table_display.set_compact(base_ui.compact_table)
        deps.theme.set_mode(DEFAULT_THEME_MODE)
        deps.sync_widgets()
        self._apply_config_reset()

    def _reset_shortcuts(self) -> None:
        deps = self._deps
        deps.session.state.ui.shortcuts = {}
        deps.shortcuts.apply_shortcuts({})

    def _reset_exception_rules(self) -> None:
        deps = self._deps
        deps.session.state.ui.exception_tokens = []
        deps.session.state.ui.disabled_exception_tokens = []
        deps.filter_bar.set_exception_tokens([])
        deps.filter_bar.set_disabled_exception_count(0)
        classifier = deps.session.classifier
        if classifier is not None:
            classifier.set_exception_tokens([])
        self._apply_config_reset()

    def _persist_columns(self) -> None:
        """바뀐 열 배치를 활성 프리셋 배치와 저장 상태에 반영하고 필터 바 라벨과 보기 메뉴를 다시 맞춤"""
        deps = self._deps
        layout = deps.session.state.ui.layout
        store_preset_layout(deps.table_view, layout, layout.column_preset)
        deps.table_display.sync_preset()

    def _apply_config_reset(self) -> None:
        """되돌린 설정을 분류기와 표에 반영하고 캐시를 다시 평가"""
        deps = self._deps
        session = deps.session
        config: ClassifierConfig = session.state.to_classifier_config()
        classifier = session.classifier
        if classifier is not None:
            classifier.set_config(config)
            classifier.set_recursive(session.state.ui.recursive_scan)
        session.sync_classifier_and_table(deps.filter_bar, deps.table_model, config)
        deps.re_evaluate()
