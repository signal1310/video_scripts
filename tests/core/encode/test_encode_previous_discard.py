"""이전 결과 폐기 기록 판정 검증: 같은 원본과 인코더, 화질 기준값은 제외, 원본이나 인코더, 화질 기준값이 다르거나 직접 지정이거나 키프레임 초과면 대상, 기록이 없으면 대상"""

import unittest

from src.core.encode.encode_previous_discard import DiscardFact, is_previously_discarded

_SIZE: int = 1000
_MTIME: int = 5_000
_ENCODER: str = "hevc_nvenc"
_QUALITY: float = 22.0


class IsPreviouslyDiscardedTests(unittest.TestCase):
    """이전 폐기 기록과 이번 원본, 인코더, 화질 기준값을 비교해 재인코딩 대상에서 뺄지 정함"""

    def setUp(self) -> None:
        self.fact = DiscardFact(_SIZE, _MTIME, _ENCODER, _QUALITY)

    def _discarded(
        self,
        *,
        size: int = _SIZE,
        mtime: int = _MTIME,
        manual: str | None = None,
        encoder: str = _ENCODER,
        quality: float = _QUALITY,
        keyframe: bool = False,
    ) -> bool:
        return is_previously_discarded(
            size, mtime, self.fact, manual, encoder=encoder, quality=quality, keyframe_exceeded=keyframe
        )

    def test_same_source_encoder_and_quality_is_excluded(self) -> None:
        self.assertTrue(self._discarded())

    def test_changed_size_or_mtime_makes_the_source_a_target_again(self) -> None:
        self.assertFalse(self._discarded(size=_SIZE + 1))
        self.assertFalse(self._discarded(mtime=_MTIME + 1))

    def test_changed_encoder_or_quality_makes_the_source_a_target_again(self) -> None:
        self.assertFalse(self._discarded(encoder="h264_nvenc"))
        self.assertFalse(self._discarded(quality=_QUALITY + 4.0))

    def test_manual_target_is_a_target_even_with_the_same_source(self) -> None:
        self.assertFalse(self._discarded(manual="1080p"))
        self.assertFalse(self._discarded(manual="축소 안 함"))

    def test_keyframe_exceeded_source_is_a_target_even_with_the_same_source(self) -> None:
        """키프레임 초과 영상은 교정 목적이라 폐기 기록과 같아도 대상에 남김"""
        self.assertFalse(self._discarded(keyframe=True))

    def test_no_record_is_a_target(self) -> None:
        self.assertFalse(
            is_previously_discarded(_SIZE, _MTIME, None, None, encoder=_ENCODER, quality=_QUALITY, keyframe_exceeded=False)
        )


if __name__ == "__main__":
    unittest.main()
