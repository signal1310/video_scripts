"""개발자 모드 해석과 설정 변경 기록 모듈

- 저장 값과 환경 변수 가운데 하나라도 켜져 있으면 개발자 모드로 동작
- 설정이 바뀐 항목만 한 줄씩 남겨 어떤 값이 언제 바뀌었는지 사후에 확인
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from src.core.config import ClassifierConfig

DEV_MODE_ENV: str = "VIDEO_SCRIPTS_DEV"
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
DIAGNOSTICS_LOGGER_NAME: str = "src.diagnostics"
CHANGE_LINE_TEMPLATE: str = "{key}: {before} -> {after}"
UNKNOWN_VALUE: str = "없음"


def env_flag_enabled(value: str | None) -> bool:
    """환경 변수 값이 켜짐을 뜻하는 표기인지 판정"""
    return value is not None and value.strip().lower() in TRUTHY_VALUES


def resolve_developer_mode(
    configured: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """저장 설정과 환경 변수에서 최종 개발자 모드 여부 결정

    - 환경 변수는 실행 때만 켜는 임시 수단이므로 저장 값을 덮어쓰지 않음
    """
    source = os.environ if environ is None else environ
    return bool(configured) or env_flag_enabled(source.get(DEV_MODE_ENV))


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    """데이터클래스 값을 점으로 이어진 키 사전으로 펼침"""
    if not is_dataclass(value) or isinstance(value, type):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for item in fields(value):
        child = f"{prefix}.{item.name}" if prefix else item.name
        flat.update(_flatten(child, getattr(value, item.name)))
    return flat


def collect_setting_changes(
    before: ClassifierConfig,
    after: ClassifierConfig,
) -> list[str]:
    """두 설정의 차이를 `키: 이전 -> 이후` 문구 목록으로 반환(변경 없으면 빈 목록)"""
    old = _flatten("", before)
    new = _flatten("", after)
    changes: list[str] = []
    for key in sorted(new):
        previous = old.get(key, UNKNOWN_VALUE)
        current = new[key]
        if previous != current:
            changes.append(CHANGE_LINE_TEMPLATE.format(key=key, before=previous, after=current))
    return changes


def log_setting_changes(
    before: ClassifierConfig,
    after: ClassifierConfig,
    *,
    developer_mode: bool,
    logger: logging.Logger | None = None,
) -> list[str]:
    """설정 변경을 DEBUG로 남기고 남긴 문구 목록 반환. 개발자 모드가 아니면 기록하지 않음"""
    if not developer_mode:
        return []
    changes = collect_setting_changes(before, after)
    target = logger or logging.getLogger(DIAGNOSTICS_LOGGER_NAME)
    for line in changes:
        target.debug("설정 변경 %s", line)
    return changes
