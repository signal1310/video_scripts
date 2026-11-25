"""인코딩 중 닫기 요청의 처리 방법 판정을 검증"""

import unittest

from src.core.encode.encode_close import CloseAction, CloseChoice, close_action


class CloseActionTests(unittest.TestCase):
    """실행 여부와 예약 여부 조합에 따른 닫기 처리가 맞는지 검증"""

    def test_idle_app_closes_at_once_whether_or_not_a_close_was_pending(self) -> None:
        """실행 중이 아니면 예약 여부와 관계없이 바로 닫는지 검증"""
        self.assertIs(close_action(is_running=False, close_pending=False), CloseAction.CLOSE_NOW)
        self.assertIs(close_action(is_running=False, close_pending=True), CloseAction.CLOSE_NOW)

    def test_running_batch_asks_unless_a_close_is_already_reserved(self) -> None:
        """실행 중이고 예약이 없으면 확인 창을 열고, 예약이 있으면 배치가 끝나기를 기다리는지 검증"""
        self.assertIs(close_action(is_running=True, close_pending=False), CloseAction.ASK)
        self.assertIs(close_action(is_running=True, close_pending=True), CloseAction.WAIT)

    def test_choices_have_the_three_outcomes_and_keep_is_the_no_op(self) -> None:
        """확인 창의 선택 값이 세 가지이며 계속이 닫기를 무시하는 값인지 검증"""
        self.assertEqual({choice.value for choice in CloseChoice}, {"immediate", "after_current", "keep"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
