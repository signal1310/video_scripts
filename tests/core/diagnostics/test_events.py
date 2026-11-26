"""사용자 동작 기록 문구: 파일 이름만 남기기, 폴더 목록 요약, 오류 첫 줄 자르기 검증"""

import unittest

from src.core.diagnostics.events import (
    ERROR_LINE_LIMIT,
    describe_error,
    describe_file,
    describe_folder_change,
    describe_task,
)


class TestEventTexts(unittest.TestCase):
    """기록 문구가 경로와 긴 오류를 줄여 남기는지 검증"""

    def test_describe_task_joins_name_and_action(self) -> None:
        """작업 이름과 동작이 한 문구로 합쳐지는지 검증"""
        self.assertEqual(describe_task("정밀 분석", "시작"), "정밀 분석 시작")

    def test_describe_file_keeps_only_the_basename(self) -> None:
        """파일 경로가 이름만 남는지 검증"""
        self.assertEqual(describe_file("시작", "C:/media/clip.mp4"), "시작: clip.mp4")
        self.assertEqual(describe_file("중지", ""), "중지: 알 수 없음")

    def test_describe_folder_change_summarizes_a_multi_root_spec(self) -> None:
        """여러 작업 폴더는 첫 폴더 이름과 나머지 개수로 줄이는지 검증"""
        self.assertEqual(describe_folder_change("C:/media/movies"), "변경: movies")
        self.assertEqual(
            describe_folder_change("C:/media/movies;D:/clips"),
            "변경: movies 외 1개",
        )

    def test_describe_folder_change_without_roots(self) -> None:
        """폴더 목록이 비면 동작 이름만 남는지 검증"""
        self.assertEqual(describe_folder_change("  "), "변경")

    def test_describe_error_keeps_only_the_first_line_and_trims(self) -> None:
        """오류 문구가 첫 줄만 남고 상한을 넘지 않는지 검증"""
        self.assertEqual(describe_error("\n  첫 줄  \n두 번째 줄"), "첫 줄")
        self.assertEqual(len(describe_error("가" * (ERROR_LINE_LIMIT + 50))), ERROR_LINE_LIMIT)
        self.assertEqual(describe_error("   "), "")


if __name__ == "__main__":
    unittest.main()
