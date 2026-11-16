"""영상별 출력 해상도 직접 지정 하위 메뉴의 항목 상태 규칙과 메뉴 구성 모듈"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu

from src.core.models import SmartAction, VideoEvaluation
from src.core.resolution_tiers import (
    KEEP_RESOLUTION_KEY,
    RESOLUTION_TIERS,
    Orientation,
    ResolutionTier,
    is_meaningful_downscale,
)

MANUAL_TARGET_MENU_TITLE: str = "출력 해상도 지정"
MANUAL_TARGET_AUTO_LABEL: str = "자동"
MANUAL_TARGET_KEEP_LABEL: str = "축소 안 함"
MANUAL_TARGET_CLEAR_ALL_LABEL: str = "직접 지정 모두 해제"
_SEPARATOR_BEFORE_INDEX: int = 2
_EXCEPTION_BLOCK_TEXT: str = "예외 규칙 대상이라 처리하지 않습니다."
_HDR_BLOCK_TEXT: str = "HDR 영상은 색 정보 보호를 위해 처리하지 않습니다."
_NO_MEANINGFUL_DOWNSCALE_TEXT: str = "선택한 영상 중 이 규격으로 의미 있게 축소되는 영상이 없습니다."
_BLOCKED_ACTIONS: frozenset[SmartAction] = frozenset(
    {SmartAction.PASS_EXCEPTION, SmartAction.PASS_HDR}
)


@dataclass(frozen=True, slots=True)
class ManualTargetOption:
    """직접 지정 메뉴 항목 하나의 값, 표시 문구, 활성, 체크 상태와 비활성 사유"""

    value: str | None
    label: str
    enabled: bool
    checked: bool
    tooltip: str


def is_manual_target_blocked(ev: VideoEvaluation) -> bool:
    """예외 규칙, HDR 보존 행은 출력 해상도 판정 대상이 아니므로 규격 지정을 저장하지 않음"""
    return ev.smart_action in _BLOCKED_ACTIONS


def _all_checked(evals: Sequence[VideoEvaluation], value: str | None) -> bool:
    """행이 있고 모두 같은 직접 지정 값일 때만 체크 표시"""
    return bool(evals) and all(ev.manual_target == value for ev in evals)


def _blocked_tooltip(blocked: Sequence[VideoEvaluation]) -> str:
    """보존 대상만 선택했을 때 규격 항목을 비활성화하는 사유, 예외 규칙이 하나라도 있으면 예외 문구 우선"""
    if any(ev.smart_action == SmartAction.PASS_EXCEPTION for ev in blocked):
        return _EXCEPTION_BLOCK_TEXT
    return _HDR_BLOCK_TEXT


def tier_menu_label(tier: ResolutionTier) -> str:
    """규격 항목 표시 문구, 가로형 기준 박스 크기를 괄호로 덧붙임"""
    box_w, box_h = tier.box(Orientation.LANDSCAPE)
    return f"{tier.display} ({box_w}x{box_h})"


def build_manual_target_options(evals: Sequence[VideoEvaluation]) -> list[ManualTargetOption]:
    """선택 행 기준으로 자동, 축소 안 함, 규격 항목의 활성, 체크, 비활성 사유를 계산

    - 보존 대상(예외 규칙, HDR)만 선택했으면 축소 안 함과 규격 항목을 모두 비활성화
    - 그 규격으로 의미 있게 축소되는 보존 대상이 아닌 행이 하나도 없으면 그 규격 항목만 비활성화
    - 자동은 설정 해제 용도이므로 행이 있으면 항상 활성화
    - 규격 판별은 is_meaningful_downscale을 그대로 사용해 판정 규칙을 복제하지 않음
    """
    if not evals:
        return [
            ManualTargetOption(None, MANUAL_TARGET_AUTO_LABEL, False, False, ""),
            ManualTargetOption(KEEP_RESOLUTION_KEY, MANUAL_TARGET_KEEP_LABEL, False, False, ""),
            *(
                ManualTargetOption(tier.key, tier_menu_label(tier), False, False, "")
                for tier in reversed(RESOLUTION_TIERS)
            ),
        ]
    blocked = [ev for ev in evals if is_manual_target_blocked(ev)]
    open_evals = [ev for ev in evals if not is_manual_target_blocked(ev)]
    all_blocked_tooltip = _blocked_tooltip(blocked) if not open_evals else ""
    # - 보존 행은 지정 값을 받지 않으므로 체크 기준에서 제외. "자동"은 보존 행의 옛 값도 풀 수 있어 전체 기준이 필요하면 전체, 보존 행만 있으면 전체를 기준으로 함
    auto_basis = open_evals or list(evals)
    options = [
        ManualTargetOption(None, MANUAL_TARGET_AUTO_LABEL, True, _all_checked(auto_basis, None), ""),
        ManualTargetOption(
            KEEP_RESOLUTION_KEY,
            MANUAL_TARGET_KEEP_LABEL,
            bool(open_evals),
            _all_checked(open_evals, KEEP_RESOLUTION_KEY),
            all_blocked_tooltip,
        ),
    ]
    for tier in reversed(RESOLUTION_TIERS):
        has_meaningful = any(
            is_meaningful_downscale(ev.video.width, ev.video.height, tier) for ev in open_evals
        )
        if has_meaningful:
            tooltip = ""
        elif open_evals:
            tooltip = _NO_MEANINGFUL_DOWNSCALE_TEXT
        else:
            tooltip = all_blocked_tooltip
        options.append(
            ManualTargetOption(
                tier.key,
                tier_menu_label(tier),
                has_meaningful,
                _all_checked(open_evals, tier.key),
                tooltip,
            )
        )
    return options


def fill_manual_target_menu(
    menu: QMenu,
    evals: Sequence[VideoEvaluation],
    on_select: Callable[[str | None], object],
    *,
    on_clear_all: Callable[[], None] | None = None,
) -> None:
    """하위 메뉴 항목을 다시 구성하고, 전체 해제 항목은 지정 시에만 맨 아래에 추가"""
    menu.clear()
    for index, option in enumerate(build_manual_target_options(evals)):
        if index == _SEPARATOR_BEFORE_INDEX:
            menu.addSeparator()
        act = QAction(option.label, menu)
        act.setCheckable(True)
        act.setChecked(option.checked)
        act.setEnabled(option.enabled)
        if option.tooltip:
            act.setToolTip(option.tooltip)
            act.setStatusTip(option.tooltip)
        act.triggered.connect(lambda _checked=False, v=option.value: on_select(v))
        menu.addAction(act)
    if on_clear_all is not None:
        menu.addSeparator()
        clear_act = QAction(MANUAL_TARGET_CLEAR_ALL_LABEL, menu)
        clear_act.triggered.connect(lambda _checked=False: on_clear_all())
        menu.addAction(clear_act)
