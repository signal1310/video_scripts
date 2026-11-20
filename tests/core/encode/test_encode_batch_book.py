"""재인코딩 배치 항목 상태의 표기, 툴팁 문장, 일시중지 표시, 처리 수와 하단 요약 문구를 검증"""

import unittest

from src.core.encode.encode_batch_book import (
    PAUSED_TOOLTIP,
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_RUNNING_PAUSED,
    STATUS_SKIPPED,
    EncodeBatchBook,
    status_label,
    status_tooltip,
)
from src.core.encode.encode_state import EncodeState

_A = "/src/a.mp4"
_B = "/src/b.mp4"
_C = "/src/c.mp4"


class StatusTextTests(unittest.TestCase):
    """상태 표기와 툴팁 문장의 문구를 검증"""

    def test_paused_label_applies_only_to_running_items(self) -> None:
        """일시중지 표기는 진행 중 항목에만 붙고 다른 상태는 그대로인지 검증"""
        self.assertEqual(status_label(EncodeState.RUNNING, paused=True), STATUS_RUNNING_PAUSED)
        self.assertEqual(status_label(EncodeState.RUNNING, paused=False), STATUS_RUNNING)
        self.assertEqual(status_label(EncodeState.PENDING, paused=True), STATUS_PENDING)
        self.assertEqual(status_label(EncodeState.DONE, paused=True), STATUS_DONE)

    def test_tooltip_sentences_follow_the_approved_wording(self) -> None:
        """상태별 툴팁 문장이 승인된 문구이고, 사유 상태만 둘째 줄에 결과 사유를 붙이는지 검증"""
        self.assertEqual(status_tooltip(EncodeState.PENDING, False, ""), "재인코딩 순서를 기다리는 중입니다.")
        self.assertEqual(status_tooltip(EncodeState.RUNNING, False, ""), "재인코딩하는 중입니다.")
        self.assertEqual(status_tooltip(EncodeState.RUNNING, True, ""), PAUSED_TOOLTIP)
        self.assertEqual(status_tooltip(EncodeState.DONE, False, "무시되는 사유"), "재인코딩을 마쳤습니다.")
        self.assertEqual(
            status_tooltip(EncodeState.DISCARDED, False, "결과가 원본보다 큼"),
            "재인코딩 결과를 쓰지 않고 지웠습니다. 원본은 그대로입니다.\n사유: 결과가 원본보다 큼",
        )
        self.assertEqual(
            status_tooltip(EncodeState.FAILED, False, "인코딩 실패(종료 코드 1)"),
            "재인코딩하지 못했습니다. 원본은 그대로입니다.\n사유: 인코딩 실패(종료 코드 1)",
        )
        self.assertEqual(
            status_tooltip(EncodeState.SKIPPED, False, "인터레이스 원본"),
            "재인코딩하지 않고 건너뛰었습니다. 원본은 그대로입니다.\n사유: 인터레이스 원본",
        )
        self.assertEqual(
            status_tooltip(EncodeState.REPLACE_PENDING, False, ""),
            "결과 파일을 원본 자리에 아직 옮기지 못했습니다. 원본과 결과 파일이 모두 남아 있습니다.",
        )

    def test_empty_reason_omits_the_second_line(self) -> None:
        """사유 상태라도 결과 사유가 비어 있으면 둘째 줄을 만들지 않는지 검증"""
        self.assertEqual(
            status_tooltip(EncodeState.FAILED, False, ""),
            "재인코딩하지 못했습니다. 원본은 그대로입니다.",
        )


class EncodeBatchBookTests(unittest.TestCase):
    """배치 저장소의 시작, 상태 갱신, 일시중지, 처리 수와 요약 문구를 검증"""

    def setUp(self) -> None:
        self.book = EncodeBatchBook()

    def test_empty_book_has_no_batch_and_ignores_unknown_paths(self) -> None:
        """시작 전에는 배치가 없고 대상이 아닌 경로의 값은 모두 없음으로 나오는지 검증"""
        self.assertFalse(self.book.has_batch)
        self.book.apply(_A, EncodeState.DONE)
        self.assertIsNone(self.book.state_of(_A))
        self.assertIsNone(self.book.label_of(_A))
        self.assertIsNone(self.book.tooltip_of(_A))

    def test_start_marks_every_target_pending_and_drops_the_previous_batch(self) -> None:
        """새 배치는 대상을 모두 대기로 두고 이전 배치의 상태와 사유를 버리는지 검증"""
        self.book.start([_A, _B])
        self.book.apply(_A, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.book.start([_C])
        self.assertTrue(self.book.has_batch)
        self.assertIsNone(self.book.state_of(_A))
        self.assertEqual(self.book.state_of(_C), EncodeState.PENDING)
        self.assertEqual(self.book.total(), 1)

    def test_apply_updates_state_label_and_reason_tooltip(self) -> None:
        """상태 갱신이 표기와 툴팁 문장에 반영되는지 검증"""
        self.book.start([_A])
        self.book.apply(_A, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.assertEqual(self.book.label_of(_A), STATUS_FAILED)
        self.assertEqual(
            self.book.tooltip_of(_A),
            "재인코딩하지 못했습니다. 원본은 그대로입니다.\n사유: 인코딩 실패(종료 코드 1)",
        )

    def test_pause_changes_only_running_labels(self) -> None:
        """일시중지 전환이 진행 중 표기에만 반영되고 해제하면 원래 표기로 돌아오는지 검증"""
        self.book.start([_A, _B])
        self.book.apply(_A, EncodeState.RUNNING)
        self.book.apply(_B, EncodeState.DONE)
        self.book.set_paused(True)
        self.assertTrue(self.book.paused)
        self.assertEqual(self.book.label_of(_A), STATUS_RUNNING_PAUSED)
        self.assertEqual(self.book.label_of(_B), STATUS_DONE)
        self.book.set_paused(False)
        self.assertEqual(self.book.label_of(_A), STATUS_RUNNING)

    def test_processed_counts_finished_attempts_and_summary_text_shows_it(self) -> None:
        """처리 수가 완료, 결과 폐기, 실패, 건너뜀, 교체 대기를 세고 요약 문구에 나오는지 검증"""
        self.book.start([_A, _B, _C, "/src/d.mp4", "/src/e.mp4"])
        self.book.apply(_A, EncodeState.DONE)
        self.book.apply(_B, EncodeState.DISCARDED, "결과가 원본보다 큼")
        self.book.apply(_C, EncodeState.SKIPPED, "인터레이스 원본")
        self.book.apply("/src/d.mp4", EncodeState.REPLACE_PENDING, "중단으로 교체 미완료")
        self.book.apply("/src/e.mp4", EncodeState.RUNNING)
        self.assertEqual(self.book.processed(), 4)
        self.assertEqual(self.book.summary_text(), "재인코딩 (4/5 처리)")

    def test_summary_tooltip_lists_every_status_in_order_with_running_paused_included(self) -> None:
        """요약 툴팁이 제목과 상태 순서대로 개수를 보이고, 일시중지 항목을 진행 중에 세는지 검증"""
        self.book.start([_A, _B, _C])
        self.book.apply(_A, EncodeState.RUNNING)
        self.book.apply(_B, EncodeState.RUNNING)
        self.book.set_paused(True)
        self.book.apply(_C, EncodeState.DONE)
        self.assertEqual(
            self.book.summary_tooltip().split("\n"),
            [
                "상태별 개수",
                "대기 0개",
                "진행 중 2개",
                "완료 1개",
                "결과 폐기 0개",
                "실패 0개",
                "건너뜀 0개",
                "교체 대기 0개",
            ],
        )

    def test_counts_by_status_has_every_summary_status_even_when_empty(self) -> None:
        """개수 사전이 빈 상태도 모두 가지고 있어 화면 줄이 빠지지 않는지 검증"""
        self.book.start([_A])
        counts = self.book.counts_by_status()
        self.assertEqual(list(counts), [STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_DISCARDED, STATUS_FAILED, STATUS_SKIPPED, STATUS_REPLACE_PENDING])
        self.assertEqual(counts[STATUS_PENDING], 1)


class ItemPauseTests(unittest.TestCase):
    """영상 하나의 일시중지 표시, 대상 목록, 상태가 바뀔 때의 정리를 검증"""

    def setUp(self) -> None:
        self.book = EncodeBatchBook()
        self.book.start([_A, _B])

    def test_item_pause_changes_only_that_running_item(self) -> None:
        """개별 일시중지가 그 항목의 진행 중 표기만 바꾸고 다른 항목은 그대로인지 검증"""
        self.book.apply(_A, EncodeState.RUNNING)
        self.book.apply(_B, EncodeState.RUNNING)
        self.book.set_item_paused(_A, True)
        self.assertTrue(self.book.is_item_paused(_A))
        self.assertEqual(self.book.label_of(_A), STATUS_RUNNING_PAUSED)
        self.assertEqual(self.book.label_of(_B), STATUS_RUNNING)
        self.assertEqual(self.book.tooltip_of(_A), PAUSED_TOOLTIP)

    def test_item_pause_is_ignored_for_paths_outside_the_batch(self) -> None:
        """배치 대상이 아닌 경로의 개별 일시중지는 기록하지 않는지 검증"""
        self.book.set_item_paused(_C, True)
        self.assertFalse(self.book.is_item_paused(_C))

    def test_leaving_the_running_state_clears_the_item_pause(self) -> None:
        """상태 이벤트가 오면 개별 일시중지 표시가 풀리는지 검증"""
        self.book.apply(_A, EncodeState.RUNNING)
        self.book.set_item_paused(_A, True)
        self.book.apply(_A, EncodeState.PENDING)
        self.assertFalse(self.book.is_item_paused(_A))
        self.assertEqual(self.book.label_of(_A), STATUS_PENDING)

    def test_unpausing_an_item_restores_its_running_label(self) -> None:
        """개별 일시중지를 해제하면 진행 중 표기로 돌아오는지 검증"""
        self.book.apply(_A, EncodeState.RUNNING)
        self.book.set_item_paused(_A, True)
        self.book.set_item_paused(_A, False)
        self.assertEqual(self.book.label_of(_A), STATUS_RUNNING)

    def test_items_list_every_target_with_state_and_reason(self) -> None:
        """결과 목록용 항목 목록이 대상 순서대로 상태와 사유를 주는지 검증"""
        self.book.apply(_A, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.assertEqual(
            self.book.items(),
            ((_A, EncodeState.FAILED, "인코딩 실패(종료 코드 1)"), (_B, EncodeState.PENDING, "")),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
