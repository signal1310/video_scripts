"""다단계 되돌리기, 다시 실행 히스토리 스택의 기록, 이동, 병합, 상한 규칙을 검증하는 단위 테스트"""

import unittest

from src.core.config import ClassifierConfig, ThresholdsConfig
from src.core.undo_history import MAX_UNDO_STEPS, UNDO_COALESCE_WINDOW_MS, UndoableSnapshot, UndoHistory


def _snap(step: int, *, over_bitrate_ratio: float | None = None) -> UndoableSnapshot:
    """예외 규칙 번호 또는 판별 기준 값으로 서로 다른 상태 스냅샷 생성"""
    config = ClassifierConfig()
    if over_bitrate_ratio is not None:
        config.thresholds = ThresholdsConfig(over_bitrate_ratio=over_bitrate_ratio)
    return UndoableSnapshot(
        config=config,
        exception_tokens=(f"rule{step}",),
        disabled_exception_tokens=(),
        manual_target_tiers={},
    )


class TestUndoHistoryStack(unittest.TestCase):
    """이산 변경의 연속 되돌리기, 다시 실행과 스택 초기화 규칙을 검증"""

    def test_five_steps_undo_to_initial_and_redo_to_final(self) -> None:
        """5단계 기록 후 연속 되돌리기로 초기 상태, 연속 다시 실행으로 최종 상태에 도달함을 검증"""
        history = UndoHistory()
        states = [_snap(i) for i in range(6)]
        for i in range(5):
            self.assertTrue(history.record(f"변경 {i}", states[i], states[i + 1]))
        self.assertEqual(history.undo_count, 5)

        last_undone = None
        for _ in range(5):
            last_undone = history.undo()
            self.assertIsNotNone(last_undone)
        assert last_undone is not None
        self.assertTrue(last_undone.before.same_state(states[0]))
        self.assertFalse(history.can_undo)
        self.assertEqual(history.redo_count, 5)

        last_redone = None
        for _ in range(5):
            last_redone = history.redo()
            self.assertIsNotNone(last_redone)
        assert last_redone is not None
        self.assertTrue(last_redone.after.same_state(states[5]))
        self.assertFalse(history.can_redo)

    def test_new_change_after_partial_undo_clears_redo_stack(self) -> None:
        """중간 단계까지 되돌린 뒤 새 변경을 기록하면 다시 실행 스택이 비워짐을 검증"""
        history = UndoHistory()
        states = [_snap(i) for i in range(4)]
        for i in range(3):
            history.record(f"변경 {i}", states[i], states[i + 1])
        history.undo()
        history.undo()
        self.assertEqual(history.redo_count, 2)

        history.record("새 변경", states[0], _snap(10))
        self.assertFalse(history.can_redo)
        self.assertEqual(history.redo_count, 0)
        self.assertEqual(history.undo_count, 2)
        self.assertEqual(history.undo_label, "새 변경")

    def test_labels_reflect_top_of_each_stack(self) -> None:
        """되돌리기와 다시 실행 라벨이 각 스택 최상단 항목을 가리킴을 검증"""
        history = UndoHistory()
        self.assertIsNone(history.undo_label)
        self.assertIsNone(history.redo_label)
        history.record("첫 번째", _snap(0), _snap(1))
        history.record("두 번째", _snap(1), _snap(2))
        self.assertEqual(history.undo_label, "두 번째")
        history.undo()
        self.assertEqual(history.undo_label, "첫 번째")
        self.assertEqual(history.redo_label, "두 번째")

    def test_no_op_record_returns_false_and_keeps_redo_stack(self) -> None:
        """상태 변화가 없는 기록은 무시하고 다시 실행 스택을 유지함을 검증"""
        history = UndoHistory()
        history.record("변경", _snap(0), _snap(1))
        history.undo()
        self.assertFalse(history.record("무변경", _snap(0), _snap(0).clone()))
        self.assertTrue(history.can_redo)
        self.assertEqual(history.undo_count, 0)

    def test_selection_difference_alone_is_not_recorded(self) -> None:
        """선택 영상 목록만 다른 상태는 변경으로 기록하지 않음을 검증"""
        history = UndoHistory()
        before = _snap(0)
        after = UndoableSnapshot(
            config=before.config.copy(),
            exception_tokens=before.exception_tokens,
            disabled_exception_tokens=before.disabled_exception_tokens,
            manual_target_tiers={},
            selected_filenames=frozenset({"a.mp4"}),
        )
        self.assertFalse(history.record("선택만 변경", before, after))
        self.assertEqual(history.undo_count, 0)

    def test_recorded_snapshots_are_independent_of_source_objects(self) -> None:
        """기록 이후 원본 스냅샷의 가변 컬렉션을 바꿔도 히스토리 항목이 변하지 않음을 검증"""
        history = UndoHistory()
        before = UndoableSnapshot(
            config=ClassifierConfig(),
            exception_tokens=(),
            disabled_exception_tokens=(),
            manual_target_tiers={"C:/work": {"a.mp4": "720p"}},
        )
        after = UndoableSnapshot(
            config=ClassifierConfig(),
            exception_tokens=("rule",),
            disabled_exception_tokens=(),
            manual_target_tiers={"C:/work": {"a.mp4": "720p"}},
        )
        history.record("변경", before, after)
        before.manual_target_tiers["C:/work"]["a.mp4"] = "keep"

        entry = history.undo()
        assert entry is not None
        self.assertEqual(entry.before.manual_target_tiers, {"C:/work": {"a.mp4": "720p"}})

    def test_clear_empties_both_stacks(self) -> None:
        """작업 폴더 전환 등에서 clear 호출 시 두 스택이 모두 비워짐을 검증"""
        history = UndoHistory()
        history.record("변경", _snap(0), _snap(1))
        history.record("변경2", _snap(1), _snap(2))
        history.undo()
        history.clear()
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.undo_label)
        self.assertIsNone(history.redo_label)

    def test_history_cap_drops_oldest_steps(self) -> None:
        """최대 단계 수를 넘으면 가장 오래된 단계부터 제거됨을 검증"""
        history = UndoHistory()
        total = MAX_UNDO_STEPS + 5
        states = [_snap(i) for i in range(total + 1)]
        for i in range(total):
            history.record(f"변경 {i}", states[i], states[i + 1])
        self.assertEqual(history.undo_count, MAX_UNDO_STEPS)

        last_undone = None
        while history.can_undo:
            last_undone = history.undo()
        assert last_undone is not None
        self.assertTrue(last_undone.before.same_state(states[total - MAX_UNDO_STEPS]))


class TestUndoHistoryCoalescing(unittest.TestCase):
    """판별 기준 연속 변경의 시간 창 병합과 이산 변경 분리를 검증"""

    def test_continuous_drag_within_window_merges_into_one_step(self) -> None:
        """병합 창 안에서 이어지는 판별 기준 변경이 한 단계로 합쳐짐을 검증"""
        history = UndoHistory()
        values = [1.0, 1.1, 1.2, 1.3, 1.4]
        states = [_snap(0, over_bitrate_ratio=v) for v in values]
        for i, timestamp in enumerate([0, 300, 600, 900]):
            history.record("판별 기준 조정", states[i], states[i + 1], coalesce_key="criteria", timestamp_ms=timestamp)
        self.assertEqual(history.undo_count, 1)

        entry = history.undo()
        assert entry is not None
        self.assertTrue(entry.before.same_state(states[0]))
        self.assertTrue(entry.after.same_state(states[4]))
        self.assertFalse(history.can_undo)

    def test_gap_over_window_creates_separate_step(self) -> None:
        """병합 창을 넘긴 판별 기준 변경은 별도 단계로 쌓임을 검증"""
        history = UndoHistory()
        s0, s1, s2 = (_snap(0, over_bitrate_ratio=v) for v in (1.0, 1.1, 1.2))
        history.record("판별 기준 조정", s0, s1, coalesce_key="criteria", timestamp_ms=0)
        history.record("판별 기준 조정", s1, s2, coalesce_key="criteria", timestamp_ms=UNDO_COALESCE_WINDOW_MS + 1)
        self.assertEqual(history.undo_count, 2)

    def test_discrete_changes_are_never_merged(self) -> None:
        """병합 키가 없는 이산 변경은 시간이 같아도 매번 별도 단계로 쌓임을 검증"""
        history = UndoHistory()
        states = [_snap(i) for i in range(4)]
        for i in range(3):
            history.record("출력 해상도 지정", states[i], states[i + 1], timestamp_ms=0)
        self.assertEqual(history.undo_count, 3)

    def test_criteria_after_discrete_change_does_not_merge_into_it(self) -> None:
        """이산 변경 직후의 판별 기준 변경은 병합 대상이 아님을 검증"""
        history = UndoHistory()
        s0, s1, s2 = (_snap(0, over_bitrate_ratio=v) for v in (1.0, 1.1, 1.2))
        history.record("예외 규칙 추가", _snap(9), s0, timestamp_ms=0)
        history.record("판별 기준 조정", s0, s1, coalesce_key="criteria", timestamp_ms=10)
        history.record("판별 기준 조정", s1, s2, coalesce_key="criteria", timestamp_ms=20)
        self.assertEqual(history.undo_count, 2)

    def test_drag_returning_to_original_value_drops_the_entry(self) -> None:
        """드래그로 원래 값에 되돌아오면 병합 단계가 제거되어 빈 히스토리가 됨을 검증"""
        history = UndoHistory()
        s0 = _snap(0, over_bitrate_ratio=1.0)
        s1 = _snap(0, over_bitrate_ratio=1.5)
        history.record("판별 기준 조정", s0, s1, coalesce_key="criteria", timestamp_ms=0)
        recorded = history.record("판별 기준 조정", s1, s0, coalesce_key="criteria", timestamp_ms=100)
        self.assertFalse(recorded)
        self.assertFalse(history.can_undo)
