"""재인코딩 경로 계산: 보관 폴더, 별도 출력 폴더의 상대 경로, 같은 이름 처리, 임시 파일, 제외 폴더"""

import os
import tempfile
import unittest

from src.core.config import (
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_SKIP,
    ExecutionConfig,
)
from src.core.encode_paths import (
    archive_path_of,
    choose_free_path,
    excluded_folder_names,
    in_place_output_path,
    numbered_candidate,
    part_path_of,
    separate_output_path,
)
from src.core.file_manager import get_video_filenames

_ROOT = os.path.join("work", "root")


class TestResultPaths(unittest.TestCase):
    """결과 경로와 보관 경로는 원본 폴더 기준으로 계산"""

    def test_in_place_output_changes_only_the_extension(self) -> None:
        source = os.path.join(_ROOT, "sub", "clip.avi")
        self.assertEqual(in_place_output_path(source, "mp4"), os.path.join(_ROOT, "sub", "clip.mp4"))
        self.assertEqual(in_place_output_path(source, "mkv"), os.path.join(_ROOT, "sub", "clip.mkv"))

    def test_archive_path_sits_under_archive_folder_of_the_source_directory(self) -> None:
        source = os.path.join(_ROOT, "sub", "clip.mp4")
        self.assertEqual(
            archive_path_of(source, "_원본보관"),
            os.path.join(_ROOT, "sub", "_원본보관", "clip.mp4"),
        )

    def test_separate_output_keeps_relative_folder_layout(self) -> None:
        """작업 폴더 기준 하위 폴더 계층을 출력 폴더 아래에 그대로 재현"""
        source = os.path.join(_ROOT, "a", "b", "clip.avi")
        self.assertEqual(
            separate_output_path(source, _ROOT, "_재인코딩결과", "mp4"),
            os.path.join(_ROOT, "_재인코딩결과", "a", "b", "clip.mp4"),
        )

    def test_separate_output_for_root_level_file(self) -> None:
        source = os.path.join(_ROOT, "clip.mkv")
        self.assertEqual(
            separate_output_path(source, _ROOT, "_재인코딩결과", "mkv"),
            os.path.join(_ROOT, "_재인코딩결과", "clip.mkv"),
        )

    def test_part_file_is_named_after_the_final_path(self) -> None:
        self.assertEqual(part_path_of(os.path.join(_ROOT, "clip.mp4")), os.path.join(_ROOT, "clip.mp4.part"))


class TestNameConflict(unittest.TestCase):
    """같은 이름 처리: 건너뜀은 충돌 시 None, 번호 정책은 비어 있는 첫 번호 사용"""

    def test_free_candidate_is_used_as_is(self) -> None:
        candidate = os.path.join(_ROOT, "clip.mp4")
        self.assertEqual(choose_free_path(candidate, NAME_CONFLICT_SKIP, lambda _p: False), candidate)

    def test_skip_policy_returns_none_on_conflict(self) -> None:
        candidate = os.path.join(_ROOT, "clip.mp4")
        self.assertIsNone(choose_free_path(candidate, NAME_CONFLICT_SKIP, lambda _p: True))

    def test_number_policy_takes_first_free_number(self) -> None:
        taken = {os.path.join(_ROOT, "clip.mp4"), os.path.join(_ROOT, "clip (1).mp4")}
        candidate = os.path.join(_ROOT, "clip.mp4")
        self.assertEqual(
            choose_free_path(candidate, NAME_CONFLICT_NUMBER, lambda p: p in taken),
            os.path.join(_ROOT, "clip (2).mp4"),
        )

    def test_numbered_candidate_keeps_stem_and_extension(self) -> None:
        self.assertEqual(numbered_candidate(os.path.join(_ROOT, "영상 a.mp4"), 3), os.path.join(_ROOT, "영상 a (3).mp4"))

    def test_unknown_policy_never_overwrites(self) -> None:
        self.assertIsNone(choose_free_path(os.path.join(_ROOT, "clip.mp4"), "overwrite", lambda _p: True))


class TestScanSkipsEncodeFolders(unittest.TestCase):
    """재귀 스캔이 보관 폴더와 출력 폴더의 영상을 대상으로 다시 잡지 않음(재처리 방지)"""

    def test_recursive_scan_ignores_archive_and_output_folders(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for relative in ("clip.mp4", os.path.join("_원본보관", "old.mp4"), os.path.join("_재인코딩결과", "new.mkv")):
                os.makedirs(os.path.dirname(os.path.join(root, relative)), exist_ok=True)
                with open(os.path.join(root, relative), "wb") as handle:
                    handle.write(b"v")
            names = get_video_filenames(
                root, recursive=True, excluded_dirnames=excluded_folder_names(ExecutionConfig())
            )
        self.assertEqual(names, ["clip.mp4"])


class TestExcludedFolderNames(unittest.TestCase):
    """스캔 제외 목록은 보관 폴더와 출력 폴더 이름(빈 이름은 제외)"""

    def test_both_folder_names_are_excluded(self) -> None:
        execution = ExecutionConfig()
        self.assertEqual(excluded_folder_names(execution), ("_원본보관", "_재인코딩결과"))

    def test_blank_names_are_not_excluded(self) -> None:
        execution = ExecutionConfig(archive_dirname=" ", output_dirname="out")
        self.assertEqual(excluded_folder_names(execution), ("out",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
