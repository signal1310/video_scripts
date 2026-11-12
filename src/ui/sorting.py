"""
- 하위 호환성을 위해 유지되는 모듈입니다.
- 신규 코드는 `src.cui.sorting` 사용을 권장합니다.
"""
from src.cui.sorting import WindowsNameOrder, col, desc, t

__all__ = ["WindowsNameOrder", "col", "desc", "t"]
