"""시작 복원 흐름이 판정 범위를 넘기고, 영상별 줄 대신 요약 한 줄만 남기는지 검증"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from src.gui.controllers import restore_flow as restore_flow_mod
from src.gui.controllers.restore_flow import RESTORE_LABEL, RestoreFlow
from src.gui.controllers.worker import ClassifierWorker
from src.service import analysis_probe as probe_mod
from tests._factories import make_video

_APP = QApplication.instance() or QApplication([])


def _flow() -> tuple[RestoreFlow, MagicMock, MagicMock, MagicMock, dict[str, MagicMock]]:
    """세션과 작업 실행기를 대역으로 둔 복원 흐름 생성

    - 판정 워커는 실제 스레드를 띄우지 않도록 대역으로 바꿔 끼움
    - 콜백 대역은 흐름이 보관한 값이 아니라 만든 객체를 그대로 돌려 호출 여부를 확인
    """
    session = MagicMock()
    tasks = MagicMock()
    tasks.is_running.return_value = False
    callbacks: dict[str, MagicMock] = {
        name: MagicMock() for name in ("set_busy", "set_status", "set_pending", "apply_filters", "re_evaluate")
    }
    flow = RestoreFlow(
        session,
        tasks,
        QObject(),
        callbacks["set_busy"],
        callbacks["set_status"],
        callbacks["set_pending"],
        callbacks["apply_filters"],
        callbacks["re_evaluate"],
    )
    resolver = MagicMock()
    flow._resolver = resolver
    return flow, session, tasks, resolver, callbacks


def _stored(*, legacy: bool = False, include_keyframe: bool = False) -> probe_mod.StoredRows:
    """행 3개(키프레임 없음)와 훑은 범위를 담은 판정 결과"""
    rows = tuple(make_video(filename=f"v{index}.mp4", keyframe_interval=None) for index in range(3))
    entries = tuple(probe_mod.FileEntry(f"v{index}.mp4", 10, index) for index in range(3))
    return probe_mod.StoredRows(
        rows,
        frozenset({"v0.mp4"}) if legacy else frozenset(),
        include_keyframe,
        entries,
        (),
        probe_mod.RestoreScope("C:/work", False, ("archive",)),
    )


class RestoreFlowStartTests(unittest.TestCase):
    """복원 시작이 판정에 작업 폴더와 스캔 제외 폴더를 그대로 넘기는지 검증"""

    def test_start_forwards_the_scan_scope_to_the_resolver(self) -> None:
        """판정과 이어지는 분석이 같은 범위를 보도록 제외 폴더를 넘기는지 검증"""
        flow, session, _tasks, resolver, _callbacks = _flow()
        clf = MagicMock(root_dir="C:/work", recursive=True)
        clf.service.excluded_dirnames = ("archive", "out")
        session.classifier = clf

        with (
            patch.object(restore_flow_mod, "is_analyzed_dir", return_value=True),
            patch.object(resolver, "start") as start,
        ):
            flow.start()

        start.assert_called_once_with(
            "C:/work", recursive=True, excluded_dirnames=("archive", "out")
        )

    def test_start_is_skipped_when_the_folder_was_never_analyzed(self) -> None:
        """한 번도 분석하지 않은 폴더는 판정을 시작하지 않는지 검증"""
        flow, session, _tasks, resolver, _callbacks = _flow()
        session.classifier = MagicMock(root_dir="C:/work", recursive=False)
        with (
            patch.object(restore_flow_mod, "is_analyzed_dir", return_value=False),
            patch.object(resolver, "start") as start,
        ):
            flow.start()
        start.assert_not_called()


class RestoreFlowApplyTests(unittest.TestCase):
    """판정 결과 적용이 복원 분석 실행과 로그 한 줄 남기기를 지키는지 검증"""

    def test_restore_analysis_runs_with_prescan_rows_and_restore_flag(self) -> None:
        """복원 분석을 실행할 때 판정 결과와 복원 표시를 넘기는지 검증"""
        flow, session, tasks, _resolver, _callbacks = _flow()
        clf = MagicMock(root_dir="C:/work", recursive=False)
        session.classifier = clf
        stored = _stored()

        flow.apply_resolved(stored)

        tasks.launch.assert_called_once_with(
            clf,
            ClassifierWorker.TASK_ANALYZE_FAST,
            label=RESTORE_LABEL,
            prescan=stored,
            restore=True,
        )

    def test_full_keyframe_rows_restore_in_full_mode(self) -> None:
        """판정 결과가 키프레임을 모두 가지면 정밀 분석으로 복원하는지 검증"""
        flow, session, tasks, _resolver, _callbacks = _flow()
        session.classifier = MagicMock(root_dir="C:/work", recursive=False)

        flow.apply_resolved(_stored(include_keyframe=True))

        self.assertEqual(tasks.launch.call_args.args[1], ClassifierWorker.TASK_ANALYZE_FULL)

    def test_legacy_rows_log_one_summary_line_without_per_video_lines(self) -> None:
        """이전 형식 행만 있는 폴더는 저장 결과를 보여 주고 로그를 요약 한 줄로 끝내는지 검증"""
        flow, session, tasks, _resolver, callbacks = _flow()
        session.classifier = MagicMock(root_dir="C:/work", recursive=False)
        notice = MagicMock(text="이 폴더의 영상 중 1개를 다시 분석해야 합니다.", tooltip="사유")
        session.install_stored_rows.return_value = notice
        stored = _stored(legacy=True)

        with self.assertLogs("src.gui.controllers.restore_flow", level="INFO") as logs:
            flow.apply_resolved(stored)

        tasks.launch.assert_not_called()
        session.install_stored_rows.assert_called_once_with(stored)
        self.assertFalse(any("영상 분석 결과" in line for line in logs.output))
        summary = [line for line in logs.output if "이전 분석 결과 복원" in line]
        self.assertEqual(len(summary), 1)
        self.assertIn("3개 영상", summary[0])
        callbacks["set_status"].assert_called_once_with(notice.text, notice.tooltip)
        callbacks["set_busy"].assert_called_once_with(False, "")
        callbacks["re_evaluate"].assert_called_once_with()

    def test_unrelated_result_clears_busy_state(self) -> None:
        """복원할 결과가 없으면 미리 켠 바쁨 상태를 해제하는지 검증"""
        flow, session, tasks, _resolver, callbacks = _flow()
        session.classifier = MagicMock(root_dir="C:/work", recursive=False)

        flow.apply_resolved(None)

        callbacks["set_busy"].assert_called_once_with(False, "")
        callbacks["set_status"].assert_called_once_with("", "")
        tasks.launch.assert_not_called()

    def test_result_is_ignored_while_a_task_runs(self) -> None:
        """판정 대기 중 사용자가 시작한 작업이 있으면 상태를 건드리지 않는지 검증"""
        flow, session, tasks, _resolver, callbacks = _flow()
        session.classifier = MagicMock(root_dir="C:/work", recursive=False)
        tasks.is_running.return_value = True

        flow.apply_resolved(_stored())

        tasks.launch.assert_not_called()
        callbacks["set_busy"].assert_not_called()
        callbacks["set_status"].assert_not_called()

    def test_cancel_forwards_to_the_resolver(self) -> None:
        """진행 중인 판정 취소가 판정 워커로 전달되는지 검증"""
        flow, _session, _tasks, resolver, _callbacks = _flow()

        flow.cancel()

        resolver.cancel.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
