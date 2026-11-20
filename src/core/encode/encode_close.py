"""재인코딩 중 메인 창 닫기 요청을 어떻게 처리할지 정하는 순수 함수

- 실행 중이 아니면 바로 닫고, 실행 중이면 확인 대화상자를 연다
- 종료를 예약한 뒤에는 배치가 끝날 때까지 다시 묻지 않고 닫기를 기다리게 함
"""

from enum import Enum


class CloseAction(Enum):
    """닫기 요청에 대한 처리: 바로 닫기, 확인 대화상자 열기, 예약 뒤 기다리기"""

    CLOSE_NOW = "close_now"
    ASK = "ask"
    WAIT = "wait"


class CloseChoice(Enum):
    """확인 대화상자의 선택. 계속은 닫기를 무시함"""

    IMMEDIATE = "immediate"
    AFTER_CURRENT = "after_current"
    KEEP = "keep"


def close_action(is_running: bool, close_pending: bool) -> CloseAction:
    """닫기 요청의 처리 방법. 실행 중이 아니면 바로 닫고, 예약이 있으면 기다림"""
    if not is_running:
        return CloseAction.CLOSE_NOW
    if close_pending:
        return CloseAction.WAIT
    return CloseAction.ASK
