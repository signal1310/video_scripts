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


if __name__ == "__main__":
    unittest.main()
