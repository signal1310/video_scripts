"""도메인 에러 코드, 표준 예외 호환 예외 클래스 및 경계 오류 포맷터 정의"""

from enum import Enum


class ErrorCode(str, Enum):
    """애플리케이션 전반에서 발생 가능한 도메인 오류 코드 정의"""

    ROOT_DIR_NOT_FOUND = "E_ROOT_DIR_NOT_FOUND"
    ROOT_DIR_INVALID = "E_ROOT_DIR_INVALID"
    ROOT_DIR_READ_ERROR = "E_ROOT_DIR_READ_ERROR"
    RULE_NESTING_EXCEEDED = "E_RULE_NESTING_EXCEEDED"
    RULE_SYNTAX_UNSUPPORTED = "E_RULE_SYNTAX_UNSUPPORTED"
    RULE_POWER_RANGE = "E_RULE_POWER_RANGE"
    RULE_POWER_TYPE = "E_RULE_POWER_TYPE"
    RULE_SEQ_REPEAT_LIMIT = "E_RULE_SEQ_REPEAT_LIMIT"
    RULE_ATTR_ACCESS_DENIED = "E_RULE_ATTR_ACCESS_DENIED"
    RULE_CALL_DENIED = "E_RULE_CALL_DENIED"
    RULE_INDEX_TYPE = "E_RULE_INDEX_TYPE"
    RULE_LAMBDA_FORMAT = "E_RULE_LAMBDA_FORMAT"
    PROBE_TIMEOUT = "E_PROBE_TIMEOUT"
    PROBE_FAILED = "E_PROBE_FAILED"
    PROBE_INVALID_OUTPUT = "E_PROBE_INVALID_OUTPUT"
    WORK_CANCELLED = "E_WORK_CANCELLED"
    FILE_MOVE_BATCH_FAILED = "E_FILE_MOVE_BATCH_FAILED"
    INVALID_SORT_KEY = "E_INVALID_SORT_KEY"
    UNKNOWN_TASK_TYPE = "E_UNKNOWN_TASK_TYPE"


DEFAULT_ERROR_HINTS: dict[ErrorCode, str] = {
    ErrorCode.ROOT_DIR_NOT_FOUND: "프로젝트 루트에 '.rootdir' 파일을 생성하거나 환경설정에서 기본 작업 폴더를 지정해 주세요.",
    ErrorCode.ROOT_DIR_INVALID: "상단 경로 입력란이나 [폴더 선택] 버튼에서 실제로 존재하는 폴더를 선택해 주세요.",
    ErrorCode.ROOT_DIR_READ_ERROR: "'.rootdir' 파일 인코딩(UTF-8)과 읽기 권한을 확인해 주세요.",
    ErrorCode.PROBE_TIMEOUT: "FFprobe 응답이 지연되고 있습니다. 파일이 네트워크 드라이브에 있거나 손상되지 않았는지 확인해 주세요.",
    ErrorCode.PROBE_FAILED: "FFprobe가 설치되어 있는지 확인하고, 손상되거나 지원되지 않는 영상 파일이 아닌지 점검해 주세요.",
    ErrorCode.PROBE_INVALID_OUTPUT: "FFprobe 버전 호환성이나 대상 영상 파일의 컨테이너 상태를 확인해 주세요.",
    ErrorCode.FILE_MOVE_BATCH_FAILED: "영상 파일이 탐색기나 미디어 플레이어 등 다른 프로그램에서 열려 있지 않은지 확인해 주세요.",
}


class VideoScriptsError(Exception):
    """에러 코드와 사용자 안내 힌트를 보관하는 공통 도메인 예외 기반 클래스"""

    code: ErrorCode
    hint: str | None

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.hint = hint
        super().__init__(message)


class DomainFileNotFoundError(VideoScriptsError, FileNotFoundError):
    """파일 또는 폴더 미존재 시 발생하는 도메인 예외"""


class DomainValueError(VideoScriptsError, ValueError):
    """유효하지 않은 입력 값이나 조건식 구문 오류 도메인 예외"""


class DomainRuntimeError(VideoScriptsError, RuntimeError):
    """외부 프로세스 실행 또는 파일 배치 처리 실패 도메인 예외"""


class DomainTimeoutError(VideoScriptsError, TimeoutError):
    """FFprobe 등 외부 작업 제한 시간 초과 도메인 예외"""


class DomainInterruptedError(VideoScriptsError, InterruptedError):
    """사용자 요청이나 중단 신호에 의한 작업 취소 도메인 예외"""


class DomainAttributeError(VideoScriptsError, AttributeError):
    """허용되지 않은 속성이나 메서드 접근 도메인 예외"""


class DomainTypeError(VideoScriptsError, TypeError):
    """지원되지 않는 피연산자 또는 정렬 키 타입 전달 도메인 예외"""


def format_user_error(exc: BaseException) -> str:
    """예외 객체를 UI/CUI 경계용 사용자 안내 문구로 변환"""
    message = str(exc).strip() or exc.__class__.__name__
    hint: str | None = None

    if isinstance(exc, VideoScriptsError):
        hint = exc.hint or DEFAULT_ERROR_HINTS.get(exc.code)
    elif isinstance(exc, PermissionError):
        hint = "파일이나 폴더에 대한 접근 권한이 부족하거나 다른 프로그램에서 사용 중인지 확인해 주세요."
    elif isinstance(exc, OSError):
        hint = "디스크 여유 공간, 파일 경로 유효성 및 읽기/쓰기 권한을 확인해 주세요."

    if hint and hint not in message:
        return f"{message}\n\n안내: {hint}"
    return message
