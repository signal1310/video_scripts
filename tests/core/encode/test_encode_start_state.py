"""재인코딩 시작 항목의 활성 조건과 비활성 사유 순서를 검증(순수 함수, FFmpeg 없이 동작)"""

import unittest

from src.core.encode.encode_start_state import (
    ANALYSIS_BUSY_TEXT,
    BATCH_FINISHED_TEXT,
    ENCODE_RUNNING_TEXT,
    ENCODER_CHECKING_TEXT,
    FFMPEG_UNUSABLE_PREFIX,
    NO_TARGET_TEXT,
    StartState,
    evaluate_start_state,
)
from src.core.encode.encoder_trial import NOT_LISTED_REASON, TRIAL_FAILED_REASON, EncoderTrialResult
from src.core.encode.ffmpeg_capability import FfmpegCapability

_USABLE = FfmpegCapability("ffmpeg", "ffmpeg version 6.1", (6, 1), usable=True)
_UNUSABLE = FfmpegCapability("ffmpeg", "", None, usable=False, reason="찾을 수 없음")
_ENCODER_OK = EncoderTrialResult("libx264", usable=True, reason="")


def _state(
    *,
    analysis_busy: bool = False,
    encode_running: bool = False,
    has_targets: bool = True,
    capability: FfmpegCapability = _USABLE,
    encoder: EncoderTrialResult | None = _ENCODER_OK,
    has_batch: bool = False,
) -> StartState:
    return evaluate_start_state(
        analysis_busy=analysis_busy,
        encode_running=encode_running,
        has_targets=has_targets,
        capability=capability,
        encoder=encoder,
        has_batch=has_batch,
    )


class EvaluateStartStateTests(unittest.TestCase):
    """조건별 활성 여부와 사유 문구, 검사 순서를 검증"""

    def test_everything_ready_enables_the_item_without_reason(self) -> None:
        """모든 조건이 갖춰지면 활성이고 사유가 없는지 검증"""
        state = _state()
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, "")
        self.assertFalse(state.pending)

    def test_running_analysis_gives_the_analysis_reason(self) -> None:
        """분석 중이면 분석이 끝난 뒤 시작하라는 사유를 주는지 검증"""
        state = _state(analysis_busy=True)
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, ANALYSIS_BUSY_TEXT)

    def test_running_encode_keeps_the_item_enabled_and_gives_the_window_reason(self) -> None:
        """재인코딩이 진행 중이면 창을 열 수 있게 활성으로 두고 창 열기 사유를 주는지 검증"""
        state = _state(encode_running=True)
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, ENCODE_RUNNING_TEXT)

    def test_no_target_gives_the_no_target_reason(self) -> None:
        """재인코딩 대상이 없으면 대상 없음 사유를 주는지 검증"""
        state = _state(has_targets=False)
        self.assertEqual(state.reason, NO_TARGET_TEXT)

    def test_no_target_with_a_finished_batch_stays_enabled_to_reopen_the_window(self) -> None:
        """대상이 없어도 끝난 배치가 있으면 결과 확인을 위해 활성으로 두는지 검증"""
        state = _state(has_targets=False, has_batch=True)
        self.assertTrue(state.enabled)
        self.assertEqual(state.reason, BATCH_FINISHED_TEXT)
        self.assertFalse(state.pending)

    def test_batch_flag_does_not_override_analysis_busy(self) -> None:
        """끝난 배치가 있어도 분석 중에는 분석 사유로 막는지 검증"""
        state = _state(analysis_busy=True, has_targets=False, has_batch=True)
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, ANALYSIS_BUSY_TEXT)

    def test_unusable_ffmpeg_gives_its_reason_after_the_prefix(self) -> None:
        """FFmpeg를 쓸 수 없으면 FFmpeg 사유를 접두 문구 뒤에 붙이는지 검증"""
        state = _state(capability=_UNUSABLE, encoder=None)
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, f"{FFMPEG_UNUSABLE_PREFIX}찾을 수 없음")

    def test_pending_encoder_trial_is_checking_and_asks_for_a_recheck(self) -> None:
        """인코더 시험이 끝나지 않으면 확인하는 중 문구를 보이고 다시 확인이 필요하다고 표시하는지 검증"""
        state = _state(encoder=None)
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, ENCODER_CHECKING_TEXT)
        self.assertEqual(state.reason, "인코더를 확인하는 중입니다.")
        self.assertTrue(state.pending)

    def test_encoder_failure_gives_the_trial_reason(self) -> None:
        """인코더 시험이 실패하면 시험 결과의 사유 문구를 그대로 주는지 검증"""
        failed = EncoderTrialResult("hevc_nvenc", usable=False, reason=TRIAL_FAILED_REASON)
        state = _state(encoder=failed)
        self.assertEqual(state.reason, TRIAL_FAILED_REASON)

    def test_missing_encoder_gives_the_listing_reason(self) -> None:
        """FFmpeg 인코더 목록에 없으면 목록 사유를 주는지 검증"""
        missing = EncoderTrialResult("av1_nvenc", usable=False, reason=NOT_LISTED_REASON)
        state = _state(encoder=missing)
        self.assertEqual(state.reason, NOT_LISTED_REASON)

    def test_analysis_is_checked_before_the_other_conditions(self) -> None:
        """여러 조건이 동시에 막혀도 승인 순서대로 첫 사유(분석 중)를 주는지 검증"""
        state = _state(analysis_busy=True, encode_running=True, has_targets=False, capability=_UNUSABLE)
        self.assertEqual(state.reason, ANALYSIS_BUSY_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
