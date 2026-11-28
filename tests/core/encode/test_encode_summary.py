"""인코딩 창 요약 탭의 순수 값 계산(encode_summary) 단위 테스트"""

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
    NET_SAVED_EPSILON_MB,
    attention_text,
    brief_values,
    delta_text,
    designation_caution_line,
    designation_counts,
    designation_line,
    designation_summary_line,
    done_badge,
    done_count,
    final_speed_text,
    net_saved_grew,
    net_saved_text,
    saving_text,
    status_chips,
    status_counts,
    target_count,
    target_line,
)
from src.core.smart_action import SmartAction
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
        self.assertEqual(values.encoder, "HEVC NVENC, 화질 기준값 22.0")
        self.assertEqual(values.result_policy, "원본 보관 후 교체")
        self.assertFalse(values.net_saved_grew)

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
        self.assertTrue(grew.net_saved_grew)
        self.assertFalse(same.net_saved_grew)

    def test_delta_text_and_grew_flag_agree_on_the_epsilon_boundaries(self) -> None:
        """절약 문장과 색 판정이 같은 여유값에서 갈리고, 경계와 큰 값에서도 서로 어긋나지 않음"""
        epsilon = NET_SAVED_EPSILON_MB
        for delta, text, grew in (
            (epsilon, "변화 없음", False),
            (-epsilon, "변화 없음", False),
            (epsilon + 1.0, "1.1 MB 절약", False),
            (-epsilon - 1.0, "1.1 MB 증가", True),
            (0.0, "변화 없음", False),
            (5000.0, "4.9 GB 절약", False),
            (-5000.0, "4.9 GB 증가", True),
        ):
            with self.subTest(delta=delta):
                self.assertEqual(delta_text(delta), text)
                self.assertEqual(net_saved_grew(delta), grew)
        self.assertEqual(net_saved_text(100.0, 40.0), "60.0 MB 절약")
        self.assertEqual(net_saved_text(40.0, 100.0), "60.0 MB 증가")
        self.assertEqual(net_saved_text(100.0, 100.0), "변화 없음")
        self.assertFalse(net_saved_grew(100.0 - 40.0))
        self.assertTrue(net_saved_grew(40.0 - 100.0))

    def test_target_line_joins_the_count_and_the_size_change(self) -> None:
        """대상 줄은 대상 수와 예상 용량 변화를 쉼표로 이어 한 줄로 만듦"""
        self.assertEqual(target_line("12개", "24.8GB -> 21.7GB"), "대상 12개, 24.8GB -> 21.7GB")
        self.assertEqual(target_line("1/2개", "24.8GB -> 21.7GB"), "대상 1/2개, 24.8GB -> 21.7GB")

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


class DesignationCountTests(unittest.TestCase):
    """용량 절약과 변환 우선으로 나눈 대상 수와 시작 확인 창 요약 줄을 검증(제안 2.3절, 2.4절)"""

    def test_counts_split_saving_and_transform_targets(self) -> None:
        """절약 선별 대상과 직접 지정 대상을 나눠 세고, 직접 지정 중 인코딩 근거가 있는 영상을 함께 셈"""
        plain = make_eval(video=make_video(filename="a.mp4"))
        manual = make_eval(
            video=make_video(filename="b.mp4", has_encode_tag=False),
            smart_action=SmartAction.ENCODE_MANUAL,
        )
        manual_again = make_eval(
            video=make_video(filename="c.mp4", has_encode_tag=True),
            smart_action=SmartAction.ENCODE_MANUAL,
            encoded_evidence=True,
        )
        counts = designation_counts([plain, manual, manual_again])
        self.assertEqual(counts.total, 3)
        self.assertEqual(counts.saving, 1)
        self.assertEqual(counts.transform, 2)
        self.assertEqual(counts.already_designated, 1)

    def test_already_encoded_count_uses_keyword_evidence_too(self) -> None:
        """태그 상세는 없고 태그 이름 키워드만 있는 지정 영상도 이미 인코딩한 영상으로 셈(판정과 같은 근거)"""
        keyword_only = make_eval(
            video=make_video(filename="b.mp4", has_encode_tag=False, encode_keywords="re_encoded"),
            smart_action=SmartAction.ENCODE_MANUAL,
            encoded_evidence=True,
        )
        counts = designation_counts([keyword_only])
        self.assertEqual(counts.transform, 1)
        self.assertEqual(counts.already_designated, 1)

    def test_transform_count_follows_the_gate_purpose(self) -> None:
        """게이트 목적이 변환 우선인 키프레임 교정과 호환 인코딩도 직접 지정으로 셈(단일 기준)"""
        keyframe = make_eval(
            video=make_video(filename="k.mp4"),
            smart_action=SmartAction.ENCODE_OPT_KF,
            is_keyframe_exceeded=True,
        )
        compat = make_eval(video=make_video(filename="c.mp4"), smart_action=SmartAction.ENCODE_COMPAT)
        plain = make_eval(video=make_video(filename="p.mp4"))
        counts = designation_counts([keyframe, compat, plain])
        self.assertEqual(counts.transform, 2)
        self.assertEqual(counts.saving, 1)

    def test_lines_show_the_split_the_designation_and_the_caution(self) -> None:
        """대상 줄과 직접 지정 줄, 주의 줄 문구를 만들고 대상이 없으면 빈 문자열로 둠"""
        counts = designation_counts(
            [
                make_eval(video=make_video(filename="a.mp4")),
                make_eval(video=make_video(filename="b.mp4"), smart_action=SmartAction.ENCODE_MANUAL),
            ]
        )
        self.assertEqual(designation_summary_line(counts), "전체 인코딩 대상: 2개 (용량 절약 1개, 변환 우선 1개)")
        self.assertEqual(designation_line(counts), "직접 지정 / 변환 우선 1개")
        self.assertEqual(designation_caution_line(counts), "")
        empty = designation_counts([])
        self.assertEqual(designation_summary_line(empty), "전체 인코딩 대상: 0개 (용량 절약 0개, 변환 우선 0개)")
        self.assertEqual(designation_line(empty), "")
        self.assertEqual(designation_caution_line(empty), "")
        designated = designation_counts(
            [
                make_eval(
                    video=make_video(filename="c.mp4", has_encode_tag=True),
                    smart_action=SmartAction.ENCODE_MANUAL,
                    encoded_evidence=True,
                )
            ]
        )
        self.assertEqual(
            designation_caution_line(designated),
            "⚠ 이미 인코딩한 영상 1개 포함 (직접 지정으로 대상에 들어감)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
