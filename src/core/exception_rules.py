"""AST 화이트리스트 기반 사용자 예외 조건식 파싱 및 안전 실행 모듈"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

from src.core.exceptions import (
    DomainAttributeError,
    DomainTypeError,
    DomainValueError,
    ErrorCode,
)
from src.core.models import VideoProps

_MAX_EXPR_AST_DEPTH: int = 32
_MAX_POW_EXPONENT: int = 20
_MAX_POW_BASE: int = 10_000_000
_MAX_SEQ_REPEAT: int = 1_000
_ERR_SEQ_REPEAT_LIMIT: str = "시퀀스 반복 허용 상한을 초과했습니다."

_SAFE_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
}
_SAFE_STR_METHODS: frozenset[str] = frozenset(
    {
        "lower",
        "upper",
        "strip",
        "lstrip",
        "rstrip",
        "startswith",
        "endswith",
        "isdigit",
        "isnumeric",
    }
)
_SAFE_VIDEO_ATTRS: frozenset[str] = frozenset(
    {
        "filename",
        "width",
        "height",
        "rotate_type",
        "fps",
        "vid_kbps",
        "aud_kbps",
        "vid_size_MB",
        "duration",
        "codec",
        "keyframe_interval",
        "moved_dirname",
        "aspect_ratio",
    }
)

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
    ast.Invert: operator.invert,
}
_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


def _cmp_in(left: Any, right: Any) -> bool:
    return left in right


def _cmp_not_in(left: Any, right: Any) -> bool:
    return left not in right


_CMP_OPS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: _cmp_in,
    ast.NotIn: _cmp_not_in,
}


def split_exception_tokens(raw: str) -> list[str]:
    """세미콜론/쉼표 구분 예외 키워드 문자열을 공백 제거된 토큰 리스트로 파싱"""
    if not raw:
        return []
    return [tok.strip() for tok in raw.replace(";", ",").split(",") if tok.strip()]


def merge_exception_tokens(
    existing: list[str], additions: list[str]
) -> tuple[list[str], bool]:
    """기존 토큰 리스트에 대소문자 중복 없이 신규 예외 키워드들을 병합하고 변경 여부를 반환"""
    merged = list(existing)
    seen_lower = {t.lower() for t in merged}
    changed = False
    for kw in additions:
        clean = kw.strip()
        if clean and clean.lower() not in seen_lower:
            merged.append(clean)
            seen_lower.add(clean.lower())
            changed = True
    return merged, changed


def exclude_exception_tokens(existing: list[str], removals: list[str]) -> list[str]:
    """기존 토큰 리스트에서 지정한 예외 키워드(대소문자 무관)를 제거"""
    remove_lower = {k.strip().lower() for k in removals if k.strip()}
    if not remove_lower:
        return existing
    return [t for t in existing if t.lower() not in remove_lower]


def _extract_const_number(node: ast.AST) -> int | float | None:
    """단순 숫자 상수(음수 부호 포함) AST에서 수치 값을 추출"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        inner = node.operand
        if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
            return -inner.value if isinstance(node.op, ast.USub) else inner.value
    return None


def _is_likely_sequence_node(node: ast.AST) -> bool:
    """정적 검증 단계에서 문자열·시퀀스 타입일 가능성이 있는 노드인지 판별"""
    if isinstance(node, (ast.Tuple, ast.List)):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Attribute) and node.attr in {"filename", "codec", "moved_dirname"}:
        return True
    if isinstance(node, ast.Attribute) and node.attr in _SAFE_STR_METHODS:
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)


def _validate_expr_ast(node: ast.AST, param_name: str, depth: int = 0) -> None:
    """허용된 연산과 속성만 사용하는 안전한 조건식 AST인지 재귀 검증"""
    if depth > _MAX_EXPR_AST_DEPTH:
        raise DomainValueError(
            "조건식 중첩 깊이가 허용 상한을 초과했습니다.",
            code=ErrorCode.RULE_NESTING_EXCEEDED,
        )

    next_depth = depth + 1

    if isinstance(node, ast.Constant):
        if node.value is not None and not isinstance(
            node.value, (int, float, str, bool)
        ):
            raise DomainValueError(
                f"허용되지 않은 리터럴 타입입니다: {type(node.value).__name__}",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        return

    if isinstance(node, ast.Name):
        if node.id != param_name:
            raise DomainValueError(
                f"허용되지 않은 식별자입니다: '{node.id}'",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        return

    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise DomainValueError(
                f"내부 속성 접근은 허용되지 않습니다: '{node.attr}'",
                code=ErrorCode.RULE_ATTR_ACCESS_DENIED,
            )
        if isinstance(node.value, ast.Name) and node.value.id == param_name:
            if node.attr not in _SAFE_VIDEO_ATTRS:
                raise DomainValueError(
                    f"존재하지 않거나 허용되지 않은 비디오 속성입니다: '{node.attr}'",
                    code=ErrorCode.RULE_ATTR_ACCESS_DENIED,
                )
        elif node.attr not in _SAFE_STR_METHODS:
            raise DomainValueError(
                f"허용되지 않은 속성 접근입니다: '{node.attr}'",
                code=ErrorCode.RULE_ATTR_ACCESS_DENIED,
            )
        _validate_expr_ast(node.value, param_name, next_depth)
        return

    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            raise DomainValueError(
                "키워드 인자 또는 가변 인자 전개는 지원하지 않습니다.",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        if isinstance(node.func, ast.Name):
            if node.func.id not in _SAFE_FUNCTIONS:
                raise DomainValueError(
                    f"허용되지 않은 함수 호출입니다: '{node.func.id}'",
                    code=ErrorCode.RULE_CALL_DENIED,
                )
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("_") or node.func.attr not in _SAFE_STR_METHODS:
                raise DomainValueError(
                    f"허용되지 않은 메서드 호출입니다: '{node.func.attr}'",
                    code=ErrorCode.RULE_CALL_DENIED,
                )
            _validate_expr_ast(node.func.value, param_name, next_depth)
        else:
            raise DomainValueError(
                "허용되지 않은 호출 대상입니다.",
                code=ErrorCode.RULE_CALL_DENIED,
            )
        for arg in node.args:
            _validate_expr_ast(arg, param_name, next_depth)
        return

    if isinstance(node, ast.Subscript):
        _validate_expr_ast(node.value, param_name, next_depth)
        _validate_expr_ast(node.slice, param_name, next_depth)
        return

    if isinstance(node, ast.Slice):
        for bound in (node.lower, node.upper, node.step):
            if bound is not None:
                _validate_expr_ast(bound, param_name, next_depth)
        return

    if isinstance(node, ast.BoolOp):
        for val in node.values:
            _validate_expr_ast(val, param_name, next_depth)
        return

    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPS:
            raise DomainValueError(
                f"허용되지 않은 단항 연산자입니다: {type(node.op).__name__}",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        _validate_expr_ast(node.operand, param_name, next_depth)
        return

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS and not isinstance(node.op, ast.Pow):
            raise DomainValueError(
                f"허용되지 않은 이항 연산자입니다: {type(node.op).__name__}",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        if isinstance(node.op, ast.Pow):
            const_base = _extract_const_number(node.left)
            const_exp = _extract_const_number(node.right)
            if const_base is not None and abs(const_base) > _MAX_POW_BASE:
                raise DomainValueError(
                    "거듭제곱 밑 허용 범위를 초과했습니다.",
                    code=ErrorCode.RULE_POWER_RANGE,
                )
            if const_exp is not None and abs(const_exp) > _MAX_POW_EXPONENT:
                raise DomainValueError(
                    "거듭제곱 지수 허용 범위를 초과했습니다.",
                    code=ErrorCode.RULE_POWER_RANGE,
                )
        elif isinstance(node.op, ast.Mult):
            left_num = _extract_const_number(node.left)
            right_num = _extract_const_number(node.right)
            if (
                right_num is not None
                and isinstance(right_num, int)
                and abs(right_num) > _MAX_SEQ_REPEAT
                and _is_likely_sequence_node(node.left)
            ) or (
                left_num is not None
                and isinstance(left_num, int)
                and abs(left_num) > _MAX_SEQ_REPEAT
                and _is_likely_sequence_node(node.right)
            ):
                raise DomainValueError(_ERR_SEQ_REPEAT_LIMIT, code=ErrorCode.RULE_SEQ_REPEAT_LIMIT)
        _validate_expr_ast(node.left, param_name, next_depth)
        _validate_expr_ast(node.right, param_name, next_depth)
        return

    if isinstance(node, ast.Compare):
        if any(type(op) not in _CMP_OPS for op in node.ops):
            raise DomainValueError(
                "허용되지 않은 비교 연산자입니다.",
                code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
            )
        _validate_expr_ast(node.left, param_name, next_depth)
        for comp in node.comparators:
            _validate_expr_ast(comp, param_name, next_depth)
        return

    if isinstance(node, ast.IfExp):
        _validate_expr_ast(node.test, param_name, next_depth)
        _validate_expr_ast(node.body, param_name, next_depth)
        _validate_expr_ast(node.orelse, param_name, next_depth)
        return

    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            _validate_expr_ast(elt, param_name, next_depth)
        return

    raise DomainValueError(
        f"허용되지 않은 문법 요소입니다: {type(node).__name__}",
        code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
    )


def _eval_guarded_binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Pow):
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise DomainTypeError(
                "거듭제곱은 숫자 피연산자만 허용됩니다.",
                code=ErrorCode.RULE_POWER_TYPE,
            )
        if abs(left) > _MAX_POW_BASE or abs(right) > _MAX_POW_EXPONENT:
            raise DomainValueError(
                "거듭제곱 허용 범위를 초과했습니다.",
                code=ErrorCode.RULE_POWER_RANGE,
            )
        return left**right

    if isinstance(op, ast.Mult):
        seq_pair = (
            (left, right)
            if isinstance(left, (str, tuple, list)) and isinstance(right, int)
            else (right, left)
            if isinstance(right, (str, tuple, list)) and isinstance(left, int)
            else None
        )
        if seq_pair is not None:
            seq_obj, repeat_count = seq_pair
            if (
                abs(repeat_count) > _MAX_SEQ_REPEAT
                or len(seq_obj) * max(repeat_count, 0) > _MAX_SEQ_REPEAT
            ):
                raise DomainValueError(_ERR_SEQ_REPEAT_LIMIT, code=ErrorCode.RULE_SEQ_REPEAT_LIMIT)

    return _BIN_OPS[type(op)](left, right)


def _eval_expr_ast(node: ast.AST, env: dict[str, Any]) -> Any:
    """사전 검증된 AST 노드를 eval/compile 없이 직접 해석하여 값을 평가"""
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return env[node.id]

    if isinstance(node, ast.Attribute):
        target = _eval_expr_ast(node.value, env)
        if isinstance(target, VideoProps) and node.attr in _SAFE_VIDEO_ATTRS:
            return getattr(target, node.attr)
        raise DomainAttributeError(
            f"접근할 수 없는 속성입니다: '{node.attr}'",
            code=ErrorCode.RULE_ATTR_ACCESS_DENIED,
        )

    if isinstance(node, ast.Call):
        args = [_eval_expr_ast(arg, env) for arg in node.args]
        if isinstance(node.func, ast.Name):
            return _SAFE_FUNCTIONS[node.func.id](*args)
        if isinstance(node.func, ast.Attribute):
            recv = _eval_expr_ast(node.func.value, env)
            if isinstance(recv, str) and node.func.attr in _SAFE_STR_METHODS:
                return getattr(recv, node.func.attr)(*args)
            raise DomainAttributeError(
                f"호출할 수 없는 메서드입니다: '{node.func.attr}'",
                code=ErrorCode.RULE_CALL_DENIED,
            )

    if isinstance(node, ast.Subscript):
        target = _eval_expr_ast(node.value, env)
        if not isinstance(target, (str, list, tuple, dict)):
            raise DomainTypeError(
                f"인덱싱을 지원하지 않는 타입입니다: {type(target).__name__}",
                code=ErrorCode.RULE_INDEX_TYPE,
            )
        if isinstance(node.slice, ast.Slice):
            lower = _eval_expr_ast(node.slice.lower, env) if node.slice.lower is not None else None
            upper = _eval_expr_ast(node.slice.upper, env) if node.slice.upper is not None else None
            step = _eval_expr_ast(node.slice.step, env) if node.slice.step is not None else None
            return target[slice(lower, upper, step)]
        return target[_eval_expr_ast(node.slice, env)]

    if isinstance(node, ast.BoolOp):
        is_and = isinstance(node.op, ast.And)
        result: Any = is_and
        for val_node in node.values:
            result = _eval_expr_ast(val_node, env)
            if (is_and and not result) or (not is_and and result):
                return result
        return result

    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_eval_expr_ast(node.operand, env))

    if isinstance(node, ast.BinOp):
        left = _eval_expr_ast(node.left, env)
        right = _eval_expr_ast(node.right, env)
        return _eval_guarded_binop(node.op, left, right)

    if isinstance(node, ast.Compare):
        left = _eval_expr_ast(node.left, env)
        for op, comp_node in zip(node.ops, node.comparators, strict=True):
            right = _eval_expr_ast(comp_node, env)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        branch = node.body if _eval_expr_ast(node.test, env) else node.orelse
        return _eval_expr_ast(branch, env)

    if isinstance(node, ast.Tuple):
        return tuple(_eval_expr_ast(elt, env) for elt in node.elts)

    if isinstance(node, ast.List):
        return [_eval_expr_ast(elt, env) for elt in node.elts]

    if isinstance(node, ast.Set):
        return {_eval_expr_ast(elt, env) for elt in node.elts}

    raise DomainValueError(
        f"지원하지 않는 AST 노드입니다: {type(node).__name__}",
        code=ErrorCode.RULE_SYNTAX_UNSUPPORTED,
    )


def _build_safe_predicate(
    body_node: ast.AST, param_name: str
) -> Callable[[VideoProps], bool]:
    """검증된 AST 본문으로부터 예외 안전 판정 클로저를 생성"""

    def _predicate(
        v: VideoProps,
        _body: ast.AST = body_node,
        _param: str = param_name,
    ) -> bool:
        try:
            return bool(_eval_expr_ast(_body, {_param: v}))
        except (
            TypeError,
            ValueError,
            ZeroDivisionError,
            KeyError,
            IndexError,
            AttributeError,
        ):
            return False

    return _predicate


def validate_exception_token(
    token: str,
) -> tuple[Callable[[VideoProps], bool] | None, str | None]:
    """예외 규칙 토큰을 AST 화이트리스트로 검증해 판정 함수 및 오류 메시지 반환

    파이썬 람다·속성 조건식(lambda, v.xxx, v[xxx])의 구문 또는 보안 규칙 위반 시 오류 반환
    """
    expr = token.strip()
    if not expr:
        return None, None

    is_lambda = expr.startswith("lambda ") or expr.startswith("lambda:") or expr == "lambda"
    if is_lambda:
        try:
            tree = ast.parse(expr, mode="eval")
            if not isinstance(tree.body, ast.Lambda):
                return None, f"호출 가능한 람다 식이 아닙니다: '{expr}'"
            args = tree.body.args
            if (
                args.posonlyargs
                or args.kwonlyargs
                or args.vararg is not None
                or args.kwarg is not None
                or args.defaults
                or len(args.args) != 1
            ):
                return None, f"단일 위치 인자(예: lambda v: ...)만 지원합니다: '{expr}'"

            param_name = args.args[0].arg
            body_node = tree.body.body
            _validate_expr_ast(body_node, param_name=param_name)
            return _build_safe_predicate(body_node, param_name), None
        except (SyntaxError, ValueError) as exc:
            return None, f"람다 식 오류 ({exc}): '{expr}'"

    if "v." in expr or "v[" in expr:
        try:
            tree = ast.parse(expr, mode="eval")
            if isinstance(tree.body, ast.Lambda):
                raise DomainValueError(
                    "람다 식은 'lambda v: ...' 형태로 입력해야 합니다.",
                    code=ErrorCode.RULE_LAMBDA_FORMAT,
                )
            _validate_expr_ast(tree.body, param_name="v")
            return _build_safe_predicate(tree.body, "v"), None
        except (SyntaxError, ValueError) as exc:
            return None, f"조건식 오류 ({exc}): '{expr}'"

    kw = expr.lower()
    return (lambda v, k=kw: k in v.filename.lower()), None


def parse_exception_token(token: str) -> Callable[[VideoProps], bool] | None:
    """사용자 예외 규칙 문자열을 Callable 판정 함수로 변환

    람다 식(lambda v: ...), 속성 조건식(v.fps >= 60), 단순 파일명 키워드 모두 지원
    """
    fn, _ = validate_exception_token(token)
    return fn
