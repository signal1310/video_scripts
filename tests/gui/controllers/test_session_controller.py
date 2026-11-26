"""세션 코디네이터의 설정 반영 단위 테스트"""

import os
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.settings.state_codec import AppState
from src.core.settings.ui_state import ColumnFilterSnapshot
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_STALE_ACTION_VALUE: str = "품질 23.9 인코딩"
_LOGGER_NAME: str = "src.gui.table.table_state_sync"


def _visible(proxy: VideoSortFilterProxyModel) -> list[str]:
    """프록시가 보여 주는 파일 이름 목록"""
    return [ev.video.filename for ev in proxy.iter_visible_evaluations()]


class RestoreTableStateTests(unittest.TestCase):
    """복원할 때 사라진 선택 값을 저장 상태에서도 지워 같은 안내가 반복되지 않는지 검증"""

    def _coordinator(self, columns: dict[str, ColumnFilterSnapshot]) -> SessionCoordinator:
        """열 필터 저장값만 가진 세션 조율자, 저장 예약은 대역"""
        coordinator = SessionCoordinator.__new__(SessionCoordinator)
        coordinator.state = AppState()
        coordinator.state.ui.filters.columns = dict(columns)
        coordinator.state.ui.filters.sort_keys = []
        coordinator._schedule_persist = mock.Mock()
        return coordinator

    def _table_view(self) -> mock.Mock:
        """정렬 화살표만 쓰는 표 뷰 대역"""
        header = QHeaderView(Qt.Orientation.Horizontal)
        self.addCleanup(header.deleteLater)
        view = mock.Mock()
        view.filter_header = header
        return view

    def _proxy(self, frame_results: dict[str, FrameAnalysisResult] | None = None) -> VideoSortFilterProxyModel:
        model = VideoTableModel()
        model.set_data_source(
            [make_eval(video=make_video(filename="a.mp4", codec="h264"))], root_dir="C:/work"
        )
        if frame_results is not None:
            model.set_frame_analysis_results(frame_results)
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        return proxy

    def test_stale_column_is_removed_from_the_saved_state(self) -> None:
        """사라진 선택 값만 있던 열은 저장 상태에서 지우고 저장을 예약하는지 검증"""
        coordinator = self._coordinator(
            {"action": ColumnFilterSnapshot(selected_values=(_STALE_ACTION_VALUE,))}
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            coordinator.restore_table_state(self._proxy(), self._table_view())
        self.assertEqual(coordinator.state.ui.filters.columns, {})
        cast("mock.Mock", coordinator._schedule_persist).assert_called_once()

    def test_second_restore_keeps_the_cleaned_state_and_does_not_log(self) -> None:
        """한 번 복원한 뒤에는 저장 상태에 사라진 값이 없어 두 번째 복원이 기록을 남기지 않는지 검증"""
        coordinator = self._coordinator(
            {
                "action": ColumnFilterSnapshot(
                    selected_values=(make_eval().action_label, _STALE_ACTION_VALUE)
                )
            }
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            coordinator.restore_table_state(self._proxy(), self._table_view())
        saved = coordinator.state.ui.filters.columns
        self.assertEqual(
            saved, {"action": ColumnFilterSnapshot(selected_values=(make_eval().action_label,))}
        )
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            coordinator.restore_table_state(self._proxy(), self._table_view())
        self.assertEqual(coordinator.state.ui.filters.columns, saved)

    def test_unchanged_state_does_not_schedule_a_save(self) -> None:
        """버릴 값이 없으면 저장 상태를 건드리지도 저장을 예약하지도 않는지 검증"""
        live = make_eval().action_label
        coordinator = self._coordinator(
            {"action": ColumnFilterSnapshot(selected_values=(live,))}
        )
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            coordinator.restore_table_state(self._proxy(), self._table_view())
        self.assertEqual(
            coordinator.state.ui.filters.columns,
            {"action": ColumnFilterSnapshot(selected_values=(live,))},
        )
        cast("mock.Mock", coordinator._schedule_persist).assert_not_called()

    def test_stale_active_frame_filter_is_removed_and_persisted(self) -> None:
        """이전 표기의 활성 화면 선택은 복원 뒤 저장 상태에서도 지우고 저장을 예약하는지 검증"""
        coordinator = self._coordinator(
            {"active_frame": ColumnFilterSnapshot(selected_values=("전체",))}
        )
        result = FrameAnalysisResult(
            active_region=ActiveRegion(1920, 1080, 0, 0, False),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier="1080p",
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        proxy = self._proxy({"a.mp4": result})
        with self.assertLogs(_LOGGER_NAME, level="INFO"):
            coordinator.restore_table_state(proxy, self._table_view())
        self.assertEqual(coordinator.state.ui.filters.columns, {})
        self.assertIsNone(proxy.get_column_filter(VideoTableModel.COL_ACTIVE_FRAME))
        self.assertEqual(_visible(proxy), ["a.mp4"])
        cast("mock.Mock", coordinator._schedule_persist).assert_called_once()


class ApplyFrameMinTierTests(unittest.TestCase):
    """화질 분석 대상 해상도 변경이 세션 상태, 분류기, 저장에 반영되는지 검증"""

    def test_updates_state_classifier_and_persists(self) -> None:
        """새 측정 대상 값이 반환 설정과 상태에 담기고 분류기, 저장이 함께 갱신됨"""
        fake = mock.Mock()
        fake.state = AppState()
        result = SessionCoordinator.apply_frame_min_tier(
            cast("SessionCoordinator", fake), "720p"
        )
        self.assertEqual(result.encode.frame_analysis_min_tier, "720p")
        self.assertEqual(
            fake.state.to_classifier_config().encode.frame_analysis_min_tier, "720p"
        )
        fake.classifier.set_config.assert_called_once_with(result)
        fake.persist.assert_called_once()


class ManualTargetAndSelectionResetTests(unittest.TestCase):
    """정리 뒤 직접 지정과 선택 기억을 DB 기준으로 다시 맞추는지 검증"""

    def _coordinator(self) -> SessionCoordinator:
        """DB와 설정 저장을 대역으로 바꾼 세션 조율자"""
        coordinator = SessionCoordinator.__new__(SessionCoordinator)
        coordinator.state = AppState()
        coordinator.state.ui.manual_target_tiers = {"root": {"a.mp4": "720p"}}
        coordinator._saved_manual_targets = {"root": {"a.mp4": "720p"}}
        coordinator.classifier = mock.Mock()
        coordinator._saved_selections_by_root = {"root": frozenset({"a.mp4"})}
        coordinator._pending_selection_save = ("root", frozenset({"a.mp4"}))
        coordinator._selection_persist_timer = None
        return coordinator

    def test_reload_manual_targets_clears_memory_and_resets_saved_baseline(self) -> None:
        """직접 지정을 DB에서 다시 읽어 빈 값을 반영하고 저장 기준도 맞추는지 검증"""
        coordinator = self._coordinator()
        with mock.patch(
            "src.gui.controllers.session_controller.load_manual_target_store", return_value={}
        ) as load:
            coordinator.reload_manual_targets()
        load.assert_called_once_with({})
        self.assertEqual(coordinator.state.ui.manual_target_tiers, {})
        self.assertEqual(coordinator._saved_manual_targets, {})
        classifier = cast("mock.Mock", coordinator.classifier)
        classifier.set_manual_target_store.assert_called_once_with({})

    def test_clear_saved_selections_drops_cached_and_pending_selection(self) -> None:
        """선택 기억을 비우면 세션이 기억한 값과 대기 중인 저장이 사라지는지 검증"""
        coordinator = self._coordinator()
        coordinator.clear_saved_selections()
        self.assertEqual(coordinator._saved_selections_by_root, {})
        self.assertIsNone(coordinator._pending_selection_save)


if __name__ == "__main__":
    unittest.main()
