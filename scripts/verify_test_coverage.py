"""테스트 대응과 함수 실행 검사 스크립트

- 모듈 대응: `src/<경로>/<모듈>.py`마다 `tests/<경로>/test_<모듈>.py`가 있어야 함(`__init__.py`, `__main__.py` 제외)
- 함수 실행: `src`의 모든 함수, 메서드가 전체 테스트 실행에서 본문 한 줄 이상 실행되어야 함(커버리지 데이터 기준)
- 실행 여부만 보며 결과 검증(assert)까지 보장하지 않음. Qt 스레드(`QThread`) 안에서만 도는 코드는 커버리지가 추적하지 못함
- 기존 미충족 항목은 예외 파일에서 관리. 스크립트는 항목을 추가하지 않고, `--write`는 해소되거나 사라진 항목만 제거
- 해소된 항목이 예외 파일에 남아 있으면 실패로 처리해 목록이 줄어들기만 하게 함
- 타이머처럼 실행마다 실행 여부가 달라지는 함수는 별도 목록(임시 보관소, 목표 0건)에 두고 실행 여부와 무관하게 통과(사라지면 정리 대상)
- 기준선(`baseline_total`)은 예외 목록 도입 시점의 전체 항목 수로, 스크립트가 바꾸지 않음. 실행할 때마다 남은 항목 수와 해소 비율을 출력
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from coverage import CoverageData

EXCEPTIONS_REL_PATH: str = "scripts/test_coverage_exceptions.json"
MODULES_KEY: str = "modules_without_test"
FUNCTIONS_KEY: str = "unexecuted_functions"
NONDETERMINISTIC_KEY: str = "nondeterministic_functions"
BASELINE_KEY: str = "baseline_total"
PERCENT: int = 100
SOURCE_DIR: str = "src"
TESTS_DIR: str = "tests"
TEST_PREFIX: str = "test_"
SKIPPED_MODULE_NAMES: frozenset[str] = frozenset({"__init__.py", "__main__.py"})
# - 구현이 없는 선언(추상 메서드, 오버로드 서명, 프로토콜)은 실행할 본문이 없어 검사에서 뺌
STUB_DECORATORS: frozenset[str] = frozenset({"abstractmethod", "overload"})
FUNCTION_ID_SEP: str = "::"
DUPLICATE_SEP: str = "#"
JSON_INDENT: int = 2


@dataclass(frozen=True)
class Exceptions:
    """예외 파일: 대응 테스트가 없는 모듈, 실행되지 않는 함수, 실행 여부가 실행마다 달라지는 함수 목록"""

    modules: frozenset[str] = frozenset()
    functions: frozenset[str] = frozenset()
    nondeterministic: frozenset[str] = frozenset()
    baseline_total: int = 0

    @property
    def remaining(self) -> int:
        """남은 예외 항목 수(모듈, 미실행 함수, 실행 여부 불안정 함수의 합)"""
        return len(self.modules) + len(self.functions) + len(self.nondeterministic)


@dataclass(frozen=True)
class CoverageReport:
    """검사 결과. 실패(새 미충족 항목)와 해소 후보(예외 파일에서 지울 항목)"""

    failures: list[str]
    stale: list[str]


def source_modules(repo_root: pathlib.Path) -> list[str]:
    """검사 대상 `src` 모듈의 POSIX 상대 경로 목록"""
    return sorted(
        path.relative_to(repo_root).as_posix()
        for path in (repo_root / SOURCE_DIR).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def expected_test_path(module_rel: str) -> str:
    """모듈에 대응하는 테스트 파일 경로(`src/core/x.py`는 `tests/core/test_x.py`, `src/x.py`는 `tests/test_x.py`)"""
    parts = pathlib.PurePosixPath(module_rel).parts
    return pathlib.PurePosixPath(TESTS_DIR, *parts[1:-1], TEST_PREFIX + parts[-1]).as_posix()


def modules_without_test(repo_root: pathlib.Path, modules: list[str]) -> set[str]:
    """대응 테스트 파일이 없는 모듈 집합"""
    return {
        module
        for module in modules
        if pathlib.PurePosixPath(module).name not in SKIPPED_MODULE_NAMES
        and not (repo_root / expected_test_path(module)).is_file()
    }


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ""


def _is_stub_statement(node: ast.stmt) -> bool:
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return node.value.value is Ellipsis or isinstance(node.value.value, str)
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _body_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """함수 본문 문장이 차지하는 줄 번호. 구현이 없는 선언이면 빈 집합"""
    if any(_decorator_name(dec) in STUB_DECORATORS for dec in node.decorator_list):
        return set()
    if all(_is_stub_statement(stmt) for stmt in node.body):
        return set()
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    lines: set[int] = set()
    for stmt in body:
        lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    return lines


def _walk_functions(
    nodes: list[ast.stmt], prefix: str
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in nodes:
        if isinstance(node, ast.ClassDef):
            yield from _walk_functions(node.body, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + node.name, node
            yield from _walk_functions(node.body, f"{prefix}{node.name}.")
        else:
            for field_name in ("body", "orelse", "finalbody", "handlers"):
                children = getattr(node, field_name, None)
                if isinstance(children, list):
                    yield from _walk_functions([c for c in children if isinstance(c, ast.stmt)], prefix)


def function_lines(source: str) -> dict[str, set[int]]:
    """모듈 소스의 함수별 본문 줄 번호. 이름은 `클래스.메서드`, `바깥.안쪽` 형식이며 같은 이름은 `#2`부터 붙임"""
    result: dict[str, set[int]] = {}
    for qualname, node in _walk_functions(ast.parse(source).body, ""):
        lines = _body_lines(node)
        if not lines:
            continue
        name = qualname
        index = 2
        while name in result:
            name = f"{qualname}{DUPLICATE_SEP}{index}"
            index += 1
        result[name] = lines
    return result


def module_functions(repo_root: pathlib.Path, modules: list[str]) -> dict[str, set[int]]:
    """모든 검사 대상 함수의 ID(`경로::이름`)별 본문 줄 번호"""
    found: dict[str, set[int]] = {}
    for module in modules:
        source = (repo_root / module).read_text(encoding="utf-8")
        for name, lines in function_lines(source).items():
            found[module + FUNCTION_ID_SEP + name] = lines
    return found


def unexecuted_functions(functions: Mapping[str, set[int]], executed: Mapping[str, set[int]]) -> set[str]:
    """본문이 한 줄도 실행되지 않은 함수 ID 집합"""
    return {
        function_id
        for function_id, lines in functions.items()
        if not lines & executed.get(function_id.split(FUNCTION_ID_SEP)[0], set())
    }


def load_executed_lines(data_file: pathlib.Path, repo_root: pathlib.Path) -> dict[str, set[int]]:
    """커버리지 데이터 파일에서 저장소 상대 경로별 실행 줄 번호를 읽음"""
    data = CoverageData(basename=str(data_file))
    data.read()
    root_norm = os.path.normcase(str(repo_root.resolve()))
    executed: dict[str, set[int]] = {}
    for measured in data.measured_files():
        measured_norm = os.path.normcase(os.path.abspath(measured))
        if not measured_norm.startswith(root_norm + os.sep):
            continue
        rel = pathlib.PurePath(os.path.relpath(measured_norm, root_norm)).as_posix()
        executed[rel] = set(data.lines(measured) or [])
    return executed


def progress_line(exceptions: Exceptions) -> str:
    """남은 예외 항목 수와 기준선 대비 해소 비율 한 줄. 기준선이 없으면 빈 문자열"""
    baseline = exceptions.baseline_total
    if baseline <= 0:
        return ""
    remaining = exceptions.remaining
    if remaining > baseline:
        return f"진행률: 남은 {remaining}건 / 기준선 {baseline}건 (기준선 초과 {remaining - baseline}건)"
    resolved = baseline - remaining
    return f"진행률: 남은 {remaining}건 / 기준선 {baseline}건 (해소 {resolved}건, {resolved * PERCENT / baseline:.1f}%)"


def _require_str_list(raw: object, key: str) -> frozenset[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"예외 파일 '{key}' 형식 오류: {EXCEPTIONS_REL_PATH}")
    return frozenset(raw)


def _require_int(raw: object, key: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"예외 파일 '{key}' 형식 오류: {EXCEPTIONS_REL_PATH}")
    return raw


def load_exceptions(repo_root: pathlib.Path) -> Exceptions:
    """예외 파일을 읽고, 파일이 없으면 빈 목록 반환"""
    path = repo_root / EXCEPTIONS_REL_PATH
    if not path.is_file():
        return Exceptions()
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"예외 파일 형식 오류: {EXCEPTIONS_REL_PATH}")
    return Exceptions(
        modules=_require_str_list(raw.get(MODULES_KEY, []), MODULES_KEY),
        functions=_require_str_list(raw.get(FUNCTIONS_KEY, []), FUNCTIONS_KEY),
        nondeterministic=_require_str_list(raw.get(NONDETERMINISTIC_KEY, []), NONDETERMINISTIC_KEY),
        baseline_total=_require_int(raw.get(BASELINE_KEY, 0), BASELINE_KEY),
    )


def write_exceptions(repo_root: pathlib.Path, exceptions: Exceptions) -> None:
    """예외 파일을 정렬 JSON으로 저장"""
    payload = {
        BASELINE_KEY: exceptions.baseline_total,
        MODULES_KEY: sorted(exceptions.modules),
        FUNCTIONS_KEY: sorted(exceptions.functions),
        NONDETERMINISTIC_KEY: sorted(exceptions.nondeterministic),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=JSON_INDENT) + "\n"
    (repo_root / EXCEPTIONS_REL_PATH).write_text(text, encoding="utf-8", newline="\n")


def check_coverage(
    missing_tests: set[str],
    function_ids: set[str],
    unexecuted: set[str] | None,
    exceptions: Exceptions,
) -> CoverageReport:
    """현재 미충족 항목을 예외 파일과 대조. 함수 실행 정보가 없으면(None) 모듈 대응과 함수 존재만 검사"""
    failures = [f"대응 테스트 없음: {m} (필요: {expected_test_path(m)})" for m in sorted(missing_tests - exceptions.modules)]
    stale = [f"모듈 항목 해소: {m}" for m in sorted(exceptions.modules - missing_tests)]
    stale += [f"없는 함수: {f}" for f in sorted(exceptions.nondeterministic - function_ids)]
    if unexecuted is not None:
        allowed = exceptions.functions | exceptions.nondeterministic
        failures += [f"테스트에서 실행되지 않는 함수: {f}" for f in sorted(unexecuted - allowed)]
        stale += [f"함수 항목 해소: {f}" for f in sorted(exceptions.functions - unexecuted)]
    return CoverageReport(failures=failures, stale=stale)


def shrink_exceptions(
    missing_tests: set[str],
    function_ids: set[str],
    unexecuted: set[str] | None,
    exceptions: Exceptions,
) -> Exceptions:
    """해소되거나 사라진 항목만 제거. 새 항목은 추가하지 않음"""
    functions = exceptions.functions if unexecuted is None else exceptions.functions & unexecuted
    return Exceptions(
        modules=exceptions.modules & missing_tests,
        functions=functions,
        nondeterministic=exceptions.nondeterministic & function_ids,
        baseline_total=exceptions.baseline_total,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 모듈 대응과 함수 실행을 예외 파일과 대조하고 필요 시 해소된 항목을 제거"""
    parser = argparse.ArgumentParser(description="src 모듈의 대응 테스트와 함수 실행 여부를 점검합니다.")
    parser.add_argument(
        "--data-file",
        type=pathlib.Path,
        help="전체 테스트를 실행한 커버리지 데이터 파일. 없으면 모듈 대응만 검사합니다.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="예외 파일만 읽어 진행률 한 줄을 출력하고 끝냅니다(검사를 실행하지 않습니다).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="해소되거나 사라진 예외 항목만 제거합니다(항목을 추가하지 않습니다).",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.progress:
        print(progress_line(load_exceptions(repo_root)))
        return 0
    modules = source_modules(repo_root)
    missing_tests = modules_without_test(repo_root, modules)
    functions = module_functions(repo_root, modules)
    unexecuted: set[str] | None = None
    if args.data_file is not None:
        unexecuted = unexecuted_functions(functions, load_executed_lines(args.data_file, repo_root))
    exceptions = load_exceptions(repo_root)

    if args.write:
        exceptions = shrink_exceptions(missing_tests, set(functions), unexecuted, exceptions)
        write_exceptions(repo_root, exceptions)

    report = check_coverage(missing_tests, set(functions), unexecuted, exceptions)
    for line in report.failures:
        print(f"[test-coverage FAIL] {line}", file=sys.stderr)
    for line in report.stale:
        print(f"[test-coverage FAIL] {line} (예외 파일에서 제거 필요, --write)", file=sys.stderr)
    if report.failures or report.stale:
        return 1
    print(
        f"[test-coverage] OK (모듈 {len(modules)}개, 대응 테스트 없음 예외 {len(exceptions.modules)}건, "
        f"미실행 함수 예외 {len(exceptions.functions)}건, 실행 여부 불안정 함수 {len(exceptions.nondeterministic)}건)"
    )
    line = progress_line(exceptions)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
