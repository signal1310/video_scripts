"""통합 설정, 상태(data/*) 영속화, 기본 작업 경로 조회 및 기본 노트북 생성 모듈"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import src.core.paths as paths_mod
from src.core.config import PathsConfig
from src.core.exceptions import DomainFileNotFoundError, DomainRuntimeError
from src.core.paths import DATA_DIR_NAME, DEFAULT_ROOT_DIR, get_data_dir
from src.core.settings.state_codec import (
    MAX_RECENT_DIRS,
    AppState,
    parse_state_mapping,
    render_state_jsonc,
    strip_jsonc_comments,
)
from src.core.settings.ui_state import UiLayoutState, UiState
from src.core.workspace_paths import parse_root_dirs

CONFIG_FILENAME = "config.json"
NOTEBOOK_FILENAME = "classify.ipynb"

__all__ = [
    "CONFIG_FILENAME",
    "DATA_DIR_NAME",
    "MAX_RECENT_DIRS",
    "NOTEBOOK_FILENAME",
    "AppState",
    "build_default_notebook_payload",
    "ensure_default_config_files",
    "ensure_default_notebook_file",
    "get_data_dir",
    "get_notebook_path",
    "get_state_path",
    "load_app_state",
    "record_recent_dir",
    "record_recent_roots",
    "reset_app_state",
    "resolve_effective_root_dir",
    "save_app_state",
]


def _markdown_cell(source: list[str]) -> dict[str, Any]:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source,
    }


def _code_cell(source: list[str]) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def build_default_notebook_payload() -> dict[str, Any]:
    """초기 실행 시 생성할 기본 Jupyter 노트북(classify.ipynb) nbformat 딕셔너리 반환"""
    return {
        "cells": [
            _markdown_cell([
                "### 1. 화질 기준값 기반 영상 분석과 인코딩 판정 조회 (화질 3구간 + 1초 키프레임 + 분당 절약 선별)\n",
                "- 작업 폴더의 영상을 분석해 권장 처리를 표로 출력합니다. 파일은 이동하거나 변경하지 않습니다.\n",
                "- 권장 처리는 원본 해상도 품질 인코딩(과화질, 키프레임 초과 적정), 저화질 프리셋 품질 인코딩(저화질 키프레임 초과), 축소 재인코딩 판정이며, 그 밖의 영상은 원본을 보존합니다. 적용 품질값은 품질값 열에 표시합니다.",
            ]),
            _code_cell([
                "from src import *\n",
                "classifier = VideoClassifier()\n",
                "classifier.add_exception_rule(lambda v: 'keep_origin' in v.filename)\n",
                "# 필요 시 과화질 경계 배수 즉시 변경 가능 (기본값 1.2, 720p 기준 HEVC 출력은 1440kbps 초과)\n",
                "# classifier.set_over_bitrate_ratio(1.2)\n",
                "\n",
                "classifier.print(sort_by=\"saved\", limit=30)\n",
                "# 요약만 출력: classifier.print(summary_only=True)\n",
                "# 복합 정렬: classifier.print(sort_key=lambda c: [col(c[t['예상 절약']], desc), col(WindowsNameOrder(c[t['이름']]))])",
            ]),
            _markdown_cell(["### 2. 키프레임 간격까지 포함해 전체 영상 정보 조회"]),
            _code_cell([
                "from src import *\n",
                "classifier = VideoClassifier()\n",
                "classifier.set_filename_max_length(80) ## 필요한 경우 파일 이름 열의 너비를 즉시 조정\n",
                "classifier.include_keyframe_interval() ## 키프레임 간격까지 확인하려면 포함 (생략 시 고속 분석)\n",
                "\n",
                "classifier.print(limit=50)",
            ]),
        ],
        "metadata": {
            "kernelspec": {
                "display_name": ".venv (3.10.11)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "codemirror_mode": {
                    "name": "ipython",
                    "version": 3,
                },
                "file_extension": ".py",
                "mimetype": "text/x-python",
                "name": "python",
                "nbconvert_exporter": "python",
                "pygments_lexer": "ipython3",
                "version": "3.10.11",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 2,
    }


def get_notebook_path() -> str:
    """실행 루트의 기본 Jupyter 노트북(classify.ipynb) 절대 경로 반환"""
    return os.path.join(paths_mod.get_app_base_dir(), NOTEBOOK_FILENAME)


def ensure_default_notebook_file() -> str:
    """classify.ipynb가 없으면 기본 템플릿으로 자동 생성하고 경로 반환"""
    return paths_mod.ensure_utf8_file(
        get_notebook_path(),
        lambda: json.dumps(
            build_default_notebook_payload(), ensure_ascii=False, indent=1
        )
        + "\n",
    )


def get_state_path() -> str:
    """통합 설정, 상태 JSONC 파일(data/config.json) 절대 경로 반환"""
    return os.path.join(paths_mod.get_data_dir(), CONFIG_FILENAME)


def resolve_effective_root_dir() -> str:
    """data/rootdir.txt 기준 정의에서 유효한 기본 작업 폴더 경로 반환"""
    try:
        return paths_mod.get_root_dir().strip()
    except (DomainFileNotFoundError, DomainRuntimeError):
        logging.getLogger(__name__).debug("data/rootdir.txt 조회 실패로 빈 경로 반환")
    return ""


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


def record_recent_roots(recent_dirs: list[str], root_spec: str) -> list[str]:
    """작업 폴더 목록의 각 폴더를 최근 목록에 기록하고 주 작업 폴더가 맨 앞에 오도록 역순 기록"""
    updated = recent_dirs
    for path in reversed(parse_root_dirs(root_spec)):
        updated = record_recent_dir(updated, path)
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
    """data/* 및 classify.ipynb가 없으면 기본값 파일로 자동 생성하고 현재 AppState 반환"""
    return load_app_state(create_missing=True)


def load_app_state(*, create_missing: bool = False) -> AppState:
    """통합 설정, 경로(data/*) 계층에서 유효한 AppState 로드"""
    if create_missing:
        paths_mod.ensure_default_rootdir_file()
        ensure_default_notebook_file()

    state_path = get_state_path()
    if not os.path.exists(state_path):
        initial_state = AppState()
        if create_missing:
            save_app_state(initial_state)
        return initial_state

    raw = _read_raw_state_json(state_path)
    if raw is None:
        return AppState()

    return parse_state_mapping(raw)


def save_app_state(
    state: AppState,
    *,
    root_dir: str | None = None,
) -> None:
    """통합 설정(data/config.json)을 저장하고 root_dir 지정 시 data/rootdir.txt를 함께 갱신"""
    if root_dir is not None:
        paths_mod.set_root_dir(root_dir)

    paths_mod.write_utf8_file(get_state_path(), render_state_jsonc(state))


def reset_app_state(
    *,
    preserve_root_dir: bool = True,
    preserve_recent_dirs: bool = False,
    preserve_layout: bool = True,
) -> AppState:
    """모든 분석 설정과 GUI 필터, 예외 상태를 기본값으로 복원하고 영구 저장"""
    current = load_app_state()
    fresh = AppState(
        paths=PathsConfig(
            recent_dirs=list(current.paths.recent_dirs) if preserve_recent_dirs else [],
        ),
        ui=UiState(
            layout=UiLayoutState(
                window_geometry=current.ui.layout.window_geometry if preserve_layout else "",
                table_header_state=(
                    current.ui.layout.table_header_state if preserve_layout else ""
                ),
                settings_tab=current.ui.layout.settings_tab if preserve_layout else 0,
                help_tab=current.ui.layout.help_tab if preserve_layout else 0,
                top_area_collapsed=(
                    current.ui.layout.top_area_collapsed if preserve_layout else False
                ),
                table_top_filename=(
                    current.ui.layout.table_top_filename if preserve_layout else ""
                ),
                table_top_row=current.ui.layout.table_top_row if preserve_layout else 0,
                table_left_scroll=(
                    current.ui.layout.table_left_scroll if preserve_layout else 0
                ),
                table_current_filename=(
                    current.ui.layout.table_current_filename if preserve_layout else ""
                ),
            ),
        ),
    )
    save_app_state(
        fresh,
        root_dir=None if preserve_root_dir else DEFAULT_ROOT_DIR,
    )
    return fresh
