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
        self.assertFalse(row.compare_ready)

    def test_done_row_reports_comparable_pair_with_archive(self) -> None:
        """원본 보관 파일과 결과 파일이 모두 있으면 화질 비교 가능으로 표시하고 보관 경로를 넘김"""
        output = os.path.join(self.root, "out.mp4")
        archive = os.path.join(self.root, "source_원본보관.mp4")
        for path in (output, archive):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.DONE, "output_path": output, "archive_path": archive},
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_result.call_args.args[0]
        self.assertTrue(row.compare_ready)
        self.assertEqual(row.archive_path, archive)

    def test_replace_original_done_row_is_not_comparable(self) -> None:
        """원본 교체로 원본과 결과가 같은 파일이면 비교 불가로 표시함"""
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.DONE, "output_path": self.source, "archive_path": None},
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_result.call_args.args[0]
        self.assertFalse(row.compare_ready)

    def test_pending_row_requires_source_and_part_files(self) -> None:
        """교체 대기는 원본과 임시 결과 파일이 모두 있어야 화질 비교 가능으로 표시함"""
        part = os.path.join(self.root, "source.mp4.part")
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.REPLACE_PENDING, "part_path": part, "output_size": 40},
        )
        with open(part, "wb") as handle:
            handle.write(b"x")
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_result.call_args.args[0]
        self.assertTrue(row.compare_ready)
        os.remove(part)
        self.feed.record(self.batch_id, self.source)
        self.assertFalse(self.window.set_result.call_args.args[0].compare_ready)

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

    def test_discarded_row_with_sample_prediction_uses_the_prediction_label(self) -> None:
        """샘플 예측으로 폐기된 항목은 예측 크기를 측정 크기로 넘기고 샘플 예측 표시를 참으로 둠"""
        self.history.update_item(
            self.batch_id,
            self.source,
            {"state": EncodeState.DISCARDED, "sample_predicted_bytes": 150, "output_size": None},
        )
        self.feed.record(self.batch_id, self.source)
        row = self.window.set_discard.call_args.args[0]
        self.assertEqual((row.measured_bytes, row.estimated, row.sample_predicted), (150, False, True))

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
