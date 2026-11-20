"""재인코딩 임시 파일 정리 서비스 검증: 묶음 판정 모으기, 옮기기 사유, 이어 하지 않음의 임시 파일 옮기기

- 임시 폴더와 기록 DB만 쓰고, 휴지통 함수는 기록용 대역으로 바꿈. 실제 휴지통에는 닿지 않음
"""

import os
import tempfile
import time
import unittest

from src.core.encode.encode_leftovers import BlockReason, LeftoverGroup
from src.core.encode.encode_state import EncodeState
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_leftovers import (
    SKIP_BLOCKED,
    SKIP_CHANGED,
    SKIP_MISSING,
    SKIP_TRASH_FAILED,
    LeftoverItem,
    discard_batch_parts,
    scan_leftovers,
    trash_leftovers,
)
from src.service.encode.encode_reasons import REPLACE_FORFEITED

OLD_MTIME_NS: int = 1_600_000_000 * 1_000_000_000
OUTPUT_DIR: str = "_재인코딩결과"


class _Recorder:
    """옮긴 경로를 기록하고 정해진 결과를 돌려주는 휴지통 대역"""

    def __init__(self, result: bool = True) -> None:
        self.calls: list[str] = []
        self._result: bool = result

    def __call__(self, path: str) -> bool:
        self.calls.append(path)
        return self._result


class _LeftoverCase(unittest.TestCase):
    """임시 작업 폴더와 기록 DB, 파일과 기록을 만드는 도우미"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(os.path.join(self.root, OUTPUT_DIR))
        self.history = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)

    def _write(self, name: str, *, data: bytes = b"part", old: bool = True) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        if old:
            os.utime(path, ns=(OLD_MTIME_NS, OLD_MTIME_NS))
        return path

    def _batch(self, source_name: str, state: EncodeState, part: str | None, reason: str = "", *, closed: bool = True) -> None:
        source = os.path.join(self.root, source_name)
        batch_id = self.history.create_batch(self.root, "{}", [NewItem(source, 4, 1, "{}")])
        fields: dict[str, object] = {"state": state, "reason": reason, "part_path": part}
        self.history.update_item(batch_id, source, fields)
        if closed:
            self.history.close_batch(batch_id)

    def _scan(self) -> dict[str, LeftoverItem]:
        items = scan_leftovers(self.history, [self.root], OUTPUT_DIR, time.time_ns())
        return {os.path.basename(item.path): item for item in items}


class ScanLeftoversTest(_LeftoverCase):
    """작업 폴더 검사: 묶음과 고를 수 없는 이유를 기록과 파일 모양으로 나눔"""

    def test_scan_sorts_every_part_into_its_group_and_block_reason(self) -> None:
        """기록, 결과 모양, 출처 불명, 교체 대기, 재개 대상, 최근 변경을 각각 맞는 묶음으로 나눔"""
        recorded = self._write("recorded.mp4.part")
        self._batch("recorded.mp4", EncodeState.FAILED, recorded, "실패")
        self._write("own.mp4", data=b"source")
        self._write(os.path.join(OUTPUT_DIR, "own.mp4.part"))
        self._write("stray.part")
        replace = self._write("replace.mp4.part")
        self._batch("replace.mp4", EncodeState.REPLACE_PENDING, replace, "교체 실패")
        resume = self._write("resume.mp4.part")
        self._batch("resume.mp4", EncodeState.RUNNING, resume, closed=False)
        self._write("fresh.part", old=False)
        found = self._scan()
        self.assertEqual(found["recorded.mp4.part"].verdict.group, LeftoverGroup.RECORDED)
        self.assertEqual(found["own.mp4.part"].verdict.group, LeftoverGroup.OWN_RESULT)
        self.assertEqual(found["stray.part"].verdict.group, LeftoverGroup.UNKNOWN)
        self.assertEqual(found["replace.mp4.part"].verdict.block, BlockReason.REPLACE)
        self.assertEqual(found["resume.mp4.part"].verdict.block, BlockReason.RESUME_TARGET)
        self.assertEqual(found["fresh.part"].verdict.block, BlockReason.RECENT)

    def test_forfeited_failure_is_blocked_while_other_failures_stay_selectable(self) -> None:
        """교체 포기 결과는 실제 결과물이라 막고, 같은 실패라도 교체 포기가 아니면 기록된 파일로 고를 수 있음"""
        forfeited = self._write("forfeited.mp4.part")
        self._batch("forfeited.mp4", EncodeState.FAILED, forfeited, REPLACE_FORFEITED)
        failed = self._write("failed.mp4.part")
        self._batch("failed.mp4", EncodeState.FAILED, failed, "다른 실패")
        found = self._scan()
        self.assertEqual(found["forfeited.mp4.part"].verdict.block, BlockReason.REPLACE)
        self.assertEqual(found["failed.mp4.part"].verdict.group, LeftoverGroup.RECORDED)

    def test_recorded_paths_match_across_separator_and_case_differences(self) -> None:
        """기록 경로와 탐색 경로가 대소문자만 다르면 같은 파일로 보고 기록 묶음에 넣음"""
        path = self._write("Case.mp4.part")
        self._batch("Case.mp4", EncodeState.FAILED, path.upper() if os.name == "nt" else path, "실패")
        found = self._scan()
        self.assertEqual(found["Case.mp4.part"].verdict.group, LeftoverGroup.RECORDED)


class TrashLeftoversTest(_LeftoverCase):
    """옮기기: 고를 수 있는 파일만 옮기고 나머지는 사유와 함께 건너뜀"""

    def test_selected_files_are_trashed_and_blocked_or_stale_ones_are_skipped_with_reasons(self) -> None:
        """고를 수 있는 파일만 휴지통으로 옮기고, 고를 수 없거나 없거나 바뀐 파일은 사유와 함께 건너뜀"""
        self._write("ok.part")
        replace = self._write("replace.mp4.part")
        self._batch("replace.mp4", EncodeState.REPLACE_PENDING, replace, "교체 실패")
        gone = self._write("gone.part")
        changed = self._write("changed.part")
        items = self._scan()
        os.remove(gone)
        with open(changed, "wb") as handle:
            handle.write(b"longer data")
        recorder = _Recorder()
        outcomes = trash_leftovers(
            [items["ok.part"], items["replace.mp4.part"], items["gone.part"], items["changed.part"]],
            recorder,
        )
        reasons = {os.path.basename(o.path): (o.moved, o.reason) for o in outcomes}
        self.assertEqual(reasons["ok.part"], (True, ""))
        self.assertEqual(reasons["replace.mp4.part"], (False, SKIP_BLOCKED))
        self.assertEqual(reasons["gone.part"], (False, SKIP_MISSING))
        self.assertEqual(reasons["changed.part"], (False, SKIP_CHANGED))
        self.assertEqual(recorder.calls, [os.path.join(self.root, "ok.part")])

    def test_failed_trash_is_reported_as_a_skip_not_raised(self) -> None:
        """휴지통 옮기기가 실패하면 예외 없이 옮기지 못했다는 사유를 남김"""
        self._write("stuck.part")
        outcomes = trash_leftovers([self._scan()["stuck.part"]], _Recorder(result=False))
        self.assertEqual((outcomes[0].moved, outcomes[0].reason), (False, SKIP_TRASH_FAILED))


class DiscardBatchPartsTest(_LeftoverCase):
    """이어 하지 않음: 배치가 기록한 진행 중 임시 파일만 옮기고 배치를 닫음"""

    def test_discard_moves_only_the_batch_work_in_progress_and_closes_the_batch(self) -> None:
        """이어 하지 않음은 배치가 기록한 진행 중 임시 파일만 옮기고, 교체 대기 결과와 완료 항목은 남기며 배치를 닫음"""
        pending = self._write("pending.mp4.part")
        replace = self._write("replace.mp4.part")
        source = os.path.join(self.root, "pending.mp4")
        replace_source = os.path.join(self.root, "replace.mp4")
        batch_id = self.history.create_batch(
            self.root,
            "{}",
            [NewItem(source, 4, 1, "{}"), NewItem(replace_source, 4, 1, "{}")],
        )
        self.history.update_item(batch_id, source, {"state": EncodeState.PENDING, "part_path": pending})
        self.history.update_item(batch_id, replace_source, {"state": EncodeState.REPLACE_PENDING, "part_path": replace})
        recorder = _Recorder()

        outcomes = discard_batch_parts(self.history, batch_id, recorder)

        self.assertEqual([o.path for o in outcomes], [pending])
        self.assertEqual(recorder.calls, [pending])
        self.assertTrue(os.path.exists(replace))
        rows = {os.path.basename(row.source_path): row for row in self.history.list_items(batch_id)}
        self.assertIsNone(rows["pending.mp4"].part_path)
        self.assertEqual(rows["replace.mp4"].part_path, replace)
        self.assertEqual(self.history.list_open_batches(), [])

    def test_part_records_report_whether_the_batch_is_still_open(self) -> None:
        """기록 조회는 임시 파일 경로마다 항목 상태와 배치 열림 여부를 돌려줌"""
        path = self._write("open.mp4.part")
        self._batch("open.mp4", EncodeState.RUNNING, path, closed=False)
        record = self.history.part_records()[path]
        self.assertEqual((record.state, record.batch_open), (EncodeState.RUNNING, True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
