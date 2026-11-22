"""GUI 진입 환경 설정 검증: Windows Qt FFmpeg 하드웨어 디코더 우회 기본값"""

import os
import sys
import unittest
from unittest import mock

from src.gui import MULTIMEDIA_HW_DECODE_ENV, configure_multimedia_environment


class ConfigureMultimediaEnvironmentTests(unittest.TestCase):
    """Windows에서만 소프트웨어 디코더 기본값을 두고 사용자가 지정한 값은 유지하는지 검증"""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(MULTIMEDIA_HW_DECODE_ENV, None)

    def test_windows_defaults_to_the_software_decoder(self) -> None:
        """Windows에서 하드웨어 디코더 우회 값이 없으면 소프트웨어 디코더로 설정하는지 검증"""
        with mock.patch.object(sys, "platform", "win32"):
            configure_multimedia_environment()
        self.assertEqual(os.environ.get(MULTIMEDIA_HW_DECODE_ENV), "none")

    def test_windows_keeps_a_user_specified_value(self) -> None:
        """사용자가 직접 지정한 디코더 값을 덮어쓰지 않는지 검증"""
        os.environ[MULTIMEDIA_HW_DECODE_ENV] = "d3d11va"
        with mock.patch.object(sys, "platform", "win32"):
            configure_multimedia_environment()
        self.assertEqual(os.environ[MULTIMEDIA_HW_DECODE_ENV], "d3d11va")

    def test_other_platforms_leave_the_environment_untouched(self) -> None:
        """Windows가 아니면 환경 변수를 만들지 않는지 검증"""
        with mock.patch.object(sys, "platform", "linux"):
            configure_multimedia_environment()
        self.assertNotIn(MULTIMEDIA_HW_DECODE_ENV, os.environ)


if __name__ == "__main__":
    unittest.main(verbosity=2)
