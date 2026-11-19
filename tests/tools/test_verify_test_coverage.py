"""테스트 대응과 함수 실행 검사 스크립트(`scripts/verify_test_coverage.py`) 단위 테스트"""

import pathlib
import tempfile
import textwrap
import unittest

from coverage import CoverageData
from scripts.verify_test_coverage import (
    Exceptions,
    check_coverage,
    expected_test_path,
    function_lines,
    load_exceptions,
    load_executed_lines,
    module_functions,
    modules_without_test,
    progress_line,
    shrink_exceptions,
    source_modules,
    unexecuted_functions,
    write_exceptions,
)

_SAMPLE = textwrap.dedent(
    '''
    import abc
    from typing import overload


    def plain(x: int) -> int:
        """설명"""
        return x + 1


    def doc_only() -> None:
        """설명만 있음"""


    class Port(abc.ABC):
        @abc.abstractmethod
        def run(self) -> None:
            raise NotImplementedError

        def size(self) -> int:
            return 1

        @property
        def name(self) -> str:
            return "a"

        @name.setter
        def name(self, value: str) -> None:
            self._name = value


    def outer() -> int:
        def inner() -> int:
            return 2
        return inner()


    @overload
    def conv(x: int) -> int: ...
    @overload
    def conv(x: str) -> str: ...
    def conv(x: object) -> object:
        return x
    '''
)


def _write(root: pathlib.Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ModuleMappingTests(unittest.TestCase):
    """모듈별 대응 테스트 경로 규칙과 누락 판정을 검증"""

    def test_expected_test_path_mirrors_source_path(self) -> None:
        self.assertEqual(expected_test_path("src/core/encode/encode_gate.py"), "tests/core/encode/test_encode_gate.py")
        self.assertEqual(expected_test_path("src/classifier.py"), "tests/test_classifier.py")

    def test_modules_without_test_skips_package_and_entry_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for rel in ("src/core/a.py", "src/core/b.py", "src/core/__init__.py", "src/__main__.py"):
                _write(root, rel)
            _write(root, "tests/core/test_a.py")
            modules = source_modules(root)
            self.assertEqual(modules_without_test(root, modules), {"src/core/b.py"})


class FunctionLinesTests(unittest.TestCase):
    """함수 이름 규칙과 검사에서 빼는 선언(설명만, 추상, 오버로드)을 검증"""

    def test_names_and_skipped_declarations(self) -> None:
        names = set(function_lines(_SAMPLE))
        self.assertEqual(
            names,
            {"plain", "Port.size", "Port.name", "Port.name#2", "outer", "outer.inner", "conv"},
        )

    def test_docstring_line_is_not_counted_as_body(self) -> None:
        lines = function_lines(_SAMPLE)["plain"]
        source_lines = _SAMPLE.splitlines()
        self.assertEqual([source_lines[n - 1].strip() for n in sorted(lines)], ["return x + 1"])

    def test_unexecuted_functions_uses_body_lines_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write(root, "src/m.py", "def a() -> int:\n    return 1\n\n\ndef b() -> int:\n    return 2\n")
            functions = module_functions(root, ["src/m.py"])
            self.assertEqual(set(functions), {"src/m.py::a", "src/m.py::b"})
            executed = {"src/m.py": {1, 2, 5}}
            self.assertEqual(unexecuted_functions(functions, executed), {"src/m.py::b"})


class CoverageDataTests(unittest.TestCase):
    """커버리지 데이터 파일에서 저장소 상대 경로별 실행 줄을 읽는지 검증"""

    def test_lines_are_keyed_by_repo_relative_path_and_outside_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = pathlib.Path(tmp)
            inside = root / "src" / "m.py"
            data_file = root / ".coverage"
            data = CoverageData(basename=str(data_file))
            data.add_lines({str(inside): [1, 2], str(pathlib.Path(other) / "x.py"): [3]})
            data.write()
            self.assertEqual(load_executed_lines(data_file, root), {"src/m.py": {1, 2}})


class ExceptionRatchetTests(unittest.TestCase):
    """예외 목록 대조, 실행 여부 불안정 함수 처리, 해소 항목 제거(항목 추가 없음)를 검증"""

    _IDS = frozenset({"src/a.py::f", "src/a.py::g", "src/a.py::t"})

    def test_new_items_fail_and_listed_items_pass(self) -> None:
        exceptions = Exceptions(modules=frozenset({"src/a.py"}), functions=frozenset({"src/a.py::f"}))
        report = check_coverage({"src/a.py", "src/b.py"}, set(self._IDS), {"src/a.py::f", "src/a.py::g"}, exceptions)
        self.assertEqual(len(report.failures), 2)
        self.assertEqual(report.stale, [])

    def test_resolved_entries_are_reported_for_removal(self) -> None:
        exceptions = Exceptions(modules=frozenset({"src/a.py"}), functions=frozenset({"src/a.py::f"}))
        report = check_coverage(set(), set(self._IDS), set(), exceptions)
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.stale), 2)

    def test_nondeterministic_entries_pass_whether_executed_or_not(self) -> None:
        exceptions = Exceptions(nondeterministic=frozenset({"src/a.py::t"}))
        for unexecuted in (set(), {"src/a.py::t"}):
            report = check_coverage(set(), set(self._IDS), unexecuted, exceptions)
            self.assertEqual((report.failures, report.stale), ([], []))

    def test_nondeterministic_entry_for_missing_function_is_reported(self) -> None:
        exceptions = Exceptions(nondeterministic=frozenset({"src/a.py::gone"}))
        report = check_coverage(set(), set(self._IDS), None, exceptions)
        self.assertEqual(len(report.stale), 1)

    def test_function_check_is_skipped_without_coverage_data(self) -> None:
        exceptions = Exceptions(functions=frozenset({"src/a.py::f"}))
        report = check_coverage(set(), set(self._IDS), None, exceptions)
        self.assertEqual((report.failures, report.stale), ([], []))
        self.assertEqual(shrink_exceptions(set(), set(self._IDS), None, exceptions), exceptions)

    def test_shrink_removes_resolved_entries_and_never_adds(self) -> None:
        exceptions = Exceptions(
            modules=frozenset({"src/a.py", "src/b.py"}),
            functions=frozenset({"src/a.py::f"}),
            nondeterministic=frozenset({"src/a.py::t", "src/a.py::gone"}),
            baseline_total=9,
        )
        shrunk = shrink_exceptions({"src/b.py", "src/c.py"}, set(self._IDS), {"src/a.py::g"}, exceptions)
        expected = Exceptions(
            modules=frozenset({"src/b.py"}), nondeterministic=frozenset({"src/a.py::t"}), baseline_total=9
        )
        self.assertEqual(shrunk, expected)

    def test_exceptions_round_trip_through_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "scripts").mkdir()
            exceptions = Exceptions(
                modules=frozenset({"src/b.py", "src/a.py"}),
                functions=frozenset({"src/a.py::f"}),
                nondeterministic=frozenset({"src/a.py::t"}),
                baseline_total=7,
            )
            write_exceptions(root, exceptions)
            self.assertEqual(load_exceptions(root), exceptions)
            self.assertEqual(load_exceptions(root / "missing"), Exceptions())


class ProgressLineTests(unittest.TestCase):
    """남은 항목 수와 기준선 대비 해소 비율 표시를 검증"""

    def test_remaining_counts_all_three_lists(self) -> None:
        exceptions = Exceptions(
            modules=frozenset({"a"}), functions=frozenset({"b", "c"}), nondeterministic=frozenset({"d"})
        )
        self.assertEqual(exceptions.remaining, 4)

    def test_progress_shows_resolved_count_and_percent(self) -> None:
        exceptions = Exceptions(modules=frozenset({"a"}), functions=frozenset({"b"}), baseline_total=8)
        self.assertEqual(progress_line(exceptions), "진행률: 남은 2건 / 기준선 8건 (해소 6건, 75.0%)")

    def test_progress_is_empty_without_baseline_and_flags_growth_beyond_baseline(self) -> None:
        self.assertEqual(progress_line(Exceptions(modules=frozenset({"a"}))), "")
        grown = Exceptions(modules=frozenset({"a", "b", "c"}), baseline_total=2)
        self.assertIn("기준선 초과 1건", progress_line(grown))

    def test_baseline_is_never_changed_by_write_or_shrink(self) -> None:
        exceptions = Exceptions(modules=frozenset({"a"}), baseline_total=5)
        self.assertEqual(shrink_exceptions(set(), set(), set(), exceptions).baseline_total, 5)


if __name__ == "__main__":
    unittest.main()
