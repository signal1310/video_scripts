"""영상별 직접 지정 백업(내보내기, 불러오기)의 형식, 범위, 원자적 쓰기 검증"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.config import ClassifierConfig
from src.core.exceptions import DomainValueError
from src.core.models import ActiveFrame
from src.core.resolution_tiers import ENCODE_DESIGNATION_KEY
from src.core.workspace_paths import logical_name_for_path, normalize_root_key
from src.service.analysis_store import AnalysisStore
from src.service.designations_backup import (
    KIND_CROP_EXCLUSION,
    KIND_MANUAL_FRAME,
    KIND_MANUAL_TARGET,
    KIND_VR_OVERRIDE,
    ImportResult,
    export_designations,
    import_designations,
)
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.session import ClassifierSession


class _BackupTestCase(unittest.TestCase):
    """임시 작업 폴더와 저장소를 두는 기반 테스트 케이스"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = os.path.realpath(tmp.name)
        self.root = os.path.join(self.tmp_dir, "work")
        self.other_root = os.path.join(self.tmp_dir, "other")
        os.makedirs(self.root)
        os.makedirs(self.other_root)
        self.db_path = os.path.join(self.tmp_dir, "video_cache.sqlite3")
        self.analysis_store = AnalysisStore(self.db_path)
        self.addCleanup(self.analysis_store.close)
        self.frame_store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.frame_store.close)
        self.backup_path = os.path.join(self.tmp_dir, "designations.json")

    def _make_video(self, root: str, name: str, payload: bytes) -> str:
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path


def _frame() -> ActiveFrame:
    """테스트가 쓰는 활성 화면 값"""
    return ActiveFrame(width=640, height=360, x=100, y=80)


class TestExportDesignations(_BackupTestCase):
    """네 종류 지정의 내보내기 형식과 범위 검증"""

    def test_round_trip_exports_all_kinds_with_content_key(self) -> None:
        """네 종류 지정이 경로, 크기, 지문, 저장 값과 함께 JSON으로 나가는지 검증"""
        path = self._make_video(self.root, "clip.mp4", b"payload-1")
        self.analysis_store.write_manual_targets(
            {normalize_root_key(self.root): {"clip.mp4": ENCODE_DESIGNATION_KEY}}
        )
        self.frame_store.set_manual_active_frame(path, _frame(), frame_width=1920, frame_height=1080)
        self.frame_store.set_crop_exclusion(path, True)
        self.frame_store.set_vr_override(source_path=path, filename="clip.mp4", manual_vr=True)

        count = export_designations(self.analysis_store, self.frame_store, None, self.backup_path)

        self.assertEqual(count, 4)
        with open(self.backup_path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["format"], "video_scripts_designations")
        self.assertEqual(document["version"], 1)
        by_kind = {entry["kind"]: entry for entry in document["entries"]}
        self.assertEqual(by_kind[KIND_MANUAL_TARGET]["tier"], ENCODE_DESIGNATION_KEY)
        self.assertEqual(by_kind[KIND_MANUAL_FRAME]["manual_width"], _frame().width)
        self.assertIs(by_kind[KIND_CROP_EXCLUSION][KIND_CROP_EXCLUSION], True)
        self.assertIs(by_kind[KIND_VR_OVERRIDE][KIND_VR_OVERRIDE], True)
        for entry in document["entries"]:
            self.assertEqual(os.path.normcase(str(entry["path"])), os.path.normcase(path))
            self.assertEqual(entry["filesize_bytes"], len(b"payload-1"))
            self.assertIsInstance(entry["fingerprint"], str)

    def test_scope_filter_limits_exported_entries(self) -> None:
        """root_dirs에 드는 작업 폴더의 지정만 내보내는지 검증"""
        in_scope = self._make_video(self.root, "in.mp4", b"in")
        out_scope = self._make_video(self.other_root, "out.mp4", b"out")
        # - write_manual_targets는 맵 전체를 교체하므로 두 작업 폴더를 한 번에 저장해야 함
        self.analysis_store.write_manual_targets(
            {
                normalize_root_key(self.root): {"in.mp4": ENCODE_DESIGNATION_KEY},
                normalize_root_key(self.other_root): {"out.mp4": ENCODE_DESIGNATION_KEY},
            }
        )
        self.assertTrue(
            self.analysis_store.read_manual_target_rows(),
            "두 작업 폴더의 행이 저장되어야 합니다",
        )

        count = export_designations(
            self.analysis_store, self.frame_store, [os.path.normcase(self.root)], self.backup_path
        )

        with open(self.backup_path, encoding="utf-8") as handle:
            document = json.load(handle)
        exported = [os.path.normcase(str(entry["path"])) for entry in document["entries"]]
        self.assertEqual(count, 1, document)
        self.assertIn(os.path.normcase(in_scope), exported)
        self.assertNotIn(os.path.normcase(out_scope), exported)

    def test_scope_parent_folder_exports_manual_target(self) -> None:
        """범위가 작업 폴더의 상위 폴더여도 그 안의 직접 지정을 내보내는지 검증"""
        in_scope = self._make_video(self.root, "in.mp4", b"in")
        self.analysis_store.write_manual_targets(
            {normalize_root_key(self.root): {"in.mp4": ENCODE_DESIGNATION_KEY}}
        )
        self.frame_store.set_crop_exclusion(in_scope, True)

        count = export_designations(
            self.analysis_store, self.frame_store, [self.tmp_dir], self.backup_path
        )

        with open(self.backup_path, encoding="utf-8") as handle:
            document = json.load(handle)
        kinds = [entry["kind"] for entry in document["entries"]]
        self.assertEqual(count, 2, document)
        self.assertIn(KIND_MANUAL_TARGET, kinds)
        self.assertIn(KIND_CROP_EXCLUSION, kinds)

    def test_scope_omits_out_of_range_folder_of_multi_root_key(self) -> None:
        """여러 폴더를 한 키로 저장한 행도 범위 밖 폴더의 파일은 내보내지 않는지 검증"""
        in_scope = self._make_video(self.root, "in.mp4", b"in")
        out_scope = self._make_video(self.other_root, "out.mp4", b"out")
        spec = f"{self.root};{self.other_root}"
        key = normalize_root_key(spec)
        located_in = logical_name_for_path(in_scope, spec)
        located_out = logical_name_for_path(out_scope, spec)
        assert located_in is not None and located_out is not None
        self.analysis_store.write_manual_targets(
            {key: {located_in[1]: ENCODE_DESIGNATION_KEY, located_out[1]: ENCODE_DESIGNATION_KEY}}
        )

        export_designations(self.analysis_store, self.frame_store, [self.root], self.backup_path)

        with open(self.backup_path, encoding="utf-8") as handle:
            document = json.load(handle)
        exported = [os.path.normcase(str(entry["path"])) for entry in document["entries"]]
        self.assertEqual(exported, [os.path.normcase(in_scope)], document)

    def test_export_replaces_file_atomically_on_write_error(self) -> None:
        """쓰기 실패 때 기존 백업 파일을 남기고 임시 파일도 지우는지 검증"""
        self._make_video(self.root, "clip.mp4", b"payload")
        self.analysis_store.write_manual_targets(
            {normalize_root_key(self.root): {"clip.mp4": ENCODE_DESIGNATION_KEY}}
        )
        export_designations(self.analysis_store, self.frame_store, None, self.backup_path)
        with open(self.backup_path, encoding="utf-8") as handle:
            before = handle.read()

        with (
            patch("src.service.designations_backup.os.replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            export_designations(self.analysis_store, self.frame_store, None, self.backup_path)

        with open(self.backup_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)
        leftovers = [name for name in os.listdir(self.tmp_dir) if name.endswith(".part")]
        self.assertEqual(leftovers, [])


class TestImportDesignations(_BackupTestCase):
    """백업 불러오기의 짝짓기와 적용 검증"""

    def _session(self) -> ClassifierSession:
        """현재 작업 폴더를 self.root로 두는 세션"""
        return ClassifierSession(self.root, ClassifierConfig())

    def _prepare_backup(self) -> str:
        """네 종류 지정을 저장하고 백업을 만들어 영상 경로를 돌려줌"""
        path = self._make_video(self.root, "clip.mp4", b"payload-1")
        self.analysis_store.write_manual_targets(
            {normalize_root_key(self.root): {"clip.mp4": ENCODE_DESIGNATION_KEY}}
        )
        self.frame_store.set_manual_active_frame(path, _frame(), frame_width=1920, frame_height=1080)
        self.frame_store.set_crop_exclusion(path, True)
        self.frame_store.set_vr_override(source_path=path, filename="clip.mp4", manual_vr=True)
        export_designations(self.analysis_store, self.frame_store, None, self.backup_path)
        return path

    def _clear_all(self) -> None:
        """네 종류 지정을 모두 지움(캐시 분실 재현)"""
        self.analysis_store.clear_manual_targets()
        self.frame_store.clear_manual_active_frames()
        self.frame_store.clear_crop_exclusions()
        self.frame_store.clear_vr_overrides()

    def test_import_restores_all_kinds_after_cache_loss(self) -> None:
        """캐시를 비운 뒤 불러오면 네 종류 지정이 모두 돌아오는지 검증"""
        path = self._prepare_backup()
        self._clear_all()

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result.applied, 4)
        self.assertEqual(result.unmatched, 0)
        self.assertEqual(result.skipped_invalid, 0)
        targets = self.analysis_store.read_manual_targets({})[normalize_root_key(self.root)]
        self.assertEqual(targets["clip.mp4"], ENCODE_DESIGNATION_KEY)
        frames = self.frame_store.load_manual_active_frames({path: (1920, 1080)})
        self.assertEqual(frames[path].width, _frame().width)
        self.assertIn(path, self.frame_store.load_crop_exclusions([path]))
        self.assertIn(path, self.frame_store.load_vr_overrides([path]))

    def test_import_after_rename_matches_by_content_key(self) -> None:
        """이름이 바뀐 영상도 같은 내용이면 지정을 되찾는지 검증"""
        self._prepare_backup()
        renamed = os.path.join(self.root, "renamed.mp4")
        os.rename(os.path.join(self.root, "clip.mp4"), renamed)
        self._clear_all()

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result.applied, 4)
        self.assertEqual(result.unmatched, 0)
        targets = self.analysis_store.read_manual_targets({})[normalize_root_key(self.root)]
        self.assertEqual(targets["renamed.mp4"], ENCODE_DESIGNATION_KEY)
        self.assertIn(renamed, self.frame_store.load_vr_overrides([renamed]))

    def test_unmatched_and_malformed_entries_are_counted(self) -> None:
        """못 찾은 항목과 잘못된 항목이 세어지고 나머지는 계속 적용되는지 검증"""
        self._prepare_backup()
        with open(self.backup_path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["entries"].append(
            {
                "kind": KIND_MANUAL_TARGET,
                "path": os.path.join(self.root, "missing.mp4"),
                "filesize_bytes": 1,
                "fingerprint": "f",
                "tier": ENCODE_DESIGNATION_KEY,
            }
        )
        document["entries"].append({"kind": "unknown_kind", "path": "x.mp4"})
        with open(self.backup_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result.applied, 4)
        self.assertEqual(result.unmatched, 1)
        self.assertEqual(result.skipped_invalid, 1)

    def test_wrong_format_or_version_is_rejected(self) -> None:
        """형식이나 버전이 다른 백업은 도메인 오류로 거절되는지 검증"""
        self._prepare_backup()
        for document in (
            {"format": "other", "version": 1, "entries": []},
            {"format": "video_scripts_designations", "version": 99, "entries": []},
        ):
            with open(self.backup_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            with self.assertRaises(DomainValueError):
                import_designations(
                    self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
                )

    def test_unreadable_or_malformed_documents_are_rejected(self) -> None:
        """읽지 못한 파일, 잘못된 JSON, 항목 목록이 없는 문서가 도메인 오류로 거절되는지 검증"""
        self._prepare_backup()
        valid_header: dict[str, object] = {
            "format": "video_scripts_designations",
            "version": 1,
        }
        for document in (
            [1, 2],
            valid_header,
            {**valid_header, "entries": "not-a-list"},
        ):
            with open(self.backup_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            with self.assertRaises(DomainValueError):
                import_designations(
                    self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
                )

        with open(self.backup_path, "wb") as handle:
            handle.write(b"\xff\xfe not utf-8")
        with self.assertRaises(DomainValueError):
            import_designations(
                self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
            )

        with open(self.backup_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(DomainValueError):
            import_designations(
                self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
            )

        os.unlink(self.backup_path)
        with self.assertRaises(DomainValueError):
            import_designations(
                self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
            )

    def test_import_matches_by_path_and_size_without_fingerprint(self) -> None:
        """지문이 없는 항목도 같은 경로와 크기의 영상에 적용되는지 검증"""
        path = self._make_video(self.root, "clip.mp4", b"abc")
        document = {
            "format": "video_scripts_designations",
            "version": 1,
            "entries": [
                {
                    "kind": KIND_MANUAL_TARGET,
                    "path": path,
                    "filesize_bytes": 3,
                    "fingerprint": None,
                    "tier": ENCODE_DESIGNATION_KEY,
                }
            ],
        }
        with open(self.backup_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result, ImportResult(applied=1, unmatched=0, skipped_invalid=0))
        targets = self.analysis_store.read_manual_targets({})[normalize_root_key(self.root)]
        self.assertEqual(targets["clip.mp4"], ENCODE_DESIGNATION_KEY)

    def test_import_keeps_other_root_rows(self) -> None:
        """한 작업 폴더의 지정을 불러올 때 다른 작업 폴더의 저장 행이 남는지 검증"""
        work_video = self._make_video(self.root, "a.mp4", b"aaa")
        self._make_video(self.other_root, "b.mp4", b"bbb")
        self.analysis_store.write_manual_targets(
            {
                normalize_root_key(self.root): {"a.mp4": ENCODE_DESIGNATION_KEY},
                normalize_root_key(self.other_root): {"b.mp4": ENCODE_DESIGNATION_KEY},
            }
        )
        self.analysis_store.clear_manual_targets([self.root])
        document = {
            "format": "video_scripts_designations",
            "version": 1,
            "entries": [
                {
                    "kind": KIND_MANUAL_TARGET,
                    "path": work_video,
                    "filesize_bytes": os.stat(work_video).st_size,
                    "fingerprint": None,
                    "tier": ENCODE_DESIGNATION_KEY,
                }
            ],
        }
        with open(self.backup_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result, ImportResult(applied=1, unmatched=0, skipped_invalid=0))
        stored = self.analysis_store.read_manual_targets({})
        self.assertEqual(stored[normalize_root_key(self.root)]["a.mp4"], ENCODE_DESIGNATION_KEY)
        self.assertEqual(stored[normalize_root_key(self.other_root)]["b.mp4"], ENCODE_DESIGNATION_KEY)

    def test_non_boolean_switch_values_are_skipped(self) -> None:
        """잘라내기 제외와 수동 VR 보호 값이 참/거짓이 아니면 건너뛰는지 검증"""
        path = self._make_video(self.root, "clip.mp4", b"abc")
        size = os.stat(path).st_size
        document = {
            "format": "video_scripts_designations",
            "version": 1,
            "entries": [
                {
                    "kind": KIND_CROP_EXCLUSION,
                    "path": path,
                    "filesize_bytes": size,
                    "fingerprint": None,
                    KIND_CROP_EXCLUSION: "false",
                },
                {
                    "kind": KIND_VR_OVERRIDE,
                    "path": path,
                    "filesize_bytes": size,
                    "fingerprint": None,
                    KIND_VR_OVERRIDE: "false",
                },
            ],
        }
        with open(self.backup_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result, ImportResult(applied=0, unmatched=0, skipped_invalid=2))
        self.assertEqual(self.frame_store.load_crop_exclusions([path]), set())
        self.assertEqual(self.frame_store.load_vr_overrides([path]), set())

    def test_false_switch_values_clear_designations_and_count_as_applied(self) -> None:
        """거짓 값 항목은 지정을 지우고도 적용 건수에 드는지 검증"""
        path = self._make_video(self.root, "clip.mp4", b"abc")
        size = os.stat(path).st_size
        self.frame_store.set_crop_exclusion(path, True)
        self.frame_store.set_vr_override(source_path=path, filename="clip.mp4", manual_vr=True)
        document = {
            "format": "video_scripts_designations",
            "version": 1,
            "entries": [
                {
                    "kind": KIND_CROP_EXCLUSION,
                    "path": path,
                    "filesize_bytes": size,
                    "fingerprint": None,
                    KIND_CROP_EXCLUSION: False,
                },
                {
                    "kind": KIND_VR_OVERRIDE,
                    "path": path,
                    "filesize_bytes": size,
                    "fingerprint": None,
                    KIND_VR_OVERRIDE: False,
                },
            ],
        }
        with open(self.backup_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        result = import_designations(
            self._session(), self.analysis_store, self.frame_store, self.backup_path, [self.root]
        )

        self.assertEqual(result, ImportResult(applied=2, unmatched=0, skipped_invalid=0))
        self.assertEqual(self.frame_store.load_crop_exclusions([path]), set())
        self.assertEqual(self.frame_store.load_vr_overrides([path]), set())


if __name__ == "__main__":
    unittest.main()
