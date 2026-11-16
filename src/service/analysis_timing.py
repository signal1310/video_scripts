"""분석 모드별 항목당 소요 시간 이력을 data 폴더에 기록하고 예상 분석 시간을 산출하는 모듈

- 최근 HISTORY_LIMIT회 평균으로 예상하며, 이력이 없으면 예상값을 만들지 않음
- 읽기, 쓰기 실패는 경고 로그만 남기고 예상 기능만 비활성화(분석 결과에는 영향 없음)
"""

import json
import logging
import os
from dataclasses import dataclass

from src.core.paths import get_data_dir, write_utf8_file

logger = logging.getLogger(__name__)

TIMING_FILENAME: str = "analysis_timing.json"
HISTORY_LIMIT: int = 10
MODE_FAST: str = "fast"
MODE_FULL: str = "full"


@dataclass(frozen=True, slots=True)
class AnalysisEstimate:
    """분석 시작 전 예상값: 프로빙이 필요한 영상 수와 예상 소요 초(이력이 없으면 None)"""

    pending_count: int
    expected_sec: float | None


def _timing_path() -> str:
    return os.path.join(get_data_dir(), TIMING_FILENAME)


def _load_history() -> dict[str, list[float]]:
    path = _timing_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            loaded: object = json.load(f)
    except (OSError, ValueError):
        logger.warning("분석 시간 이력 읽기 실패로 빈 이력을 사용합니다: %s", path, exc_info=True)
        return {}
    if not isinstance(loaded, dict):
        return {}
    history: dict[str, list[float]] = {}
    for mode, values in loaded.items():
        if isinstance(values, list):
            history[mode] = [
                float(v) for v in values if isinstance(v, (int, float)) and v > 0
            ]
    return history


def average_seconds_per_item(mode: str) -> float | None:
    """모드별 최근 항목당 소요 초의 평균, 이력이 없으면 None"""
    values = _load_history().get(mode, [])
    if not values:
        return None
    return sum(values) / len(values)


def record_seconds_per_item(mode: str, seconds_per_item: float | None) -> None:
    """완료된 분석의 항목당 소요 초를 모드별 이력 끝에 추가하고 최근 HISTORY_LIMIT회만 유지"""
    if seconds_per_item is None or seconds_per_item <= 0:
        return
    history = _load_history()
    history[mode] = [*history.get(mode, []), seconds_per_item][-HISTORY_LIMIT:]
    try:
        write_utf8_file(_timing_path(), json.dumps(history, ensure_ascii=False, indent=1))
    except OSError:
        logger.warning("분석 시간 이력 저장 실패", exc_info=True)


def expected_analysis_seconds(mode: str, pending_count: int) -> float | None:
    """프로빙 대상 수와 모드별 평균으로 예상 소요 초 산출

    - 대상이 없으면 이력과 무관하게 0 반환
    """
    if pending_count <= 0:
        return 0.0
    per_item = average_seconds_per_item(mode)
    return None if per_item is None else per_item * pending_count
