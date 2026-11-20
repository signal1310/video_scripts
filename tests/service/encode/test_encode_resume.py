"""이어서 재인코딩 준비 검증: 보관 뒤 중단 항목의 교체 대기 전환, 원본 변경 제외, 기록 밖 임시 파일 목록"""

import os
import tempfile
import unittest

from src.core.encode.encode_state import EncodeState
from src.service.encode.encode_history import EncodeHistory, ItemRow, NewItem
from src.service.encode.encode_reasons import ARCHIVE_INTERRUPTED, PART_LEFT, RESUME_SOURCE_CHANGED
from src.service.encode.encode_resume import (
    ResumeCount,
    count_resume,
    find_unrecorded_parts,
    leftover_part_path,
    prepare_resume,
)

SOURCE_SIZE: int = 4
SOURCE_MTIME_NS: int = 1_600_000_000_000_000_000


class ResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root: str = self._tmp.name
        self.history = EncodeHistory(os.path.join(self.root, "video_cache.sqlite3"), sleep=lambda _s: None)

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()

    def test_interrupted_after_archive_becomes_replace_pending(self) -> None:
        """원본이 보관 폴더로 옮겨진 뒤 끊긴 진행 중 항목은 결과를 지우지 않고 교체 대기로 둠"""
        source = os.path.join(self.root, "a.mp4")
        archive = os.path.join(self.root, "_orig", "a.mp4")
        part = os.path.join(self.root, "a.mp4.part")
        os.makedirs(os.path.dirname(archive))
        for path in (archive, part):
            with open(path, "wb") as handle:
                handle.write(b"data")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])
        self.history.update_item(
            batch_id,
            source,
            {"state": EncodeState.RUNNING, "part_path": part, "archive_path": archive},
        )

        prepare_resume(self.history, batch_id)

        row = self.history.list_items(batch_id)[0]
        self.assertIs(row.state, EncodeState.REPLACE_PENDING)
        self.assertEqual(row.reason, ARCHIVE_INTERRUPTED)
        self.assertTrue(os.path.exists(part))

    def test_changed_source_is_skipped_and_unrecorded_parts_are_listed(self) -> None:
        """원본 크기가 기록과 다르면 건너뜀으로 바꾸고, 기록 밖 임시 파일은 삭제하지 않고 목록으로만 돌려줌"""
        source = os.path.join(self.root, "b.mp4")
        with open(source, "wb") as handle:
            handle.write(b"changed-bytes")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])
        orphan = os.path.join(self.root, "orphan.mp4.part")
        with open(orphan, "wb") as handle:
            handle.write(b"o")

        report = prepare_resume(self.history, batch_id)

        self.assertEqual((report.resumable, report.excluded, report.removed_parts), (0, 1, 0))
        row = self.history.list_items(batch_id)[0]
        self.assertIs(row.state, EncodeState.SKIPPED)
        self.assertEqual(row.reason, RESUME_SOURCE_CHANGED)
        self.assertEqual(find_unrecorded_parts([self.root], recorded=set()), [orphan])
        self.assertTrue(os.path.exists(orphan))

    def test_count_resume_matches_prepare_and_does_not_change_rows(self) -> None:
        """대화상자용 개수는 기록을 바꾸지 않고, 같은 조건의 준비 결과와 같아야 함"""
        same = _make_source(self.root, "same.mp4", b"data", SOURCE_MTIME_NS)
        changed = _make_source(self.root, "changed.mp4", b"longer-data", SOURCE_MTIME_NS)
        batch_id = self.history.create_batch(
            self.root,
            "{}",
            [
                NewItem(same, SOURCE_SIZE, SOURCE_MTIME_NS, "{}"),
                NewItem(changed, SOURCE_SIZE, SOURCE_MTIME_NS, "{}"),
            ],
        )

        before = count_resume(self.history, batch_id)

        self.assertEqual((before.resumable, before.excluded), (1, 1))
        self.assertTrue(all(row.state is EncodeState.PENDING for row in self.history.list_items(batch_id)))
        report = prepare_resume(self.history, batch_id)
        self.assertEqual((report.resumable, report.excluded), (before.resumable, before.excluded))

    def test_count_resume_skips_items_interrupted_after_archive(self) -> None:
        """보관 뒤 끊긴 항목은 교체 대기로 바뀌므로 재개 개수에 넣지 않음"""
        source = os.path.join(self.root, "c.mp4")
        archive = os.path.join(self.root, "_orig", "c.mp4")
        part = os.path.join(self.root, "c.mp4.part")
        os.makedirs(os.path.dirname(archive))
        for path in (archive, part):
            with open(path, "wb") as handle:
                handle.write(b"data")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])
        self.history.update_item(batch_id, source, {"state": EncodeState.RUNNING, "part_path": part, "archive_path": archive})

        self.assertEqual(count_resume(self.history, batch_id), ResumeCount(resumable=0, excluded=0, replace_pending=1))

    def test_count_resume_counts_replace_pending_items_apart_from_resumable_ones(self) -> None:
        """교체 대기 항목은 재개 개수에 넣지 않고 교체 대기 개수로 따로 셈(교체만 남은 배치의 문장용)"""
        pending = os.path.join(self.root, "pending.mp4")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(pending, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])
        self.history.update_item(batch_id, pending, {"state": EncodeState.REPLACE_PENDING, "reason": "교체 실패"})

        self.assertEqual(count_resume(self.history, batch_id), ResumeCount(resumable=0, excluded=0, replace_pending=1))

    def test_leftover_part_is_the_recorded_part_or_the_planned_part_of_a_part_left_failure(self) -> None:
        """교체 대기와 실패 행이 가리키는 임시 파일: 기록된 임시 파일, 또는 임시 파일 때문에 실패한 행의 결과 경로에 붙은 임시 파일"""
        source = os.path.join(self.root, "d.mp4")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])

        def row_after(fields: dict[str, object]) -> ItemRow:
            self.history.update_item(batch_id, source, fields)
            return self.history.list_items(batch_id)[0]

        recorded = os.path.join(self.root, "d.mp4.part")
        self.assertEqual(leftover_part_path(row_after({"state": EncodeState.REPLACE_PENDING, "part_path": recorded})), recorded)
        self.assertEqual(leftover_part_path(row_after({"state": EncodeState.FAILED, "part_path": recorded})), recorded)
        planned = os.path.join(self.root, "_재인코딩결과", "d.mp4")
        self.assertEqual(
            leftover_part_path(row_after({"state": EncodeState.FAILED, "reason": PART_LEFT, "part_path": None, "output_path": planned})),
            planned + ".part",
        )
        self.assertIsNone(leftover_part_path(row_after({"state": EncodeState.DONE, "part_path": None, "output_path": planned})))

    def test_recorded_parts_include_closed_batches_and_unrecorded_list_ignores_same_path(self) -> None:
        """닫힌 배치의 임시 파일도 기록된 것으로 보고, 기록 경로는 구분자 차이와 무관하게 같은 파일로 봄"""
        source = os.path.join(self.root, "d.mp4")
        part = os.path.join(self.root, "d.mp4.part")
        with open(part, "wb") as handle:
            handle.write(b"p")
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, SOURCE_SIZE, SOURCE_MTIME_NS, "{}")])
        self.history.update_item(batch_id, source, {"part_path": part})
        self.history.close_batch(batch_id)

        recorded = self.history.recorded_part_paths()

        self.assertEqual(recorded, {part})
        self.assertEqual(find_unrecorded_parts([self.root], recorded=recorded), [])
        dotted = os.path.join(self.root, ".", "d.mp4.part")
        self.assertEqual(find_unrecorded_parts([self.root], recorded={dotted}), [])


def _make_source(root: str, name: str, data: bytes, mtime_ns: int) -> str:
    """기록과 같은 크기와 수정 시각을 가진 원본 파일을 만듦"""
    path = os.path.join(root, name)
    with open(path, "wb") as handle:
        handle.write(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


if __name__ == "__main__":
    unittest.main(verbosity=2)
