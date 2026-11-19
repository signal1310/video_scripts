"""인코딩 자식 프로세스의 진행 줄 전달, 일시중지와 재개, 우선순위 매핑, 취소와 종료 결과를 검증하는 단위 테스트

- 실제 자식 프로세스는 현재 파이썬 인터프리터로 띄우는 짧은 스크립트이며 FFmpeg는 실행하지 않음
"""

import sys
import time
import unittest

import psutil

from src.core.encode.encode_process import EncodeProcess, resolve_priority_value

_PRINT_SCRIPT = "print('frame=1'); print('progress=continue'); print('frame=2'); print('progress=end')"
_SLEEP_SCRIPT = "import time; time.sleep(60)"


class TestProcessOutput(unittest.TestCase):
    """진행 줄 전달과 정상 종료"""

    def test_progress_lines_are_delivered_and_exit_is_clean(self) -> None:
        """stdout의 진행 줄이 콜백으로 전달되고 종료 코드 0은 취소되지 않은 정상 종료임을 검증"""
        lines: list[str] = []
        process = EncodeProcess([sys.executable, "-c", _PRINT_SCRIPT], on_progress_line=lines.append)
        process.start()
        outcome = process.wait(timeout=30.0)
        self.assertEqual(outcome.returncode, 0)
        self.assertFalse(outcome.cancelled)
        self.assertEqual(lines, ["frame=1", "progress=continue", "frame=2", "progress=end"])

    def test_stderr_tail_keeps_the_last_lines(self) -> None:
        """stderr 출력의 마지막 줄이 종료 결과에 보존됨을 검증"""
        script = "import sys; sys.stderr.write('first\\nlast line\\n')"
        process = EncodeProcess([sys.executable, "-c", script])
        process.start()
        outcome = process.wait(timeout=30.0)
        self.assertEqual(outcome.stderr_tail[-1], "last line")


class TestProcessControl(unittest.TestCase):
    """일시중지, 재개, 우선순위, 취소"""

    def test_pause_and_resume_change_process_state(self) -> None:
        """일시중지하면 상태가 stopped가 되고 재개하면 다시 실행 상태가 됨을 검증"""
        process = EncodeProcess([sys.executable, "-c", _SLEEP_SCRIPT])
        process.start()
        try:
            child = psutil.Process(process.pid)
            process.pause()
            self.assertEqual(child.status(), psutil.STATUS_STOPPED)
            process.resume()
            deadline = time.monotonic() + 5.0
            while child.status() == psutil.STATUS_STOPPED and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertNotEqual(child.status(), psutil.STATUS_STOPPED)
        finally:
            process.cancel()
            process.wait(timeout=30.0)

    def test_cancel_kills_running_process_and_reports_cancelled(self) -> None:
        """취소하면 실행 중인 프로세스가 종료되고 결과에 취소가 기록됨을 검증"""
        process = EncodeProcess([sys.executable, "-c", _SLEEP_SCRIPT])
        process.start()
        process.cancel()
        outcome = process.wait(timeout=30.0)
        self.assertTrue(outcome.cancelled)
        self.assertNotEqual(outcome.returncode, 0)

    def test_priority_names_map_to_psutil_constants(self) -> None:
        """설정 우선순위 이름이 psutil 상수로 매핑되고, 알 수 없는 이름은 보통으로 대체됨을 검증"""
        self.assertEqual(resolve_priority_value("normal"), int(psutil.NORMAL_PRIORITY_CLASS))
        self.assertEqual(resolve_priority_value("low"), int(psutil.IDLE_PRIORITY_CLASS))
        self.assertEqual(resolve_priority_value("unknown"), int(psutil.NORMAL_PRIORITY_CLASS))

    def test_control_before_start_is_rejected(self) -> None:
        """시작 전에 일시중지나 종료 대기를 호출하면 명확한 오류를 냄을 검증"""
        process = EncodeProcess([sys.executable, "-c", "pass"])
        with self.assertRaises(RuntimeError):
            process.pause()
        with self.assertRaises(RuntimeError):
            process.wait(timeout=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
