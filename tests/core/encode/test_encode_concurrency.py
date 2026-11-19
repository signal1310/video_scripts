"""동시 재인코딩 수 계산: 설정 값 우선, 자동 값은 인코더 종류와 코어 수로 제한"""

import unittest

from src.core.config import CONCURRENT_JOBS_MAX
from src.core.encode.encode_concurrency import AUTO_JOBS_CPU, AUTO_JOBS_HARDWARE, resolve_concurrency


class TestResolveConcurrency(unittest.TestCase):
    """설정 값과 자동 규칙"""

    def test_explicit_setting_is_used_as_is(self) -> None:
        self.assertEqual(resolve_concurrency(2, is_hardware=True, cpu_count=16), 2)

    def test_explicit_setting_is_capped_at_input_maximum(self) -> None:
        self.assertEqual(resolve_concurrency(CONCURRENT_JOBS_MAX + 5, is_hardware=False, cpu_count=16), CONCURRENT_JOBS_MAX)

    def test_auto_uses_hardware_default_for_hardware_encoders(self) -> None:
        self.assertEqual(resolve_concurrency(0, is_hardware=True, cpu_count=16), AUTO_JOBS_HARDWARE)

    def test_auto_uses_cpu_default_for_software_encoders(self) -> None:
        self.assertEqual(resolve_concurrency(0, is_hardware=False, cpu_count=16), AUTO_JOBS_CPU)

    def test_auto_is_limited_by_core_count_and_never_below_one(self) -> None:
        self.assertEqual(resolve_concurrency(0, is_hardware=True, cpu_count=1), 1)
        self.assertEqual(resolve_concurrency(0, is_hardware=False, cpu_count=0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
