"""측정 실패 기록: 실패 파일과 사유는 경고로 남기고, 진행 콜백에는 파일명만 전달됨을 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.analysis.prober import MAX_FAILURE_LOG_LINES, probe_files
from src.core.exceptions import DomainRuntimeError, ErrorCode


def _always_fail(filepath: str, **_kwargs: object) -> dict[str, object]:
    raise DomainRuntimeError("moov atom not found", code=ErrorCode.PROBE_FAILED)


class TestProbeFailureLogging(unittest.TestCase):
    """ffprobe 측정 실패가 로그와 결과 목록에 올바르게 반영되는지 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root: str = tmp.name

    def _touch(self, names: list[str]) -> None:
        for name in names:
            with open(os.path.join(self.root, name), "wb"):
                pass

    def test_failed_files_are_logged_with_reason_and_progress_stays_plain(self) -> None:
        """실패 파일명과 사유가 경고로 남고, 진행 콜백 파일명에는 실패 문구가 섞이지 않음을 검증"""
        self._touch(["a.mp4", "b.mp4"])
        progress: list[str] = []
        with (
            patch("src.core.analysis.prober.run_ffprobe", side_effect=_always_fail),
            self.assertLogs("src.core.analysis.prober", level="WARNING") as logs,
        ):
            result = probe_files(
                self.root,
                ["a.mp4", "b.mp4"],
                False,
                on_progress=lambda _current, _total, name: progress.append(name),
            )

        self.assertEqual(result, [])
        self.assertEqual(sorted(progress), ["a.mp4", "b.mp4"])
        output = "\n".join(logs.output)
        self.assertIn("분석 실패: a.mp4 (moov atom not found)", output)
        self.assertIn("분석 실패: b.mp4 (moov atom not found)", output)

    def test_failures_beyond_limit_are_summarized(self) -> None:
        """상한을 넘는 실패는 개별 경고를 상한까지만 남기고 생략된 개수를 안내함을 검증"""
        names = [f"v{i:02d}.mp4" for i in range(MAX_FAILURE_LOG_LINES + 2)]
        self._touch(names)
        with (
            patch("src.core.analysis.prober.run_ffprobe", side_effect=_always_fail),
            self.assertLogs("src.core.analysis.prober", level="WARNING") as logs,
        ):
            probe_files(self.root, names, False)

        failure_lines = [line for line in logs.output if "분석 실패:" in line]
        self.assertEqual(len(failure_lines), MAX_FAILURE_LOG_LINES)
        self.assertIn("분석 실패 2건은 목록에서 생략했습니다.", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
