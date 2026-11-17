"""테이블 행 탐색기 위치 표시가 Windows Shell API와 폴백 경로를 올바르게 호출함을 검증"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.gui.widgets.table_actions import _reveal_windows_shell, reveal_video_file
from tests._factories import make_eval, make_video


class TestRevealVideoFile(unittest.TestCase):
    """탐색기 위치 표시가 Shell API 우선 및 explorer 폴백으로 동작함을 검증"""

    def test_reveal_uses_windows_shell_api_when_available(self) -> None:
        """Windows Shell API가 성공하면 explorer 프로세스를 따로 띄우지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample, clip & 1.mp4")
            with open(path, "wb") as f:
                f.write(b"v")
            ev = make_eval(make_video(filename="sample, clip & 1.mp4"))

            with (
                patch("src.gui.widgets.table_actions.os.name", "nt"),
                patch(
                    "src.gui.widgets.table_actions._reveal_windows_shell",
                    return_value=True,
                ) as shell_mock,
                patch("src.gui.widgets.table_actions.subprocess.Popen") as popen_mock,
            ):
                self.assertTrue(reveal_video_file(tmp, ev))

            shell_mock.assert_called_once_with(os.path.abspath(os.path.normpath(path)))
            popen_mock.assert_not_called()

    def test_reveal_falls_back_to_explorer_command_when_shell_fails(self) -> None:
        """Shell API가 실패하면 explorer.exe /select 폴백 명령을 호출함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample clip.mp4")
            with open(path, "wb") as f:
                f.write(b"v")
            ev = make_eval(make_video(filename="sample clip.mp4"))

            with (
                patch("src.gui.widgets.table_actions.os.name", "nt"),
                patch(
                    "src.gui.widgets.table_actions._reveal_windows_shell",
                    return_value=False,
                ),
                patch("src.gui.widgets.table_actions.subprocess.Popen") as popen_mock,
            ):
                self.assertTrue(reveal_video_file(tmp, ev))

            popen_mock.assert_called_once()
            (cmd,) = popen_mock.call_args.args
            self.assertEqual(
                cmd, f'explorer.exe /select,"{os.path.abspath(os.path.normpath(path))}"'
            )

    def test_reveal_returns_false_when_file_is_missing(self) -> None:
        """대상 파일이 없으면 False를 반환하고 탐색기를 호출하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            ev = make_eval(make_video(filename="missing.mp4"))
            with (
                patch("src.gui.widgets.table_actions._reveal_windows_shell") as shell_mock,
                patch("src.gui.widgets.table_actions.subprocess.Popen") as popen_mock,
            ):
                self.assertFalse(reveal_video_file(tmp, ev))

            shell_mock.assert_not_called()
            popen_mock.assert_not_called()

    def test_reveal_windows_shell_balances_com_and_frees_pidl(self) -> None:
        """_reveal_windows_shell이 PIDL 해제와 COM 초기/해제를 짝맞춰 실행함을 검증"""
        shell32 = SimpleNamespace(
            ILCreateFromPathW=MagicMock(return_value=12345),
            ILFree=MagicMock(),
            SHOpenFolderAndSelectItems=MagicMock(return_value=0),
        )
        ole32 = SimpleNamespace(
            CoInitializeEx=MagicMock(return_value=0),
            CoUninitialize=MagicMock(),
        )
        fake_windll = SimpleNamespace(shell32=shell32, ole32=ole32)

        with patch("src.gui.widgets.table_actions.ctypes.windll", fake_windll, create=True):
            self.assertTrue(_reveal_windows_shell(r"C:\videos\a,b.mp4"))

        shell32.ILCreateFromPathW.assert_called_once_with(r"C:\videos\a,b.mp4")
        shell32.SHOpenFolderAndSelectItems.assert_called_once_with(12345, 0, None, 0)
        shell32.ILFree.assert_called_once_with(12345)
        ole32.CoUninitialize.assert_called_once()

        shell32.ILFree.reset_mock()
        ole32.CoInitializeEx.return_value = -2147417850  # RPC_E_CHANGED_MODE
        ole32.CoUninitialize.reset_mock()
        with patch("src.gui.widgets.table_actions.ctypes.windll", fake_windll, create=True):
            self.assertTrue(_reveal_windows_shell(r"C:\videos\a,b.mp4"))

        shell32.ILFree.assert_called_once_with(12345)
        ole32.CoUninitialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
