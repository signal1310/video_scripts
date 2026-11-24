"""재인코딩 창 요약 탭의 순수 값 계산(encode_summary) 단위 테스트"""

import unittest

from src.core.encode.encode_batch_book import (
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
)
from src.core.encode.encode_state import EncodeState
from src.core.encode.encode_summary import (
    attention_text,
    brief_values,
    done_badge,
    done_count,
    final_speed_text,
    saving_text,
    status_chips,
    status_counts,
    target_count,
)
from tests._factories import make_eval, make_video


class EncodeSummaryTests(unittest.TestCase):
    """브리핑 카드, 상태 칩, 주의 안내, 성적표 문구를 검증"""

    def test_brief_values_use_targets_and_signed_savings(self) -> None:
        """대상 수와 현재, 예상 용량, 순 절약, 인코더 기준값, 결과 파일 처리를 한 번에 만듦"""
        evaluations = [
            make_eval(video=make_video(filename="a.mp4", vid_size_MB=100.0), expected_after_mb=50.0, expected_delta_mb=50.0),
            make_eval(video=make_video(filename="b.mp4", vid_size_MB=200.0), expected_after_mb=150.0, expected_delta_mb=50.0),
        ]
        values = brief_values(
            evaluations, baseline_quality=22.0, encoder_label="HEVC NVENC", result_policy_label="원본 보관 후 교체"
        )
        self.assertEqual(values.target, "2개")
        self.assertIn("->", values.size_change)
        self.assertEqual(values.net_saved, "100.0 MB 절약")
        self.assertEqual(values.encoder, "HEVC NVENC / 화질 기준값 22.0")
        self.assertEqual(values.result_policy, "원본 보관 후 교체")

    def test_brief_values_show_growth_or_no_change(self) -> None:
        """순 절약이 음수면 증가, 0에 가까우면 변화 없음으로 표기"""
        grew = brief_values(
            [make_eval(expected_delta_mb=-20.0)],
            baseline_quality=22.0,
            encoder_label="HEVC",
            result_policy_label="원본 교체",
        )
        same = brief_values(
            [make_eval(expected_delta_mb=0.0)],
            baseline_quality=22.0,
            encoder_label="HEVC",
            result_policy_label="원본 교체",
        )
        self.assertEqual(grew.net_saved, "20.0 MB 증가")
        self.assertEqual(same.net_saved, "변화 없음")

    def test_status_counts_and_chips_keep_a_fixed_order_with_nonzero_items_only(self) -> None:
        """상태별 개수를 세고 완료, 진행 중, 대기, 교체 대기, 폐기, 실패, 건너뜀 순서로 0개를 뺌"""
        items = [
            ("a", EncodeState.DONE, ""),
            ("b", EncodeState.DONE, ""),
            ("c", EncodeState.FAILED, "사유"),
            ("d", EncodeState.PENDING, ""),
        ]
        counts = status_counts(items)
        self.assertEqual(counts[STATUS_DONE], 2)
        self.assertEqual(counts[STATUS_FAILED], 1)
        self.assertEqual(
            status_chips(counts),
            ((STATUS_DONE, 2), (STATUS_PENDING, 1), (STATUS_FAILED, 1)),
        )
        self.assertEqual(done_count(items), 2)
        self.assertEqual(target_count(items), 4)
        self.assertEqual(done_badge(2, 4), "2/4개 완료")

    def test_all_status_labels_are_known(self) -> None:
        """일시중지와 무관한 모든 상태 코드가 상태 칩 순서에 들어가는지 검증"""
        items = [
            ("a", EncodeState.RUNNING, ""),
            ("b", EncodeState.DISCARDED, ""),
            ("c", EncodeState.REPLACE_PENDING, ""),
            ("d", EncodeState.SKIPPED, ""),
        ]
        counts = status_counts(items)
        statuses = [status for status, _count in status_chips(counts)]
        self.assertEqual(
            statuses,
            [STATUS_RUNNING, STATUS_REPLACE_PENDING, STATUS_DISCARDED, STATUS_SKIPPED],
        )

    def test_attention_text_lists_only_problem_states(self) -> None:
        """폐기, 실패, 교체 대기가 있을 때만 주의 안내를 만들고 없으면 빈 문자열"""
        self.assertEqual(attention_text({STATUS_DONE: 3}), "")
        text = attention_text({STATUS_DISCARDED: 1, STATUS_FAILED: 2, STATUS_DONE: 3})
        self.assertEqual(text, "주의가 필요한 영상 3개: 결과 폐기 1, 실패 2")

    def test_saving_and_speed_text_switch_between_live_and_final(self) -> None:
        """진행 중에는 완료분, 완료 후에는 최종 성적과 총 소요 시간을 씀

        - 완료 후 최종 성적에는 최종 용량 변화를 함께 넣고, 진행 중에는 넘겨도 쓰지 않음
        """
        self.assertTrue(saving_text(60.0, 80.0, finished=False).startswith("완료분 절약: 실제"))
        self.assertTrue(saving_text(60.0, 80.0, finished=True).startswith("최종 성적: 실제"))
        self.assertEqual(
            saving_text(60.0, 80.0, finished=True, size_change="160.0 MB -> 100.0 MB"),
            "최종 성적: 160.0 MB -> 100.0 MB, 실제 60.0 MB, 예상 80.0 MB",
        )
        self.assertEqual(
            saving_text(60.0, None, finished=False, size_change="160.0 MB -> 100.0 MB"),
            "완료분 절약: 실제 60.0 MB, 예상 정보 없음",
        )
        self.assertEqual(final_speed_text("02:30", "1.20배"), "총 소요 02:30, 평균 1.20배")


if __name__ == "__main__":
    unittest.main(verbosity=2)
