"""재인코딩 결과 기록기 검증: 완료 결과 크기, 폐기 항목의 예상과 측정 크기, 기록이 없거나 결과가 없는 항목은 넘기지 않음

- 이력 DB는 임시 폴더에 만들고, 진행 창은 호출을 기록하는 대역으로 바꿈
"""

import os
import tempfile
import unittest
from unittest import mock

from src.core.encode.encode_progress_values import DiscardRow, ResultRow
from src.core.encode.encode_state import EncodeState
from src.gui.controllers.encode_result_feed import ResultFeed
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_job import job_to_json, jobs_from_evaluations
from tests._factories import make_eval, make_video

_NAME: str = "a.mp4"


class ResultFeedTests(unittest.TestCase):
    """이력 행의 상태에 따라 결과 줄이나 폐기 줄을 진행 창에 넘기는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        with open(os.path.join(self.root, _NAME), "wb") as handle:
            handle.write(b"source")
        job = jobs_from_evaluations([make_eval(video=make_video(filename=_NAME))], self.root)[0]
        self.source = job.source_path
        self.history = EncodeHistory(os.path.join(self.root, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)
        self.batch_id = self.history.create_batch(
            self.root, "{}", [NewItem(self.source, job.source_size, job.source_mtime_ns, job_to_json(job))]
        )
        self.window = mock.Mock()
        self.feed = ResultFeed(self.history, lambda: self.window)

    def test_done_row_passes_its_result_file_size_to_the_window(self) -> None:
        """완료 항목은 결과 파일 경로와 크기를 결과 줄로 넘김"""
        output = os.path.join(self.root, "_재인코딩결과", _NAME)
        self.history.update_item(
            self.batch_id, self.source, {"state": EncodeState.DONE, "output_path": output, "output_size": 40}
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_result.call_args.args[0]
        self.assertIsInstance(row, ResultRow)
        self.assertEqual((row.output_path, row.output_bytes), (output, 40))

    def test_discarded_row_passes_the_estimate_when_the_run_was_stopped_early(self) -> None:
        """조기 중단으로 폐기된 항목은 추정 크기를 측정 크기로 넘기고 추정 표시를 참으로 둠"""
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.DISCARDED, "early_stop_estimate_bytes": 150, "output_size": None},
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_discard.call_args.args[0]
        self.assertIsInstance(row, DiscardRow)
        self.assertEqual((row.measured_bytes, row.estimated), (150, True))

    def test_discarded_row_without_estimate_passes_the_measured_result_size(self) -> None:
        """게이트에서 폐기된 항목은 잰 결과 크기를 측정 크기로 넘기고 추정 표시를 거짓으로 둠"""
        self.history.update_item(
            self.batch_id, self.source, {"state": EncodeState.DISCARDED, "output_size": 90}
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_discard.call_args.args[0]
        self.assertEqual((row.measured_bytes, row.estimated), (90, False))

    def test_missing_row_or_missing_result_file_passes_nothing(self) -> None:
        """기록이 없는 경로와 결과 파일이 없는 완료 항목은 진행 창을 건드리지 않음"""
        self.feed.record(self.batch_id, os.path.join(self.root, "none.mp4"))
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.DONE, "output_path": os.path.join(self.root, "missing.mp4"), "output_size": None},
        )
        self.feed.record(self.batch_id, self.source)
        self.window.set_result.assert_not_called()
        self.window.set_discard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
