import io
import unittest
from contextlib import redirect_stdout
from typing import Any, cast

from src.core.sorting import WindowsNameOrder as CoreWindowsNameOrder
from src.cui.cui_printer import (
    CONSOLE_PROGRESS_LINE_WIDTH,
    CUIPrinter,
    TablePrinter,
    print_console_progress,
    sanitize_text,
    truncate_text,
)
from src.cui.sorting import WindowsNameOrder, col, t


class TestCUIModule(unittest.TestCase):
    """윈도우 자연 정렬, 이모지 문자열 정제, 전각 문자 너비 계산 및 t 매핑 사전의 무결성을 검증"""

    def test_windows_name_order_natural_sort(self) -> None:
        """윈도우 탐색기 방식 자연 정렬(vid_2가 vid_10보다 앞섬)을 검증"""
        self.assertIs(CoreWindowsNameOrder, WindowsNameOrder)
        raw_list = ["vid_10.mp4", "vid_2.mp4", "vid_1.mp4", "vid_20.mp4"]
        sorted_list = sorted(raw_list, key=WindowsNameOrder)
        self.assertEqual(sorted_list, ["vid_1.mp4", "vid_2.mp4", "vid_10.mp4", "vid_20.mp4"])

    def test_print_console_progress_clears_line_on_last_item(self) -> None:
        """마지막 항목에서만 진행률 줄을 공백으로 지우고 커서를 줄 시작으로 되돌림을 검증"""
        erase = "\r" + " " * CONSOLE_PROGRESS_LINE_WIDTH + "\r"
        partial_buf = io.StringIO()
        with redirect_stdout(partial_buf):
            print_console_progress(1, 2, "a.mp4")
        self.assertNotIn(erase, partial_buf.getvalue())

        last_buf = io.StringIO()
        with redirect_stdout(last_buf):
            print_console_progress(2, 2, "a.mp4")
        out = last_buf.getvalue()
        self.assertIn("\r[progress100.0%] 파일 'a.mp4' 완료", out)
        self.assertTrue(out.endswith(erase))

    def test_col_wrapper_types_and_inversion(self) -> None:
        """col 래퍼의 숫자, 문자열, 자연 정렬 토큰 리스트 역전 기능을 검증"""
        self.assertEqual(col(10, order_by_desc=False), 10)
        self.assertEqual(col(10, order_by_desc=True), -10)
        self.assertEqual(col(3.14, order_by_desc=True), -3.14)

        # 문자열 역전
        asc_str = col("abc", order_by_desc=False)
        desc_str = col("abc", order_by_desc=True)
        self.assertNotEqual(asc_str, desc_str)

        # NULL(None) 및 지원하지 않는 타입 처리
        self.assertEqual(col(None, order_by_desc=False), float("inf"))
        self.assertEqual(col(None, order_by_desc=True), float("-inf"))
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
        """ASCII 고속 패스 및 전각 문자 2칸 너비 기준 max_len 이내 말줄임표(...) 절단 동작을 검증"""
        self.assertEqual(truncate_text("", max_len=10), "")
        self.assertEqual(truncate_text("clip.mp4", max_len=8), "clip.mp4")
        self.assertEqual(truncate_text("abcdefghij", max_len=8), "abcde...")
        self.assertEqual(truncate_text("abcd", max_len=1), ".")

        # 한글 5자 = 10칸 너비
        text = "안녕하세요반갑습니다"
        truncated = truncate_text(text, max_len=10)
        self.assertEqual(truncated, "안녕하...")
        self.assertEqual(truncate_text("가_test_나.mp4", max_len=14), "가_test_나.mp4")
        self.assertEqual(truncate_text("가_test_나.mp4", max_len=12), "가_test_...")
        self.assertEqual(truncate_text("한abcdefg.mp4", max_len=8), "한abc...")

    def test_t_dictionary_dual_key_resolution(self) -> None:
        """축약형과 한글 풀네임 모두 동일한 컴팩트 콘솔 테이블 헤더로 매핑되는지 검증"""
        self.assertEqual(t["BR"], "\nBR")
        self.assertEqual(t["b-rate"], "\nBR")
        self.assertEqual(t["비트레이트"], "\nBR")
        self.assertEqual(t["최적 BR"], "최적\nBR")
        self.assertEqual(t["최적 b-rate"], "최적\nBR")
        self.assertEqual(t["최적 비트레이트"], "최적\nBR")
        self.assertEqual(t["BR 비율"], "BR\n비율")
        self.assertEqual(t["b-rate 비율"], "BR\n비율")
        self.assertEqual(t["비트레이트 비율"], "BR\n비율")
        self.assertEqual(t["KF 간격"], "KF\n간격")
        self.assertEqual(t["키프레임 간격"], "KF\n간격")
        self.assertEqual(t["가로"], "\n너비")
        self.assertEqual(t["너비"], "\n너비")
        self.assertEqual(t["W"], "\n너비")
        self.assertEqual(t["세로"], "\n높이")
        self.assertEqual(t["높이"], "\n높이")
        self.assertEqual(t["H"], "\n높이")
        self.assertEqual(t["회전"], "\n회전")
        self.assertEqual(t["예상후"], "예상후\n(MB)")
        self.assertEqual(t["예상 용량"], "예상후\n(MB)")
        self.assertEqual(t["조치"], "권장\n작업")
        self.assertEqual(t["권장 작업"], "권장\n작업")
        self.assertEqual(t["사유"], "\n사유")
        self.assertEqual(t["판정 사유"], "\n사유")

    def test_cui_printer_handles_empty_table(self) -> None:
        """빈 테이블 데이터 리스트 출력 시 예외 없이 통과함을 검증"""
        TablePrinter.print([], sort_key=None, filename_maxlen=80, sanitize_emoji=True)

    def test_cui_printer_uses_report_preview_without_service_or_disk_io(self) -> None:
        """CUIPrinter.print_report가 디스크 조회 없이 moved_dirname(실이동), target_dirname(모의), 원본 보존 그룹을 구분 출력함을 검증"""
        import io
        from contextlib import redirect_stdout

        from src.core.config import ClassifierConfig
        from src.core.models import (
            EfficiencySummary,
            QualityZone,
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
            efficiency_summary=EfficiencySummary(
                min_saved_mb=5.0,
                min_mb_per_min=3.0,
                exempt_saved_mb=50.0,
                encode_duration_sec=120.0,
                low_savings_count=0,
                low_efficiency_count=0,
                skipped_over_duration_sec=0.0,
                skipped_over_potential_saved_mb=0.0,
            ),
            evaluations=[
                VideoEvaluation(
                    video=vid_moved,
                    optimal_kbps=3500,
                    bitrate_ratio=2.57,
                    quality_zone=QualityZone.OVER,
                    is_keyframe_defective=False,
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
        self.assertIn("이동 안 함 (원본 보존)", out)
        self.assertIn("이동 완료: '_1_표준인코딩_CQ22' 폴더", out)
        self.assertIn("이동 예정: '_1_표준인코딩_CQ22' 폴더 (실제 이동 전)", out)
        self.assertIn("* 열 약어: BR = 비트레이트(kbps), KF = 키프레임 간격(초)", out)
        self.assertEqual(out.count("* 열 약어:"), 1)
        self.assertIn("너비", out)
        self.assertIn("높이", out)
        self.assertLess(out.index("* 열 약어:"), out.index("너비"))
        self.assertIn("현재 총 용량", out)
        self.assertIn("BR", out)
        self.assertIn("권장", out)
        self.assertIn("작업", out)
        self.assertIn("사유", out)
        self.assertIn("CQ22 인코딩", out)
        self.assertIn("원본 보존", out)
        self.assertIn("과화질 감축", out)
        self.assertIn("목표 비트레이트", out)
        self.assertNotIn("목표 b-rate", out)
        self.assertIn("기준 충족", out)

    def test_cui_printer_precision_unmeasured_kf_limit_summary_and_sort_by(self) -> None:
        """열별 정밀도, FPS 정수 생략, 미측정 KF 표기, limit/summary_only, sort_by 기본/프리셋 정렬을 검증"""
        import io
        from contextlib import redirect_stdout

        from src.core.config import ClassifierConfig, DisplayConfig
        from src.core.messages import ColumnId
        from src.core.models import (
            EfficiencySummary,
            QualityZone,
            SmartAction,
            SummaryReport,
            VideoEvaluation,
            VideoProps,
        )

        evals = [
            VideoEvaluation(
                video=VideoProps(
                    "clip_10.mp4", 1920, 1080, 0, 30.0, 9000, 128, 1200.0, 60.0, "h264", None
                ),
                optimal_kbps=3500,
                bitrate_ratio=2.571,
                quality_zone=QualityZone.OVER,
                is_keyframe_defective=False,
                expected_after_mb=600.0,
                expected_delta_mb=600.0,
                smart_action=SmartAction.ENCODE_CQ22_OVER,
                target_dirname="_1_표준인코딩_CQ22",
            ),
            VideoEvaluation(
                video=VideoProps(
                    "clip_2.mp4", 1920, 1080, 0, 29.97, 12000, 128, 2400.0, 60.0, "h264", 1.5
                ),
                optimal_kbps=3500,
                bitrate_ratio=3.428,
                quality_zone=QualityZone.OVER,
                is_keyframe_defective=False,
                expected_after_mb=900.0,
                expected_delta_mb=1500.0,
                smart_action=SmartAction.ENCODE_CQ22_OVER,
                target_dirname="_1_표준인코딩_CQ22",
            ),
        ]
        rep = SummaryReport(
            root_dir="C:/dummy",
            total_count=2,
            encoded_count=2,
            passed_count=0,
            total_orig_mb=3600.0,
            total_expected_after_mb=1500.0,
            gross_saved_mb=2100.0,
            keyframe_increase_mb=0.0,
            net_saved_mb=2100.0,
            efficiency_summary=EfficiencySummary(
                min_saved_mb=5.0,
                min_mb_per_min=3.0,
                exempt_saved_mb=50.0,
                encode_duration_sec=120.0,
                low_savings_count=0,
                low_efficiency_count=0,
                skipped_over_duration_sec=0.0,
                skipped_over_potential_saved_mb=0.0,
            ),
            evaluations=evals,
        )
        cfg = ClassifierConfig(
            display=DisplayConfig(
                fps_decimals=2,
                fps_trim_whole=True,
                ratio_decimals=3,
                keyframe_decimals=1,
                size_decimals=0,
                saved_size_decimals=1,
                size_unit="mb",
            )
        )

        # 1. 기본 정렬(이름 자연 정렬: clip_2 < clip_10), FPS 정수 생략(30 / 29.97), KF 미측정, MB 표기
        buf = io.StringIO()
        with redirect_stdout(buf):
            CUIPrinter.print_report(rep, cfg)
        out = buf.getvalue()
        self.assertLess(out.index("clip_2.mp4"), out.index("clip_10.mp4"))
        self.assertIn(" 30 ", out)
        self.assertIn("29.97", out)
        self.assertIn("2.571", out)
        self.assertIn("미측정", out)
        self.assertNotIn("-1.000", out)
        self.assertIn("1,200", out)
        self.assertNotIn("\u200b", out)
        for line in out.splitlines():
            if "clip_2.mp4" in line:
                self.assertEqual(line.count("│"), 2)
                break

        # 2. sort_by="saved" + limit=1
        buf_limit = io.StringIO()
        with redirect_stdout(buf_limit):
            CUIPrinter.print_report(
                rep, cfg, limit=1, sort_by=ColumnId.SAVED_SIZE, descending=True
            )
        out_limit = buf_limit.getvalue()
        self.assertIn("clip_2.mp4", out_limit)
        self.assertNotIn("clip_10.mp4", out_limit)
        self.assertIn("외 1개 생략", out_limit)

        # 3. summary_only=True / include_summary=False / print_tables 분리 출력
        buf_sum = io.StringIO()
        with redirect_stdout(buf_sum):
            CUIPrinter.print_report(rep, cfg, summary_only=True)
        out_sum = buf_sum.getvalue()
        self.assertNotIn("clip_2.mp4", out_sum)
        self.assertIn("판별 기준 요약", out_sum)
        self.assertIn("전체 작업 폴더 용량 변화 시뮬레이션", out_sum)

        buf_no_sum = io.StringIO()
        with redirect_stdout(buf_no_sum):
            CUIPrinter.print_report(rep, cfg, include_summary=False)
        out_no_sum = buf_no_sum.getvalue()
        self.assertIn("clip_2.mp4", out_no_sum)
        self.assertNotIn("판별 기준 요약", out_no_sum)

        buf_tables = io.StringIO()
        with redirect_stdout(buf_tables):
            CUIPrinter.print_tables(
                rep, cfg, limit=1, sort_by=ColumnId.SAVED_SIZE, descending=True
            )
        out_tables = buf_tables.getvalue()
        self.assertIn("clip_2.mp4", out_tables)
        self.assertIn("외 1개 생략", out_tables)
        self.assertNotIn("판별 기준 요약", out_tables)

        # 4. UNIX 스타일 size_unit="auto" 및 "gb"
        from src.core.formatting import format_size_mb

        self.assertEqual(format_size_mb(0.5, 2, unit="auto"), "512.00 KB")
        self.assertEqual(format_size_mb(500.0, 1, unit="auto"), "500.0 MB")
        self.assertEqual(format_size_mb(2048.0, 2, unit="auto"), "2.00 GB")
        self.assertEqual(format_size_mb(512.0, 2, unit="gb"), "0.50")
        self.assertEqual(
            format_size_mb(512.0, 2, unit="gb", show_unit=True), "0.50 GB"
        )
        self.assertEqual(
            format_size_mb(-2048.0, 2, unit="auto", signed=True), "-2.00 GB"
        )

        buf_gb = io.StringIO()
        cfg_gb = ClassifierConfig(
            display=DisplayConfig(
                size_unit="gb",
                size_decimals=2,
                after_size_decimals=2,
                saved_size_decimals=2,
            )
        )
        with redirect_stdout(buf_gb):
            CUIPrinter.print_report(rep, cfg_gb)
        out_gb = buf_gb.getvalue()
        table_gb = out_gb.split("=" * 10)[0]
        self.assertIn("(GB)", table_gb)
        self.assertIn("1.17", table_gb)
        self.assertNotIn("1.17 GB", table_gb)

        buf_auto = io.StringIO()
        cfg_auto = ClassifierConfig(display=DisplayConfig(size_unit="auto", size_decimals=2))
        with redirect_stdout(buf_auto):
            CUIPrinter.print_report(rep, cfg_auto)
        out_auto = buf_auto.getvalue()
        self.assertIn("1.17 GB", out_auto)
        self.assertIn("600.00 MB", out_auto)
        self.assertNotIn("(MB)", out_auto.split("=" * 10)[0])

        # 5. Jupyter 커널 환경에서는 표 -> 요약 순으로 display 2블록 분리 출력
        from unittest.mock import patch

        emitted: list[dict[str, str]] = []

        def _fake_display(obj: dict[str, str], *, raw: bool = False) -> None:
            self.assertTrue(raw)
            emitted.append(obj)

        buf_nb = io.StringIO()
        with (
            patch("src.cui.cui_printer._resolve_notebook_display", return_value=_fake_display),
            redirect_stdout(buf_nb),
        ):
            CUIPrinter.print_report(rep, cfg, limit=1, sort_by=ColumnId.SAVED_SIZE)

        self.assertEqual(buf_nb.getvalue(), "")
        self.assertEqual(len(emitted), 2)
        self.assertIn("clip_2.mp4", emitted[0]["text/plain"])
        self.assertNotIn("판별 기준 요약", emitted[0]["text/plain"])
        self.assertIn("판별 기준 요약", emitted[1]["text/plain"])
        self.assertNotIn("clip_2.mp4", emitted[1]["text/plain"])

        # 6. 원본 보존(시간 비효율) 행도 CUI 표에는 가상 인코딩 예상 절감량을 표시하고 총합 요약에서는 제외
        ev_slow_pass = VideoEvaluation(
            video=VideoProps("slow_only.mp4", 1280, 720, 0, 30.0, 3000, 128, 40.0, 600.0, "h264", 1.0),
            optimal_kbps=1500,
            bitrate_ratio=2.0,
            quality_zone=QualityZone.OVER,
            is_keyframe_defective=False,
            expected_after_mb=40.0,
            expected_delta_mb=0.0,
            smart_action=SmartAction.PASS_LOW_EFFICIENCY,
            target_dirname=None,
            hypothetical_after_mb=20.0,
            hypothetical_delta_mb=20.0,
        )
        rep_hyp = SummaryReport(
            root_dir="C:/dummy",
            total_count=1,
            encoded_count=0,
            passed_count=1,
            total_orig_mb=40.0,
            total_expected_after_mb=40.0,
            gross_saved_mb=0.0,
            keyframe_increase_mb=0.0,
            net_saved_mb=0.0,
            efficiency_summary=EfficiencySummary(5.0, 5.0, 50.0, 0.0, 0, 1, 600.0, 20.0),
            evaluations=[ev_slow_pass],
        )
        buf_hyp = io.StringIO()
        with redirect_stdout(buf_hyp):
            CUIPrinter.print_report(rep_hyp, ClassifierConfig(display=DisplayConfig(size_unit="mb")))
        out_hyp = buf_hyp.getvalue()
        table_part, summary_part = out_hyp.split("[1] 판별 기준 요약")
        self.assertIn("+20.00", table_part)
        self.assertIn("0.0 MB", summary_part)


if __name__ == "__main__":
    unittest.main(verbosity=2)
