from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config import DEFAULT_CONFIGS, ClassifierConfig
from src.core.models import VideoProps
from src.gui.constants import (
    FILTER_DEBOUNCE_MS,
    THRESHOLD_BOUNDS,
    split_exception_tokens,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

if TYPE_CHECKING:
    from src.classifier import VideoClassifier


_MAX_EXPR_AST_DEPTH: int = 32
_MAX_POW_EXPONENT: int = 20
_MAX_POW_BASE: int = 10_000_000
_MAX_SEQ_REPEAT: int = 1_000

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


def _extract_const_number(node: ast.AST) -> int | float | None:
    """단순 숫자 상수(음수 부호 포함) AST에서 수치 값을 추출."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        inner = node.operand
        if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
            return -inner.value if isinstance(node.op, ast.USub) else inner.value
    return None


def _is_likely_sequence_node(node: ast.AST) -> bool:
    """정적 검증 단계에서 문자열·시퀀스 타입일 가능성이 있는 노드인지 판별."""
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
    """허용된 연산과 속성만 사용하는 안전한 조건식 AST인지 재귀 검증."""
    if depth > _MAX_EXPR_AST_DEPTH:
        raise ValueError("조건식 중첩 깊이가 허용 상한을 초과했습니다.")

    next_depth = depth + 1

    if isinstance(node, ast.Constant):
        if node.value is not None and not isinstance(
            node.value, (int, float, str, bool)
        ):
            raise ValueError(f"허용되지 않은 리터럴 타입입니다: {type(node.value).__name__}")
        return

    if isinstance(node, ast.Name):
        if node.id != param_name:
            raise ValueError(f"허용되지 않은 식별자입니다: '{node.id}'")
        return

    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(f"내부 속성 접근은 허용되지 않습니다: '{node.attr}'")
        if isinstance(node.value, ast.Name) and node.value.id == param_name:
            if node.attr not in _SAFE_VIDEO_ATTRS:
                raise ValueError(f"존재하지 않거나 허용되지 않은 비디오 속성입니다: '{node.attr}'")
        elif node.attr not in _SAFE_STR_METHODS:
            raise ValueError(f"허용되지 않은 속성 접근입니다: '{node.attr}'")
        _validate_expr_ast(node.value, param_name, next_depth)
        return

    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            raise ValueError("키워드 인자 또는 가변 인자 전개는 지원하지 않습니다.")
        if isinstance(node.func, ast.Name):
            if node.func.id not in _SAFE_FUNCTIONS:
                raise ValueError(f"허용되지 않은 함수 호출입니다: '{node.func.id}'")
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("_") or node.func.attr not in _SAFE_STR_METHODS:
                raise ValueError(f"허용되지 않은 메서드 호출입니다: '{node.func.attr}'")
            _validate_expr_ast(node.func.value, param_name, next_depth)
        else:
            raise ValueError("허용되지 않은 호출 대상입니다.")
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
            raise ValueError(f"허용되지 않은 단항 연산자입니다: {type(node.op).__name__}")
        _validate_expr_ast(node.operand, param_name, next_depth)
        return

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS and not isinstance(node.op, ast.Pow):
            raise ValueError(f"허용되지 않은 이항 연산자입니다: {type(node.op).__name__}")
        if isinstance(node.op, ast.Pow):
            const_base = _extract_const_number(node.left)
            const_exp = _extract_const_number(node.right)
            if const_base is not None and abs(const_base) > _MAX_POW_BASE:
                raise ValueError("거듭제곱 밑 허용 범위를 초과했습니다.")
            if const_exp is not None and abs(const_exp) > _MAX_POW_EXPONENT:
                raise ValueError("거듭제곱 지수 허용 범위를 초과했습니다.")
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
                raise ValueError("시퀀스 반복 허용 상한을 초과했습니다.")
        _validate_expr_ast(node.left, param_name, next_depth)
        _validate_expr_ast(node.right, param_name, next_depth)
        return

    if isinstance(node, ast.Compare):
        if any(type(op) not in _CMP_OPS for op in node.ops):
            raise ValueError("허용되지 않은 비교 연산자입니다.")
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

    raise ValueError(f"허용되지 않은 문법 요소입니다: {type(node).__name__}")


def _eval_guarded_binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Pow):
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise TypeError("거듭제곱은 숫자 피연산자만 허용됩니다.")
        if abs(left) > _MAX_POW_BASE or abs(right) > _MAX_POW_EXPONENT:
            raise ValueError("거듭제곱 허용 범위를 초과했습니다.")
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
                raise ValueError("시퀀스 반복 허용 상한을 초과했습니다.")

    return _BIN_OPS[type(op)](left, right)


def _eval_expr_ast(node: ast.AST, env: dict[str, Any]) -> Any:
    """사전 검증된 AST 노드를 eval/compile 없이 직접 해석하여 값을 평가."""
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return env[node.id]

    if isinstance(node, ast.Attribute):
        target = _eval_expr_ast(node.value, env)
        if isinstance(target, VideoProps) and node.attr in _SAFE_VIDEO_ATTRS:
            return getattr(target, node.attr)
        raise AttributeError(f"접근할 수 없는 속성입니다: '{node.attr}'")

    if isinstance(node, ast.Call):
        args = [_eval_expr_ast(arg, env) for arg in node.args]
        if isinstance(node.func, ast.Name):
            return _SAFE_FUNCTIONS[node.func.id](*args)
        if isinstance(node.func, ast.Attribute):
            recv = _eval_expr_ast(node.func.value, env)
            if isinstance(recv, str) and node.func.attr in _SAFE_STR_METHODS:
                return getattr(recv, node.func.attr)(*args)
            raise AttributeError(f"호출할 수 없는 메서드입니다: '{node.func.attr}'")

    if isinstance(node, ast.Subscript):
        target = _eval_expr_ast(node.value, env)
        if not isinstance(target, (str, list, tuple, dict)):
            raise TypeError(f"인덱싱을 지원하지 않는 타입입니다: {type(target).__name__}")
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

    raise ValueError(f"지원하지 않는 AST 노드입니다: {type(node).__name__}")


def _build_safe_predicate(
    body_node: ast.AST, param_name: str
) -> Callable[[VideoProps], bool]:
    """검증된 AST 본문으로부터 예외 안전 판정 클로저를 생성."""

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
    """
    - 예외 규칙 토큰을 AST 화이트리스트로 검증하고 판정 함수(Callable) 및 오류 메시지를 반환.
    - 파이썬 람다/속성 조건식(lambda, v.xxx, v[xxx])이 구문 또는 보안 규칙 오류를 가지면 오류를 반환.
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
                raise ValueError("람다 식은 'lambda v: ...' 형태로 입력해야 합니다.")
            _validate_expr_ast(tree.body, param_name="v")
            return _build_safe_predicate(tree.body, "v"), None
        except (SyntaxError, ValueError) as exc:
            return None, f"조건식 오류 ({exc}): '{expr}'"

    kw = expr.lower()
    return (lambda v, k=kw: k in v.filename.lower()), None


def parse_exception_token(token: str) -> Callable[[VideoProps], bool] | None:
    """
    - 사용자가 입력한 예외 규칙 문자열을 Callable 판정 함수로 변환.
    - 파이썬 람다 식 (예: lambda v: v.duration < 10), 속성 조건식 (예: v.fps >= 60, v.width < 720),
      또는 단순 파일명 키워드 (예: sample, 무손실)를 유연하게 지원.
    """
    fn, _ = validate_exception_token(token)
    return fn


class FilterTuningBar(QFrame, ThemeAwareMixin):
    """
    - 검색, 필터링, 임계값 조정 및 예외 규칙 입력을 제공하는 컨트롤 바 위젯.
    """

    filter_changed = pyqtSignal()
    thresholds_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False
        self._user_editing_exc: bool = False
        self._exc_debounce_timer = QTimer(self)
        self._exc_debounce_timer.setSingleShot(True)
        self._exc_debounce_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._exc_debounce_timer.timeout.connect(self._on_spin_or_text_changed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # 1행: 검색 및 필터링
        row_top = QHBoxLayout()
        row_top.setSpacing(10)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.textChanged.connect(self.filter_changed.emit)

        lbl_zone = QLabel("화질 등급:")
        lbl_zone.setObjectName("SectionLabel")
        self.combo_zone = QComboBox()
        self.combo_zone.addItem("전체", "ALL")
        self.combo_zone.addItem("과화질", "OVER")
        self.combo_zone.addItem("적정화질", "OPTIMAL")
        self.combo_zone.addItem("저화질", "LOW")
        self.combo_zone.setFixedWidth(100)
        self.combo_zone.currentIndexChanged.connect(self.filter_changed.emit)

        lbl_target = QLabel("표시 대상:")
        lbl_target.setObjectName("SectionLabel")
        self.combo_target = QComboBox()
        self.combo_target.addItem("전체", "ALL")
        self.combo_target.addItem("인코딩 대상", "ENCODE_ONLY")
        self.combo_target.addItem("원본 보존", "PASS_ONLY")
        self.combo_target.addItem("키프레임 불량", "DEFECT_KF")
        self.combo_target.setFixedWidth(120)
        self.combo_target.currentIndexChanged.connect(self.filter_changed.emit)

        self.lbl_filter_count = QLabel("표시: 0 / 0개")
        self.lbl_filter_count.setObjectName("SectionLabel")
        self.lbl_filter_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_filter_count.setMinimumWidth(110)

        row_top.addWidget(self.search_edit, stretch=1)
        row_top.addWidget(lbl_zone)
        row_top.addWidget(self.combo_zone)
        row_top.addSpacing(4)
        row_top.addWidget(lbl_target)
        row_top.addWidget(self.combo_target)
        row_top.addSpacing(6)
        row_top.addWidget(self.lbl_filter_count)
        main_layout.addLayout(row_top)

        # 2행: 임계값 조정 및 예외 규칙
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(10)

        lbl_over = QLabel("과화질 배수:")
        lbl_over.setObjectName("SectionLabel")
        self.spin_over_ratio = QDoubleSpinBox()
        self.spin_over_ratio.setRange(*THRESHOLD_BOUNDS["over_bitrate_ratio"])
        self.spin_over_ratio.setSingleStep(0.05)
        self.spin_over_ratio.setDecimals(2)
        self.spin_over_ratio.setSuffix("x")
        self.spin_over_ratio.setValue(float(DEFAULT_CONFIGS["THRESHOLD_OPTIMAL_BITRATE_RATE"]))
        self.spin_over_ratio.setFixedWidth(85)
        self.spin_over_ratio.setToolTip(
            "최적 비트레이트 대비 과화질 판정 배수 (기본 1.20x = 720p H.264 기준 1800kbps)"
        )
        self.spin_over_ratio.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_kf = QLabel("KF 상한:")
        lbl_kf.setObjectName("SectionLabel")
        self.spin_kf_limit = QDoubleSpinBox()
        self.spin_kf_limit.setRange(*THRESHOLD_BOUNDS["keyframe_threshold"])
        self.spin_kf_limit.setSingleStep(0.1)
        self.spin_kf_limit.setDecimals(1)
        self.spin_kf_limit.setSuffix("s")
        self.spin_kf_limit.setValue(float(DEFAULT_CONFIGS["THRESHOLD_KEYFRAME_INTERVAL"]))
        self.spin_kf_limit.setFixedWidth(80)
        self.spin_kf_limit.setToolTip(
            "허용 가능한 최대 키프레임 간격 (초과 시 1초 키프레임 교정 대상으로 분류)"
        )
        self.spin_kf_limit.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_small = QLabel("소형 기준:")
        lbl_small.setObjectName("SectionLabel")
        self.spin_small_mb = QDoubleSpinBox()
        self.spin_small_mb.setRange(*THRESHOLD_BOUNDS["small_footage_mb"])
        self.spin_small_mb.setSingleStep(5.0)
        self.spin_small_mb.setDecimals(1)
        self.spin_small_mb.setSuffix("MB")
        self.spin_small_mb.setValue(float(DEFAULT_CONFIGS["SMALL_FOOTAGE_SIZE_MB"]))
        self.spin_small_mb.setFixedWidth(92)
        self.spin_small_mb.setToolTip(
            "소형 클립(Footage)으로 판별할 용량 기준 (기본 30MB)"
        )
        self.spin_small_mb.valueChanged.connect(self._on_spin_or_text_changed)

        self._default_exc_tooltip = (
            "파일명 키워드 또는 파이썬 람다/조건식(예: v.duration < 10, v.fps >= 60)을 입력하면 "
            "해당 영상을 인코딩에서 제외하고 원본 보존으로 분류합니다. (쉼표로 다중 등록 가능)"
        )
        lbl_exc = QLabel("예외 규칙:")
        lbl_exc.setObjectName("SectionLabel")
        self.edit_exceptions = QLineEdit()
        self._action_exc = self.edit_exceptions.addAction(
            get_icon("ban", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.edit_exceptions.setPlaceholderText(
            "파일명 키워드(예: sample, 무손실) 또는 파이썬 조건식(예: v.duration < 10, v.fps >= 60)"
        )
        self.edit_exceptions.setClearButtonEnabled(True)
        self.edit_exceptions.setToolTip(self._default_exc_tooltip)
        self.edit_exceptions.textEdited.connect(self._on_exceptions_text_edited)
        self.edit_exceptions.textChanged.connect(self._on_exceptions_text_changed)
        self.edit_exceptions.returnPressed.connect(self._flush_exceptions_immediately)

        row_bottom.addWidget(lbl_over)
        row_bottom.addWidget(self.spin_over_ratio)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_kf)
        row_bottom.addWidget(self.spin_kf_limit)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_small)
        row_bottom.addWidget(self.spin_small_mb)
        row_bottom.addSpacing(8)
        row_bottom.addWidget(lbl_exc)
        row_bottom.addWidget(self.edit_exceptions, stretch=1)
        main_layout.addLayout(row_bottom)

        self.init_theme_listener()

    def set_busy(self, busy: bool) -> None:
        """
        - 백그라운드 작업 중 임계값 및 예외 규칙 컨트롤을 잠가 스레드 레이스 컨디션을 방지.
        """
        enabled = not busy
        self.spin_over_ratio.setEnabled(enabled)
        self.spin_kf_limit.setEnabled(enabled)
        self.spin_small_mb.setEnabled(enabled)
        self.edit_exceptions.setEnabled(enabled)

    def get_exception_tokens(self) -> list[str]:
        """현재 예외 규칙 입력란에 등록된 토큰 리스트를 순서대로 반환."""
        return split_exception_tokens(self.edit_exceptions.text())

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """예외 규칙 입력란을 지정된 토큰 리스트로 갱신."""
        self.edit_exceptions.setText(", ".join(tokens))

    def _validate_exceptions_ui(self) -> list[str]:
        errors: list[str] = []
        for token in self.get_exception_tokens():
            _, err = validate_exception_token(token)
            if err:
                errors.append(err)

        is_invalid = "true" if errors else "false"
        if self.edit_exceptions.property("invalid") != is_invalid:
            self.edit_exceptions.setProperty("invalid", is_invalid)
            style = self.edit_exceptions.style()
            if style is not None:
                style.unpolish(self.edit_exceptions)
                style.polish(self.edit_exceptions)
        if errors:
            self.edit_exceptions.setToolTip("\n".join(errors))
        else:
            self.edit_exceptions.setToolTip(self._default_exc_tooltip)
        return errors

    def _on_exceptions_text_edited(self, _text: str) -> None:
        self._user_editing_exc = True
        self._validate_exceptions_ui()
        if not self._updating:
            self._exc_debounce_timer.start()

    def _on_exceptions_text_changed(self, _text: str) -> None:
        if self._user_editing_exc:
            self._user_editing_exc = False
            return
        self._exc_debounce_timer.stop()
        self._validate_exceptions_ui()
        self._on_spin_or_text_changed()

    def _flush_exceptions_immediately(self) -> None:
        self._exc_debounce_timer.stop()
        self._validate_exceptions_ui()
        self._on_spin_or_text_changed()

    def _on_spin_or_text_changed(self) -> None:
        if not self._updating:
            self.thresholds_changed.emit()

    def sync_from_config(self, cfg: ClassifierConfig) -> None:
        """
        - ClassifierConfig 설정 객체의 임계값을 스핀박스 컨트롤에 동기화.
        """
        self._updating = True
        try:
            self.spin_over_ratio.setValue(cfg.over_bitrate_ratio)
            self.spin_kf_limit.setValue(cfg.keyframe_threshold)
            self.spin_small_mb.setValue(cfg.small_footage_mb)
        finally:
            self._updating = False

    def apply_to_classifier(self, classifier: VideoClassifier) -> None:
        """
        - 현재 UI의 스핀박스 임계값과 예외 규칙들을 VideoClassifier 인스턴스에 반영.
        """
        classifier.set_over_bitrate_ratio(self.spin_over_ratio.value())
        classifier.set_keyframe_threshold(self.spin_kf_limit.value())
        classifier.set_small_footage_mb(self.spin_small_mb.value())

        classifier.clear_exception_rules()
        for token in self.get_exception_tokens():
            rule_fn = parse_exception_token(token)
            if rule_fn is not None:
                classifier.add_exception_rule(rule_fn)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 입력창 액션 아이콘 색상을 갱신.
        """
        if self._action_search is not None:
            self._action_search.setIcon(
                get_icon("search", palette=palette, size=14, pad_right=7)
            )
        if self._action_exc is not None:
            self._action_exc.setIcon(
                get_icon("ban", palette=palette, size=14, pad_right=7)
            )
