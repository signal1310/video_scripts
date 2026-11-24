"""재인코딩 임시 파일 정리의 실행: 작업 폴더 검사, 기록과 파일 상태 모으기, 휴지통으로 옮기기, 이어 하지 않음의 임시 파일 옮기기

- 휴지통 옮기기 함수는 호출자가 주입함(화면은 QFile.moveToTrash를 넘김). 테스트는 가짜 함수로 실행
- 목록을 만든 뒤 옮기기 직전에 크기와 수정 시각을 다시 비교해 바뀌었으면 건너뜀
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.file_manager import VIDEO_EXTENSIONS
from src.core.encode.encode_leftovers import (
    LeftoverFacts,
    LeftoverVerdict,
    classify_leftover,
    is_result_part_name,
    source_candidates,
)
from src.core.encode.encode_paths import PART_SUFFIX
from src.core.encode.encode_state import EncodeState
from src.service.encode import encode_reasons as reasons
from src.service.encode.encode_history import EncodeHistory, PartRecord

TrashFn = Callable[[str], bool]

SKIP_MISSING: str = "파일이 없습니다"
SKIP_CHANGED: str = "목록을 만든 뒤 바뀌었습니다"
SKIP_BLOCKED: str = "고를 수 없는 파일입니다"
SKIP_TRASH_FAILED: str = "휴지통으로 옮기지 못했습니다"
SKIP_UNCONFIRMED_DRIVE: str = "휴지통을 확인할 수 없는 드라이브라 남겼습니다"


@dataclass(frozen=True, slots=True)
class LeftoverItem:
    """정리 목록의 임시 파일 한 개: 경로, 작업 폴더, 목록을 만들 때의 크기와 수정 시각, 묶음 판정"""

    path: str
    root_dir: str
    size: int
    mtime_ns: int
    verdict: LeftoverVerdict


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """옮기기 한 건의 결과. 옮기지 않았으면 사유가 있음"""

    path: str
    moved: bool
    reason: str = ""


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _replace_result(record: PartRecord) -> bool:
    """교체 대기이거나 교체 포기로 실패한 결과 파일인지. 이런 파일은 실제 결과물이라 옮기지 않음"""
    if record.state is EncodeState.REPLACE_PENDING:
        return True
    return record.state is EncodeState.FAILED and record.reason == reasons.REPLACE_FORFEITED


def is_protected_part(record: PartRecord) -> bool:
    """정리에서 옮기면 안 되는 임시 파일인지: 교체 대기, 교체 포기 실패, 열린 배치에서 진행 중인 파일

    - 교체 대기와 교체 포기는 결과물이 원본 자리를 기다리는 상태라 지우면 작업이 유실됨
    - 진행 중 파일은 인코더가 쓰고 있어 지우면 실행 중인 작업이 실패함
    """
    if _replace_result(record):
        return True
    return record.state is EncodeState.RUNNING and record.batch_open


def scan_leftovers(
    history: EncodeHistory,
    roots: Sequence[str],
    output_dirname: str,
    now_ns: int,
) -> list[LeftoverItem]:
    """작업 폴더 아래 .part 파일을 모두 모아 묶음을 정함. 기록을 바꾸지 않음

    - 기록 경로는 구분자와 대소문자 차이를 무시하고 맞춤
    - 결과 파일 모양은 이름과 대응 원본 영상의 존재로 판단
    """
    records = {_key(path): record for path, record in history.part_records().items()}
    items: list[LeftoverItem] = []
    for root in roots:
        for directory, _dirs, names in os.walk(root):
            for name in names:
                if not name.lower().endswith(PART_SUFFIX):
                    continue
                path = os.path.join(directory, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                record = records.get(_key(path))
                own_shape = is_result_part_name(name) and any(
                    os.path.isfile(candidate)
                    for candidate in source_candidates(path, output_dirname, VIDEO_EXTENSIONS)
                )
                facts = LeftoverFacts(
                    recorded_state=None if record is None else record.state,
                    replace_result=record is not None and _replace_result(record),
                    batch_open=record is not None and record.batch_open,
                    own_result_shape=own_shape,
                    age_ns=max(0, now_ns - stat.st_mtime_ns),
                )
                items.append(LeftoverItem(path, root, stat.st_size, stat.st_mtime_ns, classify_leftover(facts)))
    return sorted(items, key=lambda item: item.path)


def trash_leftovers(selected: Sequence[LeftoverItem], trash: TrashFn) -> list[CleanupOutcome]:
    """고른 임시 파일을 휴지통으로 옮김. 고를 수 없거나, 없거나, 목록 뒤에 바뀐 파일은 건너뛰고 사유를 남김"""
    outcomes: list[CleanupOutcome] = []
    for item in selected:
        if not item.verdict.selectable:
            outcomes.append(CleanupOutcome(item.path, False, SKIP_BLOCKED))
            continue
        try:
            stat = os.stat(item.path)
        except OSError:
            outcomes.append(CleanupOutcome(item.path, False, SKIP_MISSING))
            continue
        if stat.st_size != item.size or stat.st_mtime_ns != item.mtime_ns:
            outcomes.append(CleanupOutcome(item.path, False, SKIP_CHANGED))
            continue
        outcomes.append(trash_path(item.path, trash))
    return outcomes


def trash_path(path: str, trash: TrashFn) -> CleanupOutcome:
    """경로 하나를 휴지통으로 옮김. 파일이 없으면 건너뜀"""
    if not os.path.isfile(path):
        return CleanupOutcome(path, False, SKIP_MISSING)
    moved = trash(path)
    return CleanupOutcome(path, moved, "" if moved else SKIP_TRASH_FAILED)


def discard_batch_parts(
    history: EncodeHistory,
    batch_id: int,
    trash: TrashFn,
    movable: Callable[[str], bool],
) -> list[CleanupOutcome]:
    """이어 하지 않음: 배치가 기록한 진행 중, 대기 항목의 임시 파일을 휴지통으로 옮기고 배치를 닫음

    - movable이 거짓인 파일(휴지통을 확인할 수 없는 드라이브)은 옮기지 않고 기록 경로를 남겨 정리 창에서 고르게 함
    - 옮긴 행은 임시 파일 경로를 지워 기록과 파일 상태를 맞춤. 원본과 교체 대기 결과는 건드리지 않음
    """
    outcomes: list[CleanupOutcome] = []
    for row in history.list_items(batch_id):
        if row.state not in (EncodeState.RUNNING, EncodeState.PENDING):
            continue
        if row.part_path is None or not os.path.exists(row.part_path):
            continue
        if not movable(row.part_path):
            outcomes.append(CleanupOutcome(row.part_path, False, SKIP_UNCONFIRMED_DRIVE))
            continue
        outcome = trash_path(row.part_path, trash)
        if outcome.moved:
            history.update_item(batch_id, row.source_path, {"part_path": None})
        outcomes.append(outcome)
    history.close_batch(batch_id)
    return outcomes
