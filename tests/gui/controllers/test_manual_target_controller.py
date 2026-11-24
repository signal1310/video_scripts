"""출력 해상도 직접 지정 컨트롤러의 단일 지정, 후보 적용, 모두 해제 검증"""

import unittest
from typing import cast
from unittest import mock

from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.settings.undo_history import UndoableSnapshot
from src.gui.controllers.manual_target_controller import (
    ManualTargetController,
    ManualTargetHost,
    _pending_output_tiers,
)
from src.gui.controllers.session_controller import SessionCoordinator
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])


class _Model:
    def __init__(self) -> None:
        self.pending: dict[str, object] = {}
        self.candidates: dict[str, str] = {"a.mp4": "720p", "b.mp4": "1080p"}

    def set_pending_output_tiers(self, tiers: dict[str, object]) -> None:
        self.pending = dict(tiers)

    def frame_candidates(self, evals: list[VideoEvaluation]) -> dict[str, str]:
        return {ev.video.filename: self.candidates[ev.video.filename] for ev in evals if ev.video.filename in self.candidates}


class _Table:
    def __init__(self) -> None:
        self.table_model = _Model()
        self.selected: set[str] = set()

    def get_selected_filenames(self) -> set[str]:
        return set(self.selected)

    def select_filenames(self, names: set[str]) -> None:
        self.selected = set(names)


class _Session:
    def __init__(self) -> None:
        self.applied: list[tuple[list[str], str | None]] = []
        self.cleared = 0
        self.count = 1
        self.min_tiers: list[str] = []

    def apply_manual_targets(self, filenames: list[str], value: str | None) -> None:
        self.applied.append((list(filenames), value))

    def apply_frame_min_tier(self, min_tier: str) -> None:
        self.min_tiers.append(min_tier)

    def manual_target_count(self) -> int:
        return self.count

    def clear_manual_targets(self) -> None:
        self.cleared += 1


def _snapshot() -> UndoableSnapshot:
    """되돌리기 기록에 넘길 기본 스냅샷"""
    return UndoableSnapshot(
        config=ClassifierConfig(),
        exception_tokens=(),
        disabled_exception_tokens=(),
        manual_target_tiers={},
    )


class ManualTargetControllerTests(unittest.TestCase):
    """직접 지정 경로가 세션 저장, 재평가, 되돌리기 기록과 안내로 이어지는지 검증"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.table = _Table()
        self.session = _Session()
        self.notes: list[str] = []
        self.changes: list[str] = []
        self.busy: bool = False
        session = mock.Mock(wraps=self.session, spec=["apply_manual_targets", "manual_target_count", "clear_manual_targets", "apply_frame_min_tier"])
        table_view = mock.Mock(wraps=self.table, spec=["get_selected_filenames", "select_filenames", "table_model"])
        table_view.table_model = self.table.table_model
        self.host = ManualTargetHost(
            parent=self.parent,
            session=cast("SessionCoordinator", session),
            table_view=cast("VideoTableView", table_view),
            is_busy=lambda: self.busy,
            re_evaluate=lambda done: done(),
            capture_state=_snapshot,
            record_change=lambda label, before: self.changes.append(label),
            notify=self.notes.append,
        )
        self.controller = ManualTargetController(self.host)
        self.evals = [make_eval(video=make_video(filename="a.mp4")), make_eval(video=make_video(filename="b.mp4"))]

    def test_request_applies_one_value_and_records_change(self) -> None:
        self.assertTrue(self.controller.request(self.evals, "720p"))
        self.assertEqual(self.session.applied, [(["a.mp4", "b.mp4"], "720p")])
        self.assertEqual(self.table.table_model.pending["a.mp4"], RESOLUTION_TIER_BY_KEY["720p"])
        self.assertEqual(self.changes, ["출력 해상도 지정 (720p, 2개)"])
        self.assertIn("선택 2개 영상", self.notes[-1])

    def test_request_reports_false_when_busy_or_without_targets(self) -> None:
        """작업 중이거나 지정할 영상이 없으면 거짓을 돌려 저장하지 않는지 검증"""
        self.busy = True
        self.assertFalse(self.controller.request(self.evals, "720p"))
        self.assertEqual(self.session.applied, [])
        self.busy = False
        self.assertFalse(self.controller.request([], "720p"))

    def test_frame_min_tier_is_saved_with_history(self) -> None:
        """헤더 팝업의 측정 대상 변경을 설정에 저장하고 되돌리기 기록에 남김"""
        self.controller.set_frame_analysis_min_tier("720p")
        self.assertEqual(self.session.min_tiers, ["720p"])
        self.assertEqual(self.changes, ["화질 분석 대상 해상도"])

    def test_apply_candidates_groups_by_candidate_and_skips_missing(self) -> None:
        self.table.table_model.candidates = {"a.mp4": "720p", "b.mp4": "1080p"}
        third = make_eval(video=make_video(filename="c.mp4"))
        self.controller.apply_candidates([*self.evals, third])
        self.assertEqual(
            self.session.applied,
            [(["a.mp4"], "720p"), (["b.mp4"], "1080p")],
        )
        self.assertEqual(self.changes, ["화질 분석 후보 적용"])
        self.assertIn("제외 1개", self.notes[-1])

    def test_apply_candidates_without_candidates_only_notifies(self) -> None:
        self.table.table_model.candidates = {}
        self.controller.apply_candidates(self.evals)
        self.assertEqual(self.session.applied, [])
        self.assertIn("축소 후보가 없습니다", self.notes[-1])

    def test_clear_asks_and_clears_when_confirmed(self) -> None:
        with mock.patch("src.gui.controllers.manual_target_controller.QMessageBox.question", return_value=mock.ANY) as ask:
            self.controller.clear()
        self.assertTrue(ask.called)
        self.assertEqual(self.session.cleared, 1)
        self.assertEqual(self.changes, ["출력 해상도 직접 지정 모두 해제"])

    def test_clear_returns_without_records(self) -> None:
        self.session.count = 0
        self.controller.clear()
        self.assertEqual(self.session.cleared, 0)
        self.assertEqual(_pending_output_tiers(None, ["a.mp4"]), {})


if __name__ == "__main__":
    unittest.main()
