"""
- 하위 호환성을 위해 유지되는 모듈입니다.
- 신규 코드는 `src.cui.cui_printer` 사용을 권장합니다.
"""
from src.cui.cui_printer import (
    CUIPrinter,
    SortKey,
    TablePrinter,
    sanitize_text,
    truncate_text,
)

__all__ = [
    "CUIPrinter",
    "SortKey",
    "TablePrinter",
    "sanitize_text",
    "truncate_text",
]
