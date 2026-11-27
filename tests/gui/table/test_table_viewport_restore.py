"""표 가로 스크롤 위치와 포커스 행 복원의 저장, 지연 적용, 사용자 조작 뒤 해제를 검증하는 GUI 테스트"""

import os
import unittest

from PyQt6.QtWidgets import QAbstractSlider, QApplication

from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.settings.ui_state import UiLayoutState
from src.gui.table.table_grouping import filename_at_row
from src.gui.table.table_viewport_restore import (
    FOCUS_RESTORE_SETTLE_MS,
    VIEWPORT_RESTORE_DEADLINE_MS,
    TableViewportRestoreController,
    attach_viewport_restore,
    capture_viewport_state,
)
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_ROW_COUNT: int = 120
_ROOT_DIR: str = "C:/work"


def _evaluations(count: int = _ROW_COUNT) -> list[VideoEvaluation]:
    """파일명이 fNNN.mp4 꼴로 붙은 평가 결과 목록"""
    videos = [make_video(filename=f"f{index:03d}.mp4") for index in range(count)]
    return VideoEvaluator.evaluate_all(_ROOT_DIR, videos, ClassifierConfig()).evaluations


class TableViewportRestoreTests(GuiTestCase):
    """가로 스크롤 위치와 포커스 행의 저장과 복원을 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.view.resize(400, 300)
        self.view.show()
        self.addCleanup(self.view.close)
        self.evals = _evaluations()
        self.controller = attach_viewport_restore(self.view, UiLayoutState())

    def _load(self) -> None:
        self.view.table_model.set_data_source(self.evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

    def test_capture_returns_zero_and_empty_name_for_empty_table(self) -> None:
        """행이 없으면 가로 스크롤 0과 빈 파일명을 저장함을 검증"""
        self.assertEqual(capture_viewport_state(self.view), (0, ""))

    def test_capture_reads_scroll_offset_and_focus_filename(self) -> None:
        """가로 스크롤 값과 포커스 행 파일명을 함께 저장함을 검증"""
        self._load()
        bar = self.view.horizontalScrollBar()
        assert bar is not None
        self.assertGreater(bar.maximum(), 0)
        # - 포커스 행 이동은 그 행이 보이도록 스크롤하므로 가로 위치는 그 뒤에 옮김
        self.view.set_current_row(7)
        bar.setValue(40)

        self.assertEqual(capture_viewport_state(self.view), (40, "f007.mp4"))

    def test_restore_applies_scroll_after_rows_arrive(self) -> None:
        """행이 아직 없을 때 예약한 가로 스크롤이 데이터 반영 뒤 적용됨을 검증"""
        self.controller.request(60, "")
        self._load()

        bar = self.view.horizontalScrollBar()
        assert bar is not None
        self.assertEqual(bar.value(), 60)
        self.assertIsNone(self.controller.pending_left)

    def test_scroll_beyond_range_clamps_into_range(self) -> None:
        """저장한 위치가 현재 스크롤 범위보다 크면 범위 안으로 잘림을 검증"""
        self.controller.request(10_000, "")
        self._load()

        bar = self.view.horizontalScrollBar()
        assert bar is not None
        self.assertGreater(bar.value(), 0)
        self.assertLessEqual(bar.value(), bar.maximum())

    def test_restore_moves_focus_without_touching_selection(self) -> None:
        """포커스 행을 파일명으로 복원하면서 선택은 그대로 두는지 검증"""
        self._load()
        self.view.select_filenames({"f001.mp4"})

        self.controller.request(0, "f042.mp4")
        _APP.processEvents()

        self.assertEqual(self.view.currentIndex().row(), 42)
        self.assertEqual(self.view.get_selected_filenames(), {"f001.mp4"})

    def test_restore_does_not_steal_keyboard_focus(self) -> None:
        """포커스 행 복원이 표에 키보드 포커스를 주지 않음을 검증"""
        self.view.clearFocus()
        self._load()

        attach_viewport_restore(self.view, UiLayoutState(table_current_filename="f042.mp4"))
        _APP.processEvents()

        self.assertEqual(self.view.currentIndex().row(), 42)
        self.assertIsNot(self.view.focusWidget(), self.view)

    def test_missing_focus_filename_keeps_current_row(self) -> None:
        """저장한 영상이 표에 없으면 포커스 행을 옮기지 않음을 검증"""
        self._load()
        self.view.set_current_row(3)

        attach_viewport_restore(self.view, UiLayoutState(table_current_filename="gone.mp4"))
        _APP.processEvents()

        self.assertEqual(self.view.currentIndex().row(), 3)

    def test_user_scroll_drops_the_pending_offset(self) -> None:
        """사용자가 스크롤바를 직접 움직이면 예약한 위치를 버림을 검증"""
        self.controller.request(60, "")
        bar = self.view.horizontalScrollBar()
        assert bar is not None
        bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepAdd)
        moved = bar.value()

        self.assertIsNone(self.controller.pending_left)

        self._load()
        # - 예약을 버렸으므로 데이터가 들어와도 사용자가 옮긴 위치에 머묾
        self.assertEqual(bar.value(), moved)

    def test_deadline_drops_offset_that_has_no_scroll_range(self) -> None:
        """행이 생겨도 스크롤 범위가 없으면 정한 시간 뒤 예약을 버림을 검증"""
        self.controller.request(60, "")
        self._load()
        bar = self.view.horizontalScrollBar()
        assert bar is not None
        bar.setMaximum(0)
        bar.setValue(0)

        self.controller.settle()

        self.assertIsNone(self.controller.pending_left)
        bar.setMaximum(500)
        _APP.processEvents()
        self.assertEqual(bar.value(), 0)

    def test_deadline_interval_is_bounded(self) -> None:
        """버리는 시각이 열 폭 계산 지연보다 여유 있게 길지 않음을 검증"""
        self.assertLessEqual(VIEWPORT_RESTORE_DEADLINE_MS, 3000)

    def test_attach_reuses_one_controller_per_view(self) -> None:
        """같은 표에 다시 연결해도 컨트롤러가 쌓이지 않음을 검증"""
        first = attach_viewport_restore(self.view, UiLayoutState())
        second = attach_viewport_restore(self.view, UiLayoutState())
        self.assertIs(first, second)

    def test_new_controller_starts_without_pending_restore(self) -> None:
        """새 컨트롤러는 예약 없이 만들어지고 해제는 예약을 모두 버림을 검증"""
        fresh = TableViewportRestoreController(self.view)
        self.assertIsNone(fresh.pending_left)
        fresh.request(30, "f001.mp4")
        fresh.release()
        self.assertIsNone(fresh.pending_left)
        self.assertEqual(self.view.currentIndex().row(), -1)

    def test_focus_restores_when_saved_video_arrives_in_a_later_rebuild(self) -> None:
        """저장한 포커스 영상이 첫 행 집합에 없다가 뒤 재구성에서 들어오면 그때 포커스를 옮김을 검증"""
        self.controller.request(0, "f100.mp4")
        self.view.table_model.set_data_source(self.evals[:10], root_dir=_ROOT_DIR)
        _APP.processEvents()
        self.assertNotEqual(self._current_filename(), "f100.mp4")

        self.view.table_model.set_data_source(self.evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

        self.assertEqual(self._current_filename(), "f100.mp4")

    def test_focus_reservation_is_dropped_when_the_settle_timer_fires(self) -> None:
        """정착 시간 타이머가 울리면 예약을 버려 뒤에 그 영상이 들어와도 포커스를 옮기지 않음을 검증"""
        self.controller.request(0, "f100.mp4")
        # - 저장한 영상이 없는 행 집합을 넣어 예약을 살린 채 정착 대기를 시작
        self.view.table_model.set_data_source(self.evals[:10], root_dir=_ROOT_DIR)
        _APP.processEvents()
        deadline = self.controller._focus_deadline
        self.assertTrue(deadline.isActive())
        self.assertTrue(deadline.isSingleShot())
        self.assertEqual(deadline.interval(), FOCUS_RESTORE_SETTLE_MS)

        deadline.timeout.emit()

        self.assertFalse(deadline.isActive())
        self.view.table_model.set_data_source(self.evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

        self.assertNotEqual(self._current_filename(), "f100.mp4")

    def test_user_focus_change_drops_the_focus_reservation(self) -> None:
        """포커스 예약이 남은 채 사용자가 다른 행을 고르면 그 예약을 버림을 검증

        - 행이 이미 있으면 request가 곧바로 예약을 소비하므로, 저장한 영상이 없는 행 집합을
          먼저 넣어 예약을 살려 둔 채 사용자 선택을 흉내 냄
        """
        self.controller.request(0, "f100.mp4")
        self.view.table_model.set_data_source(self.evals[:10], root_dir=_ROOT_DIR)
        _APP.processEvents()
        self.assertNotEqual(self._current_filename(), "f100.mp4")
        self.assertTrue(self.controller._focus_deadline.isActive())

        self.view.set_current_row(3)

        self.assertFalse(self.controller._focus_deadline.isActive())
        self.view.table_model.set_data_source(self.evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

        self.assertNotEqual(self._current_filename(), "f100.mp4")

    def test_focus_settle_time_is_bounded(self) -> None:
        """포커스 예약을 버리는 시각이 시작 복원 분석 간격보다 여유 있게 길지 않음을 검증"""
        self.assertLessEqual(FOCUS_RESTORE_SETTLE_MS, 10_000)
        self.assertGreater(FOCUS_RESTORE_SETTLE_MS, VIEWPORT_RESTORE_DEADLINE_MS)

    def test_release_drops_the_focus_reservation(self) -> None:
        """해제가 포커스 예약까지 버려 이후 재구성에서 포커스를 옮기지 않음을 검증"""
        self.controller.request(0, "f042.mp4")
        self.controller.release()

        self._load()

        self.assertNotEqual(self._current_filename(), "f042.mp4")

    def _current_filename(self) -> str:
        return filename_at_row(self.view, self.view.currentIndex().row())


if __name__ == "__main__":
    unittest.main()
