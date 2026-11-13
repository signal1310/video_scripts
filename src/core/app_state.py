"""통합 설정·GUI 상태 JSONC 영속화(data/config.json) 및 .rootdir 우선순위 계층 관리 모듈"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import src.core.config as config_mod
from src.core.config import DEFAULT_ROOT_DIR, PathsConfig, UiLayoutState, UiState
from src.core.exceptions import DomainFileNotFoundError, DomainRuntimeError
from src.core.state_codec import (
    MAX_RECENT_DIRS,
    AppState,
    extract_fallback_root_dir,
    parse_state_mapping,
    render_state_jsonc,
    strip_jsonc_comments,
)

DATA_DIR_NAME = "data"
CONFIG_FILENAME = "config.json"

__all__ = [
    "CONFIG_FILENAME",
    "DATA_DIR_NAME",
    "MAX_RECENT_DIRS",
    "AppState",
    "ensure_default_config_files",
    "get_data_dir",
    "get_state_path",
    "load_app_state",
    "record_recent_dir",
    "reset_app_state",
    "resolve_effective_root_dir",
    "save_app_state",
]


def get_data_dir() -> str:
    """포터블 백업 대상 데이터 디렉터리(실행 경로/data) 절대 경로 반환"""
    return os.path.join(config_mod.get_app_base_dir(), DATA_DIR_NAME)


def get_state_path() -> str:
    """통합 설정·상태 JSONC 파일(data/config.json) 절대 경로 반환"""
    return os.path.join(get_data_dir(), CONFIG_FILENAME)


def resolve_effective_root_dir(fallback_root_dir: str = "") -> str:
    """.rootdir 파일 우선순위를 적용해 유효한 기본 작업 폴더 경로 반환

    .rootdir 파일에 비어 있지 않은 경로가 기록되어 있으면 통합 설정 파일보다 항상 우선 적용
    """
    try:
        raw_rootdir = config_mod.get_root_dir().strip()
        if raw_rootdir:
            return raw_rootdir
    except (DomainFileNotFoundError, DomainRuntimeError):
        logging.getLogger(__name__).debug(".rootdir 조회 실패로 통합 설정 폴백")
    return fallback_root_dir.strip()


def record_recent_dir(
    recent_dirs: list[str],
    directory: str,
    max_items: int = MAX_RECENT_DIRS,
) -> list[str]:
    """최근 작업 폴더 목록 선두에 경로를 중복 없이 추가하고 최대 개수로 절단"""
    cleaned = directory.strip()
    if not cleaned:
        return list(recent_dirs[:max_items])
    normalized = os.path.normpath(cleaned)
    target_key = os.path.normcase(os.path.abspath(normalized))
    updated = [normalized]
    for item in recent_dirs:
        if not item.strip():
            continue
        norm_item = os.path.normpath(item.strip())
        if os.path.normcase(os.path.abspath(norm_item)) == target_key:
            continue
        updated.append(norm_item)
        if len(updated) >= max_items:
            break
    return updated


def _read_raw_state_json(state_path: str) -> dict[str, Any] | None:
    if not os.path.isfile(state_path):
        return None
    try:
        with open(state_path, encoding="utf-8") as f:
            stripped = strip_jsonc_comments(f.read())
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, ValueError):
        logging.getLogger(__name__).warning(
            "통합 설정 파일 파싱 실패로 기본 상태를 사용합니다: %s", state_path, exc_info=True
        )
    return None


def ensure_default_config_files() -> AppState:
    """.rootdir과 data/config.json이 없으면 기본값 파일로 자동 생성하고 현재 AppState 반환"""
    return load_app_state(create_missing=True)


def load_app_state(*, create_missing: bool = True) -> AppState:
    """통합 설정 파일(data/config.json)과 .rootdir 계층에서 유효한 AppState 로드"""
    if create_missing:
        config_mod.ensure_default_rootdir_file()

    state_path = get_state_path()
    if not os.path.exists(state_path):
        initial_state = AppState(
            paths=PathsConfig(root_dir=resolve_effective_root_dir(DEFAULT_ROOT_DIR)),
        )
        if create_missing:
            save_app_state(initial_state, sync_rootdir=False)
        return initial_state

    raw = _read_raw_state_json(state_path)
    if raw is None:
        return AppState(
            paths=PathsConfig(root_dir=resolve_effective_root_dir(DEFAULT_ROOT_DIR)),
        )

    fallback_root = extract_fallback_root_dir(raw)
    effective_root = resolve_effective_root_dir(fallback_root)
    return parse_state_mapping(raw, effective_root=effective_root)


def save_app_state(
    state: AppState,
    *,
    sync_rootdir: bool = False,
) -> None:
    """통합 설정 파일(data/config.json)을 JSONC 형식으로 저장하고 필요 시 .rootdir을 동시 갱신"""
    if sync_rootdir:
        config_mod.set_root_dir(state.paths.root_dir)

    state_path = get_state_path()
    parent_dir = os.path.dirname(state_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(render_state_jsonc(state))


def reset_app_state(
    *,
    preserve_root_dir: bool = True,
    preserve_recent_dirs: bool = False,
    preserve_layout: bool = True,
) -> AppState:
    """모든 분석 설정과 GUI 필터·예외 상태를 기본값으로 복원하고 영구 저장"""
    current = load_app_state()
    fresh = AppState(
        paths=PathsConfig(
            root_dir=current.paths.root_dir if preserve_root_dir else DEFAULT_ROOT_DIR,
            recent_dirs=list(current.paths.recent_dirs) if preserve_recent_dirs else [],
        ),
        ui=UiState(
            layout=UiLayoutState(
                window_geometry=current.ui.layout.window_geometry if preserve_layout else "",
                table_header_state=(
                    current.ui.layout.table_header_state if preserve_layout else ""
                ),
            ),
        ),
    )
    save_app_state(
        fresh,
        sync_rootdir=not preserve_root_dir,
    )
    return fresh
