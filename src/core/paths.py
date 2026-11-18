"""작업 경로(data/rootdir.txt), 실행 기준 디렉터리 조회와 UTF-8 파일 입출력 모듈"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Callable, Sequence

from src.core.exceptions import (
    DomainFileNotFoundError,
    DomainRuntimeError,
    ErrorCode,
)
from src.core.workspace_paths import format_root_dirs, parse_root_dirs

DATA_DIR_NAME: str = "data"
ROOTDIR_FILENAME: str = "rootdir.txt"

DEFAULT_ROOT_DIR: str = ""
DEFAULT_ROOTDIR_TEMPLATE: str = (
    "# Video Classifier 기본 작업 대상 비디오 폴더 경로 설정 (data/rootdir.txt)\n"
    "# - 작업 폴더 경로를 비주석 줄에 입력하세요 (여러 개는 줄을 나누거나 ;로 구분, 따옴표, ~, 환경변수 사용 가능, # 뒤는 주석)\n"
    "# - 경로를 수정, 저장하면 Jupyter 커널이나 앱 재시작 없이 즉시 반영됩니다\n"
    "# - 비워 두면 GUI 실행 시 폴더를 직접 선택해 작업할 수 있습니다\n"
    "# 예시:\n"
    "#   D:\\Videos\\Clips\n"  # hygiene: ignore
    '#   "D:\\Videos\\내 동영상"  # 공백 포함 경로, 인라인 주석 가능\n'  # hygiene: ignore
)


def get_app_base_dir() -> str:
    """실행 파일(.exe) 또는 소스 프로젝트 루트 디렉터리 절대 경로 반환"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_data_dir() -> str:
    """포터블 백업 대상 데이터 디렉터리(실행 경로/data) 절대 경로 반환"""
    return os.path.join(get_app_base_dir(), DATA_DIR_NAME)


def get_rootdir_path() -> str:
    """data/rootdir.txt 파일의 절대 경로 반환"""
    return os.path.join(get_data_dir(), ROOTDIR_FILENAME)


def write_utf8_file(path: str, content: str) -> None:
    """상위 디렉터리를 자동 생성한 뒤 UTF-8 텍스트 파일을 임시 파일 경유로 원자적 교체 기록

    - 쓰는 도중 강제 종료되어도 기존 파일이 손상되지 않도록 같은 디렉터리에 쓴 뒤 os.replace로 교체
    """
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent_dir or None, prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise


def ensure_utf8_file(path: str, content_factory: Callable[[], str]) -> str:
    """대상 파일이 없을 때만 기본 내용을 생성해 기록하고 경로 반환"""
    if not os.path.exists(path):
        write_utf8_file(path, content_factory())
    return path


def _render_rootdir_content(root_dirs: Sequence[str] = ()) -> str:
    """CUI 사용자 안내 주석을 포함한 data/rootdir.txt 파일 내용 생성

    - 작업 폴더마다 한 줄씩 기록
    """
    if not root_dirs:
        return DEFAULT_ROOTDIR_TEMPLATE
    body = "".join(f"{root_dir}\n" for root_dir in root_dirs)
    return f"{DEFAULT_ROOTDIR_TEMPLATE}{body}"


def ensure_default_rootdir_file() -> str:
    """data/rootdir.txt 파일이 없으면 CUI 안내 주석 템플릿으로 자동 생성하고 경로 반환"""
    return ensure_utf8_file(
        get_rootdir_path(),
        lambda: _render_rootdir_content(),
    )


def set_root_dirs(paths: Sequence[str]) -> None:
    """data/rootdir.txt 파일에 CUI 안내 주석과 함께 작업 폴더 목록을 저장

    - 세미콜론으로 구분된 문자열도 작업 폴더 단위로 펼쳐서 저장
    """
    write_utf8_file(get_rootdir_path(), _render_rootdir_content(parse_root_dirs(paths)))


def set_root_dir(path: str) -> None:
    """data/rootdir.txt 파일에 작업 폴더를 저장(세미콜론 구분 다중 경로 허용)"""
    set_root_dirs((path,))


def _strip_inline_comment(line: str) -> str:
    """따옴표 바깥에 위치한 주석(#) 이후 문자열을 제거"""
    quote_char: str | None = None
    for idx, ch in enumerate(line):
        if quote_char is None:
            if ch in ('"', "'"):
                quote_char = ch
            elif ch == "#" and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] in ('"', "'")):
                return line[:idx]
        elif ch == quote_char:
            quote_char = None
    return line


def _parse_rootdir_line(raw_line: str) -> str:
    """data/rootdir.txt 단일 줄에서 주석, 따옴표를 정리하고 사용자 홈(~) 및 환경변수를 확장"""
    cleaned = _strip_inline_comment(raw_line).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return ""
    return os.path.expanduser(os.path.expandvars(cleaned))


def get_root_dirs() -> list[str]:
    """data/rootdir.txt 파일에서 분석 대상 작업 폴더 목록 조회

    - 비주석 줄마다 세미콜론 구분 경로를 분할하고 중복을 제거
    - 프로세스나 Jupyter 커널 재시작 없이 파일 변경 내용 즉시 반영
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, encoding="utf-8-sig") as f:
            parsed_lines = [_parse_rootdir_line(raw_line) for raw_line in f]
    except FileNotFoundError as e:
        raise DomainFileNotFoundError(
            f"data/rootdir.txt 파일을 찾을 수 없습니다: {rootdir_path}",
            code=ErrorCode.ROOT_DIR_NOT_FOUND,
        ) from e
    except Exception as e:
        raise DomainRuntimeError(
            f"data/rootdir.txt 파일을 읽는 중 오류가 발생했습니다: {e}",
            code=ErrorCode.ROOT_DIR_READ_ERROR,
        ) from e
    return list(parse_root_dirs([line for line in parsed_lines if line]))


def get_root_dir() -> str:
    """data/rootdir.txt의 작업 폴더 목록을 세미콜론 구분 단일 문자열로 조회

    - 작업 폴더가 1개면 기존과 동일한 단일 경로 문자열 반환
    """
    return format_root_dirs(get_root_dirs())
