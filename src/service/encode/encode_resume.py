"""이어서 재인코딩 준비: 진행 중 항목 되돌림, 원본 변경 제외, 기록된 임시 파일 정리, 중단 복구

- 기록에 없는 임시 파일은 삭제하지 않고 목록으로만 돌려줌(결정 16)
- 대화상자 개수는 count_resume으로 먼저 읽고, 사용자가 이어서 재인코딩을 고른 뒤에만 prepare_resume이 기록을 바꿈
"""

import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from src.core.encode.encode_paths import PART_SUFFIX, part_path_of
from src.core.encode.encode_state import EncodeState
from src.service.encode import encode_reasons as reasons
from src.service.encode.encode_history import EncodeHistory, ItemRow


@dataclass(frozen=True, slots=True)
class ResumeReport:
    """이어서 재인코딩 준비 결과 개수"""

    resumable: int
    excluded: int
    removed_parts: int


@dataclass(frozen=True, slots=True)
class ResumeCount:
    """이어서 재인코딩 대화상자에 보일 개수(기록을 바꾸지 않고 읽은 값)"""

    resumable: int
    excluded: int
    replace_pending: int = 0


def count_resume(history: EncodeHistory, batch_id: int) -> ResumeCount:
    """prepare_resume이 나눌 항목 수를 기록을 바꾸지 않고 셈

    - 보관 뒤 끊긴 항목과 이미 교체 대기인 항목은 재개 대상이 아니라 교체 대기 개수로 셈
    """
    resumable = 0
    excluded = 0
    replace_pending = 0
    for row in history.list_items(batch_id):
        if row.state is EncodeState.REPLACE_PENDING:
            replace_pending += 1
        elif row.state in (EncodeState.RUNNING, EncodeState.PENDING):
            if _interrupted_after_archive(row):
                replace_pending += 1
            elif _same_stat(row):
                resumable += 1
            else:
                excluded += 1
    return ResumeCount(resumable=resumable, excluded=excluded, replace_pending=replace_pending)


def leftover_part_path(row: ItemRow) -> str | None:
    """교체 대기 또는 실패 행이 가리키는 임시 결과 파일 경로. 해당하지 않으면 None

    - 기록된 임시 파일 경로가 있으면 그 경로를 씀
    - 임시 파일 때문에 실패한 행은 임시 파일 경로를 기록하지 않으므로 기록된 결과 경로에 임시 파일 접미사를 붙여 씀. 존재 여부는 확인하지 않음
    """
    if row.part_path is not None and row.state in (EncodeState.REPLACE_PENDING, EncodeState.FAILED):
        return row.part_path
    if row.state is EncodeState.FAILED and row.reason == reasons.PART_LEFT and row.output_path is not None:
        return part_path_of(row.output_path)
    return None


def prepare_resume(history: EncodeHistory, batch_id: int) -> ResumeReport:
    """이어서 재인코딩 전에 진행 중 항목을 대기로 되돌리고, 원본이 바뀐 항목과 기록된 임시 파일을 정리

    - 기록에 경로가 있는 .part만 지우고, 기록에 없는 .part는 건드리지 않음(결정 16)
    - 원본 크기나 수정 시각이 기록과 다르면 건너뜀으로 바꿔 재개 대상에서 뺌
    - 원본을 보관 폴더로 옮긴 뒤 결과 배치 전에 끊긴 항목은 결과를 지우지 않고 교체 대기로 둠
    """
    for row in history.list_items(batch_id):
        if row.state not in (EncodeState.RUNNING, EncodeState.PENDING) or row.part_path is None:
            continue
        if _interrupted_after_archive(row):
            history.update_item(
                batch_id,
                row.source_path,
                {"state": EncodeState.REPLACE_PENDING, "reason": reasons.ARCHIVE_INTERRUPTED},
            )
    history.reset_running(batch_id)
    resumable = 0
    excluded = 0
    removed = 0
    for row in history.list_items(batch_id):
        if row.state is not EncodeState.PENDING:
            continue
        stat_ok = _same_stat(row)
        if not stat_ok:
            fields = {"state": EncodeState.SKIPPED, "reason": reasons.RESUME_SOURCE_CHANGED}
            history.update_item(batch_id, row.source_path, fields)
            excluded += 1
            continue
        if row.part_path is not None and os.path.exists(row.part_path):
            os.remove(row.part_path)
            removed += 1
        history.update_item(batch_id, row.source_path, {"part_path": None, "started_at": None})
        resumable += 1
    return ResumeReport(resumable=resumable, excluded=excluded, removed_parts=removed)


def _interrupted_after_archive(row: ItemRow) -> bool:
    """원본은 보관 폴더로 옮겨졌고 결과 임시 파일만 남은 상태인지 여부"""
    return (
        row.archive_path is not None
        and row.part_path is not None
        and os.path.exists(row.part_path)
        and os.path.exists(row.archive_path)
        and not os.path.exists(row.source_path)
    )


def _same_stat(row: ItemRow) -> bool:
    try:
        stat = os.stat(row.source_path)
    except OSError:
        return False
    return stat.st_size == row.source_size and stat.st_mtime_ns == row.source_mtime_ns


def find_unrecorded_parts(roots: Iterable[str], recorded: Collection[str]) -> list[str]:
    """작업 폴더 아래 기록에 없는 .part 파일 경로 목록(안내용, 삭제하지 않음)

    - 기록 경로와 비교할 때 구분자와 대소문자 차이를 무시함
    """
    known = {_path_key(path) for path in recorded}
    found: list[str] = []
    for root in roots:
        for directory, _dirs, names in os.walk(root):
            for name in names:
                path = os.path.join(directory, name)
                if name.endswith(PART_SUFFIX) and _path_key(path) not in known:
                    found.append(path)
    return sorted(found)


def _path_key(path: str) -> str:
    """경로 비교용 키. 구분자와 대소문자 차이를 없앰"""
    return os.path.normcase(os.path.normpath(path))
