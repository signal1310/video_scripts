"""진단 로그 경로, 꼬리 읽기, 파일 크기 상한과 보고서 폴더 보관 규칙 모듈

- 데이터 폴더(`data/logs`) 아래 파일 이름과 크기, 보관 개수를 한 곳에서 정의
- Qt에 의존하지 않으므로 콘솔 진입점과 테스트가 그대로 재사용
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime

from src.core.paths import get_data_dir

DIAGNOSTICS_DIR_NAME: str = "logs"
APP_LOG_NAME: str = "app.log"
NATIVE_LOG_NAME: str = "native_crash.log"
SESSION_FILENAME: str = "session.json"
BREADCRUMBS_NAME: str = "breadcrumbs.log"
DEV_LOG_NAME: str = "dev.log"
REPORT_DIR_PREFIX: str = "crash_"
REPORT_DIR_TIME_FORMAT: str = "%Y%m%d-%H%M%S"
MAX_KEPT_REPORTS: int = 10
NATIVE_LOG_MAX_BYTES: int = 1024 * 1024
BREADCRUMBS_MAX_BYTES: int = 256 * 1024
REPORT_TAIL_LINES: int = 400
TAIL_KEEP_DIVISOR: int = 2
_DECODE_ERRORS: str = "replace"


def diagnostics_dir() -> str:
    """데이터 폴더 아래 진단 로그 디렉터리 절대 경로 반환"""
    return os.path.join(get_data_dir(), DIAGNOSTICS_DIR_NAME)


def ensure_diagnostics_dir(directory: str | None = None) -> str:
    """진단 로그 디렉터리를 만들고 경로 반환. 만들지 못하면 요청한 경로만 돌려줌"""
    target = directory or diagnostics_dir()
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        return target
    return target


def diagnostics_file(name: str, directory: str | None = None) -> str:
    """진단 로그 디렉터리 아래 파일 경로 반환"""
    return os.path.join(directory or diagnostics_dir(), name)


def file_size(path: str) -> int:
    """파일 크기 반환. 없으면 0"""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def trim_file_to_tail(path: str, max_bytes: int) -> int:
    """파일이 상한을 넘으면 마지막 일부만 줄 경계에서 남기고, 남긴 크기 반환

    - 상한의 절반만 남겨 다음 실행까지 여유를 둠
    - 바이트 단위로 자르므로 UTF-8 경계가 깨진 앞부분을 버리고 줄 시작에서 다시 시작
    - 임시 파일에 쓴 뒤 교체해 도중 종료에도 반쯤 잘린 파일이 남지 않게 함
    """
    size = file_size(path)
    if size <= max_bytes or size <= 0:
        return size
    keep = max(1, max_bytes // TAIL_KEEP_DIVISOR)
    try:
        with open(path, "rb") as handle:
            handle.seek(-keep, os.SEEK_END)
            data = handle.read()
        first_break = data.find(b"\n")
        if 0 <= first_break < len(data) - 1:
            data = data[first_break + 1 :]
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".trim_", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise
    except OSError:
        return size
    return len(data)


def read_tail_lines(path: str, limit: int, *, max_bytes: int = NATIVE_LOG_MAX_BYTES) -> list[str]:
    """파일 끝에서 limit 줄까지 읽어 반환. 없거나 비면 빈 목록"""
    if limit <= 0:
        return []
    size = file_size(path)
    if size <= 0:
        return []
    start = max(0, size - max_bytes)
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", _DECODE_ERRORS)
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines[-limit:]


def read_span(path: str, offset: int, *, max_bytes: int | None = None) -> bytes:
    """파일의 offset 이후 바이트 반환. offset이 파일 크기보다 크면 빈 바이트"""
    if offset < 0:
        offset = 0
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read() if max_bytes is None else handle.read(max_bytes)
    except OSError:
        return b""


def report_dir_name(when: datetime) -> str:
    """세션 시각 기준 보고서 폴더 이름 반환"""
    return f"{REPORT_DIR_PREFIX}{when.strftime(REPORT_DIR_TIME_FORMAT)}"


def create_report_dir(directory: str, when: datetime) -> str:
    """보고서 폴더를 만들고 경로 반환. 같은 이름이 있으면 뒤에 번호를 붙임"""
    base = os.path.join(directory, report_dir_name(when))
    candidate = base
    suffix = 2
    while os.path.exists(candidate):
        candidate = f"{base}_{suffix}"
        suffix += 1
    os.makedirs(candidate, exist_ok=True)
    return candidate


def list_report_dirs(directory: str) -> list[str]:
    """보고서 폴더 목록을 이름(시간) 오름차순으로 반환"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = [
        os.path.join(directory, name)
        for name in names
        if name.startswith(REPORT_DIR_PREFIX) and os.path.isdir(os.path.join(directory, name))
    ]
    return sorted(found, key=lambda path: os.path.basename(path))


def prune_reports(directory: str, keep: int = MAX_KEPT_REPORTS) -> list[str]:
    """가장 오래된 보고서 폴더를 지워 keep개만 남기고 지운 경로 목록 반환"""
    reports = list_report_dirs(directory)
    if keep < 0 or len(reports) <= keep:
        return []
    removable = reports[: len(reports) - keep] if keep else reports
    removed: list[str] = []
    for path in removable:
        if _remove_report_dir(path):
            removed.append(path)
    return removed


def _remove_report_dir(path: str) -> bool:
    """보고서 폴더의 파일을 지운 뒤 폴더를 제거. 실패하면 False"""
    try:
        for name in os.listdir(path):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(path, name))
        os.rmdir(path)
    except OSError:
        return False
    return True
