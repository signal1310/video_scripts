"""표 행의 영상 파일 상태, 누락 건수에 맞는 안내 문구, 복사 대상 경로 목록의 순수 판정을 검증"""

import os
import tempfile
import unittest

from src.gui.table.table_actions import (
    MSG_MISSING_EXCLUDED,
    MSG_MISSING_REFRESH,
    RowFileState,
    count_missing_video_files,
    existing_video_paths,
    missing_notice,
    row_file_state,
)
from tests._factories import make_eval, make_video


class RowFileStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        with open(os.path.join(self.root, "here.mp4"), "wb") as f:
            f.write(b"v")
        self.present = make_eval(make_video(filename="here.mp4"))
        self.missing = make_eval(make_video(filename="gone.mp4"))

    def test_row_without_evaluation_or_root_is_no_row(self) -> None:
        """평가 항목이 없거나 작업 폴더가 없으면 행 없음으로 구분함을 검증"""
        self.assertEqual(row_file_state(self.root, None), (RowFileState.NO_ROW, None))
        self.assertEqual(row_file_state("", self.present), (RowFileState.NO_ROW, None))

    def test_existing_file_is_present_and_removed_file_is_missing(self) -> None:
        """파일이 있으면 경로를, 행은 있으나 파일이 없으면 누락 상태를 돌려주는지 검증"""
        state, path = row_file_state(self.root, self.present)
        self.assertEqual(state, RowFileState.PRESENT)
        self.assertEqual(path, os.path.join(self.root, "here.mp4"))
        self.assertEqual(row_file_state(self.root, self.missing), (RowFileState.MISSING, None))

    def test_counts_and_existing_paths_split_by_presence(self) -> None:
        """누락 건수와 파일이 있는 경로(중복 제외, 순서 유지)를 나눠 계산하는지 검증"""
        evals = [self.missing, self.present, self.present]
        self.assertEqual(count_missing_video_files(self.root, evals), 1)
        self.assertEqual(existing_video_paths(self.root, evals), [os.path.join(self.root, "here.mp4")])


class MissingNoticeTests(unittest.TestCase):
    def test_no_notice_when_nothing_is_missing(self) -> None:
        """누락이 없으면 안내 문구가 없음을 검증"""
        self.assertIsNone(missing_notice(0, 3))

    def test_all_missing_asks_to_check_the_list_again(self) -> None:
        """대상 전부가 없으면 목록을 다시 확인하라는 안내를 쓰는지 검증"""
        self.assertEqual(missing_notice(1, 1), MSG_MISSING_REFRESH)
        self.assertEqual(missing_notice(2, 2), MSG_MISSING_REFRESH)

    def test_partial_missing_reports_excluded_count(self) -> None:
        """일부만 없으면 제외된 건수를 알리는지 검증"""
        self.assertEqual(missing_notice(2, 5), MSG_MISSING_EXCLUDED.format(count=2))
        self.assertEqual(MSG_MISSING_REFRESH, "선택한 영상 파일을 찾을 수 없어 목록을 다시 확인합니다.")


if __name__ == "__main__":
    unittest.main()
