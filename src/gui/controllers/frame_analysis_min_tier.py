"""화질 분석 최소 규격 선택 UI(머리글 분할 버튼, 빈 상태 안내)를 컨트롤러와 잇는 도우미

- 두 곳이 같은 최소 규격 목록과 추정 문구를 써야 하므로 배선을 한 함수로 모음
- 규격이 바뀌면 수동 대상 컨트롤러에 먼저 알린 뒤 두 UI 표기를 맞춤
"""

from collections.abc import Callable
from typing import Protocol

from src.gui.controllers.manual_target_controller import ManualTargetController
from src.gui.widgets.header_bar import HeaderBar


class MinTierOverlay(Protocol):
    """빈 상태 안내가 제공하는 최소 규격 선택 인터페이스"""

    frame_min_tier_changed: object

    def set_estimate_provider(self, provider: Callable[[str], str]) -> None:
        """팝업 추정 문구 공급자 연결"""
        ...

    def set_min_tier_provider(self, provider: Callable[[], str]) -> None:
        """지금 최소 규격을 돌려줄 공급자 연결"""
        ...

    def set_frame_min_tier(self, tier_key: str) -> None:
        """최소 규격 표기 반영"""
        ...


def _min_tier_overlay(empty_overlay: object) -> MinTierOverlay | None:
    """빈 상태 안내 인터페이스를 갖춘 객체만 골라 반환

    - 표 위젯이 아직 빈 상태 안내를 만들지 않았으면 None
    - isinstance로 실제 위젯에 의존하지 않고 필요한 메서드와 신호를 갖췄는지로 판단
    """
    if empty_overlay is None:
        return None
    required = ("set_estimate_provider", "set_min_tier_provider", "set_frame_min_tier")
    if not all(hasattr(empty_overlay, name) for name in required):
        return None
    if not hasattr(empty_overlay, "frame_min_tier_changed"):
        return None
    return empty_overlay  # type: ignore[return-value]


def attach_frame_min_tier(
    header: HeaderBar,
    empty_overlay: object,
    manual_targets: ManualTargetController,
    *,
    config_tier: Callable[[], str],
    estimate_text: Callable[[str], str],
) -> None:
    """머리글과 빈 상태 안내의 최소 규격 선택을 수동 대상 컨트롤러와 같은 값으로 맞춤"""
    overlay = _min_tier_overlay(empty_overlay)

    def on_tier_changed(tier_key: str) -> None:
        manual_targets.set_frame_analysis_min_tier(tier_key)
        header.set_frame_min_tier(tier_key)
        if overlay is not None:
            overlay.set_frame_min_tier(tier_key)

    header.split_analyze.set_estimate_provider(estimate_text)
    header.set_min_tier_provider(config_tier)
    header.set_frame_min_tier(config_tier())
    header.frame_min_tier_changed.connect(on_tier_changed)
    if overlay is not None:
        overlay.set_estimate_provider(estimate_text)
        overlay.set_min_tier_provider(config_tier)
        overlay.set_frame_min_tier(config_tier())
        overlay.frame_min_tier_changed.connect(on_tier_changed)  # type: ignore[attr-defined]


__all__ = ["MinTierOverlay", "attach_frame_min_tier"]
