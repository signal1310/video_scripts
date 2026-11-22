"""재인코딩 항목 결과 사유 문구 모음(TERMINOLOGY.md 6.5절)

- 사유는 항목 상태와 함께 기록되고 결과 열에 그대로 표시되므로 문구는 이 모듈에서만 정의
- 세부 원인(예외 메시지, 태그 기록 실패 이유)은 사유 문구 대신 로그에 남김
"""

from src.core.encode.encode_gate import GateVerdict
from src.core.encode.encode_state import EncodeState

SOURCE_CHANGED: str = "원본 변경 또는 없음"
NO_DISK_SPACE: str = "공간 부족"
NO_PRIMARY_VIDEO: str = "주 영상 스트림이 없음"
INTERLACED: str = "인터레이스 원본"
NAME_EXISTS: str = "같은 이름의 파일이 있음"
ARCHIVE_EXISTS: str = "보관 폴더에 같은 이름의 파일이 있음"
PART_LEFT: str = "임시 파일이 남아 있음(정리 필요)"
PATH_IN_USE: str = "같은 결과 경로를 다른 항목이 사용 중"
TAG_NOT_RECORDED: str = "재인코딩 태그를 기록하지 못함(지원하지 않는 파일 구조)"
TAG_MISSING: str = "재인코딩 태그 누락"
LARGER_THAN_SOURCE: str = "결과가 원본보다 큼"
INTEGRITY_FAILED: str = "무결성 검사 실패"
RESUME_SOURCE_CHANGED: str = "원본이 바뀌어 재개 제외"
ARCHIVE_INTERRUPTED: str = "중단으로 교체 미완료"
EARLY_STOP_REASON: str = "진행 중 중단, 예상 크기가 원본보다 큼"
SAMPLE_PREDICT_REASON: str = "샘플 예측, 예상 크기가 원본보다 큼"
REPLACE_FORFEITED: str = "교체를 포기했습니다. 원본과 결과 파일이 모두 남아 있습니다."


def processing_error(error: Exception) -> str:
    """예상하지 못한 예외로 항목이 중단된 사유"""
    return f"처리 중 오류: {error}"


def tag_write_failed(error: Exception) -> str:
    """결과 태그 기록 중 예외가 난 사유"""
    return f"결과 태그 기록 실패: {error}"


def encode_failed(returncode: int | None) -> str:
    """FFmpeg 종료 코드가 0이 아닌 사유"""
    return f"인코딩 실패(종료 코드 {returncode})"


def encode_retry_failed(returncode: int | None) -> str:
    """하드웨어 디코딩 실패 뒤 CPU 디코딩 재시도도 실패한 사유"""
    return f"인코딩 실패(CPU 디코딩 재시도 후 종료 코드 {returncode})"


def replace_pending(error: Exception) -> str:
    """결과 배치에 실패해 교체 대기로 둘 때의 사유"""
    return f"교체 대기: {error}"


def gate_failure(verdict: GateVerdict) -> tuple[EncodeState, str]:
    """사후 게이트 불통과 판정의 상태와 사유. PASS는 호출하지 않음"""
    if verdict is GateVerdict.DISCARD_LARGER:
        return EncodeState.DISCARDED, LARGER_THAN_SOURCE
    if verdict is GateVerdict.FAIL_TAG:
        return EncodeState.FAILED, TAG_MISSING
    return EncodeState.FAILED, INTEGRITY_FAILED
