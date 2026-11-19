"""결과 경로 예약 검증: 같은 경로 충돌 거부, 해제 뒤 재예약, 대소문자와 구분자 차이 무시"""

import os
import unittest

from src.service.encode.encode_path_claims import ResultPathClaims


class TestResultPathClaims(unittest.TestCase):
    """항목 단위 예약과 해제"""

    def test_second_item_cannot_claim_a_reserved_path(self) -> None:
        """다른 항목이 같은 결과 경로를 예약 중이면 거부"""
        claims = ResultPathClaims()
        self.assertTrue(claims.claim("a.mp4", ("out/a.mp4", "out/a.mp4.part")))
        self.assertFalse(claims.claim("b.mp4", ("out/a.mp4.part",)))

    def test_release_frees_the_paths_for_the_next_item(self) -> None:
        """예약을 풀면 다음 항목이 같은 경로를 예약할 수 있음"""
        claims = ResultPathClaims()
        claims.claim("a.mp4", ("out/a.mp4",))
        claims.release("a.mp4")
        self.assertTrue(claims.claim("b.mp4", ("out/a.mp4",)))

    def test_release_of_unknown_item_is_harmless(self) -> None:
        """예약이 없는 항목을 해제해도 다른 예약은 유지"""
        claims = ResultPathClaims()
        claims.claim("a.mp4", ("out/a.mp4",))
        claims.release("unknown.mp4")
        self.assertFalse(claims.claim("b.mp4", ("out/a.mp4",)))

    def test_paths_are_compared_after_normalization(self) -> None:
        r"""같은 위치를 가리키는 다른 표기(중복 구분자, \.\ 성분)도 같은 경로로 봄"""
        claims = ResultPathClaims()
        self.assertTrue(claims.claim("a.mp4", (os.path.join("out", "a.mp4"),)))
        self.assertFalse(claims.claim("b.mp4", (os.path.join("out", ".", "a.mp4"),)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
