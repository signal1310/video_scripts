import unittest

from src.core.exception_rules import split_exception_tokens, validate_exception_token
from src.core.models import VideoProps

_SAMPLE_VID = VideoProps(
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


class TestExceptionRulesModule(unittest.TestCase):
    """AST 화이트리스트 기반 예외 조건식 파싱, 검증 및 토큰 분리 유틸리티를 검증"""

    def test_split_exception_tokens_delimiters_and_whitespace(self) -> None:
        """쉼표, 세미콜론 구분자 및 공백, 빈 토큰 정리를 검증"""
        self.assertEqual(
            split_exception_tokens(" sample ; v.fps >= 60, , RAW "),
            ["sample", "v.fps >= 60", "RAW"],
        )

    def test_exception_ast_security_and_safe_evaluation(self) -> None:
        """예외 규칙 파서가 AST 화이트리스트로 안전 식을 평가하고 위험한 속성/호출/연산을 차단하는지 검증"""
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
            self.assertEqual(fn(_SAMPLE_VID), expected, f"Mismatch for {expr}")

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
        self.assertFalse(fn_chain(_SAMPLE_VID))


    def _assert_evaluates(self, cases: list[tuple[str, bool]]) -> None:
        for expr, expected in cases:
            with self.subTest(expr=expr):
                fn, err = validate_exception_token(expr)
                self.assertIsNone(err)
                assert fn is not None
                self.assertEqual(fn(_SAMPLE_VID), expected)

    def _assert_rejected(self, exprs: list[str]) -> None:
        for expr in exprs:
            with self.subTest(expr=expr):
                fn, err = validate_exception_token(expr)
                self.assertIsNone(fn)
                self.assertIsNotNone(err)

    def test_boolean_operators_follow_truth_table(self) -> None:
        """and/or 조합이 피연산자별 진리표대로 평가되는지 검증"""
        self._assert_evaluates(
            [
                ("v.fps == 60 and v.width == 1920", True),
                ("v.fps == 60 and v.width == 0", False),
                ("v.fps == 0 and v.width == 1920", False),
                ("v.fps == 0 or v.width == 1920", True),
                ("v.fps == 60 or v.width == 0", True),
                ("v.fps == 0 or v.width == 0", False),
                ("not v.fps == 0", True),
                ("-v.fps < 0", True),
                ("v.keyframe_interval is None", False),
                ("v.codec not in ('hevc', 'av1')", True),
                ("v.codec not in ('h264',)", False),
            ]
        )

    def test_slices_and_nested_calls_evaluate(self) -> None:
        """슬라이스의 하한, 상한, 간격 생략 조합과 문자열 메서드 호출을 검증"""
        self._assert_evaluates(
            [
                ("v.codec[1:] == '264'", True),
                ("v.codec[:2] == 'h2'", True),
                ("v.codec[1:3] == '26'", True),
                ("v.codec[::2] == 'h6'", True),
                ("v.codec.upper() == 'H264'", True),
            ]
        )

    def test_pow_limits_apply_statically_and_at_runtime(self) -> None:
        """거듭제곱 밑, 지수 상한이 경계값 포함 허용, 초과 차단으로 정적, 실행 시점 모두 적용되는지 검증"""
        self._assert_evaluates(
            [
                ("lambda v: 2 ** 20 > 0", True),
                ("lambda v: 10000000 ** 2 > 0", True),
                ("v.width ** 20 > 0", True),
                ("(v.width * 5000) ** 2 > 0", True),
                ("1.5 ** (v.fps / 3) > 0", True),
                ("1.5 ** (v.fps / 2) > 0", False),
                ("(v.width * 10000) ** 2 > 0", False),
            ]
        )
        self._assert_rejected(
            [
                "lambda v: 2 ** 21 > 0",
                "lambda v: 2 ** -21 > 0",
                "lambda v: 10000001 ** 2 > 0",
                "v.width ** 21 > 0",
                "lambda v: v.width << 2",
                "lambda v: v.width @ 2",
            ]
        )

    def test_sequence_repeat_limits(self) -> None:
        """시퀀스 반복 상한이 경계값 포함 허용, 초과 차단이며 비시퀀스 곱은 막지 않는지 검증"""
        self._assert_evaluates(
            [
                ("lambda v: len('a' * 1000) == 1000", True),
                ("lambda v: len(1000 * 'a') == 1000", True),
                ("v.codec * 250 == v.codec * 250", True),
                ("v.codec * 251 == v.codec * 251", False),
                ("v.width * 1001 > 0", True),
                ("len(v.filename) * 1001 > 0", True),
            ]
        )
        self._assert_rejected(
            [
                "lambda v: len('a' * 1001) > 0",
                "lambda v: len(1001 * 'a') > 0",
                "lambda v: len('a' * -1001) > 0",
                "lambda v: len((1, 2) * 1001) > 0",
                "v.codec * 1001 == ''",
                "v.filename * 1001 == ''",
                "v.filename.lower() * 1001 == ''",
            ]
        )

    def test_nesting_depth_limit(self) -> None:
        """조건식 중첩 깊이가 상한(32)까지 허용되고 초과 시 차단되는지 검증"""
        nested = lambda n: "abs(" * n + "v.fps" + ")" * n  # noqa: E731
        self._assert_evaluates([(nested(31), True)])
        self._assert_rejected([nested(32)])

    def test_syntax_whitelist_rejections(self) -> None:
        """허용되지 않은 속성, 호출 형태, 리터럴 타입이 검증 단계에서 차단되는지 검증"""
        self._assert_rejected(
            [
                "v.codec.title == 'x'",
                "v.filename.format('x') == ''",
                "v.filename.__len__() > 0",
                "round(v.fps, ndigits=1) > 0",
                "max(*[1, 2]) > v.fps",
                "b'x' == v.codec",
                "1j > v.fps",
            ]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
