"""데이터 정리 컨트롤러의 실행 중 차단, 미리보기, 범주별 초기화와 결과 문구 검증

- 메인 창 구성 요소는 최소 대역 객체로 바꾸고, 저장 데이터 서비스는 대역이나 임시 경로로 격리
- 대화상자는 열림 여부와 선택 요청만 대역으로 확인하고 실제 저장 데이터는 건드리지 않음
"""

import os
import tempfile
import unittest
from collections.abc import Callable, Sequence
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import EncodeConfig, ExecutionConfig, ThresholdsConfig
from src.core.settings.state_codec import AppState
from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.controllers import data_cleanup_controller as controller_module
from src.gui.controllers.data_cleanup_controller import (
    ANALYSIS_BUSY_TEXT,
    ENCODE_BUSY_TEXT,
    FAILURE_HEADER_TEXT,
    FRAME_BUSY_TEXT,
    NOTHING_DONE_TEXT,
    CleanupWindowPort,
    DataCleanupController,
    DataCleanupDeps,
    result_text,
)
from src.service.data_cleanup_service import (
    ALL_CLEANUP_ITEM_KEYS,
    STORAGE_CLEANUP_ITEM_KEYS,
    UI_RESET_CLEANUP_ITEM_KEYS,
    CleanupItemKey,
    CleanupItemPreview,
    CleanupPlanResult,
    CleanupScope,
    DataCleanupService,
)

_APP = QApplication.instance() or QApplication([])
ROOT: str = os.path.join(os.sep, "work")
DEFAULT_COL_ORDER: tuple[int, ...] = (0, 1, 2, 3)
DEFAULT_HIDDEN_COLS: frozenset[int] = frozenset({4})
COLUMN_COUNT: int = 5
SIZE_BYTES: int = 3 * 1024 * 1024


class _Header:
    """열 순서를 기억하는 표 머리글 대역"""

    def __init__(self) -> None:
        self.order: list[int] = list(DEFAULT_COL_ORDER)

    def visualIndex(self, col: int) -> int:
        """열의 화면 위치"""
        return self.order.index(col)

    def moveSection(self, from_index: int, to_index: int) -> None:
        """열 한 개를 다른 위치로 옮김"""
        col = self.order.pop(from_index)
        self.order.insert(to_index, col)


class _SearchEdit:
    """검색 입력 대역"""

    def __init__(self) -> None:
        self._text: str = ""

    def text(self) -> str:
        """현재 검색어"""
        return self._text

    def clear(self) -> None:
        """검색어 지우기"""
        self._text = ""


class _GroupModel:
    """행 묶기 프록시 대역. 묶기 키와 펼치기, 되돌리기만 흉내 냄"""

    def __init__(self) -> None:
        self.group_by: str = ""
        self.expanded: int = 0
        self.applied: list[str] = []

    def expand_all_groups(self) -> None:
        """모든 그룹 펼치기 횟수 기록"""
        self.expanded += 1

    def set_group_by(self, group_by: str) -> None:
        """적용한 묶기 키 기록"""
        self.group_by = group_by
        self.applied.append(group_by)


class _TableView:
    """표 뷰 대역. 열 순서, 숨김, 정렬 초기화만 흉내 냄"""

    DEFAULT_COL_ORDER = DEFAULT_COL_ORDER
    DEFAULT_HIDDEN_COLS = DEFAULT_HIDDEN_COLS

    def __init__(self) -> None:
        self.filter_header = _Header()
        self.hidden: set[int] = set(DEFAULT_HIDDEN_COLS)
        self.reset_calls: int = 0
        self.name_fit_calls: list[bool] = []
        self.group_model = _GroupModel()
        self._user_resized_name_col: bool = False
        self._user_resized_cols: set[int] = set()

    def isColumnHidden(self, col: int) -> bool:
        """열 숨김 여부"""
        return col in self.hidden

    def setColumnHidden(self, col: int, hidden: bool) -> None:
        """열 숨김 설정"""
        if hidden:
            self.hidden.add(col)
        else:
            self.hidden.discard(col)

    def reset_table_sort(self) -> None:
        """정렬 초기화 횟수 기록"""
        self.reset_calls += 1

    def fit_name_column_to_contents(self, force: bool = False) -> None:
        """이름 열 맞춤 호출 기록"""
        self.name_fit_calls.append(force)

    def columnWidth(self, col: int) -> int:
        """열 폭"""
        return 100


class _TableModel:
    """표 모델 대역"""

    def __init__(self) -> None:
        self.encode_books: list[object] = []

    def columnCount(self) -> int:
        """열 개수"""
        return COLUMN_COUNT

    def set_encode_book(self, book: object) -> None:
        """인코딩 기록 반영 기록"""
        self.encode_books.append(book)


class _ProxyModel:
    """프록시 모델 대역"""

    def __init__(self) -> None:
        self.sort_stack: list[object] = []
        self.filter_active: bool = False
        self.cleared: int = 0
        self.review_only: bool = False
        self.review_calls: list[bool] = []

    def is_any_filter_active(self) -> bool:
        """검색어, 열 필터, 여백 확인 필요 칩 중 하나라도 켜졌는지"""
        return self.filter_active or self.review_only

    def clear_all_column_filters(self) -> None:
        """열 필터 지우기 횟수 기록"""
        self.cleared += 1

    def set_review_only(self, enabled: bool) -> None:
        """여백 확인 필요 보기 필터 적용 기록"""
        self.review_only = enabled
        self.review_calls.append(enabled)


class _FilterBar:
    """필터 바 대역"""

    def __init__(self) -> None:
        self.search_edit = _SearchEdit()
        self.compact: bool = False
        self.exception_tokens: list[str] = []
        self.disabled_exception_count: int = -1
        self.row_group: tuple[str, str, int] | None = None
        self.review_filter_cleared: int = 0

    def clear_review_filter(self) -> None:
        """여백 확인 필요 칩 해제 횟수 기록"""
        self.review_filter_cleared += 1

    def set_row_group(self, group_by: str, *, label: str, count: int) -> None:
        """행 묶기 버튼 글자 반영"""
        self.row_group = (group_by, label, count)

    def is_compact(self) -> bool:
        """좁게 보기 여부"""
        return self.compact

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """예외 토큰 반영"""
        self.exception_tokens = tokens

    def set_disabled_exception_count(self, count: int) -> None:
        """비활성 예외 개수 반영"""
        self.disabled_exception_count = count


class _TopArea:
    """상단 접기 영역 대역"""

    def __init__(self) -> None:
        self.collapsed: bool = True

    def set_collapsed(self, collapsed: bool) -> None:
        """접힘 상태 반영"""
        self.collapsed = collapsed


class _PathEdit:
    """작업 폴더 입력 대역"""

    def __init__(self, text: str = ROOT) -> None:
        self._text = text

    def text(self) -> str:
        """입력된 경로"""
        return self._text


class _HeaderBar:
    """상단 머리글 대역"""

    def __init__(self) -> None:
        self.path_edit = _PathEdit()


class _Menu:
    """메뉴 컨트롤러 대역"""

    def __init__(self) -> None:
        self.refresh_calls: int = 0

    def refresh_encode_state(self) -> None:
        """인코딩 상태 갱신 횟수 기록"""
        self.refresh_calls += 1


class _FrameAnalysis:
    """화질 분석 컨트롤러 대역"""

    def __init__(self) -> None:
        self.is_running: bool = False
        self.reload_calls: int = 0

    def reload_cached(self) -> None:
        """캐시 결과 다시 읽기 횟수 기록"""
        self.reload_calls += 1


class _EncodeBook:
    """폐기 기록부 대역"""

    def __init__(self) -> None:
        self.cleared: int = 0

    def clear_discarded(self) -> None:
        """폐기 기록 비우기 횟수 기록"""
        self.cleared += 1


class _Encode:
    """인코딩 조율자 대역"""

    def __init__(self) -> None:
        self.is_running: bool = False
        self.book = _EncodeBook()


class _Classifier:
    """분류기 대역"""

    def __init__(self) -> None:
        self.invalidated: int = 0
        self.configs: list[object] = []
        self.recursive: list[bool] = []
        self.exception_tokens: list[list[str]] = []

    def invalidate_cache(self) -> None:
        """캐시 무효화 횟수 기록"""
        self.invalidated += 1

    def set_config(self, config: object) -> None:
        """반영된 설정 기록"""
        self.configs.append(config)

    def set_recursive(self, enabled: bool) -> None:
        """하위 폴더 재귀 설정 기록"""
        self.recursive.append(enabled)

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """예외 토큰 기록"""
        self.exception_tokens.append(tokens)


class _Session:
    """세션 조율자 대역"""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.classifier: _Classifier | None = _Classifier()
        self.persist_calls: int = 0
        self.attach_calls: int = 0
        self.reload_calls: int = 0
        self.selection_clears: int = 0
        self.recent_clears: list[tuple[object, str]] = []
        self.sync_calls: list[object] = []

    def current_root_dir(self, typed: str) -> str:
        """작업 폴더 경로"""
        return typed

    def persist(self) -> None:
        """상태 저장 횟수 기록"""
        self.persist_calls += 1

    def attach_manual_targets(self) -> None:
        """직접 지정 다시 연결 횟수 기록"""
        self.attach_calls += 1

    def reload_manual_targets(self) -> None:
        """직접 지정 다시 읽기 횟수 기록"""
        self.reload_calls += 1
        self.state.ui.manual_target_tiers = {}
        self.attach_calls += 1

    def clear_saved_selections(self) -> None:
        """선택 기억 비우기 횟수 기록"""
        self.selection_clears += 1

    def clear_recent_dirs(self, menu: object, path: str) -> None:
        """최근 폴더 비우기 기록"""
        self.recent_clears.append((menu, path))

    def sync_classifier_and_table(self, filter_bar: object, table_model: object, config: object) -> None:
        """분류기와 표 동기화 기록"""
        self.sync_calls.append(config)


class _TableDisplay:
    """표 표시 조율자 대역"""

    def __init__(self) -> None:
        self.compact_calls: list[bool] = []
        self.sync_calls: int = 0

    def set_compact(self, compact: bool) -> None:
        """좁게 보기 반영"""
        self.compact_calls.append(compact)

    def sync_preset(self) -> None:
        """프리셋 표시 동기화 횟수 기록"""
        self.sync_calls += 1


class _Shortcuts:
    """단축키 컨트롤러 대역"""

    def __init__(self) -> None:
        self.applied: list[dict[str, str]] = []

    def apply_shortcuts(self, keys: dict[str, str]) -> None:
        """적용된 단축키 기록"""
        self.applied.append(keys)


class _Theme:
    """테마 관리자 대역"""

    def __init__(self) -> None:
        self.modes: list[str] = []

    def set_mode(self, mode: str) -> None:
        """설정된 테마 모드 기록"""
        self.modes.append(mode)


class _TaskRunner:
    """작업 실행기 대역"""

    def __init__(self) -> None:
        self.running: bool = False

    def is_running(self) -> bool:
        """분석 실행 여부"""
        return self.running


class _Window:
    """메인 창 대역. 컨트롤러가 이름으로 찾는 비공개 구성 요소까지 제공"""

    def __init__(self, state: AppState | None = None) -> None:
        self._session = _Session(state or AppState())
        self._encode = _Encode()
        self._theme_mgr = _Theme()
        self.re_evaluate_calls: int = 0
        self.sync_calls: int = 0
        self.status_texts: list[str] = []
        self.table_view = _TableView()
        self.table_model = _TableModel()
        self.proxy_model = _ProxyModel()
        self.filter_bar = _FilterBar()
        self.top_area = _TopArea()
        self.header_bar = _HeaderBar()
        self.menu = _Menu()
        self.frame_analysis = _FrameAnalysis()
        self.task_runner = _TaskRunner()
        self.table_display = _TableDisplay()
        self.shortcuts = _Shortcuts()

    def _re_evaluate_async(self) -> None:
        """재평가 요청 횟수 기록"""
        self.re_evaluate_calls += 1

    def _sync_widgets_from_session(self) -> None:
        """위젯 동기화 횟수 기록"""
        self.sync_calls += 1

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        """하단 상태 줄 문구 기록"""
        self.status_texts.append(text)

    def resize(self, width: int, height: int) -> None:
        """창 크기 변경 기록"""
        self.size = (width, height)


class _Service:
    """저장 데이터 정리 서비스 대역"""

    def __init__(self, fail_with: type[Exception] | None = None) -> None:
        self.fail_with = fail_with
        self.preview_scopes: list[tuple[str, str]] = []
        self.executed: list[tuple[tuple[str, ...], str, str]] = []

    def preview(self, scope: str, root: str) -> dict[str, CleanupItemPreview]:
        """범위별 미리보기. 실패 설정이 있으면 예외를 냄"""
        self.preview_scopes.append((scope, root))
        if self.fail_with is not None:
            raise self.fail_with("no root")
        return {
            key: CleanupItemPreview(key, 1, SIZE_BYTES if key == "part_files" else 0, True)
            for key in ALL_CLEANUP_ITEM_KEYS
        }

    def execute_storage_cleanup(
        self, keys: Sequence[CleanupItemKey], scope: CleanupScope, root: str
    ) -> CleanupPlanResult:
        """저장 데이터 정리 실행 기록과 결과"""
        self.executed.append((tuple(keys), scope, root))
        return CleanupPlanResult({key: 1 for key in keys}, {})


class _NoWidgetAccess:
    """속성을 읽으면 실패하는 대역. 미리보기 함수가 위젯을 읽는지 확인할 때 씀"""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"미리보기 중 위젯 속성 접근: {name}")


class _FakeDialog:
    """데이터 정리 대화상자 대역"""

    def __init__(
        self,
        storage_preview_fn: object,
        ui_preview_fn: object,
        current_root: str,
        parent: object = None,
        result: object = None,
        on_exec: Callable[[], None] | None = None,
    ) -> None:
        self.storage_preview_fn = storage_preview_fn
        self.ui_preview_fn = ui_preview_fn
        self.current_root = current_root
        self.parent = parent
        self.result = result
        self.on_exec = on_exec
        self.exec_calls: int = 0

    def exec(self) -> int:
        """대화상자 결과 코드 반환. 실행 직전 상태를 바꾸려면 on_exec 사용"""
        self.exec_calls += 1
        if self.on_exec is not None:
            self.on_exec()
        return 1 if self.result is not None else 0

    def selected_request(self) -> object:
        """선택 요청 반환"""
        return self.result if isinstance(self.result, _Request) else None


class _Request:
    """선택 요청 대역"""

    def __init__(self, scope: str, keys: tuple[str, ...]) -> None:
        self.scope = scope
        self.keys = keys


def _controller(window: _Window) -> DataCleanupController:
    """대역 창에 물린 컨트롤러"""
    return DataCleanupController(cast("CleanupWindowPort", window))


class ResultTextTest(unittest.TestCase):
    """정리 결과 문구 조합 검증"""

    def test_storage_and_ui_counts_are_reported(self) -> None:
        """저장 기록과 화면 초기화 건수를 함께 안내하는지 검증"""
        text = result_text(CleanupPlanResult({"probe_cache": 3}, {}), 2, SIZE_BYTES)
        self.assertIn("기록 3건", text)
        self.assertIn("화면 및 설정 2개 항목", text)
        self.assertIn("MB", text)

    def test_nothing_done_uses_the_empty_text(self) -> None:
        """아무것도 지우지 않았으면 안내 문구를 반환하는지 검증"""
        self.assertEqual(result_text(CleanupPlanResult({}, {}), 0, 0), NOTHING_DONE_TEXT)

    def test_errors_are_listed_with_labels(self) -> None:
        """실패 항목은 이름과 사유를 함께 보이는지 검증"""
        text = result_text(CleanupPlanResult({}, {"part_files": "잠김"}), 0, 0)
        self.assertIn(FAILURE_HEADER_TEXT, text)
        self.assertIn("임시 .part 파일: 잠김", text)


class BusyGuardTest(unittest.TestCase):
    """실행 중 작업이 있으면 대화상자를 열지 않는지 검증"""

    def setUp(self) -> None:
        self.window = _Window()
        self.controller = _controller(self.window)
        patcher = mock.patch.object(controller_module.QMessageBox, "information")
        self.information = patcher.start()
        self.addCleanup(patcher.stop)

    def test_analysis_running_blocks(self) -> None:
        """영상 분석이 실행 중이면 분석 사유를 보이는지 검증"""
        self.window.task_runner.running = True
        self.assertEqual(self.controller.busy_reason(), ANALYSIS_BUSY_TEXT)
        self.controller.open_dialog()
        self.information.assert_called_once()
        self.assertEqual(self.information.call_args.args[2], ANALYSIS_BUSY_TEXT)

    def test_frame_analysis_running_blocks(self) -> None:
        """화질 분석이 실행 중이면 화질 분석 사유를 보이는지 검증"""
        self.window.frame_analysis.is_running = True
        self.assertEqual(self.controller.busy_reason(), FRAME_BUSY_TEXT)

    def test_encode_running_blocks(self) -> None:
        """인코딩이 실행 중이면 인코딩 사유를 보이는지 검증"""
        self.window._encode.is_running = True
        self.assertEqual(self.controller.busy_reason(), ENCODE_BUSY_TEXT)

    def test_idle_has_no_reason(self) -> None:
        """실행 중인 작업이 없으면 사유가 비어 있는지 검증"""
        self.assertEqual(self.controller.busy_reason(), "")


class DialogFlowTest(unittest.TestCase):
    """대화상자 열기, 취소, 확정 흐름 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.window = _Window()
        self.controller = _controller(self.window)
        dialog_patcher = mock.patch.object(controller_module, "QMessageBox")
        self.message_box = dialog_patcher.start()
        self.addCleanup(dialog_patcher.stop)

    def _patch_dialog(self, dialog: _FakeDialog) -> mock.MagicMock:
        """컨트롤러가 쓰는 대화상자 클래스를 대역으로 바꿈"""
        patcher = mock.patch.object(controller_module, "DataCleanupDialog", return_value=dialog)
        factory = patcher.start()
        self.addCleanup(patcher.stop)
        return factory

    def test_rejected_dialog_does_nothing(self) -> None:
        """닫기로 끝나면 정리를 실행하지 않는지 검증"""
        dialog = _FakeDialog(lambda scope: {}, lambda: {}, ROOT)
        factory = self._patch_dialog(dialog)
        with mock.patch.object(self.controller, "execute_cleanup") as execute:
            self.controller.open_dialog()
        execute.assert_not_called()
        self.message_box.information.assert_not_called()
        self.assertIs(factory.call_args.kwargs["parent"], self.window)

    def test_empty_selection_does_nothing(self) -> None:
        """선택 항목이 없으면 정리를 실행하지 않는지 검증"""
        dialog = _FakeDialog(lambda scope: {}, lambda: {}, ROOT, self.parent)
        factory = self._patch_dialog(dialog)
        with mock.patch.object(self.controller, "execute_cleanup") as execute:
            self.controller.open_dialog()
        execute.assert_not_called()
        self.message_box.information.assert_not_called()
        self.assertEqual(factory.call_count, 1)

    def test_busy_again_at_confirmation_is_blocked(self) -> None:
        """대화상자를 연 뒤 작업이 시작되면 실행하지 않고 사유를 보이는지 검증"""

        def _start_encode() -> None:
            self.window._encode.is_running = True

        dialog = _FakeDialog(
            lambda scope: {},
            lambda: {},
            ROOT,
            self.parent,
            result=_Request("current", ("part_files",)),
            on_exec=_start_encode,
        )
        self._patch_dialog(dialog)
        with mock.patch.object(self.controller, "execute_cleanup") as execute:
            self.controller.open_dialog()
        execute.assert_not_called()
        self.assertEqual(self.message_box.information.call_args.args[2], ENCODE_BUSY_TEXT)

    def test_accepted_dialog_executes_and_shows_result(self) -> None:
        """확정하면 선택 범위와 항목으로 정리를 실행하고 결과를 보이는지 검증"""
        dialog = _FakeDialog(
            lambda scope: {}, lambda: {}, ROOT, self.parent, result=_Request("all", ("part_files",))
        )
        self._patch_dialog(dialog)
        with mock.patch.object(self.controller, "execute_cleanup", return_value="끝") as execute:
            self.controller.open_dialog()
        execute.assert_called_once_with("all", ("part_files",))
        self.assertEqual(self.message_box.information.call_args.args[2], "끝")

    def test_dialog_gets_precomputed_root_and_split_preview_fns(self) -> None:
        """작업 폴더를 미리 구해 넘기고, 미리보기를 저장 데이터와 화면 항목 두 함수로 나눠 넘기는지 검증"""
        dialog = _FakeDialog(lambda scope: {}, lambda: {}, ROOT)
        factory = self._patch_dialog(dialog)
        with mock.patch.object(self.controller, "execute_cleanup"):
            self.controller.open_dialog()
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["current_root"], ROOT)
        self.assertTrue(callable(kwargs["storage_preview_fn"]))
        self.assertEqual(kwargs["ui_preview_fn"], self.controller.ui_previews)


class PreviewTest(unittest.TestCase):
    """미리보기 구성 검증"""

    def setUp(self) -> None:
        self.window = _Window()
        self.controller = _controller(self.window)

    def test_storage_and_ui_items_are_all_present(self) -> None:
        """저장 데이터와 화면, 설정 항목이 모두 미리보기에 들어가는지 검증"""
        service = _Service()
        self.controller._service_cache = cast("DataCleanupService", service)
        previews = self.controller.build_preview("all")
        self.assertEqual(set(previews), set(ALL_CLEANUP_ITEM_KEYS))
        self.assertEqual(previews["part_files"].count, 1)
        self.assertEqual(previews["column_order"].count, 0)
        self.assertEqual(service.preview_scopes, [("all", ROOT)])

    def test_ui_items_count_changed_state(self) -> None:
        """기본값과 다른 화면, 설정 항목은 1건으로 세는지 검증"""
        self.controller._service_cache = cast("DataCleanupService", _Service())
        self.window._session.state.ui.shortcuts = {"file.open_dir": "Ctrl+Q"}
        previews = self.controller.build_preview("current")
        self.assertEqual(previews["shortcuts_settings"].count, 1)

    def test_storage_preview_fn_uses_captured_root_without_reading_widgets(self) -> None:
        """미리보기 함수가 만들 때 받은 작업 폴더를 쓰고 메인 창 위젯을 읽지 않는지 검증"""
        service = _Service()
        self.controller._service_cache = cast("DataCleanupService", service)
        self.controller._deps_cache = cast("DataCleanupDeps", _NoWidgetAccess())
        preview = self.controller.storage_preview_fn(ROOT)
        expected = {
            key: CleanupItemPreview(key, 1, SIZE_BYTES if key == "part_files" else 0, True)
            for key in STORAGE_CLEANUP_ITEM_KEYS
        }
        for scope in ("current", "all"):
            with self.subTest(scope=scope):
                self.assertEqual(preview(scope), expected)
        self.assertEqual(service.preview_scopes, [("current", ROOT), ("all", ROOT)])

    def test_missing_root_gives_empty_storage_previews(self) -> None:
        """작업 폴더가 없으면 현재 범위 저장 데이터를 빈 미리보기로 두는지 검증"""
        self.controller._service_cache = cast("DataCleanupService", _Service(fail_with=ValueError))
        previews = self.controller.build_preview("current")
        for key in STORAGE_CLEANUP_ITEM_KEYS:
            self.assertEqual(previews[key].count, 0)
            self.assertFalse(previews[key].available)


class ExecuteCleanupTest(unittest.TestCase):
    """정리 실행과 결과 반영 검증"""

    def setUp(self) -> None:
        self.window = _Window()
        self.controller = _controller(self.window)
        self.service = _Service()
        self.controller._service_cache = cast("DataCleanupService", self.service)
        for name in ("auto_fit_visible_columns",):
            patcher = mock.patch.object(controller_module, name, return_value="layout")
            self.addCleanup(patcher.stop)
            patcher.start()
        store_patcher = mock.patch.object(
            controller_module,
            "store_preset_layout",
            side_effect=lambda _view, layout, _key: setattr(
                layout, "table_header_state", "layout"
            ),
        )
        self.addCleanup(store_patcher.stop)
        store_patcher.start()

    def test_storage_cleanup_refreshes_views_and_saves_ui_resets(self) -> None:
        """저장 데이터를 지우고 화면, 설정 항목을 되돌린 뒤 결과와 상태를 남기는지 검증"""
        text = self.controller.execute_cleanup(
            "current", ("part_files", "probe_cache", "discarded_history", "shortcuts_settings")
        )
        self.assertIn("기록", text)
        self.assertIn("MB", text)
        self.assertEqual(len(self.service.executed), 1)
        keys, scope, root = self.service.executed[0]
        self.assertEqual(scope, "current")
        self.assertEqual(root, ROOT)
        self.assertEqual(set(keys), {"part_files", "probe_cache", "discarded_history"})
        classifier = self.window._session.classifier
        assert classifier is not None
        self.assertEqual(classifier.invalidated, 1)
        self.assertEqual(self.window._session.attach_calls, 1)
        self.assertEqual(self.window.frame_analysis.reload_calls, 1)
        self.assertEqual(self.window._encode.book.cleared, 1)
        self.assertEqual(self.window.menu.refresh_calls, 1)
        self.assertEqual(self.window._session.state.ui.shortcuts, {})
        self.assertEqual(self.window.shortcuts.applied[-1], {})
        self.assertEqual(self.window._session.persist_calls, 1)
        self.assertEqual(self.window.re_evaluate_calls, 1)
        self.assertEqual(len(self.window.status_texts), 1)
        self.assertNotIn("\n", self.window.status_texts[0])

    def test_completed_history_only_keeps_discard_marks(self) -> None:
        """인코딩 완료 이력만 지웠을 때 폐기 표시를 지우지 않고 표와 메뉴만 갱신하는지 검증"""
        self.controller.execute_cleanup("current", ("encode_output_history",))
        self.assertEqual(self.window._encode.book.cleared, 0)
        self.assertEqual(len(self.window.table_model.encode_books), 1)
        self.assertEqual(self.window.menu.refresh_calls, 1)

    def test_discarded_history_clears_discard_marks(self) -> None:
        """폐기 기록을 지웠을 때는 폐기 표시를 메모리에서 비우는지 검증"""
        self.controller.execute_cleanup("current", ("discarded_history",))
        self.assertEqual(self.window._encode.book.cleared, 1)
        self.assertEqual(len(self.window.table_model.encode_books), 1)
        self.assertEqual(self.window.menu.refresh_calls, 1)

    def test_manual_targets_are_reloaded_from_db_after_storage_cleanup(self) -> None:
        """직접 지정을 지운 뒤 메모리와 저장 기준을 DB 값으로 다시 읽는지 검증"""
        self.controller.execute_cleanup("current", ("manual_targets",))
        self.assertEqual(self.window._session.reload_calls, 1)
        self.assertEqual(self.window._session.state.ui.manual_target_tiers, {})
        self.assertEqual(self.window._session.attach_calls, 2)

    def test_unrelated_storage_cleanup_does_not_reload_manual_targets(self) -> None:
        """직접 지정을 지우지 않았으면 다시 읽지 않는지 검증"""
        self.controller.execute_cleanup("current", ("probe_cache",))
        self.assertEqual(self.window._session.reload_calls, 0)

    def test_ui_only_cleanup_skips_service(self) -> None:
        """화면, 설정 항목만 고르면 저장 데이터 서비스를 부르지 않는지 검증"""
        self.controller.execute_cleanup("all", ("column_order",))
        self.assertEqual(self.service.executed, [])

    def test_nothing_selected_reports_nothing_done(self) -> None:
        """선택 항목이 없으면 정리한 항목이 없다고 안내하는지 검증"""
        self.assertEqual(self.controller.execute_cleanup("all", ()), NOTHING_DONE_TEXT)


class UiChecksTest(unittest.TestCase):
    """화면, 설정 항목별 변경 여부 확인 검증"""

    def setUp(self) -> None:
        self.window = _Window()
        self.controller = _controller(self.window)

    def test_default_state_reports_no_change(self) -> None:
        """기본 상태에서는 모든 항목이 바뀌지 않은 것으로 보는지 검증"""
        checks = self.controller._ui_checks()
        self.assertEqual(len(checks), len(UI_RESET_CLEANUP_ITEM_KEYS))
        for key, check in checks.items():
            with self.subTest(key=key):
                self.assertFalse(check())

    def test_column_layout_changes_are_detected(self) -> None:
        """열 순서, 너비, 숨김 변경을 각각 감지하는지 검증"""
        view = self.window.table_view
        view.filter_header.order = [1, 0, 2, 3]
        self.assertTrue(self.controller._column_order_changed())
        view._user_resized_name_col = True
        self.assertTrue(self.controller._column_widths_changed())
        view._user_resized_cols = {1}
        self.assertTrue(self.controller._column_widths_changed())
        view.hidden = {0}
        self.assertTrue(self.controller._column_visibility_changed())

    def test_sort_group_changes_are_detected(self) -> None:
        """정렬, 필터, 검색어, 그룹 변경을 각각 감지하는지 검증"""
        self.window.proxy_model.sort_stack.append("name")
        self.assertTrue(self.controller._sort_group_changed())
        self.window.proxy_model.sort_stack.clear()
        self.window.proxy_model.filter_active = True
        self.assertTrue(self.controller._sort_group_changed())
        self.window.proxy_model.filter_active = False
        self.window.filter_bar.search_edit._text = "clip"
        self.assertTrue(self.controller._sort_group_changed())
        self.window.filter_bar.search_edit._text = ""
        self.window.table_view.group_model.group_by = "folder"
        self.assertTrue(self.controller._sort_group_changed())

    def test_layout_changes_are_detected(self) -> None:
        """창 배치와 최근 폴더 기억 변경을 감지하는지 검증"""
        layout = self.window._session.state.ui.layout
        layout.window_geometry = "abc"
        self.assertTrue(self.controller._window_geometry_changed())
        layout.window_geometry = ""
        layout.top_area_collapsed = True
        self.assertTrue(self.controller._window_geometry_changed())
        layout.top_area_collapsed = False
        layout.table_left_scroll = 120
        self.assertTrue(self.controller._window_geometry_changed())
        layout.table_left_scroll = 0
        self.window._session.state.paths.recent_dirs = [ROOT]
        self.assertTrue(self.controller._recent_dirs_changed())
        self.window._session.state.paths.recent_dirs = []
        layout.table_top_filename = "a.mp4"
        self.assertTrue(self.controller._recent_dirs_changed())
        layout.table_top_filename = ""
        layout.table_current_filename = "a.mp4"
        self.assertTrue(self.controller._recent_dirs_changed())

    def test_settings_changes_are_detected(self) -> None:
        """기준, 인코딩, 일반 표시, 단축키, 예외 규칙 변경을 감지하는지 검증"""
        state = self.window._session.state
        state.analysis.thresholds = ThresholdsConfig(over_bitrate_ratio=9.0)
        self.assertTrue(self.controller._criteria_changed())
        state.analysis.thresholds = ThresholdsConfig()
        state.analysis.encode = EncodeConfig(baseline_cq=30)
        self.assertTrue(self.controller._encode_settings_changed())
        state.analysis.encode = EncodeConfig()
        state.execution = ExecutionConfig(concurrent_jobs=2)
        self.assertTrue(self.controller._encode_settings_changed())
        state.execution = ExecutionConfig()
        state.ui.recursive_scan = True
        self.assertTrue(self.controller._general_display_changed())
        state.ui.recursive_scan = False
        state.ui.shortcuts = {"file.open_dir": "Ctrl+Q"}
        self.assertTrue(self.controller._shortcuts_changed())
        state.ui.shortcuts = {}
        state.ui.exception_tokens = ["bad"]
        self.assertTrue(self.controller._exception_rules_changed())
        state.ui.exception_tokens = []
        state.ui.disabled_exception_tokens = ["worse"]
        self.assertTrue(self.controller._exception_rules_changed())


class UiResetsTest(unittest.TestCase):
    """화면, 설정 항목별 초기화 검증"""

    auto_fit_visible_columns: mock.MagicMock

    def setUp(self) -> None:
        self.window = _Window()
        self.controller = _controller(self.window)
        for name in (
            "auto_fit_visible_columns",
        ):
            patcher = mock.patch.object(controller_module, name, return_value="layout")
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        store_patcher = mock.patch.object(
            controller_module,
            "store_preset_layout",
            side_effect=lambda _view, layout, _key: setattr(
                layout, "table_header_state", "layout"
            ),
        )
        self.store_preset_layout = store_patcher.start()
        self.addCleanup(store_patcher.stop)

    def test_column_order_is_restored_and_saved(self) -> None:
        """열 순서를 기본 순서로 되돌리고 저장 상태에 반영하는지 검증"""
        self.window.table_view.filter_header.order = [2, 3, 0, 1]
        self.controller._reset_column_order()
        self.assertEqual(self.window.table_view.filter_header.order, list(DEFAULT_COL_ORDER))
        self.assertEqual(self.window._session.state.ui.layout.table_header_state, "layout")
        self.assertEqual(self.window.table_display.sync_calls, 1)

    def test_column_widths_are_refit(self) -> None:
        """열 너비를 보이는 열과 이름 열 기준으로 다시 맞추는지 검증"""
        self.window.filter_bar.compact = True
        self.controller._reset_column_widths()
        self.auto_fit_visible_columns.assert_called_once()
        self.assertEqual(self.auto_fit_visible_columns.call_args.kwargs["compact"], True)
        self.assertEqual(self.window.table_view.name_fit_calls, [True])

    def test_column_width_reset_clears_user_resize_flags(self) -> None:
        """열 너비를 되돌린 뒤 사용자 조절 표시도 지워 기본값과 같아지는지 검증"""
        view = self.window.table_view
        view._user_resized_name_col = True
        view._user_resized_cols = {1, 2}
        self.assertTrue(self.controller._column_widths_changed())
        self.controller._reset_column_widths()
        self.assertFalse(view._user_resized_name_col)
        self.assertEqual(view._user_resized_cols, set())
        self.assertFalse(self.controller._column_widths_changed())

    def test_column_visibility_is_restored(self) -> None:
        """숨김 열을 기본 숨김 열만 남기도록 되돌리는지 검증"""
        self.window.table_view.hidden = {0, 1}
        self.controller._reset_column_visibility()
        self.assertEqual(self.window.table_view.hidden, set(DEFAULT_HIDDEN_COLS))

    def test_column_visibility_reset_uses_the_active_presets_default_columns(self) -> None:
        """기술 사양 프리셋에서 표시 초기화가 기본 프리셋 표시 열이 아니라 그 프리셋의 기본 표시 열로 되돌리는지 검증"""
        from src.gui.constants import PRESET_SPECS
        from src.gui.table.column_presets import hidden_columns_for_preset

        self.window._session.state.ui.layout.column_preset = PRESET_SPECS
        # - 대역 표는 열이 5개뿐이라 프리셋 숨김 열 중 표 범위 안의 것만 남음
        columns = self.window.table_model.columnCount()
        specs_hidden = {col for col in hidden_columns_for_preset(PRESET_SPECS) if col < columns}
        self.assertNotEqual(specs_hidden, set(DEFAULT_HIDDEN_COLS))
        self.window.table_view.hidden = set(DEFAULT_HIDDEN_COLS)
        self.assertTrue(self.controller._column_visibility_changed())

        self.controller._reset_column_visibility()

        self.assertEqual(self.window.table_view.hidden, specs_hidden)

    def test_sort_group_filters_are_cleared(self) -> None:
        """정렬, 그룹, 필터, 검색어, 여백 확인 필요 칩과 저장 상태를 모두 지우는지 검증"""
        state = self.window._session.state
        state.ui.filters.search = "clip"
        state.ui.filters.columns = {"name": ColumnFilterSnapshot(text_query="a")}
        state.ui.filters.sort_keys = [("name", True)]
        self.window.table_view.group_model.group_by = "folder"
        self.window.proxy_model.set_review_only(True)
        self.controller._reset_sort_group()
        self.assertEqual(self.window.table_view.reset_calls, 1)
        self.assertEqual(self.window.table_view.group_model.group_by, "")
        self.assertEqual(self.window.table_view.group_model.expanded, 1)
        self.assertEqual(self.window.filter_bar.row_group, ("", "", 0))
        self.assertEqual(self.window.proxy_model.cleared, 1)
        # - 칩은 시그널 없이 꺼지므로 프록시 보기 필터도 함께 풀려야 표가 걸러진 채 남지 않음
        self.assertEqual(self.window.filter_bar.review_filter_cleared, 1)
        self.assertFalse(self.window.proxy_model.review_only)
        self.assertFalse(self.controller._sort_group_changed())
        self.assertEqual(self.window.filter_bar.search_edit.text(), "")
        self.assertEqual(state.ui.filters.search, "")
        self.assertEqual(state.ui.filters.columns, {})
        self.assertEqual(state.ui.filters.sort_keys, [])
        self.assertEqual(state.ui.filters.group_by, "")

    def test_window_geometry_is_restored(self) -> None:
        """창 크기를 기본값으로 되돌리고 상단 영역을 펼치며 가로 스크롤 저장값을 지우는지 검증"""
        self.controller._reset_window_geometry()
        self.assertFalse(self.window.top_area.collapsed)
        layout = self.window._session.state.ui.layout
        self.assertEqual(layout.window_geometry, "")
        self.assertFalse(layout.top_area_collapsed)
        self.assertEqual(layout.table_left_scroll, 0)

    def test_recent_dirs_and_selection_are_cleared(self) -> None:
        """최근 폴더와 표 선택 기억을 지우고 저장소를 닫는지 검증"""
        store = mock.MagicMock()
        with mock.patch.object(controller_module, "open_default_store", return_value=store):
            self.controller._reset_recent_dirs_selection()
        self.assertEqual(len(self.window._session.recent_clears), 1)
        store.clear_table_selections.assert_called_once()
        store.close.assert_called_once()
        self.assertEqual(self.window._session.selection_clears, 1)
        layout = self.window._session.state.ui.layout
        self.assertEqual(layout.table_top_filename, "")
        self.assertEqual(layout.table_top_row, 0)
        self.assertEqual(layout.table_current_filename, "")

    def test_criteria_are_reset_and_reapplied(self) -> None:
        """판별 기준을 기본값으로 되돌리고 분류기와 표에 반영하는지 검증"""
        state = self.window._session.state
        state.analysis.thresholds = ThresholdsConfig(over_bitrate_ratio=9.0)
        self.controller._reset_criteria()
        self.assertEqual(state.analysis.thresholds, ThresholdsConfig())
        self.assertEqual(len(self.window._session.sync_calls), 1)
        classifier = self.window._session.classifier
        assert classifier is not None
        self.assertEqual(len(classifier.configs), 1)
        self.assertEqual(self.window.re_evaluate_calls, 1)

    def test_encode_settings_are_reset(self) -> None:
        """인코딩 설정과 실행 설정을 기본값으로 되돌리는지 검증"""
        state = self.window._session.state
        state.analysis.encode = EncodeConfig(baseline_cq=30)
        self.controller._reset_encode_settings()
        self.assertEqual(state.analysis.encode, EncodeConfig())
        self.assertEqual(state.execution, ExecutionConfig())

    def test_general_display_is_reset(self) -> None:
        """일반, 표시 설정과 테마, 좁게 보기, 위젯을 기본값으로 되돌리는지 검증"""
        state = self.window._session.state
        state.ui.recursive_scan = True
        state.ui.preview_volume = 20
        self.controller._reset_general_display()
        self.assertFalse(state.ui.recursive_scan)
        self.assertEqual(self.window.table_display.compact_calls[-1], state.ui.compact_table)
        self.assertEqual(len(self.window._theme_mgr.modes), 1)
        self.assertEqual(self.window.sync_calls, 1)

    def test_stage_background_counts_as_a_general_display_change(self) -> None:
        """배경 색과 영상 테두리 변경을 일반 표시 설정 변경으로 보는지 검증"""
        state = self.window._session.state
        self.assertFalse(self.controller._general_display_changed())
        state.ui.preview_background = "gray"
        self.assertTrue(self.controller._general_display_changed())
        state.ui.preview_background = "black"
        state.ui.preview_frame_outline = True
        self.assertTrue(self.controller._general_display_changed())

    def test_general_display_reset_restores_the_stage_background_and_outline(self) -> None:
        """일반 표시 설정 초기화가 배경 색과 영상 테두리도 기본값으로 되돌리는지 검증"""
        state = self.window._session.state
        state.ui.preview_background = "white"
        state.ui.preview_frame_outline = True
        self.controller._reset_general_display()
        self.assertEqual(state.ui.preview_background, "black")
        self.assertFalse(state.ui.preview_frame_outline)

    def test_shortcuts_are_reset(self) -> None:
        """사용자 지정 단축키를 지우고 컨트롤러에 적용하는지 검증"""
        self.window._session.state.ui.shortcuts = {"file.open_dir": "Ctrl+Q"}
        self.controller._reset_shortcuts()
        self.assertEqual(self.window._session.state.ui.shortcuts, {})
        self.assertEqual(self.window.shortcuts.applied[-1], {})

    def test_exception_rules_are_reset(self) -> None:
        """예외 규칙과 비활성 목록을 지우고 분류기, 필터 바에 반영하는지 검증"""
        state = self.window._session.state
        state.ui.exception_tokens = ["bad"]
        state.ui.disabled_exception_tokens = ["worse"]
        classifier = self.window._session.classifier
        assert classifier is not None
        self.controller._reset_exception_rules()
        self.assertEqual(state.ui.exception_tokens, [])
        self.assertEqual(state.ui.disabled_exception_tokens, [])
        self.assertEqual(self.window.filter_bar.exception_tokens, [])
        self.assertEqual(self.window.filter_bar.disabled_exception_count, 0)
        self.assertEqual(classifier.exception_tokens[-1], [])


class ServiceConstructionTest(unittest.TestCase):
    """저장 데이터 서비스 구성 검증"""

    def test_service_opens_and_closes_store_per_call(self) -> None:
        """기본 저장소와 두 이력, 설정 파일 경로로 서비스를 만들고 호출마다 연결을 닫는지 검증"""
        window = _Window()
        controller = _controller(window)
        store = mock.MagicMock(db_path=os.path.join(ROOT, "data", "video_cache.sqlite3"))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with (
            mock.patch.object(
                controller_module, "open_default_store", return_value=store
            ) as open_store,
            mock.patch.object(controller_module, "FrameAnalysisStore") as frame_store,
            mock.patch.object(controller_module, "EncodeHistory") as history,
            mock.patch.object(controller_module, "EncodeOutputHistory") as output_history,
            mock.patch.object(
                controller_module,
                "get_state_path",
                return_value=os.path.join(tmp.name, "config.json"),
            ),
        ):
            with controller._service() as first:
                state_files = controller._state_files()
            with controller._service() as second:
                pass
        self.assertIsNot(first, second)
        self.assertEqual(open_store.call_count, 2)
        self.assertEqual(store.close.call_count, 2)
        frame_store.assert_called_with(store.db_path)
        history.assert_called_with(store.db_path)
        output_history.assert_called_with(store.db_path)
        output_history.return_value.close.assert_called()
        history.return_value.close.assert_called()
        frame_store.return_value.close.assert_called()
        self.assertEqual(set(state_files), set(UI_RESET_CLEANUP_ITEM_KEYS))
        self.assertEqual(state_files["column_order"], os.path.join(tmp.name, "config.json"))

    def test_service_gets_trash_and_movable_check(self) -> None:
        """정리 서비스에 휴지통 옮기기와 영구 삭제 위험 판정을 함께 넘기는지 검증"""
        window = _Window()
        controller = _controller(window)
        store = mock.MagicMock(db_path=os.path.join(ROOT, "data", "video_cache.sqlite3"))
        with (
            mock.patch.object(controller_module, "open_default_store", return_value=store),
            mock.patch.object(controller_module, "FrameAnalysisStore"),
            mock.patch.object(controller_module, "EncodeHistory"),
            mock.patch.object(controller_module, "EncodeOutputHistory"),
            mock.patch.object(controller_module, "get_state_path", return_value=os.path.join(ROOT, "config.json")),
            mock.patch.object(controller_module, "DataCleanupService") as service_class,
            controller._service(),
        ):
            pass
        kwargs = service_class.call_args.kwargs
        self.assertEqual(kwargs["trash_fn"], controller_module.move_to_trash)
        self.assertEqual(kwargs["movable_fn"], controller_module._movable_to_trash)

    def test_movable_to_trash_rejects_unconfirmed_drives(self) -> None:
        """휴지통을 확인할 수 없는 드라이브의 임시 파일은 옮길 수 없는 것으로 보는지 검증"""
        with mock.patch.object(controller_module, "needs_permanent_confirm", return_value=False):
            self.assertTrue(controller_module._movable_to_trash(os.path.join(ROOT, "a.mp4.part")))
        with mock.patch.object(controller_module, "needs_permanent_confirm", return_value=True):
            self.assertFalse(controller_module._movable_to_trash(os.path.join(ROOT, "b.mp4.part")))

    def test_state_is_read_from_session_every_time(self) -> None:
        """설정 초기화로 세션 상태가 새 인스턴스로 바뀌어도 최신 상태를 읽는지 검증"""
        window = _Window()
        controller = _controller(window)
        self.assertFalse(controller._ui_checks()["column_order"]())
        replacement = AppState()
        replacement.ui.shortcuts = {"file.open_dir": "Ctrl+Q"}
        window._session.state = replacement
        self.assertTrue(controller._ui_checks()["shortcuts_settings"]())



if __name__ == "__main__":
    unittest.main(verbosity=2)







