"""분석을 끝낸 적 있는 작업 폴더 목록을 data 폴더에 기록하고 조회하는 모듈"""

import json
import logging
import os

from src.core.paths import get_data_dir, write_utf8_file
from src.core.workspace_paths import parse_root_dirs

ANALYZED_DIRS_FILENAME: str = "analyzed_dirs.json"
RECURSIVE_KEY_SUFFIX: str = "|recursive=1"


def _marker_path() -> str:
    return os.path.join(get_data_dir(), ANALYZED_DIRS_FILENAME)


def _dir_key(root_dir: str, *, recursive: bool = False) -> str:
    """작업 폴더 목록과 하위 폴더 포함 여부로 구분되는 기록 키

    - 단일 작업 폴더의 비재귀 키는 기존 기록과 같은 문자열을 유지
    """
    norm = ";".join(os.path.normcase(os.path.abspath(p)) for p in parse_root_dirs(root_dir))
    return f"{norm}{RECURSIVE_KEY_SUFFIX}" if recursive else norm


def _load_keys() -> list[str]:
    path = _marker_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            loaded: object = json.load(f)
    except (OSError, ValueError):
        logging.getLogger(__name__).warning(
            "분석 폴더 기록 읽기 실패로 빈 목록을 사용합니다: %s", path, exc_info=True
        )
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def is_analyzed_dir(root_dir: str, *, recursive: bool = False) -> bool:
    """지정 작업 폴더가 같은 분석 범위로 분석을 끝낸 적이 있는지 반환"""
    return _dir_key(root_dir, recursive=recursive) in _load_keys()


def mark_analyzed_dir(root_dir: str, *, recursive: bool = False) -> None:
    """분석이 결과를 내고 끝난 작업 폴더를 기록, 이미 기록된 항목은 다시 쓰지 않음"""
    keys = _load_keys()
    key = _dir_key(root_dir, recursive=recursive)
    if key in keys:
        return
    keys.append(key)
    try:
        write_utf8_file(_marker_path(), json.dumps(keys, ensure_ascii=False, indent=1))
    except OSError:
        logging.getLogger(__name__).warning("분석 폴더 기록 저장 실패", exc_info=True)
