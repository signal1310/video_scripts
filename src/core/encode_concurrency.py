"""동시 재인코딩 수 계산 모듈(4.5절)

- 설정 값 1 이상은 그대로 쓰고(상한 적용), 0은 인코더 종류별 자동 값을 씀
- 자동 값은 샤나 자동 동작(NVENC 3, x265 2)에서 가져온 초기값이며 4단계 실측 후 보정
"""

from src.core.config import CONCURRENT_JOBS_MAX

AUTO_JOBS_HARDWARE: int = 3
AUTO_JOBS_CPU: int = 2


def resolve_concurrency(configured: int, *, is_hardware: bool, cpu_count: int) -> int:
    """설정과 인코더 종류, 논리 코어 수로 실제 동시 실행 수를 정함(최소 1)"""
    if configured > 0:
        return min(configured, CONCURRENT_JOBS_MAX)
    automatic = AUTO_JOBS_HARDWARE if is_hardware else AUTO_JOBS_CPU
    return max(1, min(automatic, cpu_count))
