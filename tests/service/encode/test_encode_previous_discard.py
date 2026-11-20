"""이전 결과 폐기 기록 적용 검증: 기록과 같은 원본은 제외, 원본이 바뀌었거나 직접 지정이면 대상, 기록 조회 값과 판정이 맞물리는지 확인

- 기록 DB와 원본 파일은 임시 폴더에 만듦
"""

import os
import tempfile
import unittest
from typing import Any

from src.core.encode.encode_previous_discard import DiscardFact
from src.core.encode.encode_state import EncodeState
from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_previous_discard import find_previous_discards
from tests._factories import make_eval, make_video

_NAME: str = "clip.mp4"
_OLD_MTIME_NS: int = 1_600_000_000 * 1_000_000_000
_ONE_SEC_NS: int = 1_000_000_000


class PreviousDiscardTests(unittest.TestCase):
    """폐기 기록이 있는 원본과 이번 평가를 맞춰 재인코딩 대상에서 뺄 경로를 고르는지 검증"""

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
        batch_id = self.history.create_batch(
            self.root, "{}", [NewItem(self.source, self.stat.st_size, self.stat.st_mtime_ns, "{}")]
        )
        self.history.update_item(batch_id, self.source, {"state": EncodeState.DISCARDED})

    def _evaluation(self, **overrides: Any) -> VideoEvaluation:
        return make_eval(make_video(filename=_NAME), smart_action=SmartAction.ENCODE_OVER, **overrides)

    def test_discard_record_stores_the_size_and_mtime_at_the_time(self) -> None:
        facts = self.history.discarded_facts()
        self.assertEqual(facts, {self.source: DiscardFact(self.stat.st_size, self.stat.st_mtime_ns)})

    def test_unchanged_source_is_excluded(self) -> None:
        facts = self.history.discarded_facts()
        self.assertEqual(find_previous_discards([self._evaluation()], self.root, facts), frozenset({self.source}))

    def test_changed_mtime_makes_it_a_target_again(self) -> None:
        facts = self.history.discarded_facts()
        os.utime(self.source, ns=(_OLD_MTIME_NS + _ONE_SEC_NS, _OLD_MTIME_NS + _ONE_SEC_NS))
        self.assertEqual(find_previous_discards([self._evaluation()], self.root, facts), frozenset())

    def test_manual_target_is_a_target_again(self) -> None:
        facts = self.history.discarded_facts()
        self.assertEqual(find_previous_discards([self._evaluation(manual_target="1080p")], self.root, facts), frozenset())

    def test_non_target_evaluation_and_empty_records_exclude_nothing(self) -> None:
        facts = self.history.discarded_facts()
        passing = make_eval(make_video(filename=_NAME), smart_action=SmartAction.PASS_OPTIMAL)
        self.assertEqual(find_previous_discards([passing], self.root, facts), frozenset())
        self.assertEqual(find_previous_discards([self._evaluation()], self.root, {}), frozenset())


if __name__ == "__main__":
    unittest.main()
