"""사후 게이트 판정: 무결성, 처리 표식, 절약 목적 크기 기준과 목적별 분류 검증"""

import unittest

from src.core.encode_gate import (
    GatePurpose,
    GateVerdict,
    OutputSummary,
    duration_tolerance_sec,
    judge_result,
    purpose_for_action,
    summarize_probe,
)
from src.core.smart_action import SmartAction

_SOURCE_DURATION = 600.0
_SOURCE_SIZE = 1_000_000


def _judge(output: OutputSummary | None, *, size: int = 500_000, purpose: GatePurpose = GatePurpose.SAVING) -> GateVerdict:
    return judge_result(
        output=output,
        expected_stream_count=2,
        source_duration_sec=_SOURCE_DURATION,
        source_size_bytes=_SOURCE_SIZE,
        output_size_bytes=size,
        purpose=purpose,
    )


def _ok(*, stream_count: int = 2, duration_sec: float = _SOURCE_DURATION, has_encode_tag: bool = True) -> OutputSummary:
    return OutputSummary(stream_count=stream_count, duration_sec=duration_sec, has_encode_tag=has_encode_tag)


class TestIntegrityGate(unittest.TestCase):
    """출력 probe 실패, 스트림 수, 길이 차 기준"""

    def test_passing_output_is_accepted(self) -> None:
        self.assertIs(_judge(_ok()), GateVerdict.PASS)

    def test_failed_probe_is_integrity_failure(self) -> None:
        self.assertIs(_judge(None), GateVerdict.FAIL_INTEGRITY)

    def test_stream_count_mismatch_is_integrity_failure(self) -> None:
        self.assertIs(_judge(_ok(stream_count=1)), GateVerdict.FAIL_INTEGRITY)

    def test_duration_tolerance_uses_larger_of_half_second_and_half_percent(self) -> None:
        """짧은 원본은 0.5초, 긴 원본은 원본 길이의 0.5%가 허용 한계"""
        self.assertAlmostEqual(duration_tolerance_sec(10.0), 0.5)
        self.assertAlmostEqual(duration_tolerance_sec(7200.0), 36.0)
        self.assertIs(_judge(_ok(duration_sec=_SOURCE_DURATION + 2.9)), GateVerdict.PASS)
        self.assertIs(_judge(_ok(duration_sec=_SOURCE_DURATION + 3.1)), GateVerdict.FAIL_INTEGRITY)


class TestTagGate(unittest.TestCase):
    """처리 표식이 빠진 결과는 크기와 무관하게 배치하지 않음(AGENTS 5절 재처리 방지)"""

    def test_missing_tag_fails_even_when_smaller(self) -> None:
        self.assertIs(_judge(_ok(has_encode_tag=False), size=100), GateVerdict.FAIL_TAG)

    def test_tag_check_runs_after_integrity_check(self) -> None:
        self.assertIs(_judge(_ok(stream_count=1, has_encode_tag=False)), GateVerdict.FAIL_INTEGRITY)


class TestSizeGate(unittest.TestCase):
    """절약 목적만 크기 기준을 적용하고, 키프레임 교정과 호환 목적은 무결성만 검사"""

    def test_larger_saving_result_is_discarded(self) -> None:
        self.assertIs(_judge(_ok(), size=_SOURCE_SIZE), GateVerdict.DISCARD_LARGER)
        self.assertIs(_judge(_ok(), size=_SOURCE_SIZE + 1), GateVerdict.DISCARD_LARGER)

    def test_smaller_saving_result_passes(self) -> None:
        self.assertIs(_judge(_ok(), size=_SOURCE_SIZE - 1), GateVerdict.PASS)

    def test_keyframe_fix_and_compat_ignore_size(self) -> None:
        self.assertIs(_judge(_ok(), size=_SOURCE_SIZE * 2, purpose=GatePurpose.KEYFRAME_FIX), GateVerdict.PASS)
        self.assertIs(_judge(_ok(), size=_SOURCE_SIZE * 2, purpose=GatePurpose.COMPAT), GateVerdict.PASS)


class TestPurposeMapping(unittest.TestCase):
    """권장 처리 종류별 게이트 목적(결정 10)"""

    def test_keyframe_actions_map_to_keyframe_fix(self) -> None:
        self.assertIs(purpose_for_action(SmartAction.ENCODE_OPT_KF), GatePurpose.KEYFRAME_FIX)
        self.assertIs(purpose_for_action(SmartAction.ENCODE_LOW_KF), GatePurpose.KEYFRAME_FIX)

    def test_compat_action_maps_to_compat(self) -> None:
        self.assertIs(purpose_for_action(SmartAction.ENCODE_COMPAT), GatePurpose.COMPAT)

    def test_savings_actions_map_to_saving(self) -> None:
        for action in (SmartAction.ENCODE_OVER, SmartAction.ENCODE_CODEC_UPGRADE, SmartAction.ENCODE_DOWNSCALE):
            with self.subTest(action=action):
                self.assertIs(purpose_for_action(action), GatePurpose.SAVING)


class TestSummarizeProbe(unittest.TestCase):
    """ffprobe JSON 요약: 스트림 수, 길이, 컨테이너 태그(대소문자 무관)"""

    def test_summary_reads_streams_duration_and_tag(self) -> None:
        probe = {
            "streams": [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}],
            "format": {"duration": "600.0", "tags": {"VIDEO_SCRIPTS_ENCODE": "v=1;enc=libx264;q=22.0"}},
        }
        summary = summarize_probe(probe)
        self.assertEqual((summary.stream_count, summary.duration_sec, summary.has_encode_tag), (2, 600.0, True))

    def test_missing_pieces_are_treated_as_empty(self) -> None:
        summary = summarize_probe({"streams": "broken", "format": {"tags": {"comment": "x"}}})
        self.assertEqual((summary.stream_count, summary.duration_sec, summary.has_encode_tag), (0, 0.0, False))
        self.assertFalse(summarize_probe({}).has_encode_tag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
