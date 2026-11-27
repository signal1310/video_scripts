"""통합 데이터 정리 서비스의 항목 구성, 범위별 미리보기와 실행, 임시 파일 처리 검증"""

import os
import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.volume_identity import volume_location
from src.core.encode.encode_state import EncodeState
from src.core.encode_fingerprint import EncodeFingerprint
from src.core.models import ActiveFrame, VideoProps
from src.service import data_cleanup_service as cleanup_mod
from src.service.analysis_store import AnalysisStore, LocationRecord, StoredEntry
from src.service.data_cleanup_service import (
    ALL_CLEANUP_ITEM_KEYS,
    CATEGORY_CLEANUP_ITEM_KEYS,
    CLEANUP_CATEGORY_LABELS,
    CLEANUP_ITEM_LABELS,
    CLEANUP_PRESET_LABELS,
    CLEANUP_PRESETS,
    CLEANUP_SCOPE_ALL,
    CLEANUP_SCOPE_CURRENT,
    CLEANUP_SCOPES,
    PART_TRASH_FAILED_TEMPLATE,
    PART_UNMOVABLE_TEMPLATE,
    SCOPE_ROOTS_REQUIRED_TEXT,
    STORAGE_CLEANUP_ITEM_KEYS,
    UI_RESET_CLEANUP_ITEM_KEYS,
    CleanupItemKey,
    CleanupScope,
    DataCleanupService,
    MovableFn,
    cleanup_item_category,
    partition_cleanup_keys,
)
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_leftovers import TrashFn
from src.service.encode.encode_output_history import EncodeOutputHistory
from src.service.encode.encode_reasons import REPLACE_FORFEITED
from src.service.frame_analysis_record import FrameAnalysisRecord
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.session import normalize_root_key

_CATEGORY_SIZES: dict[str, int] = {"encode": 3, "analysis": 7, "layout": 6, "settings": 5}
_ROW_SIZE: int = 100
_ROW_MTIME: int = 1


def _key(value: str) -> CleanupItemKey:
    """테스트 입력 문자열을 항목 키 타입으로 좁힘"""
    return cast("CleanupItemKey", value)


def _scope(value: str) -> CleanupScope:
    """테스트 입력 문자열을 범위 타입으로 좁힘"""
    return cast("CleanupScope", value)


def _props(filename: str) -> VideoProps:
    """분석 캐시 한 행을 채우는 최소 VideoProps"""
    return VideoProps(
        filename=filename,
        width=1920,
        height=1080,
        rotate_type=0,
        fps=30.0,
        vid_kbps=4000,
        aud_kbps=128,
        vid_size_MB=300.0,
        duration=600.0,
        codec="hevc",
    )


def _stored(filename: str, size: int = _ROW_SIZE) -> StoredEntry:
    """분석 캐시에 넣을 행"""
    return StoredEntry(filename, size, _ROW_MTIME, "f" * 32, _props(filename))


def _frame_record(filename: str, size: int = _ROW_SIZE) -> FrameAnalysisRecord:
    """화질 분석 캐시에 넣을 행"""
    return FrameAnalysisRecord(
        filename=filename,
        filesize_bytes=size,
        mtime_ns=_ROW_MTIME,
        active_width=1080,
        active_height=1080,
        active_x=420,
        active_y=0,
        has_black_bars=True,
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier="1080p",
        valid_segments=9,
        total_segments=9,
        unmeasurable=False,
        is_vr_protected=False,
        measured_at="2026-01-01T00:00:00Z",
        analysis_version=1,
    )


class _CleanupCase(unittest.TestCase):
    """임시 폴더 두 곳과 공용 SQLite 파일 위에 정리 대상 기록을 준비"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = tmp.name
        self.root_a = os.path.join(self.base, "root_dir_a")
        self.root_b = os.path.join(self.base, "root_dir_b")
        self.trash_dir = os.path.join(self.base, "trash")
        for path in (self.root_a, self.root_b, os.path.join(self.root_a, "sub"), self.trash_dir):
            os.makedirs(path, exist_ok=True)

        self.db_path = os.path.join(self.base, "video_cache.sqlite3")
        self.analysis_store = AnalysisStore(self.db_path)
        self.addCleanup(self.analysis_store.close)
        self.frame_store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.frame_store.close)
        self.encode_history = EncodeHistory(self.db_path, sleep=lambda _s: None)
        self.addCleanup(self.encode_history.close)
        self.output_history = EncodeOutputHistory(self.db_path, sleep=lambda _s: None)
        self.addCleanup(self.output_history.close)

        self.state_file = os.path.join(self.base, "ui_state.jsonc")
        with open(self.state_file, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.trashed: list[str] = []

    def _trash(self, path: str) -> bool:
        """휴지통 옮기기를 실제 파일 이동으로 흉내 내 원본이 남는지 확인할 수 있게 함"""
        self.trashed.append(path)
        os.replace(path, os.path.join(self.trash_dir, os.path.basename(path)))
        return True

    def make_service(
        self,
        *,
        trash_fn: TrashFn | None = None,
        state_files: Mapping[CleanupItemKey, str] | None = None,
        db_path: str | None = None,
        movable_fn: MovableFn | None = None,
    ) -> DataCleanupService:
        """같은 정리 대상 기록을 보는 서비스 생성(저장소 close는 테스트 정리에서 맡음)"""
        path = self.db_path if db_path is None else db_path
        analysis = AnalysisStore(path)
        frame = FrameAnalysisStore(path)
        encode = EncodeHistory(path, sleep=lambda _s: None)
        output = EncodeOutputHistory(path, sleep=lambda _s: None)
        self.addCleanup(analysis.close)
        self.addCleanup(frame.close)
        self.addCleanup(encode.close)
        self.addCleanup(output.close)
        return DataCleanupService(
            analysis_store=analysis,
            frame_store=frame,
            encode_history=encode,
            output_history=output,
            state_files=state_files,
            trash_fn=self._trash if trash_fn is None else trash_fn,
            movable_fn=movable_fn,
        )

    def write_file(self, path: str, size: int) -> str:
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def seed_records(self) -> dict[str, str]:
        """root_dir_a(현재 범위)와 root_dir_b(다른 범위)에 항목별 기록을 하나씩 만듦"""
        paths = {
            "keep_a": self.write_file(os.path.join(self.root_a, "keep.mp4"), 10),
            "keep_b": self.write_file(os.path.join(self.root_b, "keep.mp4"), 10),
            "probe_a": self.write_file(os.path.join(self.root_a, "a.mp4"), 5),
            "part_a": self.write_file(os.path.join(self.root_a, "a.mp4.part"), 20),
            "part_b": self.write_file(os.path.join(self.root_b, "b.mp4.part"), 30),
            "orphan_a": self.write_file(os.path.join(self.root_a, "orphan.mp4.part"), 40),
            "orphan_b": self.write_file(os.path.join(self.root_b, "orphan.mp4.part"), 50),
            "crop_a": self.write_file(os.path.join(self.root_a, "crop.mp4"), 5),
            "crop_b": self.write_file(os.path.join(self.root_b, "crop.mp4"), 5),
            "vr_a": self.write_file(os.path.join(self.root_a, "vr.mp4"), 5),
            "vr_b": self.write_file(os.path.join(self.root_b, "vr.mp4"), 5),
            "ack_a": self.write_file(os.path.join(self.root_a, "ack.mp4"), 5),
            "ack_b": self.write_file(os.path.join(self.root_b, "ack.mp4"), 5),
            "output_a": self.write_file(os.path.join(self.root_a, "out.mp4"), 5),
            "output_b": self.write_file(os.path.join(self.root_b, "out.mp4"), 5),
        }
        self.analysis_store.apply(
            (),
            [_stored("a.mp4"), _stored("sub/a.mp4", size=101), _stored("root_dir_b/b.mp4", size=200)],
        )
        self.analysis_store.write_manual_targets(
            {
                normalize_root_key(self.root_a): {"a.mp4": "720p"},
                normalize_root_key(self.root_b): {"b.mp4": "1080p"},
            }
        )
        self.frame_store.set_crop_exclusion(paths["crop_a"], True)
        self.frame_store.set_crop_exclusion(paths["crop_b"], True)
        self.frame_store.set_manual_active_frame(
            paths["crop_a"], ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        self.frame_store.set_manual_active_frame(
            paths["crop_b"], ActiveFrame(1600, 1000, 260, 40), frame_width=1920, frame_height=1080
        )
        self.frame_store.set_vr_override(source_path=paths["vr_a"], filename="vr.mp4", manual_vr=True)
        self.frame_store.set_vr_override(
            source_path=paths["vr_b"], filename="root_dir_b/vr.mp4", manual_vr=True
        )
        # - 여백 확인 필요 표시를 확인한 기록도 분석 캐시를 지울 때 함께 지우는 대상
        acknowledged = ActiveRegion(1600, 1000, 260, 40, True, dark_outliers_ignored=True)
        self.frame_store.set_crop_review_acknowledged(paths["ack_a"], acknowledged)
        self.frame_store.set_crop_review_acknowledged(paths["ack_b"], acknowledged)
        self.frame_store.save(_frame_record("a.mp4"))
        self.frame_store.save(_frame_record("root_dir_b/b.mp4", size=200))
        for name, path in (("output_a", paths["output_a"]), ("output_b", paths["output_b"])):
            self.output_history.record_output(
                fingerprint=EncodeFingerprint(size=len(name) + 10, digest=name[-1] * 32),
                output_path=path,
                encoder="hevc_nvenc",
                quality=22.0,
            )
        self._add_batch(self.root_a, "a.mp4", EncodeState.DISCARDED, paths["part_a"])
        self._add_batch(self.root_b, "b.mp4", EncodeState.DISCARDED, paths["part_b"])
        self._add_batch(self.root_a, "done_a.mp4", EncodeState.DONE, None)
        self._add_batch(self.root_b, "done_b.mp4", EncodeState.DONE, None)
        return paths

    def _add_batch(self, root_dir: str, name: str, state: EncodeState, part_path: str | None) -> int:
        batch_id = self.encode_history.create_batch(
            root_dir,
            "{}",
            [NewItem(source_path=name, source_size=_ROW_SIZE, source_mtime_ns=_ROW_MTIME, plan_json="{}")],
        )
        self.encode_history.update_item(batch_id, name, {"state": state})
        if part_path is not None:
            self.encode_history.update_item(batch_id, name, {"part_path": part_path})
        return batch_id

    def remaining_probe_filenames(self) -> set[str]:
        """분석 캐시에 남은 파일명 집합"""
        return {entry.filename for entry in self.analysis_store.fetch_by_sizes([_ROW_SIZE, 101, 200])}


class TestCleanupItemCatalog(unittest.TestCase):
    """4범주 21개 항목과 6개 프리셋 구성, 항목 분류와 나누기"""

    def test_catalog_covers_twenty_one_keys_in_four_categories(self) -> None:
        self.assertEqual(len(ALL_CLEANUP_ITEM_KEYS), 21)
        self.assertEqual(len(set(ALL_CLEANUP_ITEM_KEYS)), 21)
        self.assertEqual(
            {category: len(keys) for category, keys in CATEGORY_CLEANUP_ITEM_KEYS.items()}, _CATEGORY_SIZES
        )
        self.assertEqual(set(CLEANUP_CATEGORY_LABELS), set(_CATEGORY_SIZES))
        self.assertEqual(set(CLEANUP_ITEM_LABELS), set(ALL_CLEANUP_ITEM_KEYS))
        self.assertEqual(
            set(STORAGE_CLEANUP_ITEM_KEYS) | set(UI_RESET_CLEANUP_ITEM_KEYS), set(ALL_CLEANUP_ITEM_KEYS)
        )
        self.assertEqual(set(STORAGE_CLEANUP_ITEM_KEYS) & set(UI_RESET_CLEANUP_ITEM_KEYS), set())

    def test_presets_select_keys_of_their_categories(self) -> None:
        self.assertEqual(len(CLEANUP_PRESETS), 6)
        self.assertEqual(
            tuple(CLEANUP_PRESETS),
            ("part_only", "retry_encode", "frame_only", "reanalyze_fresh", "reset_layout", "reset_all"),
        )
        self.assertEqual(set(CLEANUP_PRESET_LABELS), set(CLEANUP_PRESETS))
        self.assertEqual(CLEANUP_PRESETS["part_only"], ("part_files",))
        self.assertEqual(set(CLEANUP_PRESETS["retry_encode"]), set(CATEGORY_CLEANUP_ITEM_KEYS["encode"]))
        self.assertEqual(CLEANUP_PRESETS["frame_only"], ("frame_cache",))
        self.assertEqual(set(CLEANUP_PRESETS["reanalyze_fresh"]), set(CATEGORY_CLEANUP_ITEM_KEYS["analysis"]))
        self.assertEqual(set(CLEANUP_PRESETS["reset_layout"]), set(CATEGORY_CLEANUP_ITEM_KEYS["layout"]))
        self.assertEqual(set(CLEANUP_PRESETS["reset_all"]), set(ALL_CLEANUP_ITEM_KEYS))
        self.assertEqual(len(CLEANUP_PRESETS["reset_all"]), 21)
        self.assertEqual(
            {preset: CLEANUP_PRESET_LABELS[preset] for preset in CLEANUP_PRESETS},
            {
                "part_only": "임시 파일만 정리",
                "retry_encode": "인코딩 다시 시도",
                "frame_only": "화질 분석만 다시",
                "reanalyze_fresh": "분석 처음부터 다시",
                "reset_layout": "화면 배치만 초기화",
                "reset_all": "전체 초기화",
            },
        )

    def test_every_key_reports_its_category(self) -> None:
        for category, keys in CATEGORY_CLEANUP_ITEM_KEYS.items():
            for key in keys:
                self.assertEqual(cleanup_item_category(key), category)

    def test_unknown_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            cleanup_item_category(_key("no_such_key"))

    def test_partition_keeps_order_and_drops_duplicates(self) -> None:
        storage, ui_reset = partition_cleanup_keys(
            ("probe_cache", "part_files", "column_order", "probe_cache", "window_geometry", "part_files")
        )
        self.assertEqual(storage, ("probe_cache", "part_files"))
        self.assertEqual(ui_reset, ("column_order", "window_geometry"))
        self.assertEqual(partition_cleanup_keys(()), ((), ()))
        self.assertEqual(partition_cleanup_keys(STORAGE_CLEANUP_ITEM_KEYS)[1], ())


class TestPreviewCounts(_CleanupCase):
    """미리보기가 범위별 건수와 용량을 지우지 않고 계산하는지 검증"""

    def test_current_scope_counts_only_its_own_records(self) -> None:
        self.seed_records()
        previews = self.make_service().preview(CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(set(previews), set(ALL_CLEANUP_ITEM_KEYS))
        expected = {
            "part_files": 2,
            "discarded_history": 1,
            "encode_output_history": 2,
            "probe_cache": 2,
            "manual_targets": 1,
            "frame_cache": 1,
            "crop_exclusions": 1,
            "crop_review_acks": 1,
            "manual_active_frames": 1,
            "vr_overrides": 1,
        }
        for key, count in expected.items():
            self.assertEqual(previews[_key(key)].count, count, key)
        self.assertEqual(previews["part_files"].size_bytes, 20 + 40)
        self.assertTrue(all(preview.available for preview in previews.values()))

    def test_all_scope_counts_every_record(self) -> None:
        self.seed_records()
        previews = self.make_service().preview(CLEANUP_SCOPE_ALL)
        expected = {
            "part_files": 2,
            "discarded_history": 2,
            "encode_output_history": 4,
            "probe_cache": 3,
            "manual_targets": 2,
            "frame_cache": 2,
            "crop_exclusions": 2,
            "crop_review_acks": 2,
            "manual_active_frames": 2,
            "vr_overrides": 2,
        }
        for key, count in expected.items():
            self.assertEqual(previews[_key(key)].count, count, key)
        self.assertEqual(previews["part_files"].size_bytes, 20 + 30)

    def test_location_rows_without_name_keys_are_counted_and_cleared_in_scope(self) -> None:
        """파일명 키가 없는 위치 행도 범위별 미리보기와 실행에서 함께 지우는지 검증"""
        paths = self.seed_records()
        for root, name in ((self.root_a, "a.mp4"), (self.root_b, "b.mp4")):
            location = volume_location(os.path.join(root, name))
            self.analysis_store.record_locations(
                [LocationRecord(location.volume_id, location.volume_path, _ROW_SIZE, 7, "9" * 32)]
            )

        previews = self.make_service().preview(CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(previews["probe_cache"].count, 3)
        self.assertTrue(previews["probe_cache"].available)
        result = self.make_service().execute_storage_cleanup(["probe_cache"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(result.cleared_counts["probe_cache"], 3)

        location_a = volume_location(os.path.join(self.root_a, "a.mp4"))
        location_b = volume_location(os.path.join(self.root_b, "b.mp4"))
        self.assertEqual(
            self.analysis_store.fetch_locations([(location_a.volume_id, location_a.volume_path)]), []
        )
        self.assertEqual(
            len(self.analysis_store.fetch_locations([(location_b.volume_id, location_b.volume_path)])), 1
        )
        self.assertTrue(os.path.isfile(paths["probe_a"]))

    def test_preview_deletes_nothing(self) -> None:
        paths = self.seed_records()
        self.make_service().preview(CLEANUP_SCOPE_ALL)
        self.assertEqual(self.trashed, [])
        for name in ("keep_a", "keep_b", "part_a", "part_b", "orphan_a", "orphan_b"):
            self.assertTrue(os.path.isfile(paths[name]), name)
        self.assertEqual(self.remaining_probe_filenames(), {"a.mp4", "sub/a.mp4", "root_dir_b/b.mp4"})

    def test_layout_and_settings_items_report_state_file_size(self) -> None:
        service = self.make_service(state_files={"column_order": self.state_file})
        previews = service.preview(CLEANUP_SCOPE_ALL)
        self.assertEqual(previews["column_order"].count, 1)
        self.assertGreater(previews["column_order"].size_bytes, 0)
        self.assertEqual(previews["column_widths"].count, 0)
        self.assertEqual(previews["column_widths"].size_bytes, 0)
        self.assertTrue(previews["column_widths"].available)
        self.assertEqual(set(UI_RESET_CLEANUP_ITEM_KEYS) & set(previews), set(UI_RESET_CLEANUP_ITEM_KEYS))

    def test_missing_databases_are_reported_as_unavailable_with_zero_count(self) -> None:
        missing = os.path.join(self.base, "absent", "video_cache.sqlite3")
        service = self.make_service(db_path=missing)
        previews = service.preview(CLEANUP_SCOPE_ALL)
        for key in STORAGE_CLEANUP_ITEM_KEYS:
            if key == "part_files":
                continue
            self.assertEqual(previews[key].count, 0, key)
            self.assertFalse(previews[key].available, key)
        self.assertFalse(os.path.exists(missing))


class TestScopeValidation(_CleanupCase):
    """범위 값과 현재 범위의 작업 폴더 요구 조건"""

    def test_unknown_scope_is_rejected(self) -> None:
        service = self.make_service()
        with self.assertRaises(ValueError):
            service.preview(_scope("everything"), self.root_a)

    def test_current_scope_requires_root_spec(self) -> None:
        service = self.make_service()
        with self.assertRaises(ValueError) as caught:
            service.preview(CLEANUP_SCOPE_CURRENT, "")
        self.assertEqual(str(caught.exception), SCOPE_ROOTS_REQUIRED_TEXT)
        self.assertEqual(CLEANUP_SCOPES, (CLEANUP_SCOPE_CURRENT, CLEANUP_SCOPE_ALL))


class TestExecuteStorageCleanup(_CleanupCase):
    """실행이 현재 범위만 지우고 다른 작업 폴더와 원본 영상은 그대로 두는지 검증"""

    def test_current_scope_clears_only_its_records_and_keeps_other_root(self) -> None:
        paths = self.seed_records()
        result = self.make_service().execute_storage_cleanup(
            STORAGE_CLEANUP_ITEM_KEYS, CLEANUP_SCOPE_CURRENT, self.root_a
        )

        self.assertEqual(result.errors, {})
        self.assertEqual(
            result.cleared_counts,
            {
                "part_files": 2,
                "discarded_history": 1,
                "encode_output_history": 2,
                "probe_cache": 2,
                "manual_targets": 1,
                "frame_cache": 1,
                "crop_exclusions": 1,
                "crop_review_acks": 1,
                "manual_active_frames": 1,
                "vr_overrides": 1,
            },
        )
        self.assertEqual(set(result.cleared_counts), set(STORAGE_CLEANUP_ITEM_KEYS))

        # - 현재 범위 기록만 사라지고 다른 작업 폴더 기록은 그대로 남음
        self.assertEqual(self.remaining_probe_filenames(), {"root_dir_b/b.mp4"})
        self.assertEqual(
            self.analysis_store.read_manual_targets({}), {normalize_root_key(self.root_b): {"b.mp4": "1080p"}}
        )
        self.assertEqual(self.output_history.count_outputs(), 1)
        self.assertEqual(self.encode_history.count_discarded(), 1)
        self.assertEqual(self.encode_history.count_completed_history(), 1)
        self.assertEqual(self.frame_store.count(), 1)
        self.assertEqual(self.frame_store.count_crop_exclusions(), 1)
        self.assertEqual(self.frame_store.count_crop_review_acks(), 1)
        self.assertEqual(self.frame_store.count_manual_active_frames(), 1)
        self.assertEqual(self.frame_store.count_vr_overrides(), 1)

        # - 범위 밖 파일과 원본 영상은 손대지 않음
        for name in ("keep_a", "keep_b", "part_b", "orphan_b"):
            self.assertTrue(os.path.isfile(paths[name]), name)
        for name in ("part_a", "orphan_a"):
            self.assertFalse(os.path.isfile(paths[name]), name)
        self.assertEqual(sorted(os.path.basename(path) for path in self.trashed), ["a.mp4.part", "orphan.mp4.part"])

    def test_all_scope_clears_both_roots_and_never_touches_mp4(self) -> None:
        paths = self.seed_records()
        result = self.make_service().execute_storage_cleanup(STORAGE_CLEANUP_ITEM_KEYS, CLEANUP_SCOPE_ALL)

        self.assertEqual(result.errors, {})
        self.assertEqual(result.cleared_counts["part_files"], 2)
        self.assertEqual(result.cleared_counts["discarded_history"], 2)
        self.assertEqual(result.cleared_counts["encode_output_history"], 4)
        self.assertEqual(result.cleared_counts["probe_cache"], 3)
        self.assertEqual(result.cleared_counts["manual_targets"], 2)
        self.assertEqual(result.cleared_counts["frame_cache"], 2)
        self.assertEqual(result.cleared_counts["crop_exclusions"], 2)
        self.assertEqual(result.cleared_counts["crop_review_acks"], 2)
        self.assertEqual(result.cleared_counts["manual_active_frames"], 2)
        self.assertEqual(result.cleared_counts["vr_overrides"], 2)

        self.assertEqual(self.remaining_probe_filenames(), set())
        self.assertEqual(self.analysis_store.read_manual_targets({}), {})
        self.assertEqual(self.output_history.count_outputs(), 0)
        self.assertEqual(self.encode_history.count_discarded(), 0)
        self.assertEqual(self.encode_history.count_completed_history(), 0)
        self.assertEqual(self.frame_store.count(), 0)
        self.assertEqual(self.frame_store.count_crop_exclusions(), 0)
        self.assertEqual(self.frame_store.count_crop_review_acks(), 0)
        self.assertEqual(self.frame_store.count_manual_active_frames(), 0)
        self.assertEqual(self.frame_store.count_vr_overrides(), 0)

        # - 원본 .mp4는 하나도 지우지 않고 휴지통에는 .part만 들어감
        for name in ("keep_a", "keep_b", "crop_a", "crop_b", "vr_a", "vr_b", "output_a", "output_b"):
            self.assertTrue(os.path.isfile(paths[name]), name)
        self.assertTrue(all(path.endswith(".part") for path in self.trashed))
        self.assertEqual(len(self.trashed), 2)

    def test_ui_reset_keys_are_skipped_and_not_reported_cleared(self) -> None:
        self.seed_records()
        keys: tuple[CleanupItemKey, ...] = ("part_files", "column_order", "criteria_settings", "probe_cache")
        result = self.make_service().execute_storage_cleanup(keys, CLEANUP_SCOPE_ALL)
        self.assertEqual(set(result.cleared_counts), {"part_files", "probe_cache"})
        self.assertEqual(result.errors, {})
        self.assertEqual(self.output_history.count_outputs(), 2)

    def test_trash_failure_is_reported_with_names_and_keeps_file(self) -> None:
        paths = self.seed_records()

        def refuse(_path: str) -> bool:
            return False

        result = self.make_service(trash_fn=refuse).execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_ALL)
        self.assertEqual(result.cleared_counts, {"part_files": 0})
        self.assertEqual(
            result.errors["part_files"],
            PART_TRASH_FAILED_TEMPLATE.format(count=2, names="a.mp4.part, b.mp4.part"),
        )
        self.assertTrue(os.path.isfile(paths["part_a"]))
        self.assertTrue(os.path.isfile(paths["part_b"]))

    def test_trash_exception_is_counted_as_failure(self) -> None:
        paths = self.seed_records()

        def explode(_path: str) -> bool:
            raise OSError("휴지통을 쓸 수 없음")

        with self.assertLogs(cleanup_mod.logger, level="WARNING"):
            result = self.make_service(trash_fn=explode).execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_ALL)
        self.assertEqual(result.cleared_counts, {"part_files": 0})
        self.assertIn("part_files", result.errors)
        self.assertTrue(os.path.isfile(paths["part_a"]))

    def test_one_failing_item_keeps_others_going(self) -> None:
        self.seed_records()
        service = self.make_service()
        with (
            patch.object(
                AnalysisStore,
                "clear_for_roots",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            self.assertLogs(cleanup_mod.logger, level="WARNING"),
        ):
            result = service.execute_storage_cleanup(["probe_cache", "frame_cache"], CLEANUP_SCOPE_ALL)
        self.assertIn("probe_cache", result.errors)
        self.assertEqual(result.cleared_counts, {"frame_cache": 2})
        self.assertEqual(self.frame_store.count(), 0)

    def test_completed_history_clear_keeps_batches_with_other_items(self) -> None:
        """완료 항목만 지우고 폐기 항목이 남은 배치는 기록째로 지우지 않음"""
        self.seed_records()
        self.make_service().execute_storage_cleanup(["encode_output_history"], CLEANUP_SCOPE_ALL)
        self.assertEqual(self.encode_history.count_completed_history(), 0)
        self.assertEqual(self.encode_history.count_discarded(), 2)
        self.assertEqual(len(self.encode_history.list_open_batches()), 2)
        with sqlite3.connect(self.db_path) as conn:
            batches = conn.execute("SELECT COUNT(*) FROM encode_batch").fetchone()
        self.assertEqual(batches, (2,))


class TestMovablePartFiles(_CleanupCase):
    """휴지통이 없는 드라이브의 임시 파일은 건드리지 않고 남기는지"""

    def test_unmovable_part_is_skipped_and_reported(self) -> None:
        """옮길 수 없는 경로의 .part는 실행에서 남기고 남긴 수를 결과 사유로 안내하는지 검증"""
        paths = self.seed_records()

        def unmovable_part(path: str) -> bool:
            return os.path.basename(path) != "a.mp4.part"

        service = self.make_service(movable_fn=unmovable_part)
        preview = service.preview(CLEANUP_SCOPE_ALL)
        # - 미리보기 건수는 지금처럼 대상 임시 파일 수를 그대로 셈
        self.assertEqual(preview["part_files"].count, 2)

        result = service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_ALL)
        self.assertEqual(result.cleared_counts["part_files"], 1)
        self.assertEqual(result.errors["part_files"], PART_UNMOVABLE_TEMPLATE.format(count=1))
        self.assertTrue(os.path.isfile(paths["part_a"]))
        self.assertFalse(os.path.isfile(paths["part_b"]))
        self.assertNotIn("a.mp4.part", [os.path.basename(path) for path in self.trashed])

    def test_unmovable_parts_are_reported_with_the_current_scope(self) -> None:
        """현재 작업 폴더 범위에서 기록 밖 임시 파일까지 옮길 수 없는 것으로 판정하면 남기는지 검증"""
        paths = self.seed_records()

        def all_unmovable(_path: str) -> bool:
            return False

        service = self.make_service(movable_fn=all_unmovable)
        result = service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(result.cleared_counts["part_files"], 0)
        self.assertEqual(result.errors["part_files"], PART_UNMOVABLE_TEMPLATE.format(count=2))
        self.assertTrue(os.path.isfile(paths["part_a"]))
        self.assertTrue(os.path.isfile(paths["orphan_a"]))
        self.assertEqual(self.trashed, [])

    def test_movable_fn_receives_only_part_paths(self) -> None:
        """옮길 수 있는지 판정에 `.part` 대상 경로만 넘기는지 검증"""
        self.seed_records()
        seen: list[str] = []

        def record(path: str) -> bool:
            seen.append(os.path.basename(path))
            return True

        service = self.make_service(movable_fn=record)
        service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertTrue(seen)
        self.assertTrue(all(name.endswith(".part") for name in seen))

    def test_default_movable_fn_trashes_every_part(self) -> None:
        """판정 함수를 주지 않으면 범위의 임시 파일을 모두 옮기는지 검증"""
        self.seed_records()
        result = self.make_service().execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_ALL)
        self.assertEqual(result.cleared_counts["part_files"], 2)
        self.assertEqual(result.errors, {})


class TestProtectedPartFiles(_CleanupCase):
    """교체 대기, 교체 포기, 열린 배치에서 진행 중인 임시 파일은 미리보기와 실행에서 제외"""

    def test_protected_parts_are_neither_counted_nor_trashed(self) -> None:
        replace_pending = self.write_file(os.path.join(self.root_a, "replace.mp4.part"), 11)
        forfeited = self.write_file(os.path.join(self.root_a, "forfeited.mp4.part"), 12)
        running = self.write_file(os.path.join(self.root_a, "running.mp4.part"), 13)
        discarded = self.write_file(os.path.join(self.root_a, "discarded.mp4.part"), 14)
        self.write_file(os.path.join(self.root_a, "orphan.mp4.part"), 15)

        self._add_batch(self.root_a, "replace.mp4", EncodeState.REPLACE_PENDING, replace_pending)
        batch = self._add_batch(self.root_a, "forfeited.mp4", EncodeState.FAILED, forfeited)
        self.encode_history.update_item(batch, "forfeited.mp4", {"reason": REPLACE_FORFEITED})
        self._add_batch(self.root_a, "running.mp4", EncodeState.RUNNING, running)
        self._add_batch(self.root_a, "discarded.mp4", EncodeState.DISCARDED, discarded)
        self.assertTrue(self.encode_history.list_open_batches())

        service = self.make_service()
        previews = service.preview(CLEANUP_SCOPE_CURRENT, self.root_a)
        # - 옮겨도 되는 기록 파일 하나와 기록 밖 파일 하나만 셈
        self.assertEqual(previews["part_files"].count, 2)
        self.assertEqual(previews["part_files"].size_bytes, 14 + 15)

        result = service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(result.cleared_counts["part_files"], 2)
        self.assertEqual(result.errors, {})
        self.assertEqual(
            sorted(os.path.basename(path) for path in self.trashed),
            ["discarded.mp4.part", "orphan.mp4.part"],
        )
        for name, path in (
            ("replace", replace_pending),
            ("forfeited", forfeited),
            ("running", running),
        ):
            self.assertTrue(os.path.isfile(path), name)

    def test_running_part_of_closed_batch_is_not_protected(self) -> None:
        """닫힌 배치의 진행 중 항목은 이미 끝난 작업이라 정리 대상"""
        path = self.write_file(os.path.join(self.root_a, "closed.mp4.part"), 16)
        batch = self._add_batch(self.root_a, "closed.mp4", EncodeState.RUNNING, path)
        self.encode_history.close_batch(batch)

        service = self.make_service()
        self.assertEqual(service.preview(CLEANUP_SCOPE_CURRENT, self.root_a)["part_files"].count, 1)
        result = service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(result.cleared_counts["part_files"], 1)
        self.assertFalse(os.path.isfile(path))

    def test_non_part_suffix_is_excluded_from_count_and_clear(self) -> None:
        """기록된 경로가 `.part`가 아니면 원본일 수 있으므로 미리보기와 실행 모두 건너뜀"""
        original = self.write_file(os.path.join(self.root_a, "original.mp4"), 21)
        self._add_batch(self.root_a, "original.mp4", EncodeState.DISCARDED, original)

        service = self.make_service()
        self.assertEqual(service.preview(CLEANUP_SCOPE_CURRENT, self.root_a)["part_files"].count, 0)
        result = service.execute_storage_cleanup(["part_files"], CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(result.cleared_counts["part_files"], 0)
        self.assertTrue(os.path.isfile(original))
        self.assertEqual(self.trashed, [])


class TestSeparateHistoryDatabases(_CleanupCase):
    """결과 지문 DB와 작업 기록 DB가 다른 파일이어도 각각 확인하고 없는 파일을 만들지 않음"""

    def _make_split_service(
        self, encode_history: EncodeHistory, output_history: EncodeOutputHistory
    ) -> DataCleanupService:
        """두 이력 저장소만 다른 파일로 갈아 끼운 서비스"""
        cache_path = os.path.join(self.base, "split_cache.sqlite3")
        analysis = AnalysisStore(cache_path)
        frame = FrameAnalysisStore(cache_path)
        self.addCleanup(analysis.close)
        self.addCleanup(frame.close)
        return DataCleanupService(
            analysis_store=analysis,
            frame_store=frame,
            encode_history=encode_history,
            output_history=output_history,
            trash_fn=self._trash,
        )

    def test_preview_counts_outputs_without_creating_missing_encode_db(self) -> None:
        encode_path = os.path.join(self.base, "encode_history.sqlite3")
        output_path = os.path.join(self.base, "output_history.sqlite3")
        encode = EncodeHistory(encode_path, sleep=lambda _s: None)
        output = EncodeOutputHistory(output_path, sleep=lambda _s: None)
        self.addCleanup(encode.close)
        self.addCleanup(output.close)
        output.record_output(
            fingerprint=EncodeFingerprint(size=10, digest="a" * 32),
            output_path=os.path.join(self.root_a, "out.mp4"),
            encoder="hevc_nvenc",
            quality=22.0,
        )

        preview = self._make_split_service(encode, output).preview(
            CLEANUP_SCOPE_CURRENT, self.root_a
        )["encode_output_history"]
        self.assertEqual(preview.count, 1)
        self.assertTrue(preview.available)
        self.assertFalse(os.path.exists(encode_path))

    def test_execute_clears_encode_history_when_output_db_is_missing(self) -> None:
        encode_path = os.path.join(self.base, "encode_history.sqlite3")
        output_path = os.path.join(self.base, "output_history.sqlite3")
        encode = EncodeHistory(encode_path, sleep=lambda _s: None)
        output = EncodeOutputHistory(output_path, sleep=lambda _s: None)
        self.addCleanup(encode.close)
        self.addCleanup(output.close)
        batch = encode.create_batch(
            self.root_a,
            "{}",
            [NewItem(source_path="done.mp4", source_size=_ROW_SIZE, source_mtime_ns=_ROW_MTIME, plan_json="{}")],
        )
        encode.update_item(batch, "done.mp4", {"state": EncodeState.DONE})

        service = self._make_split_service(encode, output)
        preview = service.preview(CLEANUP_SCOPE_CURRENT, self.root_a)["encode_output_history"]
        self.assertEqual(preview.count, 1)
        self.assertTrue(preview.available)

        result = service.execute_storage_cleanup(
            ["encode_output_history"], CLEANUP_SCOPE_CURRENT, self.root_a
        )
        self.assertEqual(result.cleared_counts["encode_output_history"], 1)
        self.assertEqual(result.errors, {})
        self.assertEqual(encode.count_completed_history(), 0)
        self.assertEqual(encode.list_items(batch), [])
        self.assertFalse(os.path.exists(output_path))


class TestBareFilenameScope(_CleanupCase):
    """경로 없는 캐시 파일명은 범위 폴더에 실제 파일이 있을 때만 그 폴더 소속"""

    def test_bare_filename_of_other_root_is_not_deleted(self) -> None:
        self.write_file(os.path.join(self.root_b, "only_b.mp4"), 10)
        self.analysis_store.apply((), [_stored("only_b.mp4")])
        self.frame_store.save(_frame_record("only_b.mp4"))

        service = self.make_service()
        previews = service.preview(CLEANUP_SCOPE_CURRENT, self.root_a)
        self.assertEqual(previews["probe_cache"].count, 0)
        self.assertEqual(previews["frame_cache"].count, 0)

        result = service.execute_storage_cleanup(
            ["probe_cache", "frame_cache"], CLEANUP_SCOPE_CURRENT, self.root_a
        )
        self.assertEqual(result.cleared_counts, {"probe_cache": 0, "frame_cache": 0})
        self.assertEqual(result.errors, {})
        self.assertEqual(self.remaining_probe_filenames(), {"only_b.mp4"})
        self.assertEqual(self.frame_store.count(), 1)


class TestDefaultTrashFn(unittest.TestCase):
    """기본 휴지통 옮기기가 send2trash 유무와 오류에 따라 성공 여부를 돌려주는지 검증"""

    def test_uses_send2trash_when_installed(self) -> None:
        calls: list[str] = []
        fake_module = SimpleNamespace(send2trash=calls.append)
        with patch.object(cleanup_mod.importlib, "import_module", return_value=fake_module):
            self.assertTrue(cleanup_mod._default_trash_fn("C:/work/a.mp4.part"))
        self.assertEqual(calls, ["C:/work/a.mp4.part"])

    def test_missing_send2trash_reports_failure(self) -> None:
        with (
            patch.object(cleanup_mod.importlib, "import_module", side_effect=ImportError("no send2trash")),
            self.assertLogs(cleanup_mod.logger, level="WARNING"),
        ):
            self.assertFalse(cleanup_mod._default_trash_fn("C:/work/a.mp4.part"))

    def test_trash_error_reports_failure(self) -> None:
        def explode(_path: str) -> None:
            raise OSError("휴지통을 쓸 수 없음")

        fake_module = SimpleNamespace(send2trash=explode)
        with (
            patch.object(cleanup_mod.importlib, "import_module", return_value=fake_module),
            self.assertLogs(cleanup_mod.logger, level="WARNING"),
        ):
            self.assertFalse(cleanup_mod._default_trash_fn("C:/work/a.mp4.part"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
