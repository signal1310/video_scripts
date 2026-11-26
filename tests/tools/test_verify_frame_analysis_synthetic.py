"""합성 검증 스크립트의 정리 범위를 격리된 임시 폴더에서 검증"""

import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import verify_frame_analysis_synthetic as verify


class TestVerifyFrameAnalysisSyntheticCleanup(unittest.TestCase):
    """실행이 만든 폴더만 지우고 상위 폴더와 형제 폴더는 보존하는지 검증"""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._temp.name)
        self.shared = self.base / "shared"
        self.shared.mkdir()
        (self.shared / "keep.txt").write_text("keep", encoding="utf-8")
        self.foreign = self.base / "elsewhere"
        self.foreign.mkdir()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def assert_shared_folders_intact(self) -> None:
        self.assertTrue(self.base.is_dir())
        self.assertTrue(self.foreign.is_dir())
        self.assertTrue((self.shared / "keep.txt").is_file())

    def assert_no_run_leftovers(self) -> None:
        leftovers = [path for path in self.base.iterdir() if path.name.startswith(verify.OUT_DIR_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_create_run_dir_makes_distinct_folder_under_base(self) -> None:
        first = verify.create_run_dir(self.base)
        second = verify.create_run_dir(self.base)

        self.assertNotEqual(first, second)
        for run_dir in (first, second):
            self.assertTrue(run_dir.is_dir())
            self.assertEqual(run_dir.parent, self.base)
            self.assertTrue(run_dir.name.startswith(verify.OUT_DIR_PREFIX))

    def test_cleanup_removes_only_run_dir(self) -> None:
        run_dir = verify.create_run_dir(self.base)
        (run_dir / "artifact.mp4").write_bytes(b"x")

        with redirect_stdout(io.StringIO()):
            verify.cleanup_outputs(run_dir)

        self.assertFalse(run_dir.exists())
        self.assert_shared_folders_intact()

    def test_cleanup_ignores_path_that_is_not_a_folder(self) -> None:
        target = self.base / "plain.txt"
        target.write_text("x", encoding="utf-8")

        with redirect_stdout(io.StringIO()):
            verify.cleanup_outputs(target)

        self.assertTrue(target.is_file())

    def test_main_cleanup_after_passing_run_keeps_parent_and_siblings(self) -> None:
        with patch.object(verify, "run_verification", return_value=0), redirect_stdout(io.StringIO()):
            code = verify.main(["--out-dir", str(self.base), "--cleanup"])

        self.assertEqual(code, 0)
        self.assert_shared_folders_intact()
        self.assert_no_run_leftovers()

    def test_main_cleanup_after_failing_run_keeps_parent_and_siblings(self) -> None:
        with patch.object(verify, "create_video", return_value="[encode failed]"), redirect_stdout(io.StringIO()):
            code = verify.main(["--out-dir", str(self.base), "--cleanup"])

        self.assertEqual(code, 1)
        self.assert_shared_folders_intact()
        self.assert_no_run_leftovers()

    def test_main_without_cleanup_keeps_run_dir_only(self) -> None:
        captured: list[pathlib.Path] = []

        def _fake_run(_ffmpeg: str, run_dir: pathlib.Path) -> int:
            captured.append(run_dir)
            (run_dir / "artifact.mp4").write_bytes(b"x")
            return 1

        with patch.object(verify, "run_verification", side_effect=_fake_run), redirect_stdout(io.StringIO()):
            code = verify.main(["--out-dir", str(self.base)])

        self.assertEqual(code, 1)
        self.assertEqual(len(captured), 1)
        self.assertTrue((captured[0] / "artifact.mp4").is_file())
        self.assertEqual(captured[0].parent, self.base)
        self.assert_shared_folders_intact()


if __name__ == "__main__":
    unittest.main(verbosity=2)
