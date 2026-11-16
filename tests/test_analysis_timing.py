import json
import os
import tempfile
import unittest
from unittest.mock import patch

import src.service.analysis_timing as timing
from src.service.analysis_timing import (
    HISTORY_LIMIT,
    MODE_FAST,
    MODE_FULL,
    average_seconds_per_item,
    expected_analysis_seconds,
    record_seconds_per_item,
)

_TIMING_PATH_TARGET = "src.service.analysis_timing._timing_path"


class TestAnalysisTiming(unittest.TestCase):
    """분석 시간 이력의 기록, 평균, 예상 산출과 손상 파일 대응을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, timing.TIMING_FILENAME)
        patcher = patch(_TIMING_PATH_TARGET, return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_history_gives_no_average_or_expected(self) -> None:
        self.assertIsNone(average_seconds_per_item(MODE_FAST))
        self.assertIsNone(expected_analysis_seconds(MODE_FAST, 10))

    def test_zero_pending_expected_is_zero_without_history(self) -> None:
        self.assertEqual(expected_analysis_seconds(MODE_FULL, 0), 0.0)

    def test_record_then_average_per_mode(self) -> None:
        record_seconds_per_item(MODE_FAST, 0.5)
        record_seconds_per_item(MODE_FAST, 1.5)
        record_seconds_per_item(MODE_FULL, 4.0)
        self.assertAlmostEqual(average_seconds_per_item(MODE_FAST) or 0.0, 1.0)
        self.assertAlmostEqual(average_seconds_per_item(MODE_FULL) or 0.0, 4.0)

    def test_expected_scales_with_pending_count(self) -> None:
        record_seconds_per_item(MODE_FAST, 0.5)
        self.assertAlmostEqual(expected_analysis_seconds(MODE_FAST, 80) or 0.0, 40.0)

    def test_invalid_records_are_ignored(self) -> None:
        record_seconds_per_item(MODE_FAST, None)
        record_seconds_per_item(MODE_FAST, 0.0)
        record_seconds_per_item(MODE_FAST, -1.0)
        self.assertFalse(os.path.exists(self.path))

    def test_history_keeps_latest_limit_entries(self) -> None:
        for i in range(HISTORY_LIMIT + 5):
            record_seconds_per_item(MODE_FAST, float(i + 1))
        with open(self.path, encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(len(stored[MODE_FAST]), HISTORY_LIMIT)
        self.assertEqual(stored[MODE_FAST][-1], float(HISTORY_LIMIT + 5))

    def test_corrupted_file_reads_as_empty_history(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(average_seconds_per_item(MODE_FAST))

    def test_non_dict_payload_reads_as_empty_history(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertIsNone(average_seconds_per_item(MODE_FAST))


if __name__ == "__main__":
    unittest.main()
