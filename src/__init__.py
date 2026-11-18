"""video_scripts 공개 API 통합 export 패키지"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.classifier import VideoClassifier
    from src.core.config import (
        BitrateModelConfig,
        ClassifierConfig,
        CodecWeightsConfig,
        CuiConfig,
        DisplayConfig,
        SavingsConfig,
        ThresholdsConfig,
    )
    from src.core.messages import ColumnId
    from src.core.models import (
        QualityZone,
        SavingsSummary,
        SummaryReport,
        VideoEvaluation,
        VideoProps,
    )
    from src.core.smart_action import SmartAction
    from src.cui.sorting import (
        SortBy,
        SortTarget,
        WindowsNameOrder,
        col,
        desc,
        t,
    )
    from src.service.classifier_service import VideoClassifierService

_LAZY_EXPORTS: dict[str, str] = {
    "BitrateModelConfig": "src.core.config",
    "ClassifierConfig": "src.core.config",
    "CodecWeightsConfig": "src.core.config",
    "ColumnId": "src.core.messages",
    "CuiConfig": "src.core.config",
    "DisplayConfig": "src.core.config",
    "SavingsConfig": "src.core.config",
    "SavingsSummary": "src.core.models",
    "QualityZone": "src.core.models",
    "SmartAction": "src.core.smart_action",
    "SortBy": "src.cui.sorting",
    "SortTarget": "src.cui.sorting",
    "SummaryReport": "src.core.models",
    "ThresholdsConfig": "src.core.config",
    "VideoClassifier": "src.classifier",
    "VideoClassifierService": "src.service.classifier_service",
    "VideoEvaluation": "src.core.models",
    "VideoProps": "src.core.models",
    "WindowsNameOrder": "src.cui.sorting",
    "col": "src.cui.sorting",
    "desc": "src.cui.sorting",
    "t": "src.cui.sorting",
}


def __getattr__(name: str) -> object:
    """노트북 공개 심볼을 첫 접근 시점에 임포트해 GUI, CUI 기동 시 불필요한 로드를 방지"""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'src' has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


__all__ = [
    "BitrateModelConfig",
    "ClassifierConfig",
    "CodecWeightsConfig",
    "ColumnId",
    "CuiConfig",
    "DisplayConfig",
    "QualityZone",
    "SavingsConfig",
    "SavingsSummary",
    "SmartAction",
    "SortBy",
    "SortTarget",
    "SummaryReport",
    "ThresholdsConfig",
    "VideoClassifier",
    "VideoClassifierService",
    "VideoEvaluation",
    "VideoProps",
    "WindowsNameOrder",
    "col",
    "desc",
    "t",
]
