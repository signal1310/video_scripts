"""작업 경로(data/rootdir.txt), 실행 기준 디렉터리 조회와 UTF-8 파일 입출력 모듈"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Callable, Collection

from src.core.exceptions import (
    DomainFileNotFoundError,
    DomainRuntimeError,
    ErrorCode,
)
from src.core.resolution_tiers import RESOLUTION_TIERS

DATA_DIR_NAME: str = "data"
ROOTDIR_FILENAME: str = "rootdir.txt"

DEFAULT_ROOT_DIR: str = ""
DEFAULT_ROOTDIR_TEMPLATE: str = (
    "# Video Classifier 기본 작업 대상 비디오 폴더 경로 설정 (data/rootdir.txt)\n"
    "# - 첫 번째 비주석 줄에 작업 폴더 경로를 입력하세요 (따옴표, ~, 환경변수 사용 가능, # 뒤는 주석)\n"
    "# - 경로를 수정, 저장하면 Jupyter 커널이나 앱 재시작 없이 즉시 반영됩니다\n"
    "# - 비워 두면 GUI 실행 시 폴더를 직접 선택해 작업할 수 있습니다\n"
    "# 예시:\n"
    "#   D:\\Videos\\Clips\n"  # hygiene: ignore
    '#   "D:\\Videos\\내 동영상"  # 공백 포함 경로, 인라인 주석 가능\n'  # hygiene: ignore
)


_INVALID_DIRNAME_CHARS: frozenset[str] = frozenset('/\\:*?"<>|')


def is_valid_dirname(name: str) -> bool:
    """작업 폴더 직하위의 단일 폴더 이름으로 안전한지(구분자, 상위 경로, 예약 문자 없음) 판별"""
    stripped = name.strip()
    return (
        bool(stripped)
        and stripped not in {".", ".."}
        and not any(ch in _INVALID_DIRNAME_CHARS for ch in stripped)
        and not stripped.endswith((".", " "))
    )


def is_valid_class_dirname(name: str, reserved_dirnames: Collection[str]) -> bool:
    """분류 폴더명의 안전성과 다른 분류 폴더명과의 겹침 여부 판별"""
    text = name.strip()
    return is_valid_dirname(text) and text not in reserved_dirnames


def downscale_tier_dirnames(prefix: str) -> tuple[str, ...]:
    """축소 폴더명 앞부분에 규격 키를 붙인 규격별 분류 폴더명 목록 반환"""
    return tuple(f"{prefix}{tier.key}" for tier in RESOLUTION_TIERS)


def is_valid_downscale_prefix(prefix: str, reserved_dirnames: Collection[str]) -> bool:
    """축소 폴더명 앞부분의 비어 있음, 금지 문자, 다른 분류 폴더명과의 겹침 여부 판별

    - 규격 키를 붙인 전체 이름이 reserved_dirnames와 겹치면 한 폴더에 두 프리셋이 섞이므로 거부
    """
    text = prefix.strip()
    if not is_valid_dirname(text):
        return False
    return set(downscale_tier_dirnames(text)).isdisjoint(reserved_dirnames)


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


def _render_rootdir_content(path: str = "") -> str:
    """CUI 사용자 안내 주석을 포함한 data/rootdir.txt 파일 내용 생성"""
    cleaned = path.strip()
    if cleaned:
        return f"{DEFAULT_ROOTDIR_TEMPLATE}{cleaned}\n"
    return DEFAULT_ROOTDIR_TEMPLATE


def ensure_default_rootdir_file() -> str:
    """data/rootdir.txt 파일이 없으면 CUI 안내 주석 템플릿으로 자동 생성하고 경로 반환"""
    return ensure_utf8_file(
        get_rootdir_path(),
        lambda: _render_rootdir_content(DEFAULT_ROOT_DIR),
    )


def set_root_dir(path: str) -> None:
    """data/rootdir.txt 파일에 CUI 안내 주석과 함께 분석 대상 작업 경로를 저장"""
    write_utf8_file(get_rootdir_path(), _render_rootdir_content(path))


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


def get_root_dir() -> str:
    """data/rootdir.txt 파일에서 분석 대상 작업 경로 조회

    프로세스나 Jupyter 커널 재시작 없이 파일 변경 내용 즉시 반영
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, encoding="utf-8-sig") as f:
            for raw_line in f:
                parsed = _parse_rootdir_line(raw_line)
                if parsed:
                    return parsed
            return ""
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
