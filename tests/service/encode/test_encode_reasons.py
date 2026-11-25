"""인코딩 결과 사유 문구 검증: 승인 문구 고정, 동적 사유 형식, 게이트 판정과 상태 짝"""

import unittest

from src.core.encode.encode_gate import GateVerdict
from src.core.encode.encode_state import EncodeState
from src.service.encode import encode_reasons as reasons


class EncodeReasonsTest(unittest.TestCase):
    def test_approved_wording_is_fixed(self) -> None:
        """11.1절 승인 문구(5-0 교체 2건 포함)가 그대로 유지되는지 검증"""
        self.assertEqual(reasons.TAG_MISSING, "인코딩 태그 누락")
        self.assertEqual(reasons.TAG_NOT_RECORDED, "인코딩 태그를 기록하지 못함(지원하지 않는 파일 구조)")
        self.assertEqual(reasons.EARLY_STOP_REASON, "진행 중 중단, 예상 크기가 원본보다 큼")

    def test_dynamic_reasons_embed_detail(self) -> None:
        """예외 메시지와 종료 코드가 사유 문구에 들어가는 형식을 검증"""
        self.assertEqual(reasons.processing_error(ValueError("x")), "처리 중 오류: x")
        self.assertEqual(reasons.tag_write_failed(OSError("y")), "결과 태그 기록 실패: y")
        self.assertEqual(reasons.encode_failed(1), "인코딩 실패(종료 코드 1)")
        self.assertEqual(reasons.encode_failed(None), "인코딩 실패(종료 코드 None)")
        self.assertEqual(reasons.encode_retry_failed(2), "인코딩 실패(CPU 디코딩 재시도 후 종료 코드 2)")
        self.assertEqual(reasons.replace_pending(OSError("z")), "교체 대기: z")

    def test_gate_failure_pairs_state_and_reason(self) -> None:
        """게이트 불통과 판정마다 상태와 사유가 짝으로 정해지는지 검증"""
        self.assertEqual(
            reasons.gate_failure(GateVerdict.DISCARD_LARGER), (EncodeState.DISCARDED, "결과가 원본보다 큼")
        )
        self.assertEqual(reasons.gate_failure(GateVerdict.FAIL_TAG), (EncodeState.FAILED, "인코딩 태그 누락"))
        self.assertEqual(
            reasons.gate_failure(GateVerdict.FAIL_INTEGRITY), (EncodeState.FAILED, "결과 파일 손상 검사 실패")
        )

    def test_skip_and_fail_constants_are_nonempty(self) -> None:
        """건너뜀과 실패 사유 상수가 빈 문자열이 아님을 검증"""
        for text in (
            reasons.SOURCE_CHANGED,
            reasons.NO_DISK_SPACE,
            reasons.NO_PRIMARY_VIDEO,
            reasons.INTERLACED,
            reasons.NAME_EXISTS,
            reasons.ARCHIVE_EXISTS,
            reasons.PART_LEFT,
            reasons.PATH_IN_USE,
            reasons.LARGER_THAN_SOURCE,
            reasons.INTEGRITY_FAILED,
            reasons.RESUME_SOURCE_CHANGED,
            reasons.ARCHIVE_INTERRUPTED,
        ):
            self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
