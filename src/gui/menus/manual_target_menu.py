"""영상별 출력 해상도 직접 지정 하위 메뉴의 항목 상태 규칙과 메뉴 구성 모듈"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu

from src.core.models import VideoEvaluation
from src.core.resolution_tiers import (
    KEEP_RESOLUTION_KEY,
    RESOLUTION_TIERS,
    Orientation,
    ResolutionTier,
    is_meaningful_downscale,
)
from src.core.smart_action import SmartAction

MANUAL_TARGET_MENU_TITLE: str = "출력 해상도 지정"
MANUAL_TARGET_AUTO_LABEL: str = "자동"
MANUAL_TARGET_KEEP_LABEL: str = "축소 안 함"
MANUAL_TARGET_CLEAR_ALL_LABEL: str = "직접 지정 모두 해제"
FRAME_CANDIDATES_LABEL: str = "화질 분석 후보 적용"
FRAME_CANDIDATES_TIP: str = (
    "화질 분석에서 축소 후보가 있는 영상의 출력 해상도를 후보 규격으로 지정합니다. "
    "후보가 없거나 예외 규칙, HDR 영상은 건너뜁니다."
)
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


def manual_target_action_id(value: str | None) -> str:
    """직접 지정 값을 단축키 명령 ID로 변환하며 자동 해제는 None으로 표현"""
    if value is None:
        return "manual_target.auto"
    if value == KEEP_RESOLUTION_KEY:
        return "manual_target.keep"
    return f"manual_target.{value}"


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


def _menu_text(label: str, shortcut_key: str) -> str:
    """단축키가 있으면 탭 뒤에 붙여 메뉴 우측에 표시, 표시 전용이며 실제 바인딩은 컨트롤러가 담당"""
    return f"{label}\t{shortcut_key}" if shortcut_key else label


def fill_manual_target_menu(
    menu: QMenu,
    evals: Sequence[VideoEvaluation],
    on_select: Callable[[str | None], object],
    *,
    on_clear_all: Callable[[], None] | None = None,
    on_apply_candidates: Callable[[], None] | None = None,
    shortcuts: Mapping[str, str] | None = None,
) -> None:
    """하위 메뉴 항목을 다시 구성하고, 화질 분석 후보 적용과 전체 해제 항목은 지정 시에만 추가"""
    menu.clear()
    keys: Mapping[str, str] = shortcuts or {}
    for index, option in enumerate(build_manual_target_options(evals)):
        if index == _SEPARATOR_BEFORE_INDEX:
            menu.addSeparator()
        shortcut_key = keys.get(manual_target_action_id(option.value), "")
        act = QAction(_menu_text(option.label, shortcut_key), menu)
        act.setCheckable(True)
        act.setChecked(option.checked)
        act.setEnabled(option.enabled)
        if option.tooltip:
            act.setToolTip(option.tooltip)
            act.setStatusTip(option.tooltip)
        act.triggered.connect(lambda _checked=False, v=option.value: on_select(v))
        menu.addAction(act)
    if on_apply_candidates is not None:
        menu.addSeparator()
        apply_act = QAction(FRAME_CANDIDATES_LABEL, menu)
        apply_act.setToolTip(FRAME_CANDIDATES_TIP)
        apply_act.setStatusTip(FRAME_CANDIDATES_TIP)
        apply_act.triggered.connect(lambda _checked=False: on_apply_candidates())
        menu.addAction(apply_act)
    if on_clear_all is not None:
        menu.addSeparator()
        clear_act = QAction(
            _menu_text(MANUAL_TARGET_CLEAR_ALL_LABEL, keys.get("manual_target.clear_all", "")),
            menu,
        )
        clear_act.triggered.connect(lambda _checked=False: on_clear_all())
        menu.addAction(clear_act)
