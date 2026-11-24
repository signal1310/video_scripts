import unittest

from src.core.config import DisplayConfig
from src.core.formatting import (
    format_duration_hms,
    format_duration_ko,
    format_float_precision,
    format_keyframe_interval,
    format_size_mb,
)
from src.core.messages import LABEL_UNMEASURED

_MB_PER_GB = 1024
_MB_PER_TB = 1024 * 1024


class TestFormatting(unittest.TestCase):
    """수치, 시간, 키프레임, 용량 서식의 경계값을 검증"""

    def test_float_precision_zero_normalization_and_trim(self) -> None:
        cases = [
            ((-0.001, 2), {}, "0.00"),
            ((-0.4, 0), {}, "0"),
            ((0.0, 2), {"signed": True}, "0.00"),
            ((-0.001, 0), {"signed": True}, "0"),
            ((1234.5, 1), {"thousands": True}, "1,234.5"),
            ((1.5, 1), {"signed": True}, "+1.5"),
            ((30.0, 2), {"trim_whole": True}, "30"),
            ((29.97, 2), {"trim_whole": True}, "29.97"),
            ((30.0, 0), {"trim_whole": True}, "30"),
            ((1.0, 9), {}, "1.0000"),
            ((1.55555, -3), {}, "2"),
        ]
        for (value, decimals), options, expected in cases:
            with self.subTest(value=value, decimals=decimals, options=options):
                self.assertEqual(format_float_precision(value, decimals, **options), expected)

    def test_auto_size_scale_boundaries(self) -> None:
        cases = [
            (_MB_PER_TB, "1.0 TB"),
            (_MB_PER_TB - 1, "1,024.0 GB"),
            (-2 * _MB_PER_TB, "-2.0 TB"),
            (_MB_PER_GB, "1.0 GB"),
            (_MB_PER_GB - 0.1, "1,023.9 MB"),
            (1.0, "1.0 MB"),
            (0.0, "0.0 MB"),
            (0.5, "512.0 KB"),
        ]
        for size_mb, expected in cases:
            with self.subTest(size_mb=size_mb):
                self.assertEqual(format_size_mb(size_mb, 1, unit="auto"), expected)

    def test_fixed_units_and_unit_visibility(self) -> None:
        self.assertEqual(format_size_mb(2048, 1, unit="gb"), "2.0")
        self.assertEqual(format_size_mb(2048, 1, unit="gb", show_unit=True), "2.0 GB")
        self.assertEqual(format_size_mb(2048, 1, unit="mb"), "2,048.0")
        self.assertEqual(format_size_mb(2048, 1, unit="auto", show_unit=False), "2.0")
        self.assertEqual(format_size_mb(2048, 0, unit="mb", thousands=False), "2048")

    def test_signed_size_collapses_values_below_half_unit_to_unsigned_zero(self) -> None:
        cases = [
            ((0.04, 1, "mb"), "0.0"),
            ((-0.04, 1, "mb"), "0.0"),
            ((0.06, 1, "mb"), "+0.1"),
            ((-0.06, 1, "mb"), "-0.1"),
            ((1.0, 1, "gb"), "0.0"),
            ((1e-7, 1, "auto"), "0.0 MB"),
            ((5.0, 1, "mb"), "+5.0"),
        ]
        for (size_mb, decimals, unit), expected in cases:
            with self.subTest(size_mb=size_mb, unit=unit):
                self.assertEqual(format_size_mb(size_mb, decimals, unit=unit, signed=True), expected)

    def test_duration_formats(self) -> None:
        hms = [(0, "00:00"), (59.6, "01:00"), (3599, "59:59"), (3600, "01:00:00"), (3661, "01:01:01"), (-5, "00:00")]
        for seconds, expected in hms:
            with self.subTest(hms=seconds):
                self.assertEqual(format_duration_hms(seconds), expected)
        ko = [
            (0, "0분"), (-5, "0분"), (1, "1초"), (59, "59초"), (60, "1분"), (3599, "59분"),
            (3600, "1시간"), (3660, "1시간 1분"), (3600 * 1000, "1,000시간"),
        ]
        for seconds, expected in ko:
            with self.subTest(ko=seconds):
                self.assertEqual(format_duration_ko(seconds), expected)

    def test_keyframe_interval_label_without_warning_glyph(self) -> None:
        """미측정은 레이블로, 측정값은 소수 자릿수만 적용하고 경고 기호를 붙이지 않음을 검증"""
        display = DisplayConfig(keyframe_decimals=1)
        self.assertEqual(format_keyframe_interval(None, display), LABEL_UNMEASURED)
        self.assertEqual(format_keyframe_interval(-0.1, display), LABEL_UNMEASURED)
        self.assertEqual(format_keyframe_interval(0.0, display), "0.0")
        self.assertEqual(format_keyframe_interval(2.25, display), "2.2")
        self.assertEqual(format_keyframe_interval(2.5, display), "2.5")
        self.assertNotIn("⚠", format_keyframe_interval(5.0, display))


if __name__ == "__main__":
    unittest.main()
