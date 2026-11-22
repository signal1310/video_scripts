"""인코더 크기 보정을 뺀 기준 판정 모델로 고정하는 테스트 도우미

- 정책 체인, 절약 선별, 집계, 표 표시처럼 보정과 무관한 로직의 기존 기대값을 유지하려고 씀
- 보정 반영 결과는 판정 코어의 `TestEncoderCalibration`이 따로 검증
"""

import unittest
from unittest import mock


def _legacy_quality_size_factor(delta_cq: float, *, encoder: str | None = None) -> float:
    """보정 전 기준 모델(K 6.0, 인코더 계수 없음)의 용량 계수"""
    return 2.0 ** (-delta_cq / 6.0)


def use_legacy_size_model(case: unittest.TestCase) -> None:
    """테스트 케이스에 기준 모델 패치를 걸고 정리 작업을 등록"""
    factor = mock.patch(
        "src.core.evaluation.size_estimator.encoder_size_factor", return_value=1.0
    )
    factor.start()
    case.addCleanup(factor.stop)
    quality = mock.patch(
        "src.core.evaluation.size_estimator.quality_size_factor",
        new=_legacy_quality_size_factor,
    )
    quality.start()
    case.addCleanup(quality.stop)
