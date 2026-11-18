"""분석 캐시 행의 측정 버전별 재사용 판정 규칙(순수 함수)

- 버전이 오를 때마다 "어떤 이전 행을 다시 읽어야 하는지"를 여기서 정의하고, 해당하지 않는 행은 번호만 올려 그대로 씀
- 규칙이 없는 너무 오래된 번호는 이전 형식으로 표시만 하고 분석에는 쓰지 않음
"""

import os

from src.core.probe_parser import PROBE_VERSION

ROW_CURRENT: str = "current"
ROW_UPGRADED: str = "upgraded"
ROW_REREAD: str = "reread"
ROW_LEGACY: str = "legacy"
REUSABLE_ROW_ACTIONS: frozenset[str] = frozenset({ROW_CURRENT, ROW_UPGRADED})
OLDEST_REUSABLE_VERSION: int = 3
DURATION_FIX_VERSION: int = 5
DURATION_FIX_CONTAINER_EXTENSIONS: frozenset[str] = frozenset({"mkv", "webm"})
KEYFRAME_MEASURE_VERSION: int = 1


def row_reuse_action(stored_version: int, filename: str, current_version: int = PROBE_VERSION) -> str:
    """저장된 행의 측정 버전과 파일 이름으로 현재 분석에서 쓸 방식을 판정

    - 3에서 4: 처리 표식 값이 없던 행은 열 기본값(표식 없음)으로 번호만 올림
    - 4에서 5: 영상 스트림 길이 함수가 바뀌어 컨테이너 길이에 기대던 MKV, WebM 행만 다시 읽음
    - 3 미만이거나 현재 번호보다 큰 행은 이전 형식으로 분류
    """
    if stored_version == current_version:
        return ROW_CURRENT
    if stored_version < OLDEST_REUSABLE_VERSION or stored_version > current_version:
        return ROW_LEGACY
    extension = os.path.splitext(filename)[1].lstrip(".").lower()
    if stored_version < DURATION_FIX_VERSION <= current_version and extension in DURATION_FIX_CONTAINER_EXTENSIONS:
        return ROW_REREAD
    return ROW_UPGRADED
