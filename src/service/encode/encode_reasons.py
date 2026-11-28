"""인코딩 항목 결과 사유 문구 모음(TERMINOLOGY.md 6.5절)

- 사유는 항목 상태와 함께 기록되고 결과 열에 그대로 표시되므로 문구는 이 모듈에서만 정의
- 세부 원인(예외 메시지, 태그 기록 실패 이유)은 사유 문구 대신 로그에 남김
"""

from src.core.encode.encode_gate import (
    TRANSFORM_SIZE_SURGE as SURGE_TEMPLATE,
)
from src.core.encode.encode_gate import TRANSFORM_SIZE_SURGE_PREFIX as SURGE_PREFIX
from src.core.encode.encode_gate import (
    GateVerdict,
    is_transform_surge_reason,
    transform_surge_percent,
)
from src.core.encode.encode_state import EncodeState

SOURCE_CHANGED: str = "원본 변경 또는 없음"
SOURCE_CONTENT_CHANGED: str = "원본 내용이 바뀌어 다시 분석해야 합니다"
NO_DISK_SPACE: str = "공간 부족"
NO_PRIMARY_VIDEO: str = "주 영상 스트림이 없음"
INTERLACED: str = "인터레이스 원본"
NAME_EXISTS: str = "같은 이름의 파일이 있음"
ARCHIVE_EXISTS: str = "보관 폴더에 같은 이름의 파일이 있음"
PART_LEFT: str = "임시 파일이 남아 있음(정리 필요)"
PATH_IN_USE: str = "같은 결과 경로를 다른 항목이 사용 중"
TAG_NOT_RECORDED: str = "인코딩 태그를 기록하지 못함(지원하지 않는 파일 구조)"
TAG_MISSING: str = "인코딩 태그 누락"
LARGER_THAN_SOURCE: str = "결과가 원본보다 큼"
INTEGRITY_FAILED: str = "결과 파일 손상 검사 실패"
RESUME_SOURCE_CHANGED: str = "원본이 바뀌어 재개 제외"
ARCHIVE_INTERRUPTED: str = "중단으로 교체 미완료"
EARLY_STOP_REASON: str = "진행 중 중단, 예상 크기가 원본보다 큼"
SAMPLE_PREDICT_REASON: str = "사전 예측, 예상 크기가 원본보다 큼"
REPLACE_FORFEITED: str = "교체를 포기했습니다. 원본과 결과 파일이 모두 남아 있습니다."
TRANSFORM_PRIORITY: str = "변환 우선"
TRANSFORM_SIZE_SURGE_PREFIX: str = SURGE_PREFIX
TRANSFORM_SIZE_SURGE: str = SURGE_TEMPLATE
# - 사용자가 그대로 교체를 고른 교체 대기 항목의 사유. 급증 표식을 앞에 유지해 자동 재시도가 다시 건드리지 않게 함
REPLACED_DESIGNATED: str = "사용자 확인 후 원본을 결과 파일로 교체했습니다"
# - 사용자가 폐기를 고른 교체 대기 항목의 사유
DISCARDED_BY_USER: str = "임시 결과 파일을 삭제하고 원본을 그대로 유지했습니다"


def discard_failed(error: Exception) -> str:
    """교체 대기 항목의 임시 결과를 지우지 못한 사유"""
    return f"임시 결과 파일을 삭제하지 못했습니다: {error}"


def _surge_percent(output_size_bytes: int, source_size_bytes: int) -> int:
    return transform_surge_percent(output_size_bytes, source_size_bytes)


def transform_priority_reason(output_size_bytes: int, source_size_bytes: int) -> str:
    """변환 우선 인코딩 결과가 원본보다 커졌을 때 사유(제안 2.3절 2단계)"""
    return f"{TRANSFORM_PRIORITY}, {TRANSFORM_SIZE_SURGE_PREFIX}{_surge_percent(output_size_bytes, source_size_bytes)}%"


def transform_surge_reason(output_size_bytes: int, source_size_bytes: int) -> str:
    """변환 우선 결과가 급증 상한을 넘었을 때 사유(제안 2.3절 3단계)"""
    return TRANSFORM_SIZE_SURGE.format(percent=_surge_percent(output_size_bytes, source_size_bytes))


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


def replaced_designated(previous_reason: str) -> str:
    """사용자가 그대로 교체를 고른 교체 대기 항목의 완료 사유. 급증 표식을 앞에 유지"""
    if is_transform_surge_reason(previous_reason):
        return f"{previous_reason} / {REPLACED_DESIGNATED}"
    return REPLACED_DESIGNATED


def replace_pending_after_retry(previous_reason: str, error: Exception) -> str:
    """교체 재시도가 실패해 사유를 갱신할 때 이전 사유의 급증 표식을 유지

    - 급증 보류는 사용자가 판단할 항목이므로, 재시도 실패로 표식이 사라지면 뒤따르는
      자동 교체 재시도가 원본을 바꿔 버림. 표식을 앞에 남겨 `is_transform_surge_reason`이 계속 참이 되게 함
    """
    pending = replace_pending(error)
    if is_transform_surge_reason(previous_reason):
        return f"{previous_reason} / {pending}"
    return pending


def gate_failure(verdict: GateVerdict) -> tuple[EncodeState, str]:
    """사후 게이트 불통과 판정의 상태와 사유. PASS는 호출하지 않음"""
    if verdict is GateVerdict.DISCARD_LARGER:
        return EncodeState.DISCARDED, LARGER_THAN_SOURCE
    if verdict is GateVerdict.FAIL_TAG:
        return EncodeState.FAILED, TAG_MISSING
    return EncodeState.FAILED, INTEGRITY_FAILED
