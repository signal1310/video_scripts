"""사후 게이트 판정: 무결성, 처리 표식, 절약 목적 크기 기준과 목적별 분류 검증"""

import unittest

from src.core.encode.encode_gate import (
    GatePurpose,
    GateVerdict,
    OutputSummary,
    duration_tolerance_sec,
    judge_result,
    purpose_for_action,
    purpose_for_evaluation,
    summarize_probe,
)
from src.core.encode_tag import (
    DEFAULT_ENCODE_TAG_NAME,
    ENCODE_TAG_MODE_HIDDEN,
    ENCODE_TAG_MODE_OFF,
    ENCODE_TAG_MODE_VISIBLE,
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


def _ok(
    *,
    stream_count: int = 2,
    duration_sec: float = _SOURCE_DURATION,
    has_encode_tag: bool = True,
    keywords: tuple[str, ...] = (DEFAULT_ENCODE_TAG_NAME,),
) -> OutputSummary:
    return OutputSummary(
        stream_count=stream_count,
        duration_sec=duration_sec,
        has_encode_tag=has_encode_tag,
        keywords=keywords,
    )


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

    def test_visible_mode_requires_both_keyword_and_detail(self) -> None:
        """visible 방식은 상세 값과 태그 이름 키워드가 모두 있어야 통과함을 검증"""
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=True, keywords=("other",)),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_VISIBLE,
                tag_name="MyTag",
            ),
            GateVerdict.FAIL_TAG,
        )
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=True, keywords=("mytag",)),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_VISIBLE,
                tag_name="MyTag",
            ),
            GateVerdict.PASS,
        )

    def test_hidden_mode_requires_only_detail_and_off_mode_skips_tag_check(self) -> None:
        """hidden은 상세 값만 확인하고 off는 태그 확인을 건너뜀을 검증"""
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=True, keywords=()),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_HIDDEN,
            ),
            GateVerdict.PASS,
        )
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=False, keywords=()),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_HIDDEN,
            ),
            GateVerdict.FAIL_TAG,
        )
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=False, keywords=()),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_OFF,
            ),
            GateVerdict.PASS,
        )

    def test_tag_exempt_bypasses_tag_check_only(self) -> None:
        """moov 위치 등으로 면제된 항목은 태그 확인을 건너뛰지만 무결성과 크기는 계속 검사함을 검증"""
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=False, keywords=()),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=500_000,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_VISIBLE,
                tag_exempt=True,
            ),
            GateVerdict.PASS,
        )
        self.assertIs(
            judge_result(
                output=_ok(has_encode_tag=False, keywords=()),
                expected_stream_count=2,
                source_duration_sec=_SOURCE_DURATION,
                source_size_bytes=_SOURCE_SIZE,
                output_size_bytes=_SOURCE_SIZE + 1,
                purpose=GatePurpose.SAVING,
                tag_mode=ENCODE_TAG_MODE_VISIBLE,
                tag_exempt=True,
            ),
            GateVerdict.DISCARD_LARGER,
        )


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
        self.assertIs(purpose_for_evaluation(SmartAction.ENCODE_OVER, True), GatePurpose.KEYFRAME_FIX)
        self.assertIs(purpose_for_evaluation(SmartAction.ENCODE_OVER, False), GatePurpose.SAVING)
        self.assertIs(purpose_for_evaluation(SmartAction.ENCODE_COMPAT, False), GatePurpose.COMPAT)
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
            "format": {
                "duration": "600.0",
                "tags": {
                    "VIDEO_SCRIPTS_ENCODE": "v=1;enc=libx264;q=22.0",
                    "KEYWORDS": "여행; re_encoded",
                },
            },
        }
        summary = summarize_probe(probe)
        self.assertEqual((summary.stream_count, summary.duration_sec, summary.has_encode_tag), (2, 600.0, True))
        self.assertEqual(summary.keywords, ("여행", "re_encoded"))

    def test_missing_pieces_are_treated_as_empty(self) -> None:
        summary = summarize_probe({"streams": "broken", "format": {"tags": {"comment": "x"}}})
        self.assertEqual((summary.stream_count, summary.duration_sec, summary.has_encode_tag), (0, 0.0, False))
        self.assertFalse(summarize_probe({}).has_encode_tag)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestOutputDurationFromProbe(unittest.TestCase):
    """결과 영상 길이는 영상 스트림 길이, 영상 DURATION 태그, 컨테이너 길이 순으로 구함(결정 10)"""

    def test_longer_audio_in_mp4_does_not_inflate_duration(self) -> None:
        """MP4에서 오디오가 9초, 영상이 6초이면 영상 스트림 길이 6초를 씀을 검증"""
        probe = {
            "streams": [{"codec_type": "video", "duration": "6.000000"}, {"codec_type": "audio", "duration": "9.0"}],
            "format": {"duration": "9.000000"},
        }
        self.assertAlmostEqual(summarize_probe(probe).duration_sec, 6.0)

    def test_mkv_uses_duration_tag_when_stream_duration_missing(self) -> None:
        """MKV 영상 스트림 길이가 N/A이면 DURATION 태그 6초를 쓰고 컨테이너 길이 9.02초는 쓰지 않음을 검증"""
        probe = {
            "streams": [
                {"codec_type": "video", "duration": "N/A", "tags": {"DURATION": "00:00:06.000000000"}},
                {"codec_type": "audio", "duration": "N/A"},
            ],
            "format": {"duration": "9.020000"},
        }
        self.assertAlmostEqual(summarize_probe(probe).duration_sec, 6.0)

    def test_malformed_duration_tag_falls_back_to_container(self) -> None:
        """DURATION 태그 형식이 잘못되면 컨테이너 길이로 대체함을 검증"""
        probe = {
            "streams": [{"codec_type": "video", "tags": {"duration": "six seconds"}}],
            "format": {"duration": "9.5"},
        }
        self.assertAlmostEqual(summarize_probe(probe).duration_sec, 9.5)

    def test_mkv_output_with_tag_passes_integrity_against_six_second_source(self) -> None:
        """영상 6초 원본과 MKV 결과(태그 6초, 컨테이너 9초)가 길이 검사를 통과함을 검증"""
        probe = {
            "streams": [
                {"codec_type": "video", "duration": "N/A", "tags": {"DURATION": "00:00:06.000000000"}},
                {"codec_type": "audio", "duration": "N/A"},
            ],
            "format": {
                "duration": "9.020000",
                "tags": {"VIDEO_SCRIPTS_ENCODE": "v=1;enc=hevc_nvenc;q=22.0;at=x", "KEYWORDS": "re_encoded"},
            },
        }
        verdict = judge_result(
            output=summarize_probe(probe),
            expected_stream_count=2,
            source_duration_sec=6.0,
            source_size_bytes=_SOURCE_SIZE,
            output_size_bytes=500_000,
            purpose=GatePurpose.SAVING,
        )
        self.assertEqual(verdict, GateVerdict.PASS)


class TestDataStreamsAreNotCounted(unittest.TestCase):
    """결과 스트림 수는 데이터 스트림(챕터 트랙 포함)을 세지 않음을 검증"""

    def test_chapter_data_track_written_by_muxer_is_not_counted(self) -> None:
        """영상, 오디오 두 개에 챕터용 데이터 트랙이 더해진 MP4 결과의 스트림 수가 2임을 검증"""
        probe = {
            "streams": [
                {"codec_type": "video", "duration": "6.0"},
                {"codec_type": "audio", "duration": "6.0"},
                {"codec_type": "data", "codec_name": "bin_data"},
            ],
            "format": {"duration": "6.0"},
        }
        self.assertEqual(summarize_probe(probe).stream_count, 2)
