"""이전 결과 폐기 기록 적용 검증: 기록과 같은 원본과 인코더, 화질 기준값은 제외, 달라지거나 직접 지정이거나 키프레임 초과면 대상, 기록 조회 값과 판정이 맞물리는지 확인

- 기록 DB와 원본 파일은 임시 폴더에 만듦
"""

import json
import os
import tempfile
import unittest
from typing import Any
from unittest import mock

from src.core.encode.encode_previous_discard import DiscardFact
from src.core.encode.encode_state import EncodeState
from src.core.models import VideoEvaluation
from src.core.quality_target import BASELINE_CQ_DEFAULT
from src.core.smart_action import SmartAction
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_previous_discard import find_previous_discards
from tests._factories import make_eval, make_video

_NAME: str = "clip.mp4"
_ENCODER: str = "hevc_nvenc"
_OLD_MTIME_NS: int = 1_600_000_000 * 1_000_000_000
_ONE_SEC_NS: int = 1_000_000_000
_CHANGED_QUALITY: float = BASELINE_CQ_DEFAULT + 4.0


class PreviousDiscardTests(unittest.TestCase):
    """폐기 기록이 있는 원본과 이번 평가를 맞춰 인코딩 대상에서 뺄 경로를 고르는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.source = os.path.join(self.root, _NAME)
        with open(self.source, "wb") as handle:
            handle.write(b"source-bytes")
        os.utime(self.source, ns=(_OLD_MTIME_NS, _OLD_MTIME_NS))
        self.stat = os.stat(self.source)
        self.history = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)
        # - 배치 설정의 인코더와 계획 입력의 화질 기준값을 실제 기록 형식(JSON)으로 넣음
        batch_id = self.history.create_batch(
            self.root,
            json.dumps({"encoder": _ENCODER}),
            [NewItem(self.source, self.stat.st_size, self.stat.st_mtime_ns, json.dumps({"quality": BASELINE_CQ_DEFAULT}))],
        )
        self.history.update_item(batch_id, self.source, {"state": EncodeState.DISCARDED})

    def _evaluation(self, **overrides: Any) -> VideoEvaluation:
        return make_eval(make_video(filename=_NAME), smart_action=SmartAction.ENCODE_OVER, **overrides)

    def _excluded(self, evaluations: list[VideoEvaluation], encoder: str = _ENCODER) -> frozenset[str]:
        return find_previous_discards(evaluations, self.root, self.history.discarded_facts(), encoder)

    def test_discard_record_stores_size_mtime_encoder_and_quality(self) -> None:
        facts = self.history.discarded_facts()
        expected = DiscardFact(self.stat.st_size, self.stat.st_mtime_ns, _ENCODER, BASELINE_CQ_DEFAULT)
        self.assertEqual(facts, {self.source: expected})

    def test_unchanged_source_is_excluded(self) -> None:
        self.assertEqual(self._excluded([self._evaluation()]), frozenset({self.source}))

    def test_changed_mtime_makes_it_a_target_again(self) -> None:
        os.utime(self.source, ns=(_OLD_MTIME_NS + _ONE_SEC_NS, _OLD_MTIME_NS + _ONE_SEC_NS))
        self.assertEqual(self._excluded([self._evaluation()]), frozenset())

    def test_changed_encoder_or_quality_makes_it_a_target_again(self) -> None:
        self.assertEqual(self._excluded([self._evaluation()], encoder="h264_nvenc"), frozenset())
        self.assertEqual(self._excluded([self._evaluation(output_cq=_CHANGED_QUALITY)]), frozenset())

    def test_keyframe_exceeded_source_stays_a_target_even_with_the_same_source(self) -> None:
        """키프레임 초과 영상은 교정 목적이라 이전 폐기 기록과 같아도 대상에 남김. 정상 키프레임 영상은 그대로 뺌"""
        self.assertEqual(self._excluded([self._evaluation(is_keyframe_exceeded=True)]), frozenset())
        self.assertEqual(self._excluded([self._evaluation(is_keyframe_exceeded=False)]), frozenset({self.source}))

    def test_manual_target_is_a_target_again(self) -> None:
        self.assertEqual(self._excluded([self._evaluation(manual_target="1080p")]), frozenset())

    def test_non_target_evaluation_and_empty_records_exclude_nothing(self) -> None:
        passing = make_eval(make_video(filename=_NAME), smart_action=SmartAction.PASS_OPTIMAL)
        self.assertEqual(self._excluded([passing]), frozenset())
        self.assertEqual(find_previous_discards([self._evaluation()], self.root, {}, _ENCODER), frozenset())

    def _stat_paths(self, evaluations: list[VideoEvaluation], encoder: str = _ENCODER) -> list[str]:
        """find_previous_discards가 원본 상태를 확인한 경로 목록(작업 폴더 해석용 isdir 확인은 뺌)"""
        real_stat = os.stat

        def record(path: Any, *args: Any, **kwargs: Any) -> Any:
            return real_stat(path, *args, **kwargs)

        with mock.patch(
            "src.service.encode.encode_previous_discard.os.stat", side_effect=record
        ) as spy:
            find_previous_discards(evaluations, self.root, self.history.discarded_facts(), encoder)
        return [str(call.args[0]) for call in spy.call_args_list if str(call.args[0]) != self.root]

    def test_record_that_cannot_match_skips_reading_the_source(self) -> None:
        """인코더나 화질 기준값, 직접 지정, 키프레임이 어긋난 기록은 원본 상태를 읽지 않는지 검증"""
        self.assertEqual(self._stat_paths([self._evaluation()], encoder="h264_nvenc"), [])
        self.assertEqual(self._stat_paths([self._evaluation(output_cq=_CHANGED_QUALITY)]), [])
        self.assertEqual(self._stat_paths([self._evaluation(manual_target="1080p")]), [])
        self.assertEqual(self._stat_paths([self._evaluation(is_keyframe_exceeded=True)]), [])

    def test_record_that_can_match_reads_the_source_once(self) -> None:
        """인코더와 화질 기준값이 같은 기록은 원본 크기와 수정 시각을 한 번만 확인하는지 검증"""
        self.assertEqual(self._stat_paths([self._evaluation()]), [self.source])

    def test_record_for_another_source_is_skipped_without_reading(self) -> None:
        """폐기 기록이 없는 다른 원본은 상태 확인 없이 대상으로 남는지 검증"""
        other = make_eval(make_video(filename="other.mp4"), smart_action=SmartAction.ENCODE_OVER)
        self.assertEqual(self._stat_paths([other]), [])


if __name__ == "__main__":
    unittest.main()
