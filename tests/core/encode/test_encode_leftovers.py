"""인코딩 임시 파일 정리 판정 규칙: 고를 수 없는 조건의 우선순위, 묶음 분류, 결과 파일 모양, 원본 후보 경로"""

import os
import unittest

from src.core.encode.encode_leftovers import (
    RECENT_WRITE_NS,
    BlockReason,
    LeftoverFacts,
    LeftoverGroup,
    classify_leftover,
    is_result_part_name,
    source_candidates,
)
from src.core.encode.encode_state import EncodeState

VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov"})
OLD_AGE_NS: int = RECENT_WRITE_NS + 1


def _facts(
    *,
    state: EncodeState | None = None,
    replace: bool = False,
    open_batch: bool = False,
    own: bool = False,
    age_ns: int = OLD_AGE_NS,
) -> LeftoverFacts:
    return LeftoverFacts(
        recorded_state=state,
        replace_result=replace,
        batch_open=open_batch,
        own_result_shape=own,
        age_ns=age_ns,
    )


class TestClassifyLeftover(unittest.TestCase):
    """고를 수 없는 조건은 묶음보다 먼저 보고, 나머지는 기록, 결과 모양, 출처 불명 순서로 나눔"""

    def test_replace_results_are_blocked_first_even_when_recent(self) -> None:
        """교체 대기와 교체 포기 결과는 최근에 바뀌었어도 교체 이유로 고를 수 없음"""
        verdict = classify_leftover(_facts(state=EncodeState.REPLACE_PENDING, replace=True, age_ns=0))
        self.assertEqual(verdict.group, LeftoverGroup.BLOCKED)
        self.assertEqual(verdict.block, BlockReason.REPLACE)
        self.assertFalse(verdict.selectable)

    def test_open_batch_work_in_progress_is_blocked_as_resume_target(self) -> None:
        """열린 배치의 진행 중, 대기 항목은 이어서 인코딩 때 정리되므로 재개 대상으로 막음"""
        for state in (EncodeState.RUNNING, EncodeState.PENDING):
            with self.subTest(state=state):
                verdict = classify_leftover(_facts(state=state, open_batch=True))
                self.assertEqual(verdict.block, BlockReason.RESUME_TARGET)

    def test_closed_batch_work_in_progress_is_recorded_and_selectable(self) -> None:
        """닫힌 배치의 진행 중 항목은 이어 하지 않음으로 닫힌 뒤 기록된 임시 파일이므로 고를 수 있음"""
        verdict = classify_leftover(_facts(state=EncodeState.PENDING, open_batch=False))
        self.assertEqual(verdict.group, LeftoverGroup.RECORDED)
        self.assertTrue(verdict.selectable)

    def test_recently_written_files_are_blocked_at_the_boundary(self) -> None:
        """최근 10분 안에 바뀐 파일은 막고, 경계 시각 이상이면 풀림"""
        self.assertEqual(classify_leftover(_facts(age_ns=RECENT_WRITE_NS - 1)).block, BlockReason.RECENT)
        self.assertTrue(classify_leftover(_facts(age_ns=RECENT_WRITE_NS)).selectable)

    def test_recorded_files_win_over_result_shape_and_unknown_is_the_last_group(self) -> None:
        """기록이 있으면 결과 모양과 상관없이 기록된 파일, 기록이 없고 결과 모양도 없으면 출처를 알 수 없는 파일"""
        self.assertEqual(classify_leftover(_facts(state=EncodeState.FAILED, own=True)).group, LeftoverGroup.RECORDED)
        self.assertEqual(classify_leftover(_facts(own=True)).group, LeftoverGroup.OWN_RESULT)
        self.assertEqual(classify_leftover(_facts()).group, LeftoverGroup.UNKNOWN)


class TestResultPartShape(unittest.TestCase):
    """결과 임시 파일 이름은 영상 컨테이너 최종 이름 뒤에 .part가 붙은 모양만 인정"""

    def test_container_names_with_part_suffix_are_result_shaped(self) -> None:
        self.assertTrue(is_result_part_name("clip.mp4.part"))
        self.assertTrue(is_result_part_name("CLIP.MKV.PART"))
        self.assertFalse(is_result_part_name("clip.part"))
        self.assertFalse(is_result_part_name("clip.avi.part"))
        self.assertFalse(is_result_part_name("clip.mp4"))


class TestSourceCandidates(unittest.TestCase):
    """원본 후보는 같은 폴더와, 출력 폴더 이름을 뗀 원본 폴더를 모두 포함하고 드라이브 경로도 상대 경로로 바뀌지 않음"""

    def test_in_place_part_points_at_the_same_folder_and_stem(self) -> None:
        folder = os.path.join("root", "sub")
        candidates = source_candidates(os.path.join(folder, "clip.mp4.part"), "_인코딩결과", VIDEO_EXTS)
        self.assertIn(os.path.join(folder, "clip.mp4"), candidates)
        self.assertIn(os.path.join(folder, "clip.mkv"), candidates)
        self.assertTrue(all(os.path.dirname(path) == folder for path in candidates))

    def test_separate_output_part_also_points_at_the_source_folder_without_output_name(self) -> None:
        root = os.path.join("root", "sub")
        part = os.path.join("root", "_인코딩결과", "sub", "clip.mp4.part")
        candidates = source_candidates(part, "_인코딩결과", VIDEO_EXTS)
        self.assertIn(os.path.join(root, "clip.mp4"), candidates)

    def test_absolute_drive_paths_stay_absolute_after_removing_the_output_name(self) -> None:
        part = os.path.join(os.sep, "data", "_인코딩결과", "clip.mp4.part")
        candidates = source_candidates(part, "_인코딩결과", VIDEO_EXTS)
        self.assertIn(os.path.join(os.sep, "data", "clip.mp4"), candidates)
        self.assertTrue(all(os.path.isabs(path) for path in candidates))


if __name__ == "__main__":
    unittest.main(verbosity=2)
