"""통합 설정·GUI 상태 JSON 영속화(data/config.json) 및 .rootdir 우선순위 계층 관리 모듈"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import src.core.config as config_mod
from src.core.config import (
    DEFAULT_CONFIGS,
    DEFAULT_FILTER_SEARCH,
    DEFAULT_FILTER_TARGET,
    DEFAULT_FILTER_ZONE,
    DEFAULT_ROOT_DIR,
    DEFAULT_SCAN_MODE,
    DEFAULT_THEME_MODE,
    VALID_FILTER_TARGETS,
    VALID_FILTER_ZONES,
    VALID_SCAN_MODES,
    VALID_THEME_MODES,
    ClassifierConfig,
)
from src.core.exceptions import DomainFileNotFoundError, DomainRuntimeError

DATA_DIR_NAME = "data"
CONFIG_FILENAME = "config.json"
APP_STATE_VERSION = 1
MAX_RECENT_DIRS = 10


@dataclass
class AppState:
    """분석 설정·작업 경로·최근 폴더·예외 규칙·UI 필터·레이아웃을 통합 보관하는 단일 상태 컨테이너"""

    config: ClassifierConfig = field(default_factory=ClassifierConfig)
    root_dir: str = DEFAULT_ROOT_DIR
    recent_dirs: list[str] = field(default_factory=list)
    exception_tokens: list[str] = field(default_factory=list)
    scan_mode: str = DEFAULT_SCAN_MODE
    filter_search: str = DEFAULT_FILTER_SEARCH
    filter_zone: str = DEFAULT_FILTER_ZONE
    filter_target: str = DEFAULT_FILTER_TARGET
    theme_mode: str = DEFAULT_THEME_MODE
    window_geometry: str = ""
    table_header_state: str = ""

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 JSON 사전 표현으로 변환"""
        return {
            "version": APP_STATE_VERSION,
            "config": self.config.to_dict(),
            "root_dir": self.root_dir,
            "recent_dirs": list(self.recent_dirs),
            "exception_tokens": list(self.exception_tokens),
            "scan_mode": self.scan_mode,
            "filter_search": self.filter_search,
            "filter_zone": self.filter_zone,
            "filter_target": self.filter_target,
            "theme_mode": self.theme_mode,
            "window_geometry": self.window_geometry,
            "table_header_state": self.table_header_state,
        }


def get_data_dir() -> str:
    """포터블 백업 대상 데이터 디렉터리(실행 경로/data) 절대 경로 반환"""
    return os.path.join(config_mod.get_app_base_dir(), DATA_DIR_NAME)


def get_state_path() -> str:
    """통합 설정·상태 JSON 파일(data/config.json) 절대 경로 반환"""
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
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, ValueError):
        logging.getLogger(__name__).warning(
            "통합 설정 파일 파싱 실패로 기본 상태를 사용합니다: %s", state_path, exc_info=True
        )
    return None


def _sanitize_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text in allowed else default


def _sanitize_str_list(raw: Any, *, max_items: int | None = None) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            result.append(text)
        if max_items is not None and len(result) >= max_items:
            break
    return result


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
            config=ClassifierConfig(),
            root_dir=resolve_effective_root_dir(DEFAULT_ROOT_DIR),
        )
        if create_missing:
            save_app_state(initial_state, sync_rootdir=False)
        return initial_state

    raw = _read_raw_state_json(state_path)
    if raw is None:
        return AppState(
            config=ClassifierConfig(),
            root_dir=resolve_effective_root_dir(DEFAULT_ROOT_DIR),
        )

    raw_cfg = raw.get("config")
    if isinstance(raw_cfg, dict):
        merged_cfg = dict(DEFAULT_CONFIGS)
        for k, v in raw_cfg.items():
            if str(k) in DEFAULT_CONFIGS and v is not None:
                merged_cfg[str(k)] = str(v)
        cfg = ClassifierConfig.from_mapping(merged_cfg)
    else:
        cfg = ClassifierConfig()

    fallback_root = str(raw.get("root_dir", DEFAULT_ROOT_DIR) or "").strip()
    effective_root = resolve_effective_root_dir(fallback_root)
    recent_dirs = _sanitize_str_list(raw.get("recent_dirs"), max_items=MAX_RECENT_DIRS)
    exception_tokens = _sanitize_str_list(raw.get("exception_tokens"))

    return AppState(
        config=cfg,
        root_dir=effective_root,
        recent_dirs=recent_dirs,
        exception_tokens=exception_tokens,
        scan_mode=_sanitize_choice(
            raw.get("scan_mode"), VALID_SCAN_MODES, DEFAULT_SCAN_MODE
        ),
        filter_search=str(raw.get("filter_search", DEFAULT_FILTER_SEARCH) or ""),
        filter_zone=_sanitize_choice(
            raw.get("filter_zone"), VALID_FILTER_ZONES, DEFAULT_FILTER_ZONE
        ),
        filter_target=_sanitize_choice(
            raw.get("filter_target"), VALID_FILTER_TARGETS, DEFAULT_FILTER_TARGET
        ),
        theme_mode=_sanitize_choice(
            raw.get("theme_mode"), VALID_THEME_MODES, DEFAULT_THEME_MODE
        ),
        window_geometry=str(raw.get("window_geometry", "") or ""),
        table_header_state=str(raw.get("table_header_state", "") or ""),
    )


def save_app_state(
    state: AppState,
    *,
    sync_rootdir: bool = False,
) -> None:
    """통합 설정 파일(data/config.json)을 저장하고 필요 시 .rootdir을 동시 갱신"""
    if sync_rootdir:
        config_mod.set_root_dir(state.root_dir)

    state_path = get_state_path()
    parent_dir = os.path.dirname(state_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
        f.write("\n")


def reset_app_state(
    *,
    preserve_root_dir: bool = True,
    preserve_recent_dirs: bool = False,
    preserve_layout: bool = True,
) -> AppState:
    """모든 분석 설정과 GUI 필터·예외 상태를 기본값으로 복원하고 영구 저장"""
    current = load_app_state()
    fresh = AppState(
        config=ClassifierConfig(),
        root_dir=current.root_dir if preserve_root_dir else DEFAULT_ROOT_DIR,
        recent_dirs=list(current.recent_dirs) if preserve_recent_dirs else [],
        window_geometry=current.window_geometry if preserve_layout else "",
        table_header_state=current.table_header_state if preserve_layout else "",
    )
    save_app_state(
        fresh,
        sync_rootdir=not preserve_root_dir,
    )
    return fresh
