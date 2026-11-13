"""
- 하위 호환성을 위해 유지되는 모듈입니다.
- 신규 코드는 `from src import VideoClassifier` 또는 `from src.classifier import VideoClassifier` 사용을 권장합니다.
"""
from src import (
    ClassifierConfig,
    FootageStrategy,
    QualityZone,
    SmallFootageDistribution,
    SmartAction,
    SummaryReport,
    VideoClassifier,
    VideoClassifierService,
    VideoEvaluation,
    VideoProps,
    VideoRecord,
    WindowsNameOrder,
    col,
    desc,
    t,
)

__all__ = [
    "ClassifierConfig",
    "FootageStrategy",
    "QualityZone",
    "SmallFootageDistribution",
    "SmartAction",
    "SummaryReport",
    "VideoClassifier",
    "VideoClassifierService",
    "VideoEvaluation",
    "VideoProps",
    "VideoRecord",
    "WindowsNameOrder",
    "col",
    "desc",
    "t",
]
