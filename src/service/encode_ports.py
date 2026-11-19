"""재인코딩 실행부의 외부 인터페이스와 기본 구현: FFmpeg 프로세스와 ffprobe 호출

- 서비스는 이 인터페이스만 보고, 단위 테스트는 같은 시그니처의 가짜 구현을 주입함
"""

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from src.core.analysis.prober import run_ffprobe
from src.core.encode.encode_process import EncodeProcess, ProcessOutcome


class EncodeProcessLike(Protocol):
    """인코딩 프로세스의 실행과 제어 인터페이스(EncodeProcess와 동일 시그니처)"""

    def start(self) -> None:
        """프로세스를 시작"""
        ...

    def pause(self) -> None:
        """프로세스를 일시중지"""
        ...

    def resume(self) -> None:
        """일시중지한 프로세스를 재개"""
        ...

    def set_priority(self, name: str) -> None:
        """설정 우선순위 이름을 프로세스에 적용"""
        ...

    def cancel(self) -> None:
        """프로세스를 취소(종료)"""
        ...

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        """종료를 기다려 결과를 반환"""
        ...



ProcessFactory = Callable[[Sequence[str], Callable[[str], None]], EncodeProcessLike]


ProbeFn = Callable[[str, tuple[str, ...], threading.Event], dict[str, Any]]



def default_probe(ffprobe: str) -> ProbeFn:
    """ffprobe 실행 파일 경로로 실제 probe 함수를 만듦"""
    def probe(path: str, args: tuple[str, ...], cancel: threading.Event) -> dict[str, Any]:
        return run_ffprobe(path, extra_args=args, cancel_event=cancel, ffprobe=ffprobe)

    return probe



def default_process_factory(args: Sequence[str], on_line: Callable[[str], None]) -> EncodeProcessLike:
    """실제 FFmpeg 인코딩 프로세스를 만듦"""
    return EncodeProcess(args, on_progress_line=on_line)
