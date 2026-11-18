"""재인코딩 작업 항목의 상태 코드와 재개 대상 판별 모듈

- 저장 값은 영문 코드이며 화면 표기는 승인 용어(11.1절)를 GUI가 매핑
"""

from enum import Enum


class EncodeState(Enum):
    """재인코딩 항목 상태. 값은 작업 기록 테이블에 저장되는 코드"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    DISCARDED = "discarded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REPLACE_PENDING = "replace_pending"
    NOT_TARGET = "not_target"


BATCH_STATE_OPEN: str = "open"
BATCH_STATE_CLOSED: str = "closed"
UNFINISHED_STATES: frozenset[EncodeState] = frozenset({EncodeState.PENDING, EncodeState.RUNNING})
