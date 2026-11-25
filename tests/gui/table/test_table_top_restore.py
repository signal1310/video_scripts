"""표 맨 위 영상의 저장과 복원, 저장한 파일이 없을 때 가까운 행으로 이동하는 동작을 검증하는 GUI 테스트"""

import json
import os
import tempfile
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractSlider, QApplication

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.settings.app_state import AppState, load_app_state, save_app_state
from src.core.settings.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments
from src.core.settings.ui_state import GROUP_BY_FOLDER, UiFilterState, UiLayoutState, UiState
from src.gui.controllers.frame_analysis_controller import FRAME_ANALYSIS_FLUSH_MS
from src.gui.main_window import MainWindow
from src.gui.table.table_model import VideoTableModel
from src.gui.table.table_top_restore import ANCHOR_SETTLE_MS
from src.gui.table.video_table_view import VideoTableView
from src.service.analysis_store import AnalysisStore
from src.service.cache import DEFAULT_CACHE_REGISTRY
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase, isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])
# - PyQt6 스텁이 QTest 함수를 인스턴스 메서드로 선언해 정적 호출의 형식 검사가 실패하므로 호출 가능한 형태로 받음
_send_mouse_click: Callable[..., None] = QTest.mouseClick
_send_key_click: Callable[..., None] = QTest.keyClick

_ROW_COUNT: int = 200
# - 화질 분석 결과가 늦게 반영되며 기준 영상 위에 새로 끼어드는 행 수
_INSERTED_COUNT: int = 250
_ROW_PREFIX: str = "f"
_ROOT_DIR: str = "C:/work"
# - 메인 창 수준 재현에서 실제처럼 큰 표를 쓰기 위한 행 수
_WINDOW_ROW_COUNT: int = 5000
_WINDOW_INSERTED_COUNT: int = 250


def _evaluations(
    count: int, *, prefix: str = _ROW_PREFIX, folder: str = "", start: int = 0
) -> list[VideoEvaluation]:
    """파일명이 folder/prefixNNN.mp4 꼴로 붙은 평가 결과 목록"""
    videos = [
        make_video(filename=f"{folder}{prefix}{index:03d}.mp4")
        for index in range(start, start + count)
    ]
    return VideoEvaluator.evaluate_all(_ROOT_DIR, videos, ClassifierConfig()).evaluations


class TestTableTopRestore(GuiTestCase):
    """맨 위 영상을 기준으로 표 스크롤 위치를 저장하고 다음 데이터 반영 때 복원하는 동작을 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.view.resize(400, 300)
        self.view.show()
        self.addCleanup(self.view.close)
        self.evals = _evaluations(_ROW_COUNT)

    def _load(self, evals: list[VideoEvaluation]) -> None:
        self.view.table_model.set_data_source(evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

    def test_top_row_key_follows_scroll(self) -> None:
        """스크롤한 뒤 맨 위에 보이는 행의 파일명과 행 번호를 반환함을 검증"""
        self._load(self.evals)
        self.view.scrollTo(self.view.group_model.index(120, 0), self.view.ScrollHint.PositionAtTop)
        _APP.processEvents()
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        filename, row = top
        self.assertEqual(filename, self.evals[row].video.filename)
        self.assertEqual(row, self.view.rowAt(0))

    def test_top_row_key_is_none_for_empty_table(self) -> None:
        """표가 비어 있으면 맨 위 행 정보가 없음을 검증"""
        self.assertIsNone(self.view.top_visible_row_key())

    def test_restore_moves_saved_file_to_top_when_rows_arrive_later(self) -> None:
        """행이 아직 없을 때 요청한 복원이 분석 결과가 들어온 뒤 저장한 파일을 맨 위로 옮김을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))

    def test_missing_file_falls_back_to_saved_row(self) -> None:
        """저장한 파일이 표에 없으면 저장한 행 번호 근처로 이동함을 검증"""
        self.view.request_top_restore("gone.mp4", 80)
        self._load(self.evals)
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top[1], 80)

    def test_missing_file_past_end_clamps_to_last_row(self) -> None:
        """저장한 행 번호가 현재 행 수보다 크면 마지막 행 근처로 이동함을 검증"""
        self.view.request_top_restore("gone.mp4", _ROW_COUNT + 50)
        self._load(self.evals[:40])
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertLess(top[1], 40)

    def test_rebuild_with_rows_inserted_above_keeps_the_same_video_on_top(self) -> None:
        """첫 반영 뒤 위쪽에 행이 더 생겨도 저장한 영상이 맨 위에 남음을 검증

        - 시작 복원이 첫 행 재구성에서 기준을 놓으면, 뒤이은 화질 분석 결과 반영이 위쪽에 행을
          넣으면서 같은 스크롤 값이 다른 영상을 가리켜 저장했던 영상이 밀려남
        """
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))

        self._load([*_evaluations(_INSERTED_COUNT, prefix="a"), *self.evals])
        _APP.processEvents()

        top = self.view.top_visible_row_key()
        assert top is not None
        self.assertEqual(top, ("f150.mp4", 150 + _INSERTED_COUNT))
        self.assertEqual(self.view.rowAt(0), 150 + _INSERTED_COUNT)

    def test_layout_change_from_frame_results_keeps_the_same_video_on_top(self) -> None:
        """정렬 순서를 다시 계산하는 layoutChanged 뒤에도 저장한 영상이 맨 위에 남음을 검증"""
        self.view.proxy_model.sort(VideoTableModel.COL_NAME, Qt.SortOrder.DescendingOrder)
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", _ROW_COUNT - 1 - 150))

        # - 정렬을 되돌리면 기준 영상의 행이 150으로 바뀌므로, 기준을 다시 찾아 맨 위에 놓아야 함
        self.view.proxy_model.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        _APP.processEvents()

        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))
        self.assertEqual(self.view.rowAt(0), 150)

    def test_group_headers_keep_the_same_video_on_top(self) -> None:
        """그룹 머리글 행이 끼어 있어도 기준 영상이 머리글 아래 맨 위에 남음을 검증"""
        self.view.group_model.set_group_by(GROUP_BY_FOLDER)
        grouped = _evaluations(_ROW_COUNT, folder="g1/")
        self.view.request_top_restore("g1/f150.mp4", 150)
        self._load(grouped)
        _APP.processEvents()
        self.assertEqual(self.view.top_visible_row_key(), ("g1/f150.mp4", 151))

        self._load([*_evaluations(_INSERTED_COUNT, folder="a0/"), *grouped])
        _APP.processEvents()

        top = self.view.top_visible_row_key()
        assert top is not None
        self.assertEqual(top[0], "g1/f150.mp4")
        self.assertFalse(self.view.group_model.is_group_row(self.view.rowAt(0)))

    def test_user_scroll_ends_the_anchor_so_later_rebuilds_do_not_move_the_view(self) -> None:
        """사용자가 스크롤바로 직접 움직이면 기준이 풀려 이후 행 재구성이 화면을 옮기지 않음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        scroll_bar = self.view.verticalScrollBar()
        assert scroll_bar is not None
        scroll_bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepAdd)
        _APP.processEvents()
        moved = self.view.top_visible_row_key()
        assert moved is not None
        self.assertFalse(self.view.top_restore.active)

        self._load([*_evaluations(_INSERTED_COUNT, prefix="a"), *self.evals])
        _APP.processEvents()
        # - 기준이 풀린 뒤에는 화면 스크롤 값이 그대로라 같은 행에 다른 영상이 남음
        after = self.view.top_visible_row_key()
        assert after is not None
        self.assertEqual(after[1], moved[1])

    def test_wheel_scroll_ends_the_anchor(self) -> None:
        """휠 스크롤도 사용자 조작으로 보아 기준을 풀음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        viewport = self.view.viewport()
        assert viewport is not None
        center = QPointF(viewport.rect().center())
        QApplication.sendEvent(
            viewport,
            QWheelEvent(
                center,
                center,
                QPoint(0, 0),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            ),
        )
        _APP.processEvents()
        self.assertFalse(self.view.top_restore.active)

    def test_click_on_a_row_ends_the_anchor(self) -> None:
        """행을 눌러 위치를 고르면 이후 행 재구성이 화면을 옮기지 않음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        rect = self.view.visualRect(self.view.group_model.index(160, 0))
        _send_mouse_click(
            self.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        _APP.processEvents()
        self.assertFalse(self.view.top_restore.active)

    def test_navigation_key_ends_the_anchor(self) -> None:
        """페이지 키 이동은 사용자가 위치를 고른 것으로 보아 기준을 풀음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        _send_key_click(self.view, Qt.Key.Key_PageDown)
        _APP.processEvents()
        self.assertFalse(self.view.top_restore.active)

    def test_settle_timeout_ends_the_anchor(self) -> None:
        """정착 시간 동안 행 재구성이 없으면 기준이 풀려 다음 재구성이 화면을 옮기지 않음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        self.view.top_restore.settle()
        self.assertFalse(self.view.top_restore.active)

        before_row = self.view.rowAt(0)
        self._load([*_evaluations(_INSERTED_COUNT, prefix="a"), *self.evals])
        _APP.processEvents()
        # - 기준이 풀린 뒤에는 스크롤 값을 다시 맞추지 않아 화면 행 위치가 그대로임
        self.assertEqual(self.view.rowAt(0), before_row)

    def test_settling_interval_is_long_enough_for_one_frame_result_batch(self) -> None:
        """정착 시간이 화질 분석 결과 반영 간격보다 길어 진행 중인 반영이 기준을 풀지 않음을 검증"""
        self.assertGreater(ANCHOR_SETTLE_MS, FRAME_ANALYSIS_FLUSH_MS)

    def test_saved_key_stays_on_the_anchor_until_it_is_released(self) -> None:
        """기준이 살아 있는 동안에는 화면 위치가 아니라 기준 영상을 저장함을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        # - 기준이 살아 있는 동안 스크롤이 어긋나도 저장 값은 기준 영상의 현재 행
        self.view.scrollTo(
            self.view.group_model.index(10, 0), self.view.ScrollHint.PositionAtTop
        )
        _APP.processEvents()
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))

    def test_stale_anchor_is_dropped_when_the_folder_changes(self) -> None:
        """폴더 전환처럼 표가 비면 이전 기준을 놓아 다음 폴더 위치를 붙잡지 않음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        _APP.processEvents()

        self.view.clear_selection_history()
        self.view.table_model.set_data_source([])
        _APP.processEvents()
        self.assertFalse(self.view.top_restore.active)

    def test_saved_collapsed_group_is_applied_before_the_anchor_resolves(self) -> None:
        """저장한 접힘이 첫 행 구성에서 기준 영상보다 먼저 적용되어 기준 영상이 머리글 바로 아래에 남음을 검증"""
        from src.gui.table.table_grouping import RowGroupingCoordinator
        from src.gui.widgets.filter_bar import FilterTuningBar

        view = VideoTableView()
        self.addCleanup(view.close)
        ui = UiState(
            filters=UiFilterState(
                group_by=GROUP_BY_FOLDER,
                collapsed_groups={GROUP_BY_FOLDER: ["a0"]},
            )
        )
        RowGroupingCoordinator(view, FilterTuningBar(), ui, lambda: None)
        view.request_top_restore("g1/f150.mp4", 0)
        view.table_model.set_data_source(
            [*_evaluations(_ROW_COUNT, folder="a0/"), *_evaluations(_ROW_COUNT, folder="g1/")],
            root_dir=_ROOT_DIR,
        )
        _APP.processEvents()
        self.assertEqual(view.group_model.collapsed_labels, ["a0"])
        # - 접힌 a0 그룹의 영상 200개가 숨고 머리글 2개만 남음
        self.assertEqual(view.group_model.rowCount(), 2 + _ROW_COUNT)
        top = view.top_visible_row_key()
        assert top is not None
        self.assertEqual(top[0], "g1/f150.mp4")
        self.assertEqual(top[1], view.rowAt(0))

    def test_layout_restored_from_the_state_file_survives_two_rebuilds(self) -> None:
        """저장한 상태 파일의 맨 위 영상이 첫 반영과 위쪽 행 삽입 반영 뒤에도 맨 위에 남음을 검증"""
        with tempfile.TemporaryDirectory() as root:
            state_path = os.path.join(root, "config.json")
            state = AppState(
                ui=UiState(
                    layout=UiLayoutState(table_top_filename="f150.mp4", table_top_row=150)
                )
            )
            with patch("src.core.settings.app_state.get_state_path", return_value=state_path):
                save_app_state(state)
                loaded = load_app_state()
        self.assertEqual(loaded.ui.layout.table_top_filename, "f150.mp4")

        self.view.request_top_restore(
            loaded.ui.layout.table_top_filename, loaded.ui.layout.table_top_row
        )
        self._load(self.evals)
        _APP.processEvents()
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))

        self._load([*_evaluations(_INSERTED_COUNT, prefix="a"), *self.evals])
        _APP.processEvents()
        self.assertEqual(
            self.view.top_visible_row_key(), ("f150.mp4", 150 + _INSERTED_COUNT)
        )


class TestStartupTopRestoreWindow(GuiTestCase):
    """저장한 상태 파일과 실제 메인 창으로 시작 위치 복원이 여러 번의 행 재구성을 견디는지 검증"""

    def setUp(self) -> None:
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        DEFAULT_CACHE_REGISTRY.invalidate()
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = os.path.join(self._tmp.name, "work")
        os.makedirs(self.work)
        data_dir = os.path.join(self._tmp.name, "data")
        os.makedirs(data_dir)
        isolate_app_state(self, self._tmp.name)
        self.store = AnalysisStore(os.path.join(data_dir, "video_cache.sqlite3"))
        self.addCleanup(self.store.close)
        marker = patch("src.service.analyzed_dirs.get_data_dir", return_value=data_dir)
        marker.start()
        self.addCleanup(marker.stop)
        for target in (
            "src.classifier.open_default_store",
            "src.gui.controllers.session_controller.open_default_store",
            "src.gui.controllers.restore_resolver.open_default_store",
        ):
            shared = patch(target, return_value=self.store)
            shared.start()
            self.addCleanup(shared.stop)

    def _write_videos(self, names: list[str]) -> list[VideoEvaluation]:
        """이름 목록으로 최소 크기 파일을 만들고 그 평가 결과 목록을 반환"""
        videos = []
        for name in names:
            with open(os.path.join(self.work, name), "wb") as f:
                f.write(name.encode("utf-8"))
            videos.append(make_video(filename=name))
        return VideoEvaluator.evaluate_all(self.work, videos, ClassifierConfig()).evaluations

    def _save_anchor(self, filename: str, row: int) -> None:
        """저장 상태 파일에 맨 위 영상 기준만 기록

        - 테스트 격리가 설정 경로를 임시 폴더로 돌려 두므로 창이 읽는 파일에 바로 씀
        """
        save_app_state(
            AppState(
                ui=UiState(
                    layout=UiLayoutState(table_top_filename=filename, table_top_row=row)
                )
            )
        )

    def _open_window(self) -> MainWindow:
        mw = MainWindow(classifier=VideoClassifier(root_dir=self.work))
        self.addCleanup(mw.close)
        mw.show()
        QApplication.processEvents()
        return mw

    def test_saved_file_stays_on_top_after_the_analysis_report_rebuilds_rows(self) -> None:
        """시작 좌표 복원 뒤 분석 결과 반영이 위쪽에 행을 넣어도 저장한 영상이 맨 위에 남음을 검증"""
        names = [f"v{index:04d}.mp4" for index in range(_WINDOW_ROW_COUNT)]
        evaluations = self._write_videos(names)
        self._save_anchor("v1500.mp4", 1500)

        mw = self._open_window()
        table = mw.table_view
        table.table_model.set_data_source(evaluations, root_dir=self.work)
        QApplication.processEvents()
        self.assertEqual(table.top_visible_row_key(), ("v1500.mp4", 1500))

        # - 화질 분석 결과와 정렬이 늦게 반영되어 기준 영상 위로 행이 끼어드는 상황
        older = self._write_videos([f"a{index:04d}.mp4" for index in range(_WINDOW_INSERTED_COUNT)])
        table.table_model.set_data_source(
            [*older, *evaluations], root_dir=self.work
        )
        QApplication.processEvents()

        top = table.top_visible_row_key()
        assert top is not None
        self.assertEqual(top, ("v1500.mp4", 1500 + _WINDOW_INSERTED_COUNT))
        self.assertEqual(table.rowAt(0), 1500 + _WINDOW_INSERTED_COUNT)

    """맨 위 영상 저장 필드의 직렬화 왕복과 잘못된 행 번호 보정을 검증"""

    def test_round_trip_keeps_top_file_and_row(self) -> None:
        """저장한 파일명과 행 번호가 JSONC 렌더링과 파싱을 거쳐도 그대로 복원됨을 검증"""
        state = AppState(ui=UiState(layout=UiLayoutState(table_top_filename="a.mp4", table_top_row=42)))
        parsed = parse_state_mapping(json.loads(strip_jsonc_comments(render_state_jsonc(state))))
        self.assertEqual(parsed.ui.layout.table_top_filename, "a.mp4")
        self.assertEqual(parsed.ui.layout.table_top_row, 42)

    def test_negative_row_is_sanitized_to_zero(self) -> None:
        """음수 행 번호는 0으로 보정됨을 검증"""
        parsed = parse_state_mapping({"ui": {"layout": {"table_top_filename": "a.mp4", "table_top_row": -3}}})
        self.assertEqual(parsed.ui.layout.table_top_row, 0)


if __name__ == "__main__":
    unittest.main()
