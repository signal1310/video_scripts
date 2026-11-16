"""선택 영상의 출력 해상도 직접 지정과 모두 해제 요청을 재평가, 저장, 되돌리기 기록에 연결하는 컨트롤러 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.models import VideoEvaluation
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, ResolutionTier
from src.core.undo_history import UndoableSnapshot
from src.gui.manual_target_menu import is_manual_target_blocked
from src.gui.session_controller import SessionCoordinator
from src.gui.shortcuts_controller import manual_target_history_label
from src.gui.widgets.video_table_view import VideoTableView

_CLEAR_ALL_HISTORY_LABEL: str = "출력 해상도 직접 지정 모두 해제"
_CLEAR_CONFIRM_TITLE: str = "출력 해상도 직접 지정 해제"


@dataclass(frozen=True)
class ManualTargetHost:
    """컨트롤러가 메인 창에서 받는 의존성 묶음, 되돌리기 기록은 지연 호출로 받아 순환 참조를 피함"""

    parent: QWidget
    session: SessionCoordinator
    table_view: VideoTableView
    is_busy: Callable[[], bool]
    re_evaluate: Callable[[Callable[[], None]], None]
    capture_state: Callable[[], UndoableSnapshot]
    record_change: Callable[[str, UndoableSnapshot], None]


def _pending_output_tiers(value: str | None, targets: list[str]) -> dict[str, ResolutionTier | None]:
    """직접 지정 값으로 바로 보여 줄 출력 해상도, 자동 판정은 재평가 결과를 기다리므로 빈 맵"""
    if value is None:
        return {}
    tier = RESOLUTION_TIER_BY_KEY.get(value)
    return {name: tier for name in targets}


class ManualTargetController:
    """출력 해상도 직접 지정 요청을 처리하는 컨트롤러, 메뉴, 우클릭 메뉴, 단축키가 같은 진입점을 거침"""

    def __init__(self, host: ManualTargetHost) -> None:
        self._host = host

    def request(self, evals: list[VideoEvaluation], value: str | None) -> None:
        """선택 영상에 출력 해상도를 지정하거나 자동으로 되돌리고, 재분석 없이 다시 평가"""
        host = self._host
        # - 규격, 축소 안 함 지정은 예외, HDR 행에 저장하지 않아 나중에 예외를 풀어도 되살아나지 않게 함. 자동은 옛 값을 풀도록 모든 행에 적용
        targets = [
            ev.video.filename
            for ev in evals
            if value is None or not is_manual_target_blocked(ev)
        ]
        if host.is_busy() or not targets:
            return
        selected = host.table_view.get_selected_filenames()
        before = host.capture_state()
        host.table_view.table_model.set_pending_output_tiers(_pending_output_tiers(value, targets))
        host.session.apply_manual_targets(targets, value)
        host.re_evaluate(lambda: host.table_view.select_filenames(selected))
        host.record_change(manual_target_history_label(value), before)

    def clear(self) -> None:
        """현재 작업 폴더의 직접 지정을 확인 후 모두 해제, 지정이 없으면 묻지 않고 종료"""
        host = self._host
        if host.is_busy():
            return
        count = host.session.manual_target_count()
        if count == 0 or not self._confirm_clear(count):
            return
        selected = host.table_view.get_selected_filenames()
        before = host.capture_state()
        host.table_view.table_model.set_pending_output_tiers({})
        host.session.clear_manual_targets()
        host.re_evaluate(lambda: host.table_view.select_filenames(selected))
        host.record_change(_CLEAR_ALL_HISTORY_LABEL, before)

    def _confirm_clear(self, count: int) -> bool:
        answer = QMessageBox.question(
            self._host.parent,
            _CLEAR_CONFIRM_TITLE,
            f"현재 작업 폴더에서 직접 지정한 출력 해상도 {count}개를 모두 해제할까요?\n"
            "해제한 영상은 자동 판정으로 돌아갑니다. (되돌리기로 다시 복구할 수 있습니다.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
