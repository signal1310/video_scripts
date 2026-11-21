"""항목 실행 결과 값: 인코딩 한 번의 결과와 항목 한 건의 최종 상태

- 서비스 실행 흐름(encode_service)이 만들고 기록하며, 이 모듈은 값만 담음
"""

from dataclasses import dataclass, replace

from src.core.encode.encode_progress import EarlyStopHit
from src.core.encode.encode_state import EncodeState


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """항목 한 건의 최종 상태와 사유, 실패 시 FFmpeg 로그 끝부분, 조기 중단 기록"""

    state: EncodeState
    reason: str = ""
    stderr_tail: str = ""
    decode_retry: bool = False
    output_size: int | None = None
    early_stop: EarlyStopHit | None = None
    tuned_quality: float | None = None
    sample_predicted_bytes: int | None = None
    tune_retry_count: int = 0


@dataclass(frozen=True, slots=True)
class EncodeAttempt:
    """인코딩 실행 한 번의 결과. 실패 결과가 있으면 failure에 담고, 조기 중단이면 early_stop에 담음"""

    cancelled: bool = False
    decode_retry: bool = False
    stderr_tail: str = ""
    failure: ItemOutcome | None = None
    early_stop: EarlyStopHit | None = None
    tuned_quality: float | None = None
    sample_predicted_bytes: int | None = None
    tune_retry_count: int = 0
    tuning_reason: str = ""


def outcome_with_tuning(outcome: ItemOutcome, attempt: EncodeAttempt) -> ItemOutcome:
    """항목 최종 결과에 품질값 자동 조정과 재시도 기록을 병합"""
    return replace(
        outcome,
        tuned_quality=attempt.tuned_quality,
        sample_predicted_bytes=attempt.sample_predicted_bytes,
        tune_retry_count=attempt.tune_retry_count,
    )


def build_finish_fields(outcome: ItemOutcome, ended_at: str) -> dict[str, object]:
    """항목 완료 시 작업 기록에 저장할 열 사전을 만듦"""
    fields: dict[str, object] = {
        "state": outcome.state,
        "reason": outcome.reason,
        "stderr_tail": outcome.stderr_tail,
        "decode_retry": outcome.decode_retry,
    }
    if outcome.state is EncodeState.PENDING:
        fields["started_at"] = None
        fields["part_path"] = None
    else:
        fields["ended_at"] = ended_at
        if outcome.state is EncodeState.DONE:
            fields["part_path"] = None
    if outcome.output_size is not None:
        fields["output_size"] = outcome.output_size
    if outcome.early_stop is not None:
        fields["early_stop_progress"] = outcome.early_stop.progress
        fields["early_stop_estimate_bytes"] = outcome.early_stop.estimate_bytes
    if outcome.tuned_quality is not None:
        fields["tuned_quality"] = outcome.tuned_quality
    if outcome.sample_predicted_bytes is not None:
        fields["sample_predicted_bytes"] = outcome.sample_predicted_bytes
    if outcome.tune_retry_count > 0:
        fields["tune_retry_count"] = outcome.tune_retry_count
    return fields
