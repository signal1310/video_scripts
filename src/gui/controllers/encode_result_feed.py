"""재인코딩 결과 목록에 넣을 값을 이력 행에서 읽어 진행 창에 넘기는 기록기

- 완료, 교체 대기, 교체 포기 항목은 결과 파일 크기를, 폐기 항목은 예상 크기와 측정 크기를 넘김
- 화면 스레드에서만 쓴다
"""

import os
from collections.abc import Callable

from src.core.analysis.frame_compare import resolve_pending_paths, resolve_result_paths
from src.core.encode.encode_progress_values import DiscardRow, ResultRow, expected_output_bytes
from src.core.encode.encode_state import EncodeState
from src.gui.dialogs.encode_progress_window import EncodeProgressWindow
from src.service.encode.encode_history import EncodeHistory, ItemRow
from src.service.encode.encode_job import job_from_json

RESULT_STATES: frozenset[EncodeState] = frozenset({EncodeState.DONE, EncodeState.REPLACE_PENDING, EncodeState.FAILED})
# - 폐기 항목은 결과 목록에 크기 문구를 채우려고 결과 줄로 함께 읽음
SHOWN_STATES: frozenset[EncodeState] = RESULT_STATES | frozenset({EncodeState.DISCARDED})


class ResultFeed:
    """이력 행의 결과 값을 진행 창의 결과 목록으로 넘기는 기록기"""

    def __init__(self, history: EncodeHistory, window: Callable[[], EncodeProgressWindow]) -> None:
        self._history: EncodeHistory = history
        self._window: Callable[[], EncodeProgressWindow] = window

    def record(self, batch_id: int, source_path: str) -> None:
        """결과 줄의 값을 이력 행에서 한 번 읽어 진행 창에 넘김. 교체 대기와 포기 항목은 결과 임시 파일 경로를 씀"""
        row = self._history.get_item(batch_id, source_path)
        if row is None:
            return
        if row.state is EncodeState.DISCARDED:
            self._record_discard(row)
            return
        output = row.output_path if row.state is EncodeState.DONE else row.part_path
        output_bytes = row.output_size
        if output is None or (output_bytes is None and not os.path.isfile(output)):
            return
        if output_bytes is None:
            output_bytes = os.path.getsize(output)
        job = job_from_json(row.plan_json)
        pair = (
            resolve_result_paths(row.source_path, row.output_path, row.archive_path)
            if row.state is EncodeState.DONE
            else resolve_pending_paths(row.source_path, row.part_path)
        )
        self._window().set_result(
            ResultRow(
                source_path=source_path,
                output_path=output,
                source_bytes=row.source_size,
                output_bytes=output_bytes,
                expected_delta_mb=job.expected_delta_mb,
                archive_path=row.archive_path,
                compare_ready=pair is not None,
            )
        )

    def _record_discard(self, row: ItemRow) -> None:
        """폐기 항목의 예상 크기(계획의 예상 절약)와 측정 크기(샘플 예측, 조기 중단 추정, 게이트 측정)를 진행 창에 넘김"""
        job = job_from_json(row.plan_json)
        estimate = row.early_stop_estimate_bytes
        sample = row.sample_predicted_bytes
        sample_predicted = estimate is None and sample is not None
        self._window().set_discard(
            DiscardRow(
                source_path=row.source_path,
                source_bytes=row.source_size,
                expected_bytes=expected_output_bytes(row.source_size, job.expected_delta_mb),
                measured_bytes=sample if sample_predicted else (estimate if estimate is not None else row.output_size),
                estimated=estimate is not None,
                sample_predicted=sample_predicted,
            )
        )
