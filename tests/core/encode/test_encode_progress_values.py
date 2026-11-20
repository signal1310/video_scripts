"""재인코딩 진행 창 수치 계산과 표기(encode_progress_values)의 단위 테스트"""

import os
import unittest

from src.core.encode.encode_progress_values import (
    ResultLine,
    ResultRow,
    average_speed,
    batch_speed_text,
    bytes_to_mb,
    display_fraction,
    frames_text,
    mean_speed,
    original_kbps,
    quality_text,
    resolution_text,
    result_lines,
    result_path_text,
    saved_summary_text,
    saved_totals,
    size_change_text,
    size_text,
    speed_summary_text,
)
from src.core.encode.encode_state import EncodeState

MB_BYTES: int = 1024 * 1024


class TestFormatting(unittest.TestCase):
    """값 없음 표기와 자리수, 해상도 규격 표기"""

    def test_missing_values_show_no_value_text(self) -> None:
        """값이 없으면 모든 표기 함수가 정보 없음을 돌려주는지 검증"""
        self.assertEqual(size_text(None), "정보 없음")
        self.assertEqual(frames_text(None), "정보 없음")
        self.assertEqual(quality_text(None), "정보 없음")
        self.assertEqual(speed_summary_text(None, None), "현재 정보 없음, 평균 정보 없음")

    def test_values_are_formatted_with_separators_and_precision(self) -> None:
        """프레임 수는 천 단위 쉼표, 품질 값은 소수 한 자리, 용량은 자동 단위로 표기되는지 검증"""
        self.assertEqual(frames_text(90480), "90,480")
        self.assertEqual(quality_text(22.0), "22.0")
        self.assertEqual(size_text(1024.0 + 512.0), "1.5 GB")
        self.assertEqual(size_text(51.0), "51.0 MB")

    def test_resolution_text_uses_tier_display_or_no_scale(self) -> None:
        """축소하지 않으면 축소 안 함, 규격이 있으면 규격 표기를 쓰는지 검증"""
        self.assertEqual(resolution_text(None), "축소 안 함")
        self.assertEqual(resolution_text((1920, 1080)), "1080p")
        self.assertEqual(resolution_text((3840, 2160)), "4K")
        self.assertEqual(resolution_text((1280, 720)), "720p")

    def test_speed_summary_joins_current_and_average(self) -> None:
        """전체 속도 칸이 현재와 평균을 같은 형식으로 잇는지 검증"""
        self.assertEqual(speed_summary_text(2.11, 0.7), "현재 2.11배, 평균 0.70배")


class TestProgressMath(unittest.TestCase):
    """진행 비율, 평균 속도, 측정된 배속 평균 계산"""

    def test_display_fraction_prefers_out_time_then_frames(self) -> None:
        """진행 위치가 있으면 그 값으로, 없으면 프레임 수 기준으로, 둘 다 없으면 None으로 계산하는지 검증"""
        self.assertAlmostEqual(display_fraction(300.0, 9000, 600.0, 30.0) or 0.0, 0.5)
        self.assertAlmostEqual(display_fraction(None, 9000, 600.0, 30.0) or 0.0, 0.5)
        self.assertEqual(display_fraction(None, 99999, 600.0, 30.0), 1.0)
        self.assertIsNone(display_fraction(None, None, 600.0, 30.0))
        self.assertIsNone(display_fraction(None, 10, 0.0, 30.0))
        self.assertIsNone(display_fraction(None, 10, 600.0, 0.0))

    def test_average_speed_needs_positive_processed_and_elapsed(self) -> None:
        """처리한 길이와 경과 시간이 모두 양수일 때만 배속을 계산하는지 검증"""
        self.assertEqual(average_speed(600.0, 300.0), 2.0)
        self.assertIsNone(average_speed(0.0, 300.0))
        self.assertIsNone(average_speed(600.0, 0.0))

    def test_original_kbps_is_the_average_bitrate_of_the_source(self) -> None:
        """원본 크기와 길이로 평균 비트레이트를 구하고, 값을 모르면 None인지 검증"""
        self.assertEqual(original_kbps(1000 * 1000 // 8 * 10, 10.0), 1000.0)
        self.assertIsNone(original_kbps(0, 10.0))
        self.assertIsNone(original_kbps(100, 0.0))

    def test_mean_speed_skips_missing_values(self) -> None:
        """측정되지 않은 배속은 평균에서 빼고, 모두 없으면 None을 돌려주는지 검증"""
        self.assertEqual(mean_speed([1.0, None, 2.0]), 1.5)
        self.assertIsNone(mean_speed([None]))


class TestResults(unittest.TestCase):
    """결과 크기 변화, 결과 경로, 완료분 절약 합계"""

    def test_size_change_shows_both_sizes_and_percent(self) -> None:
        """결과가 있으면 원본에서 결과로 바뀐 크기와 변화율을, 없으면 빈 표시를 쓰는지 검증"""
        self.assertEqual(size_change_text(51 * MB_BYTES, None), "—")
        self.assertEqual(size_change_text(100 * MB_BYTES, 44 * MB_BYTES), "100.0 MB → 44.0 MB (-56%)")
        self.assertEqual(size_change_text(0, 0), "0.0 MB → 0.0 MB (+0%)")

    def test_result_path_is_relative_to_the_work_folder(self) -> None:
        """결과 경로가 작업 폴더 기준 상대 경로로 표시되는지 검증"""
        root = os.path.join("work", "root")
        output = os.path.join(root, "_재인코딩결과", "clip.mp4")
        self.assertEqual(result_path_text(output, root), "결과: " + os.path.join("_재인코딩결과", "clip.mp4"))

    def test_saved_totals_sum_completed_rows_only_where_values_exist(self) -> None:
        """완료분의 실제 절약 합계와 예상 절약 합계를 합치고, 예상 값이 없는 행은 예상 합계에서 빼는지 검증"""
        rows = [
            ResultRow("a", "a.out", 100 * MB_BYTES, 50 * MB_BYTES, 40.0),
            ResultRow("b", "b.out", 20 * MB_BYTES, 15 * MB_BYTES, None),
        ]
        actual, expected = saved_totals(rows)
        self.assertEqual(actual, 55.0)
        self.assertEqual(expected, 40.0)
        self.assertEqual(bytes_to_mb(MB_BYTES), 1.0)

    def test_saved_totals_without_completed_rows_or_expected_values(self) -> None:
        """완료분이 없으면 둘 다 None이고, 예상 값이 전혀 없으면 예상만 None인지 검증"""
        self.assertEqual(saved_totals([]), (None, None))
        actual, expected = saved_totals([ResultRow("a", "a.out", 2 * MB_BYTES, MB_BYTES, None)])
        self.assertEqual(actual, 1.0)
        self.assertIsNone(expected)

    def test_saved_summary_text_shows_missing_side_as_no_value(self) -> None:
        """실제와 예상 문구가 같은 형식이며 없는 쪽은 정보 없음으로 나오는지 검증"""
        self.assertEqual(saved_summary_text(1.5 * 1024, None), "실제 1.5 GB, 예상 정보 없음")


class TestBatchLines(unittest.TestCase):
    """결과 줄 만들기와 전체 속도 문구: 끝난 항목만 줄로 만들고, 속도는 현재와 평균을 함께 계산"""

    def test_result_lines_skip_unfinished_items_and_fill_finished_ones(self) -> None:
        """대기와 진행 중 항목은 줄에서 빼고, 완료 결과는 결과 경로와 크기 변화를, 그 밖은 빈 표시를 쓰는지 검증"""
        root = os.path.join("work", "root")
        done = os.path.join(root, "a.mp4")
        items = [
            (done, EncodeState.DONE, ""),
            (os.path.join(root, "b.mp4"), EncodeState.PENDING, ""),
            (os.path.join(root, "c.mp4"), EncodeState.FAILED, "인코딩 실패"),
        ]
        output = os.path.join(root, "_재인코딩결과", "a.mp4")
        lines = result_lines(items, {done: ResultRow(done, output, 100 * MB_BYTES, 40 * MB_BYTES, None)}, {done: root})
        self.assertEqual(
            lines,
            [
                ResultLine(
                    "a.mp4",
                    "결과: " + os.path.join("_재인코딩결과", "a.mp4"),
                    "완료",
                    "100.0 MB → 40.0 MB (-60%)",
                    "",
                    source_path=done,
                ),
                ResultLine("c.mp4", "", "실패", "—", "인코딩 실패", source_path=os.path.join(root, "c.mp4")),
            ],
        )

    def test_replace_pending_line_is_marked_for_actions(self) -> None:
        """교체 대기 항목은 조작 버튼을 붙일 수 있도록 표시하고, 원본 경로를 함께 넘김"""
        root = os.path.join("work", "root")
        pending = os.path.join(root, "d.mp4")
        lines = result_lines(
            [(pending, EncodeState.REPLACE_PENDING, "교체 실패")],
            {pending: ResultRow(pending, os.path.join(root, "d.mp4.part"), 10 * MB_BYTES, 4 * MB_BYTES, None)},
            {pending: root},
        )
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].replace_pending)
        self.assertEqual(lines[0].source_path, pending)

    def test_batch_speed_text_combines_running_and_processed_length(self) -> None:
        """현재 속도는 진행 중 영상의 배속 평균이고, 평균은 처리한 길이(완료와 진행 위치)를 경과 시간으로 나눈 값인지 검증"""
        self.assertEqual(batch_speed_text([1.25, None], [30.0], [600.0], 300.0), "현재 1.25배, 평균 2.10배")
        self.assertEqual(batch_speed_text([], [], [], 0.0), "현재 정보 없음, 평균 정보 없음")


if __name__ == "__main__":
    unittest.main(verbosity=2)
