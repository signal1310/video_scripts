"""공유 모드 파일 열기 도우미 검증: 열린 상태에서 이름 바꾸기와 삭제, 이진 읽기, 플랫폼 대체"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.core import file_sharing

_IS_WINDOWS: bool = sys.platform == "win32"


class SharedHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "clip.bin")
        with open(self.path, "wb") as handle:
            handle.write(b"c" * 64)

    def test_other_platforms_return_none(self) -> None:
        """Windows가 아니면 파일을 열지 않고 None을 돌려 일반 열기로 대체하는지 검증"""
        with patch.object(file_sharing, "IS_WINDOWS", False):
            self.assertIsNone(file_sharing.open_shared_binary(self.path))

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_binary_source_reads_the_whole_file(self) -> None:
        source = file_sharing.open_shared_binary(self.path)
        self.assertIsNotNone(source)
        assert source is not None
        self.addCleanup(source.close)
        self.assertEqual(source.read(), b"c" * 64)

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_open_file_allows_rename_and_delete(self) -> None:
        """핸들을 연 채로 이름 바꾸기와 삭제가 성공하는지 검증"""
        source = file_sharing.open_shared_binary(self.path)
        self.assertIsNotNone(source)
        assert source is not None
        renamed = os.path.join(self._tmp.name, "moved.bin")
        os.rename(self.path, renamed)
        self.assertFalse(os.path.exists(self.path))
        os.remove(renamed)
        source.close()

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_close_shared_handle_releases_the_handle(self) -> None:
        handle = file_sharing.create_shared_read_handle(self.path)
        self.assertIsNotNone(handle)
        assert handle is not None
        file_sharing.close_shared_handle(handle)
        self.assertTrue(os.path.isfile(self.path))

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(file_sharing.open_shared_binary(os.path.join(self._tmp.name, "none.bin")))
        self.assertIsNone(file_sharing.create_shared_read_handle(os.path.join(self._tmp.name, "none.bin")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
