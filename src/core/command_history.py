"""FFmpeg, FFprobe 명령 실행 기록을 담는 스레드 안전 링 버퍼 모듈

- 기록은 메모리에만 두며 저장하지 않음. 표시용 대상 이름은 이름만 남기고, 영상 열 툴팁에 쓸 원본 경로는 이 메모리 버퍼에만 둠
- 변경 알림은 어느 스레드에서든 올 수 있으므로 GUI는 신호로 넘겨 받아 화면 스레드에서 반영
- 소요 시간은 `time.perf_counter`로 재서 시간 함수를 패치하는 테스트와 간섭하지 않음
- 표시용 명령줄 인용은 이 모듈의 지역 규칙을 쓰며, 계획 인자 빌더(`ffmpeg_command.py`)는 건드리지 않음
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

COMMAND_HISTORY_LIMIT: int = 2000

STAGE_FRAME_ANALYSIS: str = "화질 분석"
STAGE_SAMPLE_ENCODE: str = "샘플 인코딩"
STAGE_MAIN_ENCODE: str = "본 인코딩"
STAGE_FRAME_COMPARE: str = "화질 비교"
STAGE_PROBING: str = "프로빙"
STAGE_VMAF: str = "VMAF 측정"
STAGE_CAPABILITY: str = "능력 시험"
COMMAND_STAGES: tuple[str, ...] = (
    STAGE_FRAME_ANALYSIS,
    STAGE_SAMPLE_ENCODE,
    STAGE_MAIN_ENCODE,
    STAGE_FRAME_COMPARE,
    STAGE_PROBING,
    STAGE_VMAF,
    STAGE_CAPABILITY,
)

# - 공백이나 따옴표가 없는 인자는 그대로 보이고, 있으면 큰따옴표로 감싸 사람이 읽는 표시를 만듦
_QUOTE_NEEDED_CHARS: frozenset[str] = frozenset({" ", "\t", '"'})

_LOCK = threading.Lock()
_ENTRIES: deque[_Entry] = deque(maxlen=COMMAND_HISTORY_LIMIT)
_SEQUENCES: Iterator[int] = itertools.count(1)
_LISTENERS: list[Callable[[], None]] = []



@dataclass(frozen=True, slots=True)
class CommandRecord:
    """완료 또는 진행 중인 외부 명령 한 건의 기록. 진행 중이면 종료 코드가 None

    - `source_path`는 명령이 다룬 원본 영상의 전체 경로이며, 영상이 없는 명령이면 빈 문자열
    """

    seq: int
    timestamp: datetime
    stage: str
    target_name: str
    args: tuple[str, ...]
    display: str
    elapsed_sec: float | None
    exit_code: int | None
    source_path: str = ""


@dataclass(slots=True)
class _Entry:
    """링 버퍼가 들고 있는 가변 기록. 종료 값이 나중에 채워짐"""

    seq: int
    timestamp: datetime
    stage: str
    target_name: str
    args: tuple[str, ...]
    display: str
    started: float
    elapsed_sec: float | None = None
    exit_code: int | None = None
    source_path: str = ""

    def to_record(self) -> CommandRecord:
        """현재 값을 불변 스냅샷으로 복사"""
        return CommandRecord(
            seq=self.seq,
            timestamp=self.timestamp,
            stage=self.stage,
            target_name=self.target_name,
            args=self.args,
            display=self.display,
            elapsed_sec=self.elapsed_sec,
            exit_code=self.exit_code,
            source_path=self.source_path,
        )

@dataclass(slots=True)
class CommandHandle:
    """기록 한 건의 진행 상태. `exit_code`를 채우면 종료 때 그 값으로 남음"""

    entry: _Entry = field(repr=False)
    exit_code: int | None = None

    @property
    def seq(self) -> int:
        """링 버퍼 안 기록 번호"""
        return self.entry.seq

    @property
    def stage(self) -> str:
        """단계 이름"""
        return self.entry.stage

    @property
    def target_name(self) -> str:
        """대상 파일 이름"""
        return self.entry.target_name

    @property
    def args(self) -> tuple[str, ...]:
        """전체 명령줄 인자"""
        return self.entry.args

    @property
    def source_path(self) -> str:
        """명령이 다룬 원본 영상의 전체 경로"""
        return self.entry.source_path

    def mark_started(self) -> None:
        """시작 시각을 지금으로 다시 잡음. 프로세스를 띄운 뒤부터 소요 시간을 재고 싶을 때 씀"""
        self.entry.started = time.perf_counter()

    def settle(self, exit_code: int | None, elapsed_sec: float | None) -> None:
        """기록의 종료 값과 소요 시간을 채움. 소요 시간을 주지 않으면 시작부터 잰 값을 씀"""
        self.entry.exit_code = exit_code
        self.entry.elapsed_sec = (
            max(0.0, time.perf_counter() - self.entry.started)
            if elapsed_sec is None
            else max(0.0, elapsed_sec)
        )


def _quote_arg(arg: str) -> str:
    """공백이나 따옴표가 있으면 큰따옴표로 감싼 표시용 인자"""
    if not arg:
        return '""'
    if any(char in _QUOTE_NEEDED_CHARS for char in arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def format_command_line(args: Sequence[str]) -> str:
    """인자 목록을 사람이 읽는 한 줄 명령줄로 표시"""
    return " ".join(_quote_arg(arg) for arg in args)


def target_name_of(path: str) -> str:
    """대상 경로에서 파일 이름만 남김. 폴더 구분자는 두 형식을 모두 받음"""
    if not path:
        return ""
    return os.path.basename(path.replace("\\", "/"))


def input_path_of(args: Sequence[str], flag: str = "-i") -> str:
    """명령 인자에서 입력 파일 경로를 찾음. 없으면 빈 문자열"""
    for index, arg in enumerate(args):
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
    return ""


def _notify() -> None:
    """등록된 변경 리스너를 잠금 밖에서 호출"""
    with _LOCK:
        listeners = tuple(_LISTENERS)
    for listener in listeners:
        listener()


def add_listener(listener: Callable[[], None]) -> None:
    """기록 변경 리스너 등록. 리스너는 기록을 바꾼 스레드에서 호출됨"""
    with _LOCK:
        if listener not in _LISTENERS:
            _LISTENERS.append(listener)


def remove_listener(listener: Callable[[], None]) -> None:
    """등록한 리스너 해제. 없는 리스너는 무시"""
    with _LOCK:
        if listener in _LISTENERS:
            _LISTENERS.remove(listener)


def record_start(
    stage: str, args: Sequence[str], *, target: str = "", source_path: str = ""
) -> CommandHandle:
    """명령 실행 기록을 열고 진행 상태 핸들을 반환. 종료 코드는 아직 None

    - `source_path`는 명령이 다룬 원본 영상의 전체 경로이며, 영상과 무관한 명령은 비움
    """
    arg_list = tuple(str(arg) for arg in args)
    with _LOCK:
        entry = _Entry(
            seq=next(_SEQUENCES),
            timestamp=datetime.now(),
            stage=stage,
            target_name=target_name_of(target),
            args=arg_list,
            display=format_command_line(arg_list),
            started=time.perf_counter(),
            source_path=source_path,
        )
        _ENTRIES.append(entry)
    _notify()
    return CommandHandle(entry=entry)


def record_finish(handle: CommandHandle, exit_code: int | None, elapsed_sec: float | None = None) -> None:
    """기록을 종료 상태로 마감. 소요 시간을 주지 않으면 시작부터 잰 값을 씀"""
    with _LOCK:
        handle.settle(exit_code, elapsed_sec)
    _notify()


def snapshot() -> tuple[CommandRecord, ...]:
    """가장 오래된 기록부터의 불변 복사본 목록"""
    with _LOCK:
        return tuple(entry.to_record() for entry in _ENTRIES)


def latest() -> CommandRecord | None:
    """가장 마지막 기록 하나의 불변 복사본. 기록이 없으면 None

    - 변경 알림 리스너는 매번 불리므로 전체 복사 없이 O(1)로 최신 한 건만 읽음
    """
    with _LOCK:
        if not _ENTRIES:
            return None
        return _ENTRIES[-1].to_record()


def clear() -> None:
    """보관한 기록을 모두 지움"""
    with _LOCK:
        _ENTRIES.clear()
    _notify()


@contextmanager
def command_scope(
    stage: str, args: Sequence[str], *, target: str = "", source_path: str = ""
) -> Generator[CommandHandle]:
    """명령 실행 구간을 감싸 시작과 종료를 한 곳에서 기록

    - 호출자는 핸들의 `exit_code`에 종료 코드를 채우고, 취소나 실패로 코드가 없으면 그대로 둠
    - 예외로 빠져나가도 종료 기록을 남기므로 실행 중 상태로 남지 않음
    - `source_path`는 명령이 다룬 원본 영상의 전체 경로이며, 영상과 무관한 명령은 비움
    """
    handle = record_start(stage, args, target=target, source_path=source_path)
    try:
        yield handle
    finally:
        record_finish(handle, handle.exit_code)

