"""재생 중 영상 파일의 공유 모드 열기 도우미가 열린 상태에서도 이름 바꾸기와 삭제를 허용하는지 검증"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.gui import shared_file_handle

_IS_WINDOWS: bool = sys.platform == "win32"


class SharedFileHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "clip.bin")
        with open(self.path, "wb") as f:
            f.write(b"c" * 64)

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_open_file_allows_rename_and_delete_while_open(self) -> None:
        """도우미로 연 파일을 핸들을 연 채로 이름 바꾸기와 삭제가 성공하는지 검증"""
        source = shared_file_handle.open_shared_read_file(self.path)
        self.assertIsNotNone(source)
        assert source is not None
        self.addCleanup(source.close)
        renamed = os.path.join(self._tmp.name, "moved.bin")
        os.rename(self.path, renamed)
        self.assertFalse(os.path.exists(self.path))
        os.remove(renamed)
        self.assertFalse(os.path.exists(renamed))

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_plain_open_blocks_delete_for_contrast(self) -> None:
        """비교 기준으로 일반 열기는 같은 상황에서 삭제가 막히는지 검증"""
        with open(self.path, "rb"), self.assertRaises(PermissionError):
            os.remove(self.path)

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_source_reads_the_whole_file(self) -> None:
        """열린 QFile이 파일 내용 전체를 읽는지 검증"""
        source = shared_file_handle.open_shared_read_file(self.path)
        self.assertIsNotNone(source)
        assert source is not None
        self.addCleanup(source.close)
        self.assertEqual(bytes(source.readAll()), b"c" * 64)

    @unittest.skipUnless(_IS_WINDOWS, "공유 모드 핸들은 Windows 전용")
    def test_missing_file_returns_none(self) -> None:
        """없는 파일은 None을 반환해 호출부가 기존 방식으로 대체하게 하는지 검증"""
        self.assertIsNone(shared_file_handle.open_shared_read_file(os.path.join(self._tmp.name, "none.bin")))

    def test_other_platforms_return_none(self) -> None:
        """Windows가 아니면 도우미가 파일을 열지 않고 None을 반환하는지 검증"""
        with patch.object(shared_file_handle, "_IS_WINDOWS", False):
            self.assertIsNone(shared_file_handle.open_shared_read_file(self.path))


if __name__ == "__main__":
    unittest.main()
