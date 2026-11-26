"""분석 작업 시작/완료/실패가 사용자 동작 기록에 남는지 검증"""

import unittest
from unittest.mock import MagicMock, patch

from src.gui.controllers.task_runner import TaskRunner
from src.gui.controllers.worker import ClassifierWorker
from tests._factories import make_eval, make_report, make_video


def _runner() -> tuple[TaskRunner, MagicMock]:
    """호스트 대역을 붙인 작업 실행기"""
    host = MagicMock()
    host.frame_analysis = MagicMock()
    host.active_worker = None
    return TaskRunner(host), host


class TaskRunnerBreadcrumbTests(unittest.TestCase):
    """작업 실행기가 주요 지점에서 동작 기록을 남기는지 검증"""

    def test_launch_notes_analysis_start(self) -> None:
        """분석 실행이 작업 이름과 시작 문구를 기록하는지 검증"""
        runner, _host = _runner()
        classifier = MagicMock(recursive=False)
        with (
            patch("src.gui.controllers.task_runner.ClassifierWorker") as worker_cls,
            patch("src.gui.controllers.task_runner.note_event") as noted,
        ):
            worker_cls.return_value = MagicMock()
            runner.launch(classifier, ClassifierWorker.TASK_ANALYZE_FAST, label="빠른 분석")
        self.assertEqual(noted.call_args[0][0], "분석")
        self.assertEqual(noted.call_args[0][1], "빠른 분석 시작")

    def test_completion_notes_the_status_line(self) -> None:
        """완료가 상태 문구를 그대로 기록하는지 검증"""
        runner, _host = _runner()
        report = make_report([make_eval(video=make_video(filename="a.mp4"))], root_dir="C:/dummy")
        with patch("src.gui.controllers.task_runner.note_event") as noted:
            runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, report)
        self.assertEqual(noted.call_args[0][0], "분석")
        self.assertTrue(noted.call_args[0][1])

    def test_failure_notes_the_first_error_line(self) -> None:
        """실패가 오류 분류와 첫 줄만 기록하는지 검증"""
        runner, _host = _runner()
        with (
            patch("src.gui.controllers.task_runner.QMessageBox.critical"),
            patch("src.gui.controllers.task_runner.note_event") as noted,
        ):
            runner.handle_failed("첫 줄 오류\n자세한 스택")
        self.assertEqual(noted.call_args[0][0], "오류")
        self.assertEqual(noted.call_args[0][1], "첫 줄 오류")


if __name__ == "__main__":
    unittest.main()
