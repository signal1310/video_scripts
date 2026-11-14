"""통합 설정·GUI 상태 JSONC 영속화(data/config.json), .rootdir 우선순위 및 기본 노트북 생성 모듈"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import src.core.config as config_mod
from src.core.config import (
    DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
    DEFAULT_DIR_STANDARD_CQ22,
    DEFAULT_ROOT_DIR,
    PathsConfig,
    UiLayoutState,
    UiState,
)
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
                "### 1. 퀄리티(CQ) 기반 통합 영상 분류 (화질 3구간 + 1초 키프레임 + 소형 영상 분포 자동 판단)\n",
                f"- **`{DEFAULT_DIR_STANDARD_CQ22}`**: 과화질 영상($r > 1.2$, 720p H.264 기준 1800kbps 초과) 및 적정화질이면서 키프레임만 불량($> 2.2$초)인 영상 (원본 해상도·비율 유지 + `CQ 22` + `1초 키프레임` 인코딩)\n",
                f"- **`{DEFAULT_DIR_LOW_QUALITY_KEYFRAME}`**: 저화질($r < 0.8$)이면서 키프레임이 불량($> 2.2$초)인 영상 (노이즈 증폭 방지 + `1초 키프레임` 인코딩)\n",
                "- **루트 경로 잔류 (원본 보존)**: 키프레임이 정상인 적정/저화질 영상 및 절감 효과가 미미한 소형 영상",
            ]),
            _code_cell([
                "from src import *\n",
                "classifier = VideoClassifier()\n",
                "classifier.add_exception_rule(lambda v: 'keep_origin' in v.filename)\n",
                "# 필요 시 과화질 경계 배수 즉시 변경 가능 (기본값 1.2 -> 720p H.264 기준 1800kbps)\n",
                "# classifier.set_over_bitrate_ratio(1.2)\n",
                "classifier.pseudo_classify_mode() # 실제로 분류(폴더 이동)하려면 주석 처리 후 실행\n",
                "\n",
                "classifier.classify()\n",
                "classifier.print(sanitize_emoji=True, sort_key=lambda c: [\n",
                "    col(c[t['예상 절감']], desc),\n",
                "    col(c[t['b-rate 비율']], desc),\n",
                "    col(WindowsNameOrder(c[t['이름']])),\n",
                "])",
            ]),
            _markdown_cell(["### 2. 분류 없이 전체 영상 정보만 조회"]),
            _code_cell([
                "from src import *\n",
                "classifier = VideoClassifier()\n",
                "classifier.set_filename_max_length(80) ## 필요한 경우 파일 이름 열의 너비를 즉시 조정\n",
                "classifier.include_keyframe_interval() ## 키프레임 간격까지 확인하려면 포함 (생략 시 고속 스캔)\n",
                "\n",
                "classifier.print(sanitize_emoji=True, sort_key=lambda c: [\n",
                "    col(WindowsNameOrder(c[t['이름']])),\n",
                "])",
            ]),
            _markdown_cell(["### 3. 분류된 영상 다시 작업 경로로 모으기"]),
            _code_cell([
                "from src import *\n",
                "classifier = VideoClassifier()\n",
                "\n",
                "classifier.unclassify_files()",
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
    return os.path.join(config_mod.get_app_base_dir(), NOTEBOOK_FILENAME)


def ensure_default_notebook_file() -> str:
    """classify.ipynb가 없으면 기본 템플릿으로 자동 생성하고 경로 반환"""
    notebook_path = get_notebook_path()
    if not os.path.exists(notebook_path):
        parent_dir = os.path.dirname(notebook_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        rendered = json.dumps(
            build_default_notebook_payload(), ensure_ascii=False, indent=1
        )
        with open(notebook_path, "w", encoding="utf-8") as f:
            f.write(rendered + "\n")
    return notebook_path


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
    """.rootdir, data/config.json, classify.ipynb가 없으면 기본값 파일로 자동 생성하고 현재 AppState 반환"""
    return load_app_state(create_missing=True)


def load_app_state(*, create_missing: bool = True) -> AppState:
    """통합 설정 파일(data/config.json)과 .rootdir 계층에서 유효한 AppState 로드"""
    if create_missing:
        config_mod.ensure_default_rootdir_file()
        ensure_default_notebook_file()

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
