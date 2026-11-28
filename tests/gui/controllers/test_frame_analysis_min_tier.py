"""화질 분석 최소 규격 선택 UI 배선 도우미 검증"""

import unittest
from typing import cast

from src.gui.controllers.frame_analysis_min_tier import attach_frame_min_tier
from src.gui.controllers.manual_target_controller import ManualTargetController


class _Signal:
    """connect와 emit만 지원하는 가짜 시그널"""

    def __init__(self) -> None:
        self.slots: list[object] = []

    def connect(self, slot: object) -> None:
        self.slots.append(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self.slots):
            assert callable(slot)
            slot(*args)


class _FakeSplitButton:
    """추정 문구 공급자만 저장하는 분할 버튼 대역"""

    def __init__(self) -> None:
        self.estimate_provider: object = None

    def set_estimate_provider(self, provider: object) -> None:
        self.estimate_provider = provider


class _FakeHeader:
    """머리글의 최소 규격 배선 지점만 갖춘 대역"""

    def __init__(self) -> None:
        self.split_analyze = _FakeSplitButton()
        self.frame_min_tier_changed = _Signal()
        self.tier_provider: object = None
        self.saved_tier: str = ""

    def set_min_tier_provider(self, provider: object) -> None:
        self.tier_provider = provider

    def set_frame_min_tier(self, tier_key: str) -> None:
        self.saved_tier = tier_key


class _FakeOverlay:
    """빈 상태 안내의 최소 규격 인터페이스만 갖춘 대역"""

    def __init__(self) -> None:
        self.frame_min_tier_changed = _Signal()
        self.estimate_provider: object = None
        self.tier_provider: object = None
        self.saved_tier: str = ""

    def set_estimate_provider(self, provider: object) -> None:
        self.estimate_provider = provider

    def set_min_tier_provider(self, provider: object) -> None:
        self.tier_provider = provider

    def set_frame_min_tier(self, tier_key: str) -> None:
        self.saved_tier = tier_key


class _FakeManualTargets:
    """최소 규격 저장만 기록하는 대역"""

    def __init__(self) -> None:
        self.tier: str = ""

    def set_frame_analysis_min_tier(self, tier_key: str) -> None:
        self.tier = tier_key


class _BareObject:
    """최소 규격 인터페이스가 없는 객체(빈 상태 안내가 아직 없을 때)"""


class AttachFrameMinTierTests(unittest.TestCase):
    """머리글, 빈 상태 안내, 수동 대상이 같은 최소 규격을 쓰는지 검증"""

    def test_header_and_overlay_share_the_min_tier(self) -> None:
        header = _FakeHeader()
        overlay = _FakeOverlay()
        targets = _FakeManualTargets()
        attach_frame_min_tier(
            cast("object", header),  # type: ignore[arg-type]
            overlay,
            cast("ManualTargetController", targets),
            config_tier=lambda: "1080p",
            estimate_text=lambda _mode: "예상",
        )

        self.assertEqual(header.saved_tier, "1080p")
        self.assertEqual(overlay.saved_tier, "1080p")
        self.assertIsNotNone(overlay.estimate_provider)
        self.assertIsNotNone(overlay.tier_provider)

        # - 어느 쪽에서 바꿔도 수동 대상과 다른 UI가 같은 값을 씀
        header.frame_min_tier_changed.emit("720p")
        self.assertEqual(targets.tier, "720p")
        self.assertEqual(overlay.saved_tier, "720p")

        overlay.frame_min_tier_changed.emit("480p")
        self.assertEqual(targets.tier, "480p")
        self.assertEqual(header.saved_tier, "480p")

    def test_overlay_without_the_interface_is_skipped(self) -> None:
        targets = _FakeManualTargets()
        attach_frame_min_tier(
            cast("object", _FakeHeader()),  # type: ignore[arg-type]
            _BareObject(),
            cast("ManualTargetController", targets),
            config_tier=lambda: "1080p",
            estimate_text=lambda _mode: "예상",
        )
        # - 빈 상태 안내가 없어도 머리글과 수동 대상 배선은 그대로 동작
        self.assertEqual(targets.tier, "")


if __name__ == "__main__":
    unittest.main()
