"""결과 경로 예약: 같은 결과 경로를 여러 항목이 동시에 쓰지 않도록 항목 단위로 예약과 해제를 관리

- 워커 스레드에서 동시에 호출되므로 내부 잠금으로 보호
- 예약 키는 정규화한 경로(대소문자와 구분자 차이 무시)
"""

import os
import threading
from collections.abc import Sequence


class ResultPathClaims:
    """원본 경로별 결과 경로 예약 표"""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._claims: dict[str, tuple[str, ...]] = {}
        self._claimed: set[str] = set()

    def claim(self, source: str, keys: Sequence[str]) -> bool:
        """결과 경로를 이 항목이 쓰도록 예약. 다른 항목이 같은 경로를 쓰는 중이면 False"""
        normalized = tuple(os.path.normcase(os.path.normpath(key)) for key in keys)
        with self._lock:
            if any(key in self._claimed for key in normalized):
                return False
            self._claimed.update(normalized)
            self._claims[source] = normalized
            return True

    def release(self, source: str) -> None:
        """항목이 끝나면 결과 경로 예약을 풀어 다음 항목이 쓸 수 있게 함"""
        with self._lock:
            for key in self._claims.pop(source, ()):
                self._claimed.discard(key)
