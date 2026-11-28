"""결과 파일 배치(encode_placement) 단위 테스트: 원자적 교체 조건과 보관 폴더, 원본 보존"""

import os
import tempfile
import unittest

from src.core.config import (
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_OVERWRITE,
    NAME_CONFLICT_SETTINGS_SUBDIR,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
)
from src.service.encode.encode_placement import (
    move_without_overwrite,
    overwrites_existing_result,
    place_result,
)

SOURCE_BYTES: int = 500
RESULT_BYTES: int = 120
OLD_RESULT_BYTES: int = 40
MTIME_NS: int = 1_600_000_000 * 1_000_000_000


class _PlacementCase(unittest.TestCase):
    """임시 폴더에 원본, 기존 결과, 임시 결과 파일을 만드는 도우미"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def _write(self, relative: str, size: int) -> str:
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"s" * size)
        return path

    def _place(
        self,
        *,
        source: str,
        part: str,
        final: str,
        policy: str,
        name_conflict: str,
    ) -> None:
        place_result(
            source=source,
            part=part,
            final=final,
            archive_dirname="_원본보관",
            policy=policy,
            mtime_ns=MTIME_NS,
            name_conflict=name_conflict,
        )


class TestMoveWithoutOverwrite(_PlacementCase):
    """덮어쓰지 않는 이동의 기본 규칙"""

    def test_existing_destination_raises_and_keeps_both_files(self) -> None:
        source = self._write("a.tmp", SOURCE_BYTES)
        destination = self._write("a.mp4", OLD_RESULT_BYTES)
        with self.assertRaises(FileExistsError):
            move_without_overwrite(source, destination)
        self.assertTrue(os.path.exists(source))
        self.assertEqual(os.path.getsize(destination), OLD_RESULT_BYTES)

    def test_missing_destination_is_renamed(self) -> None:
        source = self._write("a.tmp", SOURCE_BYTES)
        destination = os.path.join(self.root, "a.mp4")
        move_without_overwrite(source, destination)
        self.assertFalse(os.path.exists(source))
        self.assertEqual(os.path.getsize(destination), SOURCE_BYTES)


class TestOverwriteGate(_PlacementCase):
    """덮어쓰기 허용 조건은 별도 폴더에 저장 + 덮어쓰기 정책뿐"""

    def test_only_separate_policy_with_overwrite_is_allowed(self) -> None:
        self.assertTrue(overwrites_existing_result(RESULT_POLICY_SEPARATE, NAME_CONFLICT_OVERWRITE))
        for policy in (RESULT_POLICY_ARCHIVE, RESULT_POLICY_REPLACE):
            with self.subTest(policy=policy):
                self.assertFalse(overwrites_existing_result(policy, NAME_CONFLICT_OVERWRITE))
        for name_conflict in (NAME_CONFLICT_NUMBER, NAME_CONFLICT_SETTINGS_SUBDIR, "skip", ""):
            with self.subTest(name_conflict=name_conflict):
                self.assertFalse(overwrites_existing_result(RESULT_POLICY_SEPARATE, name_conflict))


class TestAtomicReplace(_PlacementCase):
    """별도 폴더 + 덮어쓰기에서 임시 결과가 기존 결과를 원자적으로 교체"""

    def test_part_replaces_existing_result_and_source_is_untouched(self) -> None:
        source = self._write("clip.mp4", SOURCE_BYTES)
        final = self._write(os.path.join("_인코딩결과", "clip.mp4"), OLD_RESULT_BYTES)
        part = self._write(os.path.join("_인코딩결과", "clip.mp4.part"), RESULT_BYTES)

        self._place(
            source=source,
            part=part,
            final=final,
            policy=RESULT_POLICY_SEPARATE,
            name_conflict=NAME_CONFLICT_OVERWRITE,
        )

        self.assertEqual(os.path.getsize(final), RESULT_BYTES)
        self.assertFalse(os.path.exists(part))
        self.assertEqual(os.path.getsize(source), SOURCE_BYTES)
        self.assertEqual(os.stat(final).st_mtime_ns, MTIME_NS)

    def test_without_overwrite_policy_existing_result_is_kept(self) -> None:
        """덮어쓰기 정책이 아니면 기존 결과를 교체하지 않고 실패하며 두 파일 모두 남음"""
        source = self._write("clip.mp4", SOURCE_BYTES)
        final = self._write(os.path.join("_인코딩결과", "clip.mp4"), OLD_RESULT_BYTES)
        part = self._write(os.path.join("_인코딩결과", "clip.mp4.part"), RESULT_BYTES)

        with self.assertRaises(FileExistsError):
            self._place(
                source=source,
                part=part,
                final=final,
                policy=RESULT_POLICY_SEPARATE,
                name_conflict=NAME_CONFLICT_NUMBER,
            )
        self.assertEqual(os.path.getsize(final), OLD_RESULT_BYTES)
        self.assertTrue(os.path.exists(part))
        self.assertEqual(os.path.getsize(source), SOURCE_BYTES)


class TestArchiveUntouched(_PlacementCase):
    """원본 보관 폴더와 원본은 같은 이름 처리 값과 무관하게 덮어쓰지 않음"""

    def test_overwrite_value_does_not_replace_archived_original(self) -> None:
        source = self._write("clip.mp4", SOURCE_BYTES)
        archived = self._write(os.path.join("_원본보관", "clip.mp4"), OLD_RESULT_BYTES)
        part = self._write("clip.mp4.part", RESULT_BYTES)

        with self.assertRaises(FileExistsError):
            self._place(
                source=source,
                part=part,
                final=source,
                policy=RESULT_POLICY_ARCHIVE,
                name_conflict=NAME_CONFLICT_OVERWRITE,
            )
        self.assertEqual(os.path.getsize(archived), OLD_RESULT_BYTES)
        self.assertEqual(os.path.getsize(source), SOURCE_BYTES)
        self.assertTrue(os.path.exists(part))

    def test_overwrite_value_cannot_replace_an_existing_result_in_replace_policy(self) -> None:
        """원본 교체에서 결과 경로에 이미 파일이 있으면 덮어쓰지 않고 원본도 그대로 둠"""
        source = self._write("clip.mp4", SOURCE_BYTES)
        final = self._write("clip.mkv", OLD_RESULT_BYTES)
        part = self._write("clip.mkv.part", RESULT_BYTES)

        with self.assertRaises(FileExistsError):
            self._place(
                source=source,
                part=part,
                final=final,
                policy=RESULT_POLICY_REPLACE,
                name_conflict=NAME_CONFLICT_OVERWRITE,
            )
        self.assertEqual(os.path.getsize(source), SOURCE_BYTES)
        self.assertEqual(os.path.getsize(final), OLD_RESULT_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)

