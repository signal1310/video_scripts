"""
- 하위 호환성을 위해 유지되는 모듈입니다.
- 신규 코드는 `src.classifier` 사용을 권장합니다.
"""
from src.classifier import VideoClassifier

__all__ = ["VideoClassifier"]
