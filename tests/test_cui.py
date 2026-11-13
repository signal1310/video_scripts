import unittest
from typing import Any, cast

from src.cui.cui_printer import CUIPrinter, TablePrinter, sanitize_text, truncate_text
from src.cui.sorting import WindowsNameOrder, col, t


class TestCUIModule(unittest.TestCase):
    """
    - 윈도우 자연 정렬, 이모지 문자열 정제, 전각 문자 너비 계산 및 t 매핑 사전의 무결성을 검증.
    """

    def test_windows_name_order_natural_sort(self) -> None:
        """
        - 윈도우 탐색기 방식 자연 정렬(vid_2가 vid_10보다 앞섬)을 검증.
        """
        raw_list = ["vid_10.mp4", "vid_2.mp4", "vid_1.mp4", "vid_20.mp4"]
        sorted_list = sorted(raw_list, key=WindowsNameOrder)
        self.assertEqual(sorted_list, ["vid_1.mp4", "vid_2.mp4", "vid_10.mp4", "vid_20.mp4"])

    def test_col_wrapper_types_and_inversion(self) -> None:
        """
        - col 래퍼의 숫자, 문자열, 자연 정렬 토큰 리스트 역전 기능을 검증.
        """
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
        """
        - 한글, 한자, 일본어, 영문, 숫자는 보존하고 터미널 열 정렬을 깨뜨리는 이모지는 '?'로 치환함을 검증.
        """
        self.assertEqual(sanitize_text("한국어_동영상_123.mp4"), "한국어_동영상_123.mp4")
        self.assertEqual(sanitize_text("日本語_漢字.mp4"), "日本語_漢字.mp4")
        self.assertEqual(sanitize_text("🔥Fire_Video✨.mp4"), "?Fire_Video?.mp4")
        self.assertEqual(sanitize_text(""), "")

    def test_truncate_text_wide_characters(self) -> None:
        """
        - 동아시아 전각 문자를 2칸 너비로 정확히 계산하여 말줄임표(...)를 부가하는지 검증.
        """
        # 한글 5자 = 10칸 너비
        text = "안녕하세요반갑습니다"
        truncated = truncate_text(text, max_len=10)
        self.assertTrue(truncated.endswith("..."))
        self.assertLessEqual(len(truncated), len(text))

    def test_t_dictionary_dual_key_resolution(self) -> None:
        """
        - 축약형과 한글 풀네임 모두 동일한 컴팩트 콘솔 테이블 헤더로 매핑되는지 검증.
        """
        self.assertEqual(t["b-rate"], "\nb-rate")
        self.assertEqual(t["비트레이트"], "\nb-rate")
        self.assertEqual(t["최적 b-rate"], "최적\nb-rate")
        self.assertEqual(t["최적 비트레이트"], "최적\nb-rate")
        self.assertEqual(t["b-rate 비율"], "b-rate\n비율")
        self.assertEqual(t["비트레이트 비율"], "b-rate\n비율")
        self.assertEqual(t["KF 간격"], "KF\n간격")
        self.assertEqual(t["키프레임 간격"], "KF\n간격")

    def test_cui_printer_handles_empty_table(self) -> None:
        """
        - 빈 테이블 데이터 리스트 출력 시 예외 없이 통과함을 검증.
        """
        TablePrinter.print([], sort_key=None, filename_maxlen=80, sanitize_emoji=True)

    def test_backward_compat_src_ui_re_exports(self) -> None:
        """
        - 이전 src.ui 모듈 경로를 통한 임포트가 정상 동작하는지 하위 호환성을 검증.
        """
        from src.ui import CUIPrinter as LegacyCUIPrinter
        from src.ui import TablePrinter as LegacyTablePrinter
        from src.ui import WindowsNameOrder as LegacyWindowsNameOrder
        from src.ui.cui_printer import sanitize_text as legacy_sanitize
        from src.ui.sorting import col as legacy_col

        self.assertIs(LegacyCUIPrinter, CUIPrinter)
        self.assertIs(LegacyTablePrinter, TablePrinter)
        self.assertIs(LegacyWindowsNameOrder, WindowsNameOrder)
        self.assertIs(legacy_sanitize, sanitize_text)
        self.assertIs(legacy_col, col)


if __name__ == "__main__":
    unittest.main(verbosity=2)
