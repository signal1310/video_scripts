import unittest
from typing import Any, cast

from src.core.sorting import WindowsNameOrder as CoreWindowsNameOrder
from src.cui.cui_printer import CUIPrinter, TablePrinter, sanitize_text, truncate_text
from src.cui.sorting import WindowsNameOrder, col, t


class TestCUIModule(unittest.TestCase):
    """윈도우 자연 정렬, 이모지 문자열 정제, 전각 문자 너비 계산 및 t 매핑 사전의 무결성을 검증"""

    def test_windows_name_order_natural_sort(self) -> None:
        """윈도우 탐색기 방식 자연 정렬(vid_2가 vid_10보다 앞섬)을 검증"""
        self.assertIs(CoreWindowsNameOrder, WindowsNameOrder)
        raw_list = ["vid_10.mp4", "vid_2.mp4", "vid_1.mp4", "vid_20.mp4"]
        sorted_list = sorted(raw_list, key=WindowsNameOrder)
        self.assertEqual(sorted_list, ["vid_1.mp4", "vid_2.mp4", "vid_10.mp4", "vid_20.mp4"])

    def test_col_wrapper_types_and_inversion(self) -> None:
        """col 래퍼의 숫자, 문자열, 자연 정렬 토큰 리스트 역전 기능을 검증"""
        self.assertEqual(col(10, order_by_desc=False), 10)
        self.assertEqual(col(10, order_by_desc=True), -10)
        self.assertEqual(col(3.14, order_by_desc=True), -3.14)

        # 문자열 역전
        asc_str = col("abc", order_by_desc=False)
        desc_str = col("abc", order_by_desc=True)
        self.assertNotEqual(asc_str, desc_str)

        # 지원하지 않는 타입 예외
        with self.assertRaises(TypeError):
            col(cast("Any", {}), order_by_desc=True)

    def test_sanitize_text_cjk_and_emojis(self) -> None:
        """한글, 한자, 일본어, 영문, 숫자는 보존하고 터미널 열 정렬을 깨뜨리는 이모지는 '?'로 치환함을 검증"""
        self.assertEqual(sanitize_text("sample_clip_01.mp4"), "sample_clip_01.mp4")
        self.assertEqual(sanitize_text("한국어_동영상_123.mp4"), "한국어_동영상_123.mp4")
        self.assertEqual(sanitize_text("日本語_漢字.mp4"), "日本語_漢字.mp4")
        self.assertEqual(sanitize_text("🔥Fire_Video✨.mp4"), "?Fire_Video?.mp4")
        self.assertEqual(sanitize_text(""), "")

    def test_truncate_text_wide_characters(self) -> None:
        """ASCII 고속 패스 및 전각 문자 2칸 너비 기준 말줄임표(...) 절단 동작을 검증"""
        self.assertEqual(truncate_text("", max_len=10), "")
        self.assertEqual(truncate_text("clip.mp4", max_len=8), "clip.mp4")
        self.assertEqual(truncate_text("abcdefghij", max_len=8), "abcdef...")
        self.assertEqual(truncate_text("abcd", max_len=1), "...")

        # 한글 5자 = 10칸 너비
        text = "안녕하세요반갑습니다"
        truncated = truncate_text(text, max_len=10)
        self.assertEqual(truncated, "안녕하...")
        self.assertEqual(truncate_text("가_test_나.mp4", max_len=14), "가_test_나.mp4")
        self.assertEqual(truncate_text("가_test_나.mp4", max_len=12), "가_test_나...")

    def test_t_dictionary_dual_key_resolution(self) -> None:
        """축약형과 한글 풀네임 모두 동일한 컴팩트 콘솔 테이블 헤더로 매핑되는지 검증"""
        self.assertEqual(t["b-rate"], "\nb-rate")
        self.assertEqual(t["비트레이트"], "\nb-rate")
        self.assertEqual(t["최적 b-rate"], "최적\nb-rate")
        self.assertEqual(t["최적 비트레이트"], "최적\nb-rate")
        self.assertEqual(t["b-rate 비율"], "b-rate\n비율")
        self.assertEqual(t["비트레이트 비율"], "b-rate\n비율")
        self.assertEqual(t["KF 간격"], "KF\n간격")
        self.assertEqual(t["키프레임 간격"], "KF\n간격")

    def test_cui_printer_handles_empty_table(self) -> None:
        """빈 테이블 데이터 리스트 출력 시 예외 없이 통과함을 검증"""
        TablePrinter.print([], sort_key=None, filename_maxlen=80, sanitize_emoji=True)

    def test_cui_printer_uses_report_preview_without_service_or_disk_io(self) -> None:
        """CUIPrinter.print_report가 디스크 조회 없이 moved_dirname(실이동)·target_dirname(모의)·원본 보존 그룹을 구분 출력함을 검증"""
        import io
        from contextlib import redirect_stdout

        from src.core.config import ClassifierConfig
        from src.core.models import (
            FootageStrategy,
            QualityZone,
            SmallFootageDistribution,
            SmartAction,
            SummaryReport,
            VideoEvaluation,
            VideoProps,
        )

        vid_moved = VideoProps(
            "moved.mp4", 1920, 1080, 0, 30.0, 9000, 128, 120.0, 60.0, "h264", 1.0,
            moved_dirname="_1_표준인코딩_CQ22",
        )
        vid_preview = VideoProps(
            "preview.mp4", 1920, 1080, 0, 30.0, 9000, 128, 120.0, 60.0, "h264", 1.0,
            moved_dirname=None,
        )
        vid_pass = VideoProps(
            "keep.mp4", 1280, 720, 0, 30.0, 1500, 128, 50.0, 60.0, "h264", 1.0,
            moved_dirname=None,
        )
        rep = SummaryReport(
            root_dir="C:/nonexistent_dummy_root",
            total_count=3,
            encoded_count=2,
            passed_count=1,
            total_orig_mb=290.0,
            total_expected_after_mb=170.0,
            gross_saved_mb=120.0,
            keyframe_increase_mb=0.0,
            net_saved_mb=120.0,
            small_distribution=SmallFootageDistribution(
                small_threshold_mb=30.0,
                min_saved_mb=5.0,
                total_over_count=2,
                small_over_count=0,
                total_potential_saved_mb=120.0,
                small_potential_saved_mb=0.0,
                small_share_ratio=0.0,
                strategy=FootageStrategy.IGNORE_SMALL,
                reason="테스트",
            ),
            evaluations=[
                VideoEvaluation(
                    video=vid_moved,
                    optimal_kbps=3500,
                    bitrate_ratio=2.57,
                    quality_zone=QualityZone.OVER,
                    is_keyframe_defective=False,
                    is_small_footage=False,
                    expected_after_mb=60.0,
                    expected_delta_mb=60.0,
                    smart_action=SmartAction.ENCODE_CQ22_OVER,
                    target_dirname="_1_표준인코딩_CQ22",
                ),
                VideoEvaluation(
                    video=vid_preview,
                    optimal_kbps=3500,
                    bitrate_ratio=2.57,
                    quality_zone=QualityZone.OVER,
                    is_keyframe_defective=False,
                    is_small_footage=False,
                    expected_after_mb=60.0,
                    expected_delta_mb=60.0,
                    smart_action=SmartAction.ENCODE_CQ22_OVER,
                    target_dirname="_1_표준인코딩_CQ22",
                ),
                VideoEvaluation(
                    video=vid_pass,
                    optimal_kbps=1500,
                    bitrate_ratio=1.0,
                    quality_zone=QualityZone.OPTIMAL,
                    is_keyframe_defective=False,
                    is_small_footage=False,
                    expected_after_mb=50.0,
                    expected_delta_mb=0.0,
                    smart_action=SmartAction.PASS_OPTIMAL,
                    target_dirname=None,
                ),
            ],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            CUIPrinter.print_report(
                report=rep,
                config=ClassifierConfig(),
            )
        out = buf.getvalue()
        self.assertIn("분류되지 않은 비디오 목록", out)
        self.assertIn("폴더로 분류된 비디오 목록", out)
        self.assertIn("폴더로 모의 분류된 비디오 목록", out)
        self.assertIn("현재 총 용량", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
