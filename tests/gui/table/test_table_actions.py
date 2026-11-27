"""테이블 행 탐색기 위치 표시가 Windows Shell API와 폴백 경로를 올바르게 호출함을 검증"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.workspace_paths import format_root_dirs, root_logical_prefix
from src.gui.table import table_actions
from src.gui.table.table_actions import (
    REVEAL_FOLDER_LIMIT,
    EvaluationPathIndex,
    _reveal_windows_shell_many,
    evaluation_at_path,
    open_media_path,
    reveal_path,
    reveal_paths,
    reveal_video_file,
)
from tests._factories import make_eval, make_video


class EvaluationPathIndexTests(unittest.TestCase):
    """경로 색인: 구성할 때만 경로를 계산하고 반복 조회는 사전 조회로 끝냄"""

    def test_index_computes_each_row_path_once_for_repeated_lookups(self) -> None:
        evals = [make_eval(video=make_video(filename=f"clip{index}.mp4")) for index in range(5)]
        root = os.path.join("C:\\", "work")
        real = table_actions.resolve_video_filepath
        with patch(
            "src.gui.table.table_actions.resolve_video_filepath", side_effect=real
        ) as resolver:
            index = EvaluationPathIndex(root, evals)
            self.assertEqual(resolver.call_count, len(evals))
            for _ in range(3):
                for name in ("CLIP1.MP4", "clip3.mp4"):
                    self.assertIsNotNone(index.find(os.path.join(root, name)))
                self.assertIsNone(index.find(os.path.join(root, "missing.mp4")))
            self.assertEqual(resolver.call_count, len(evals))

    def test_index_finds_files_of_each_root_of_a_multi_root_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            spec = format_root_dirs([first, second])
            evals = [
                make_eval(video=make_video(filename=f"{root_logical_prefix(root, spec)}a.mp4"))
                for root in (first, second)
            ]
            index = EvaluationPathIndex(spec, evals)
            for root, ev in zip((first, second), evals, strict=True):
                self.assertIs(index.find(os.path.join(root, "A.MP4")), ev)
            self.assertIsNone(index.find(os.path.join(first, "b.mp4")))


class EvaluationAtPathTests(unittest.TestCase):
    """절대 경로로 표의 평가 결과를 찾는지"""

    def test_finds_the_evaluation_by_file_and_ignores_case_and_separators(self) -> None:
        evals = [
            make_eval(video=make_video(filename="a.mp4")),
            make_eval(video=make_video(filename="sub/b.mp4")),
        ]
        root = os.path.join("C:\\", "work")
        found = evaluation_at_path(root, evals, "c:/WORK/SUB/B.MP4")
        assert found is not None
        self.assertEqual(found.video.filename, "sub/b.mp4")

    def test_returns_none_for_a_file_the_table_does_not_hold(self) -> None:
        evals = [make_eval(video=make_video(filename="a.mp4"))]
        root = os.path.join("C:\\", "work")
        self.assertIsNone(evaluation_at_path(root, evals, "C:/other/b.mp4"))
        self.assertIsNone(evaluation_at_path("", evals, "C:/work/a.mp4"))
        self.assertIsNone(evaluation_at_path(root, evals, ""))


class TestRevealPath(unittest.TestCase):
    """경로 단위 위치 표시: 파일이 있을 때만 Shell API로 선택 표시하고, 없으면 거짓"""

    def test_existing_file_is_selected_through_the_shell_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "part.mp4.part")
            with open(target, "wb") as handle:
                handle.write(b"x")
            with (
                patch("src.gui.table.table_actions._reveal_windows_shell_many", return_value=True) as shell,
                patch("src.gui.table.table_actions.os.name", "nt"),
            ):
                self.assertTrue(reveal_path(target))
            norm = os.path.abspath(os.path.normpath(target))
            shell.assert_called_once_with(os.path.dirname(norm), [norm])

    def test_missing_file_is_not_revealed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("src.gui.table.table_actions._reveal_windows_shell_many") as shell:
            self.assertFalse(reveal_path(os.path.join(tmp, "none.mp4.part")))
        shell.assert_not_called()


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
                patch("src.gui.table.table_actions.os.name", "nt"),
                patch(
                    "src.gui.table.table_actions._reveal_windows_shell_many",
                    return_value=True,
                ) as shell_mock,
                patch("src.gui.table.table_actions.subprocess.Popen") as popen_mock,
            ):
                self.assertTrue(reveal_video_file(tmp, ev))

            norm = os.path.abspath(os.path.normpath(path))
            shell_mock.assert_called_once_with(os.path.dirname(norm), [norm])
            popen_mock.assert_not_called()

    def test_reveal_falls_back_to_explorer_command_when_shell_fails(self) -> None:
        """Shell API가 실패하면 explorer.exe /select 폴백 명령을 호출함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample clip.mp4")
            with open(path, "wb") as f:
                f.write(b"v")
            ev = make_eval(make_video(filename="sample clip.mp4"))

            with (
                patch("src.gui.table.table_actions.os.name", "nt"),
                patch(
                    "src.gui.table.table_actions._reveal_windows_shell_many",
                    return_value=False,
                ),
                patch("src.gui.table.table_actions.subprocess.Popen") as popen_mock,
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
                patch("src.gui.table.table_actions._reveal_windows_shell_many") as shell_mock,
                patch("src.gui.table.table_actions.subprocess.Popen") as popen_mock,
            ):
                self.assertFalse(reveal_video_file(tmp, ev))

            shell_mock.assert_not_called()
            popen_mock.assert_not_called()

    def test_reveal_windows_shell_balances_com_and_frees_pidl(self) -> None:
        """한 파일도 폴더 창 호출로 선택 표시하며, PIDL 해제와 COM 초기/해제를 짝맞춰 실행함을 검증"""
        shell32 = SimpleNamespace(
            ILCreateFromPathW=MagicMock(return_value=12345),
            ILFindLastID=MagicMock(return_value=999),
            ILFree=MagicMock(),
            SHOpenFolderAndSelectItems=MagicMock(return_value=0),
        )
        ole32 = SimpleNamespace(
            CoInitializeEx=MagicMock(return_value=0),
            CoUninitialize=MagicMock(),
        )
        fake_windll = SimpleNamespace(shell32=shell32, ole32=ole32)

        with patch("src.gui.table.table_actions.ctypes.windll", fake_windll, create=True):
            self.assertTrue(_reveal_windows_shell_many(r"C:ideos", [r"C:ideos,b.mp4"]))

        shell32.ILCreateFromPathW.assert_any_call(r"C:ideos")
        shell32.ILCreateFromPathW.assert_any_call(r"C:ideos,b.mp4")
        self.assertEqual(shell32.SHOpenFolderAndSelectItems.call_args.args[1], 1)
        self.assertEqual(shell32.ILFree.call_count, 2)
        ole32.CoUninitialize.assert_called_once()

        shell32.ILFree.reset_mock()
        ole32.CoInitializeEx.return_value = -2147417850  # RPC_E_CHANGED_MODE
        ole32.CoUninitialize.reset_mock()
        with patch("src.gui.table.table_actions.ctypes.windll", fake_windll, create=True):
            self.assertTrue(_reveal_windows_shell_many(r"C:ideos", [r"C:ideos,b.mp4"]))

        self.assertEqual(shell32.ILFree.call_count, 2)
        ole32.CoUninitialize.assert_not_called()


class OpenMediaPathTests(unittest.TestCase):
    """경로 단위 열기: 파일이 있을 때만 기본 플레이어 실행 요청을 보냄"""

    def test_existing_file_is_started_and_missing_file_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            with patch.object(os, "startfile", create=True) as start:
                self.assertTrue(open_media_path(path))
                self.assertFalse(open_media_path(os.path.join(tmp, "missing.mp4")))
        start.assert_called_once_with(path)


class TestRevealPaths(unittest.TestCase):
    """여러 파일 위치 열기: 같은 폴더는 한 번에 열고, 폴더 창 수 상한을 넘으면 넘은 폴더 수를 돌려줌"""

    def test_files_of_one_folder_are_revealed_together_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.mp4.part")
            second = os.path.join(tmp, "b.mp4.part")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"x")
            with patch("src.gui.table.table_actions._reveal_folder") as reveal:
                skipped = reveal_paths([first, second, os.path.join(tmp, "missing.part")])
        self.assertEqual(skipped, 0)
        reveal.assert_called_once()
        folder, files = reveal.call_args.args
        self.assertEqual(folder, os.path.dirname(os.path.abspath(first)))
        self.assertEqual(files, [os.path.abspath(first), os.path.abspath(second)])

    def test_shell_many_selects_each_file_of_one_folder_in_a_single_window(self) -> None:
        """Shell API 경로는 폴더 PIDL 하나와 파일별 자식 항목을 한 번의 선택 호출로 넘김(실제 탐색기는 열지 않음)"""
        windll = MagicMock()
        windll.shell32.ILCreateFromPathW.side_effect = [10, 21, 22]
        windll.shell32.ILFindLastID.side_effect = [31, 32]
        windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
        with patch("src.gui.table.table_actions.ctypes.windll", windll, create=True):
            self.assertTrue(_reveal_windows_shell_many(r"C:\work", ["C:\\work\a.part", "C:\\work\b.part"]))
        call = windll.shell32.SHOpenFolderAndSelectItems.call_args
        self.assertEqual(call.args[0], 10)
        self.assertEqual(call.args[1], 2)
        self.assertEqual(windll.shell32.ILFree.call_count, 3)

    def test_folder_windows_beyond_the_limit_are_not_opened_and_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths: list[str] = []
            for index in range(REVEAL_FOLDER_LIMIT + 2):
                folder = os.path.join(tmp, str(index))
                os.makedirs(folder)
                path = os.path.join(folder, "a.mp4.part")
                with open(path, "wb") as handle:
                    handle.write(b"x")
                paths.append(path)
            with patch("src.gui.table.table_actions._reveal_folder") as reveal:
                skipped = reveal_paths(paths)
        self.assertEqual(reveal.call_count, REVEAL_FOLDER_LIMIT)
        self.assertEqual(skipped, 2)

    def test_folder_without_the_shell_api_falls_back_to_the_first_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp4.part")
            with open(path, "wb") as handle:
                handle.write(b"x")
            with (
                patch("src.gui.table.table_actions.os.name", "posix"),
                patch("src.gui.table.table_actions.subprocess.Popen") as popen,
            ):
                reveal_paths([path])
        popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
