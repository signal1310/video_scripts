"""CUI 출력 포맷터, 정렬, 표시 열 단위 테스트"""

import io
import unittest
from contextlib import redirect_stdout
from typing import Any, cast
from unittest.mock import patch

from src.core.config import ClassifierConfig, DisplayConfig
from src.core.messages import ColumnId
from src.core.models import EfficiencySummary, SmartAction, SummaryReport
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
from tests._factories import make_eval, make_report, make_video

_DIVIDER = "=" * 10


def _render(report: SummaryReport, cfg: ClassifierConfig | None = None, **options: Any) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        CUIPrinter.print_report(report, cfg or ClassifierConfig(), **options)
    return buf.getvalue()


def _two_clip_report() -> SummaryReport:
    return make_report(
        [
            make_eval(
                make_video(filename="clip_10.mp4", vid_kbps=9000, vid_size_MB=1200.0, keyframe_interval=None),
                bitrate_ratio=2.571,
                expected_after_mb=600.0,
                expected_delta_mb=600.0,
            ),
            make_eval(
                make_video(filename="clip_2.mp4", fps=29.97, vid_kbps=12000, vid_size_MB=2400.0, keyframe_interval=1.5),
                bitrate_ratio=3.428,
                expected_after_mb=900.0,
                expected_delta_mb=1500.0,
            ),
        ]
    )


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
        groups = {
            "\nBR": ["BR", "비트레이트"],
            "최적\nBR": ["최적 BR", "최적 비트레이트"],
            "BR\n비율": ["BR 비율", "비트레이트 비율"],
            "KF\n간격": ["KF 간격", "키프레임 간격"],
            "\n너비": ["너비"],
            "\n높이": ["높이"],
            "\n회전": ["회전"],
            "예상 용량\n(MB)": ["예상 용량"],
            "예상 절약\n(MB)": ["예상 절약"],
            "분당 절약\n(MB/분)": ["분당 절약"],
            "권장\n처리": ["권장 처리"],
            "판정\n사유": ["판정 사유"],
        }
        for header, keys in groups.items():
            for key in keys:
                with self.subTest(key=key):
                    self.assertEqual(t[key], header)

    def test_cui_printer_handles_empty_table(self) -> None:
        """빈 테이블 데이터 리스트 출력 시 예외 없이 통과함을 검증"""
        TablePrinter.print([], sort_key=None, filename_maxlen=80, sanitize_emoji=True)

    def test_cui_default_row_order_puts_action_and_size_before_specs(self) -> None:
        """CUI 기본 행 순서가 이름, 권장 작업, 화질 등급, 용량 열을 코덱, BR 등 기술 사양보다 앞에 두는지 검증"""
        from src.core.config import ClassifierConfig
        from src.core.evaluator import VideoEvaluator
        from src.core.models import VideoProps
        from src.cui.cui_printer import CUIPrinter

        cfg = ClassifierConfig()
        video = VideoProps("clip_1.mp4", 1920, 1080, 0, 30.0, 4000, 128, 50.0, 100.0, "h264", 1.0)
        report = VideoEvaluator.evaluate_all("C:/dummy_dir", [video], cfg)
        buf = io.StringIO()
        with redirect_stdout(buf):
            CUIPrinter.print_tables(report, cfg)
        row = next(line for line in buf.getvalue().splitlines() if line.startswith("clip_1.mp4"))
        self.assertLess(row.index("CQ22"), row.index("h264"))
        self.assertLess(row.index("50.00"), row.index("h264"))
        self.assertLess(row.index("h264"), row.index("4000"))

    def test_report_groups_moved_preview_and_kept_rows(self) -> None:
        """디스크 조회 없이 moved_dirname(실이동), target_dirname(모의), 원본 보존 그룹을 구분 출력함을 검증"""
        folder = ClassifierConfig().dir_standard_cq22
        moved = make_eval(make_video(filename="moved.mp4", moved_dirname=folder), optimal_kbps=3500)
        preview = make_eval(make_video(filename="preview.mp4"), optimal_kbps=3500)
        kept = make_eval(
            make_video(filename="keep.mp4", vid_size_MB=50.0),
            bitrate_ratio=1.0,
            expected_delta_mb=0.0,
            smart_action=SmartAction.PASS_OPTIMAL,
            target_dirname=None,
        )
        out = _render(make_report([moved, preview, kept], root_dir="C:/nonexistent_dummy_root"))

        self.assertIn("대상 아님 (원본 보존)", out)
        self.assertIn(f"이동 완료: '{folder}' 폴더", out)
        self.assertIn(f"이동 예정: '{folder}' 폴더 (실제 이동 전)", out)
        self.assertEqual(out.count("* 열 약어: BR = 비트레이트(kbps), KF = 키프레임 간격(초)"), 1)
        self.assertLess(out.index("* 열 약어:"), out.index("너비"))
        self.assertIn("목표 비트레이트", out)
        self.assertNotIn("목표 b-rate", out)
        for label in ("CQ22 재인코딩", "과화질", "보존", "적정"):
            self.assertIn(label, out)

    def test_report_precision_unmeasured_keyframe_and_default_order(self) -> None:
        """열별 정밀도, FPS 정수 생략, 미측정 KF 표기, 이름 자연 정렬 기본 순서를 검증"""
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
        out = _render(_two_clip_report(), cfg)

        self.assertLess(out.index("clip_2.mp4"), out.index("clip_10.mp4"))
        for expected in (" 30 ", "29.97", "2.571", "미측정", "1,200"):
            self.assertIn(expected, out)
        self.assertNotIn("-1.000", out)
        self.assertNotIn("\u200b", out)
        row = next(line for line in out.splitlines() if "clip_2.mp4" in line)
        self.assertEqual(row.count("│"), 2)

    def test_report_limit_and_sort_by_saved_size(self) -> None:
        """sort_by 정렬 후 limit만큼만 출력하고 나머지 개수를 안내함을 검증"""
        cfg = ClassifierConfig()
        out = _render(_two_clip_report(), cfg, limit=1, sort_by=ColumnId.SAVED_SIZE, descending=True)
        self.assertIn("clip_2.mp4", out)
        self.assertNotIn("clip_10.mp4", out)
        self.assertIn("외 1개 생략", out)

        buf = io.StringIO()
        with redirect_stdout(buf):
            CUIPrinter.print_tables(_two_clip_report(), cfg, limit=1, sort_by=ColumnId.SAVED_SIZE, descending=True)
        tables = buf.getvalue()
        self.assertIn("clip_2.mp4", tables)
        self.assertIn("외 1개 생략", tables)
        self.assertNotIn("판별 기준 요약", tables)

    def test_report_summary_only_and_include_summary_flags(self) -> None:
        """summary_only는 표를 생략하고 include_summary=False는 요약을 생략함을 검증"""
        summary_only = _render(_two_clip_report(), summary_only=True)
        self.assertNotIn("clip_2.mp4", summary_only)
        self.assertIn("판별 기준 요약", summary_only)
        self.assertIn("전체 작업 폴더 용량 변화 시뮬레이션", summary_only)

        table_only = _render(_two_clip_report(), include_summary=False)
        self.assertIn("clip_2.mp4", table_only)
        self.assertNotIn("판별 기준 요약", table_only)

    def test_report_size_unit_gb_and_auto(self) -> None:
        """size_unit이 gb면 헤더에 단위를 표기하고 값에는 붙이지 않으며, auto면 값마다 단위를 붙임을 검증"""
        gb_cfg = ClassifierConfig(
            display=DisplayConfig(size_unit="gb", size_decimals=2, after_size_decimals=2, saved_size_decimals=2)
        )
        gb_table = _render(_two_clip_report(), gb_cfg).split(_DIVIDER)[0]
        self.assertIn("(GB)", gb_table)
        self.assertIn("1.17", gb_table)
        self.assertNotIn("1.17 GB", gb_table)

        auto_cfg = ClassifierConfig(display=DisplayConfig(size_unit="auto", size_decimals=2))
        auto_out = _render(_two_clip_report(), auto_cfg)
        self.assertIn("1.17 GB", auto_out)
        self.assertIn("600.00 MB", auto_out)
        self.assertNotIn("(MB)", auto_out.split(_DIVIDER)[0])

    def test_notebook_display_emits_table_then_summary_blocks(self) -> None:
        """Jupyter 커널 환경에서는 stdout 없이 표, 요약 순으로 display 2블록을 내보냄을 검증"""
        emitted: list[dict[str, str]] = []

        def _fake_display(obj: dict[str, str], *, raw: bool = False) -> None:
            self.assertTrue(raw)
            emitted.append(obj)

        buf = io.StringIO()
        with (
            patch("src.cui.cui_printer._resolve_notebook_display", return_value=_fake_display),
            redirect_stdout(buf),
        ):
            CUIPrinter.print_report(_two_clip_report(), ClassifierConfig(), limit=1, sort_by=ColumnId.SAVED_SIZE)

        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(len(emitted), 2)
        table, summary = (block["text/plain"] for block in emitted)
        self.assertIn("clip_2.mp4", table)
        self.assertNotIn("판별 기준 요약", table)
        self.assertIn("판별 기준 요약", summary)
        self.assertNotIn("clip_2.mp4", summary)

    def test_kept_row_shows_hypothetical_savings_but_summary_excludes_it(self) -> None:
        """원본 보존(시간 비효율) 행은 표에 가상 절감량을 표시하고 총합 요약에서는 제외함을 검증"""
        slow = make_eval(
            make_video(filename="slow_only.mp4", width=1280, height=720, vid_kbps=3000, vid_size_MB=40.0, duration=600.0),
            expected_after_mb=40.0,
            expected_delta_mb=0.0,
            smart_action=SmartAction.PASS_LOW_EFFICIENCY,
            target_dirname=None,
            hypothetical_after_mb=20.0,
            hypothetical_delta_mb=20.0,
        )
        report = make_report([slow], efficiency_summary=EfficiencySummary(5.0, 5.0, 50.0, 0.0, 0, 1, 600.0, 20.0))
        out = _render(report, ClassifierConfig(display=DisplayConfig(size_unit="mb")))

        table_part, summary_part = out.split("[1] 판별 기준 요약")
        self.assertIn("+20.00", table_part)
        self.assertIn("0.0 MB", summary_part)


if __name__ == "__main__":
    unittest.main(verbosity=2)
