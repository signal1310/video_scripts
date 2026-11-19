"""항목 실행 결과 값: 인코딩 한 번의 결과와 항목 한 건의 최종 상태

- 서비스 실행 흐름(encode_service)이 만들고 기록하며, 이 모듈은 값만 담음
"""

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class EncodeAttempt:
    """인코딩 실행 한 번의 결과. 실패 결과가 있으면 failure에 담고, 조기 중단이면 early_stop에 담음"""

    cancelled: bool = False
    decode_retry: bool = False
    stderr_tail: str = ""
    failure: ItemOutcome | None = None
    early_stop: EarlyStopHit | None = None
