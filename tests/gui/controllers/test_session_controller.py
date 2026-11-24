"""세션 코디네이터의 설정 반영 단위 테스트"""

import unittest
from typing import cast
from unittest import mock

from src.core.settings.state_codec import AppState
from src.gui.controllers.session_controller import SessionCoordinator


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
