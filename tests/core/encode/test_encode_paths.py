"""인코딩 경로 계산: 보관 폴더, 별도 출력 폴더의 상대 경로, 같은 이름 처리, 임시 파일, 제외 폴더"""

import os
import tempfile
import unittest

from src.core.analysis.file_manager import get_video_filenames
from src.core.config import (
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_OVERWRITE,
    NAME_CONFLICT_SETTINGS_SUBDIR,
    NAME_CONFLICT_SKIP,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_SEPARATE,
    ExecutionConfig,
)
from src.core.encode.encode_paths import (
    archive_path_of,
    choose_free_path,
    excluded_folder_names,
    in_place_output_path,
    numbered_candidate,
    part_path_of,
    path_key,
    resolve_output_path,
    separate_output_path,
    settings_subdir_for,
)

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
            separate_output_path(source, _ROOT, "_인코딩결과", "mp4"),
            os.path.join(_ROOT, "_인코딩결과", "a", "b", "clip.mp4"),
        )

    def test_separate_output_for_root_level_file(self) -> None:
        source = os.path.join(_ROOT, "clip.mkv")
        self.assertEqual(
            separate_output_path(source, _ROOT, "_인코딩결과", "mkv"),
            os.path.join(_ROOT, "_인코딩결과", "clip.mkv"),
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


class TestSettingsDefinedOutput(unittest.TestCase):
    """별도 폴더에 저장 전용 정책(덮어쓰기, 설정별 하위 폴더)의 경로 계산"""

    def _resolve(self, *, name_conflict: str, taken: tuple[str, ...] = (), policy: str = RESULT_POLICY_SEPARATE) -> str | None:
        taken_set = set(taken)
        return resolve_output_path(
            source_path=os.path.join(_ROOT, "clip.mp4"),
            root_dir=_ROOT,
            output_dirname="_인코딩결과",
            container="mp4",
            name_conflict=name_conflict,
            result_policy=policy,
            encoder="libx264",
            quality=23.0,
            exists=lambda path: path in taken_set,
        )

    def test_settings_subdir_name_is_deterministic_and_filesystem_safe(self) -> None:
        """설정 하위 폴더 이름이 코덱 계열과 품질값으로 결정되고 금지 문자를 쓰지 않음"""
        self.assertEqual(settings_subdir_for("hevc_nvenc", 22.0), "hevc_cq22")
        self.assertEqual(settings_subdir_for("libx264", 23.0), "h264_crf23")
        self.assertEqual(settings_subdir_for("hevc_nvenc", 22.5), "hevc_cq22p5")
        for name in (settings_subdir_for("libx265", 19.75), settings_subdir_for("av1_nvenc", 30.0)):
            with self.subTest(name=name):
                self.assertNotIn(".", name)
                self.assertNotIn(" ", name)
                self.assertNotIn(":", name)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)

    def test_settings_subdir_inserts_one_folder_under_output_root(self) -> None:
        """출력 폴더 바로 아래에 설정 하위 폴더 한 단계가 들어감"""
        path = self._resolve(name_conflict=NAME_CONFLICT_SETTINGS_SUBDIR)
        self.assertEqual(
            path,
            os.path.join(_ROOT, "_인코딩결과", "h264_crf23", "clip.mp4"),
        )

    def test_settings_subdir_keeps_relative_layout_below_it(self) -> None:
        """설정 하위 폴더 아래에 원본의 하위 폴더 구조를 유지함"""
        source = os.path.join(_ROOT, "a", "b", "clip.mkv")
        self.assertEqual(
            separate_output_path(source, _ROOT, "_인코딩결과", "mkv", settings_subdir="hevc_cq22"),
            os.path.join(_ROOT, "_인코딩결과", "hevc_cq22", "a", "b", "clip.mkv"),
        )

    def test_settings_subdir_numbers_instead_of_overwriting_within_its_folder(self) -> None:
        """같은 설정 하위 폴더 안에 같은 이름이 있으면 번호를 붙이고 기존 파일은 그대로 둠"""
        existing = os.path.join(_ROOT, "_인코딩결과", "h264_crf23", "clip.mp4")
        path = self._resolve(name_conflict=NAME_CONFLICT_SETTINGS_SUBDIR, taken=(existing,))
        self.assertEqual(
            path,
            os.path.join(_ROOT, "_인코딩결과", "h264_crf23", "clip (1).mp4"),
        )

    def test_overwrite_keeps_the_target_path_even_when_it_exists(self) -> None:
        """덮어쓰기는 기존 결과 파일이 있어도 그 대상 경로를 돌려줌(교체는 배치 단계가 함)"""
        existing = os.path.join(_ROOT, "_인코딩결과", "clip.mp4")
        path = self._resolve(name_conflict=NAME_CONFLICT_OVERWRITE, taken=(existing,))
        self.assertEqual(path, existing)

    def test_overwrite_falls_back_to_number_for_a_path_already_taken_this_batch(self) -> None:
        """이번 배치가 이미 고른 결과 경로는 덮어쓰지 않고 번호를 붙여 다른 항목 결과를 지키기"""
        existing = os.path.join(_ROOT, "_인코딩결과", "clip.mp4")
        path = resolve_output_path(
            source_path=os.path.join(_ROOT, "clip.avi"),
            root_dir=_ROOT,
            output_dirname="_인코딩결과",
            container="mp4",
            name_conflict=NAME_CONFLICT_OVERWRITE,
            result_policy=RESULT_POLICY_SEPARATE,
            encoder="libx264",
            quality=23.0,
            exists=lambda candidate: candidate == existing,
            reserved=(path_key(existing),),
        )
        self.assertEqual(path, os.path.join(_ROOT, "_인코딩결과", "clip (1).mp4"))

    def test_overwrite_and_settings_subdir_fall_back_to_number_for_other_policies(self) -> None:
        """별도 폴더가 아닌 결과 처리 방식에서는 두 정책을 무시하고 번호 규칙으로 동작"""
        existing = os.path.join(_ROOT, "clip.mp4")
        for policy in (NAME_CONFLICT_OVERWRITE, NAME_CONFLICT_SETTINGS_SUBDIR):
            with self.subTest(policy=policy):
                path = resolve_output_path(
                    source_path=os.path.join(_ROOT, "clip.avi"),
                    root_dir=_ROOT,
                    output_dirname="_인코딩결과",
                    container="mp4",
                    name_conflict=policy,
                    result_policy=RESULT_POLICY_ARCHIVE,
                    encoder="libx264",
                    quality=23.0,
                    exists=lambda candidate, taken=existing: candidate == taken,
                )
                self.assertEqual(path, os.path.join(_ROOT, "clip (1).mp4"))


class TestScanSkipsEncodeFolders(unittest.TestCase):
    """재귀 스캔이 보관 폴더와 출력 폴더의 영상을 대상으로 다시 잡지 않음(재처리 방지)"""

    def _tree(self, root: str, relatives: tuple[str, ...]) -> list[str]:
        for relative in relatives:
            path = os.path.join(root, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"v")
        return get_video_filenames(
            root, recursive=True, excluded_dirnames=excluded_folder_names(ExecutionConfig())
        )

    def test_recursive_scan_ignores_archive_and_output_folders(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            names = self._tree(
                root,
                ("clip.mp4", os.path.join("_원본보관", "old.mp4"), os.path.join("_인코딩결과", "new.mkv")),
            )
        self.assertEqual(names, ["clip.mp4"])

    def test_recursive_scan_ignores_previous_default_output_folder(self) -> None:
        """기본값을 쓰던 작업에 남은 예전 기본 출력 폴더의 결과도 다시 잡지 않음"""
        with tempfile.TemporaryDirectory() as root:
            names = self._tree(
                root,
                (
                    "clip.mp4",
                    os.path.join("_인코딩결과", "new.mkv"),
                    os.path.join("_재인코딩결과", "sub", "old.mkv"),
                ),
            )
        self.assertEqual(names, ["clip.mp4"])


class TestExcludedFolderNames(unittest.TestCase):
    """스캔 제외 목록은 보관 폴더, 출력 폴더, 예전 기본 출력 폴더 이름(빈 이름과 중복은 제외)"""

    def test_both_folder_names_are_excluded(self) -> None:
        execution = ExecutionConfig()
        self.assertEqual(
            excluded_folder_names(execution), ("_원본보관", "_인코딩결과", "_재인코딩결과")
        )

    def test_blank_names_are_not_excluded(self) -> None:
        execution = ExecutionConfig(archive_dirname=" ", output_dirname="out")
        self.assertEqual(excluded_folder_names(execution), ("out", "_재인코딩결과"))

    def test_previous_default_saved_as_current_output_is_not_duplicated(self) -> None:
        execution = ExecutionConfig(output_dirname="_재인코딩결과")
        self.assertEqual(excluded_folder_names(execution), ("_원본보관", "_재인코딩결과"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
