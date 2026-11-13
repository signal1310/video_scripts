import unittest

from src.core.exception_rules import split_exception_tokens, validate_exception_token
from src.core.models import VideoProps


class TestExceptionRulesModule(unittest.TestCase):
    """AST 화이트리스트 기반 예외 조건식 파싱·검증 및 토큰 분리 유틸리티를 검증"""

    def test_split_exception_tokens_delimiters_and_whitespace(self) -> None:
        """쉼표·세미콜론 구분자 및 공백·빈 토큰 정리를 검증"""
        self.assertEqual(
            split_exception_tokens(" sample ; v.fps >= 60, , RAW "),
            ["sample", "v.fps >= 60", "RAW"],
        )

    def test_exception_ast_security_and_safe_evaluation(self) -> None:
        """예외 규칙 파서가 AST 화이트리스트로 안전 식을 평가하고 위험한 속성/호출/연산을 차단하는지 검증"""
        sample_vid = VideoProps(
            filename="가나다_10_고화질.mp4",
            width=1920,
            height=1080,
            rotate_type=0,
            fps=60.0,
            vid_kbps=9500,
            aud_kbps=192,
            vid_size_MB=120.0,
            duration=100.0,
            codec="h264",
            keyframe_interval=1.0,
        )
        safe_cases = [
            ("v.duration < 150", True),
            ("10 <= v.duration <= 200", True),
            ("v.codec in ('h264', 'hevc')", True),
            ("v.filename.lower().endswith('.mp4')", True),
            ("round(v.aspect_ratio, 1) >= 1.7", True),
            ("lambda p: p.fps >= 29 and len(p.filename) > 3", True),
            ("v.filename[:3] == 'vid'", False),
            ("lambda_clip", False),
        ]
        for expr, expected in safe_cases:
            fn, err = validate_exception_token(expr)
            self.assertIsNone(err, f"Unexpected error for {expr}: {err}")
            assert fn is not None
            self.assertEqual(fn(sample_vid), expected, f"Mismatch for {expr}")

        blocked_cases = [
            "v.__class__",
            "lambda v: ().__class__.__bases__[0].__subclasses__()",
            "lambda v: __import__('os')",
            "lambda v: eval('1')",
            "lambda v: [x for x in range(10)]",
            "v.duration < 9**99",
            "len(v.filename * 100000) > 0",
            "v.nonexistent_field == 1",
            "lambda: True",
            "lambda a, b: a == b",
        ]
        for bad_expr in blocked_cases:
            fn_bad, err_bad = validate_exception_token(bad_expr)
            self.assertIsNone(fn_bad, f"Unsafe expression was allowed: {bad_expr}")
            self.assertIsNotNone(err_bad)

        # 동적 체이닝 반복(예: (v.filename * 100) * 100) 런타임 상한 차단 검증
        fn_chain, err_chain = validate_exception_token("len((v.filename * 100) * 100) > 0")
        assert fn_chain is not None
        self.assertIsNone(err_chain)
        self.assertFalse(fn_chain(sample_vid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
