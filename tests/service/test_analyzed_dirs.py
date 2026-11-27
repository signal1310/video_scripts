"""분석 폴더 기록의 볼륨 위치 저장, 하위 폴더 포함 판정, 옛 문자열 키 변환을 검증"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.analysis.volume_identity import VolumeLocation
from src.core.analysis.volume_identity import volume_location as real_volume_location
from src.service import analyzed_dirs as mod
from src.service.analyzed_dirs import (
    ANALYZED_DIRS_FILENAME,
    AnalyzedDir,
    is_analyzed_dir,
    mark_analyzed_dir,
)

_GET_DATA_DIR = "src.service.analyzed_dirs.get_data_dir"
_VOLUME_LOCATION = "src.service.analyzed_dirs.volume_location"


def _legacy_key(path: str, *, recursive: bool = False) -> str:
    """옛 형식 기록 키(정규화한 경로 문자열과 하위 폴더 포함 접미사)"""
    key = os.path.normcase(os.path.abspath(path))
    return f"{key}{mod.LEGACY_RECURSIVE_SUFFIX}" if recursive else key


class AnalyzedDirsTestCase(unittest.TestCase):
    """임시 폴더를 data 폴더로 삼아 기록 파일을 읽고 씀"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = os.path.join(self._tmp.name, "data")
        os.makedirs(self.data_dir)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.nested = os.path.join(self.root, "sub")
        os.makedirs(self.nested)
        patcher = patch(_GET_DATA_DIR, return_value=self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def marker(self) -> str:
        return os.path.join(self.data_dir, ANALYZED_DIRS_FILENAME)

    def _write(self, payload: object) -> None:
        with open(self.marker, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _read(self) -> list[dict[str, object]]:
        with open(self.marker, encoding="utf-8") as f:
            return json.load(f)


class TestAnalyzedDirCovers(unittest.TestCase):
    """기록 하나의 포함 관계 판정"""

    def test_same_volume_path_is_covered_in_both_modes(self) -> None:
        location = VolumeLocation("vol:1", "work")
        self.assertTrue(AnalyzedDir("vol:1", "work", False).covers(location))
        self.assertTrue(AnalyzedDir("vol:1", "work", True).covers(location))

    def test_recursive_record_covers_any_depth_descendant(self) -> None:
        record = AnalyzedDir("vol:1", "work", True)
        self.assertTrue(record.covers(VolumeLocation("vol:1", "work/sub/dir")))

    def test_non_recursive_record_does_not_cover_descendant(self) -> None:
        record = AnalyzedDir("vol:1", "work", False)
        self.assertFalse(record.covers(VolumeLocation("vol:1", "work/sub")))

    def test_recursive_record_does_not_cover_sibling_name_prefix(self) -> None:
        record = AnalyzedDir("vol:1", "work", True)
        self.assertFalse(record.covers(VolumeLocation("vol:1", "workshop/sub")))

    def test_other_volume_is_not_covered(self) -> None:
        record = AnalyzedDir("vol:1", "", True)
        self.assertFalse(record.covers(VolumeLocation("vol:2", "work")))

    def test_recursive_volume_root_covers_every_path(self) -> None:
        record = AnalyzedDir("vol:1", "", True)
        self.assertTrue(record.covers(VolumeLocation("vol:1", "anything/here")))


class TestAnalyzedDirsStorage(AnalyzedDirsTestCase):
    """기록 파일의 저장 형식과 변환"""

    def test_mark_writes_volume_location_records(self) -> None:
        mark_analyzed_dir(self.root, recursive=True)
        stored = self._read()
        self.assertEqual(len(stored), 1)
        entry = stored[0]
        self.assertEqual(set(entry), {"volume_id", "volume_path", "recursive"})
        self.assertTrue(entry["recursive"])
        self.assertTrue(entry["volume_id"])
        self.assertTrue(is_analyzed_dir(self.root))

    def test_mark_does_not_repeat_the_same_record(self) -> None:
        mark_analyzed_dir(self.root)
        mark_analyzed_dir(self.root)
        self.assertEqual(len(self._read()), 1)

    def test_new_format_record_is_read(self) -> None:
        self._write([{"volume_id": "vol:9", "volume_path": "work", "recursive": False}])
        with patch(_VOLUME_LOCATION, return_value=VolumeLocation("vol:9", "work")):
            self.assertTrue(is_analyzed_dir("F:/work"))

    def test_malformed_record_is_ignored(self) -> None:
        self._write([{"volume_id": "vol:9"}, {"volume_path": "work"}, 7, None])
        with patch(_VOLUME_LOCATION, return_value=VolumeLocation("vol:9", "work")):
            self.assertFalse(is_analyzed_dir("F:/work"))

    def test_recursive_flag_accepts_only_a_real_bool(self) -> None:
        """저장된 하위 폴더 포함 값이 문자열 "false"나 1이면 참으로 읽지 않는지 검증"""
        self._write(
            [
                {"volume_id": "vol:9", "volume_path": "work", "recursive": "false"},
                {"volume_id": "vol:9", "volume_path": "work/sub", "recursive": 1},
            ]
        )
        with patch(_VOLUME_LOCATION, return_value=VolumeLocation("vol:9", "work/deep")):
            # - 문자열 "false" 기록이 참으로 읽히면 하위 폴더를 덮게 되므로 덮지 않아야 함
            self.assertFalse(is_analyzed_dir("F:/work/deep"))

    def test_non_list_payload_reads_as_no_record(self) -> None:
        self._write({"volume_id": "vol:9"})
        self.assertFalse(is_analyzed_dir(self.root))

    def test_corrupted_file_reads_as_no_record(self) -> None:
        with open(self.marker, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertLogs("src.service.analyzed_dirs", level="WARNING"):
            self.assertFalse(is_analyzed_dir(self.root))

    def test_save_failure_is_logged_and_does_not_raise(self) -> None:
        with (
            patch.object(mod, "write_utf8_file", side_effect=OSError("disk")),
            self.assertLogs("src.service.analyzed_dirs", level="WARNING"),
        ):
            mark_analyzed_dir(self.root)

    def test_empty_root_spec_is_not_analyzed(self) -> None:
        mark_analyzed_dir(self.root, recursive=True)
        self.assertFalse(is_analyzed_dir(""))


class TestAnalyzedDirsScope(AnalyzedDirsTestCase):
    """하위 폴더 포함 여부와 다중 작업 폴더 판정"""

    def test_non_recursive_record_does_not_mark_descendant(self) -> None:
        mark_analyzed_dir(self.root)
        self.assertTrue(is_analyzed_dir(self.root))
        self.assertFalse(is_analyzed_dir(self.nested))

    def test_recursive_record_marks_descendant(self) -> None:
        mark_analyzed_dir(self.root, recursive=True)
        self.assertTrue(is_analyzed_dir(self.nested))

    def test_recursive_view_requires_a_recursive_record(self) -> None:
        """하위 폴더 포함 화면은 하위 폴더 포함 기록이 있어야만 분석된 폴더로 판정함을 검증"""
        mark_analyzed_dir(self.root)
        self.assertTrue(is_analyzed_dir(self.root))
        self.assertFalse(is_analyzed_dir(self.root, recursive=True))
        mark_analyzed_dir(self.root, recursive=True)
        self.assertTrue(is_analyzed_dir(self.root, recursive=True))

    def test_every_root_must_be_recorded(self) -> None:
        other = os.path.join(self._tmp.name, "other")
        os.makedirs(other)
        mark_analyzed_dir(self.root)
        self.assertFalse(is_analyzed_dir(f"{self.root};{other}"))
        mark_analyzed_dir(other)
        self.assertTrue(is_analyzed_dir(f"{self.root};{other}"))

    def test_legacy_string_key_is_honored(self) -> None:
        self._write([_legacy_key(self.root)])
        self.assertTrue(is_analyzed_dir(self.root))
        self.assertFalse(is_analyzed_dir(self.nested))

    def test_legacy_recursive_key_covers_descendant(self) -> None:
        self._write([_legacy_key(self.root, recursive=True)])
        self.assertTrue(is_analyzed_dir(self.nested))

    def test_legacy_keys_are_rewritten_in_the_new_format(self) -> None:
        self._write([_legacy_key(self.root, recursive=True)])
        mark_analyzed_dir(self.nested)
        stored = self._read()
        self.assertTrue(all(set(entry) == {"volume_id", "volume_path", "recursive"} for entry in stored))
        self.assertTrue(is_analyzed_dir(self.nested))

    def test_drive_letter_change_keeps_the_record(self) -> None:
        with patch(_VOLUME_LOCATION, return_value=VolumeLocation("vol:abc", "work")):
            mark_analyzed_dir("F:/work", recursive=True)
        with patch(_VOLUME_LOCATION, return_value=VolumeLocation("vol:abc", "work/sub")):
            self.assertTrue(is_analyzed_dir("F:/sub"))

    def test_legacy_key_of_an_unmounted_folder_is_kept_as_a_string(self) -> None:
        """볼륨을 확인할 수 없는 옛 키는 문자열 그대로 남겨 이후 연결됐을 때 바꾸는지 검증"""
        legacy = _legacy_key(os.path.join(self._tmp.name, "unmounted", "videos"))
        self._write([legacy])

        def _location(path: str) -> VolumeLocation:
            # - 연결되지 않은 드라이브의 경로는 경로 기반 식별자로 대체됨
            if "unmounted" in path:
                return VolumeLocation("path:z:\\", "unmounted/videos")
            return real_volume_location(path)

        with patch(_VOLUME_LOCATION, side_effect=_location):
            mark_analyzed_dir(self.nested)
        stored = self._read()
        self.assertIn(legacy, stored)
        self.assertTrue(is_analyzed_dir(self.nested))

    def test_legacy_key_is_converted_once_the_volume_resolves(self) -> None:
        """연결된 드라이브의 옛 키는 저장할 때 볼륨 위치 기록으로 바뀌는지 검증"""
        legacy = _legacy_key(self.root, recursive=True)
        self._write([legacy])
        mark_analyzed_dir(self.nested)
        stored = self._read()
        self.assertNotIn(legacy, stored)
        self.assertTrue(stored)
        self.assertTrue(is_analyzed_dir(self.nested))


if __name__ == "__main__":
    unittest.main()
