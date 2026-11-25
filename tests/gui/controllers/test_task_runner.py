"""작업 실행기(TaskRunner)의 완료 로그, 복원 요약 한 줄, 취소와 후속 단계 연결을 검증"""

import time
import unittest
from unittest.mock import MagicMock, patch

from src.gui.controllers.task_runner import TaskRunner, format_completion_status
from src.gui.controllers.worker import ClassifierWorker
from src.gui.logging_bridge import GuiLogBridge
from tests._factories import make_eval, make_report, make_video


def _runner() -> tuple[TaskRunner, MagicMock]:
    """호스트 대역을 붙인 작업 실행기"""
    host = MagicMock()
    host.frame_analysis = MagicMock()
    host.active_worker = None
    return TaskRunner(host), host


class TaskRunnerLoggingTests(unittest.TestCase):
    """분석 완료 로그가 영상별 줄과 복원 요약 한 줄을 구분해 남기는지 검증"""

    def test_analysis_completion_logs_one_line_per_video(self) -> None:
        """빠른 분석 완료가 영상마다 권장 처리와 판정 사유를 한 줄로 기록하는지 검증"""
        runner, _host = _runner()
        evaluations = [
            make_eval(video=make_video(filename="a.mp4")),
            make_eval(video=make_video(filename="b.mp4")),
            make_eval(video=make_video(filename="c.mp4")),
        ]
        report = make_report(evaluations, root_dir="C:/dummy")
        with self.assertLogs("src.gui.controllers.task_runner", level="INFO") as logs:
            runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, report)
        expected = [
            f"INFO:src.gui.controllers.task_runner:영상 분석 결과: {ev.video.filename}, "
            f"{ev.action_label}, {ev.reason_label}"
            for ev in evaluations
        ]
        self.assertEqual([line for line in logs.output if line in expected], expected)

    def test_failed_or_missing_report_logs_no_video_lines(self) -> None:
        """실패했거나 리포트가 없으면 영상별 결과 줄을 남기지 않는지 검증"""
        runner, _host = _runner()
        with self.assertLogs("src.gui.controllers.task_runner", level="INFO") as logs:
            runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, None)
        self.assertFalse(any("영상 분석 결과" in line for line in logs.output))
        with patch("src.gui.controllers.task_runner.QMessageBox.critical"), self.assertLogs(
            "src.gui.controllers.task_runner", level="ERROR"
        ) as failed:
            runner.handle_failed("실패")
        self.assertFalse(any("영상 분석 결과" in line for line in failed.output))

    def test_volume_guard_keeps_the_log_bridge_fast_for_3000_lines(self) -> None:
        """영상 3000개를 빠른 분석으로 기록해도 로그 갱신이 1초 미만인지 검증"""
        runner, _host = _runner()
        evaluations = [
            make_eval(video=make_video(filename=f"v{index}.mp4")) for index in range(3000)
        ]
        report = make_report(evaluations, root_dir="C:/dummy")
        logs: list[str] = []
        bridge = GuiLogBridge(on_log=logs.append)
        bridge.attach("src")
        self.addCleanup(bridge.detach, "src")
        started = time.perf_counter()
        runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, report)
        self.assertLess(time.perf_counter() - started, 1.0)
        per_video = [line for line in logs if "영상 분석 결과" in line]
        self.assertEqual(len(per_video), 3000)

    def test_restore_completion_logs_one_summary_line_instead_of_per_video(self) -> None:
        """시작 복원 완료는 영상별 줄 대신 요약 한 줄만 남기는지 검증"""
        runner, host = _runner()
        evaluations = [
            make_eval(video=make_video(filename=f"v{index}.mp4")) for index in range(5)
        ]
        report = make_report(evaluations, root_dir="C:/dummy", encoded_count=3)
        with self.assertLogs("src.gui.controllers.task_runner", level="INFO") as logs:
            runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, report, restore=True)
        self.assertFalse(any("영상 분석 결과" in line for line in logs.output))
        summary = [line for line in logs.output if "이전 분석 결과 복원" in line]
        self.assertEqual(len(summary), 1)
        self.assertIn("5개 영상", summary[0])
        self.assertIn("인코딩 대상 3개", summary[0])
        host.apply_report.assert_called_once_with(report)

    def test_restore_completion_without_report_logs_no_video_lines(self) -> None:
        """복원 작업이 리포트 없이 끝나면 영상별 줄을 남기지 않는지 검증"""
        runner, _host = _runner()
        with self.assertLogs("src.gui.controllers.task_runner", level="INFO") as logs:
            runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, None, restore=True)
        self.assertFalse(any("영상 분석 결과" in line for line in logs.output))
        self.assertTrue(any("분석 완료: 총 0개 영상" in line for line in logs.output))

    def test_launch_restore_flag_is_forwarded_to_completion_handler(self) -> None:
        """복원 실행이 완료 처리에 복원 표시를 넘겨 요약 한 줄 경로를 타는지 검증"""
        runner, _host = _runner()
        clf = MagicMock(root_dir="C:/dummy", recursive=False)
        report = make_report([make_eval(video=make_video(filename="a.mp4"))])
        with (
            patch("src.gui.controllers.task_runner.ClassifierWorker") as worker_cls,
            patch.object(runner, "handle_completed") as completed,
            patch.object(runner, "_record_analyzed"),
            self.assertLogs("src.gui.controllers.task_runner", level="INFO"),
        ):
            runner.launch(clf, ClassifierWorker.TASK_ANALYZE_FAST, restore=True)
            clf_worker = worker_cls.return_value
            # - 시그널 연결 람다를 그대로 불러 완료 처리로 전달되는 값을 확인
            callback = clf_worker.task_completed.connect.call_args_list[0].args[0]
            callback(ClassifierWorker.TASK_ANALYZE_FAST, report)
        completed.assert_called_once_with(
            ClassifierWorker.TASK_ANALYZE_FAST, report, restore=True
        )
        clf_worker.start.assert_called_once_with()


class TaskRunnerLifecycleTests(unittest.TestCase):
    """취소 요청, 완료 상태 문구와 정밀 분석 뒤 후속 단계 연결을 검증"""

    def test_cancel_requests_stop_on_the_running_worker(self) -> None:
        """실행 중인 작업 취소가 워커에 중단을 요청하고 상태를 알리는지 검증"""
        runner, host = _runner()
        worker = MagicMock()
        worker.isRunning.return_value = True
        host.active_worker = worker

        runner.cancel()

        worker.cancel.assert_called_once_with()
        host.set_status_text.assert_called_once_with("작업 중단 요청 중...")

    def test_cancel_does_nothing_without_a_running_worker(self) -> None:
        """실행 중인 워커가 없으면 취소가 아무 상태도 바꾸지 않는지 검증"""
        runner, host = _runner()

        runner.cancel()

        host.set_status_text.assert_not_called()

    def test_format_completion_status_maps_builtin_task_types(self) -> None:
        """작업 유형별 완료 상태 문구를 검증"""
        self.assertEqual(
            format_completion_status(ClassifierWorker.TASK_ANALYZE_FAST, None),
            "분석 완료: 총 0개 영상",
        )

    def test_full_analysis_completion_runs_the_follow_up_stage(self) -> None:
        """정밀 분석 완료가 상태 줄 정리 뒤에 후속 단계를 부르고, 실패와 취소 시 후속 예약을 비우는지 검증"""
        runner, host = _runner()
        events: list[str] = []
        host.set_status_text.side_effect = lambda text, *_a: events.append(f"status:{text}")
        host.frame_analysis.on_analysis_finished.side_effect = lambda: events.append("followup")
        runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FULL, MagicMock(total_count=1))
        host.frame_analysis.on_analysis_finished.assert_called_once_with()
        self.assertEqual(events, ["status:", "followup"])
        runner.handle_completed(ClassifierWorker.TASK_ANALYZE_FAST, MagicMock(total_count=1))
        self.assertEqual(host.frame_analysis.on_analysis_finished.call_count, 1)
        runner.handle_cancelled("작업이 중단되었습니다.")
        host.frame_analysis.clear_pending_followup.assert_called_once_with()
        with patch("src.gui.controllers.task_runner.QMessageBox.critical"):
            runner.handle_failed("오류")
        self.assertEqual(host.frame_analysis.clear_pending_followup.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
