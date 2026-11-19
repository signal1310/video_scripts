"""계층 간 임포트 방향과 지연 로딩 경계 검증"""

import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

from tests._repo_root import REPO_ROOT as _REPO_ROOT

_SRC = _REPO_ROOT / "src"
_FACADE = "classifier"

# 계층별 임포트 허용 대상: gui, cui는 서로를 모르고, cui는 service 대신 파사드를 거치며, core는 자기 계층만 참조
_ALLOWED_TARGETS: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "service": frozenset({"core", "service"}),
    "gui": frozenset({"core", "service", "gui"}),
    "cui": frozenset({"core", "cui", _FACADE}),
}


def _imported_modules(path: pathlib.Path) -> set[str]:
    """파일이 절대 경로로 임포트하는 `src` 모듈 이름 집합"""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return {name for name in modules if name.split(".")[0] == "src"}


def _imported_layers(path: pathlib.Path) -> set[str]:
    """파일이 절대 경로로 임포트하는 src 하위 계층 이름 집합"""
    return {name.split(".")[1] for name in _imported_modules(path) if "." in name}


def _subpackage_of(module: str) -> str | None:
    """`src.<계층>.<하위 패키지>...` 모듈이 속한 하위 패키지 이름. 계층 바로 아래 모듈이면 None"""
    parts = module.split(".")
    if len(parts) >= 3 and (_SRC / parts[1] / parts[2] / "__init__.py").is_file():
        return parts[2]
    return None


def _subpackages(layer: str) -> list[pathlib.Path]:
    """계층 아래 하위 패키지 폴더 목록"""
    return sorted(p.parent for p in (_SRC / layer).glob("*/__init__.py"))


def _find_cycle(graph: dict[str, set[str]]) -> list[str]:
    """방향 그래프의 순환 하나를 노드 목록으로 반환. 없으면 빈 목록"""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, set())):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt):], nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return []

    for start in sorted(graph):
        if start not in state:
            found = visit(start)
            if found:
                return found
    return []


def _loaded_modules_after(statement: str) -> set[str]:
    """새 인터프리터에서 임포트 문을 실행한 뒤 로드된 모듈 이름 집합 반환"""
    code = f"import json, sys; {statement}; print(json.dumps(sorted(sys.modules)))"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_REPO_ROOT, capture_output=True, text=True, check=True, env=env
    )
    return set(json.loads(out.stdout))


class TestLayerBoundaries(unittest.TestCase):
    """소스 파일의 임포트가 계층 방향 규칙을 지키는지 검증"""

    def test_static_imports_follow_layer_matrix(self) -> None:
        """core, service, gui, cui 각 파일이 허용된 계층만 임포트함을 검증"""
        violations: list[str] = []
        for layer, allowed in _ALLOWED_TARGETS.items():
            for path in sorted((_SRC / layer).rglob("*.py")):
                forbidden = _imported_layers(path) - allowed
                if forbidden:
                    violations.append(f"{path.relative_to(_REPO_ROOT).as_posix()} -> {sorted(forbidden)}")
        self.assertEqual(violations, [])

    def test_every_source_package_has_a_layer_rule(self) -> None:
        """새 최상위 패키지가 생기면 계층 규칙에 등록하도록 강제"""
        packages = {p.name for p in _SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
        self.assertEqual(packages, set(_ALLOWED_TARGETS))


class TestPackageLayout(unittest.TestCase):
    """`AGENTS.md` 1절 디렉터리 규칙(영역 패키지 한 단계, 소유 영역 우선)과 테스트 미러 구조를 검증"""

    def test_source_packages_are_at_most_one_level_deep(self) -> None:
        """`src/<계층>/<패키지>/` 아래에 더 깊은 하위 패키지가 없음을 검증"""
        too_deep = sorted(
            path.relative_to(_REPO_ROOT).as_posix()
            for path in _SRC.rglob("*.py")
            if len(path.relative_to(_SRC).parts) > 3
        )
        self.assertEqual(too_deep, [])

    def test_architecture_package_list_matches_source(self) -> None:
        """ARCHITECTURE.md 원칙 2의 패키지 목록이 실제 하위 패키지와 일치함을 검증"""
        arch_text = (_REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        package_line = next(line for line in arch_text.splitlines() if line.strip().startswith("- 영역 패키지:"))
        documented = set(re.findall(r"`((?:core|service|gui|cui)/[a-z_]+)`", package_line))
        actual = {f"{layer}/{pkg.name}" for layer in _ALLOWED_TARGETS for pkg in _subpackages(layer)}
        self.assertEqual(documented, actual)

    def test_core_area_packages_do_not_import_each_other(self) -> None:
        """core 영역 패키지가 다른 core 영역을 임포트하지 않음을 검증(여러 영역이 쓰는 어휘는 core 바로 아래)"""
        violations: list[str] = []
        for package in _subpackages("core"):
            for path in sorted(package.glob("*.py")):
                for module in sorted(_imported_modules(path)):
                    target = _subpackage_of(module)
                    if module.startswith("src.core.") and target not in (None, package.name):
                        violations.append(f"{path.relative_to(_REPO_ROOT).as_posix()} -> {module}")
        self.assertEqual(violations, [])

    def test_gui_packages_have_no_import_cycles(self) -> None:
        """gui 하위 패키지 사이의 임포트 방향에 순환이 없음을 검증"""
        graph: dict[str, set[str]] = {}
        for package in _subpackages("gui"):
            targets: set[str] = set()
            for path in package.glob("*.py"):
                for module in _imported_modules(path):
                    target = _subpackage_of(module)
                    if module.startswith("src.gui.") and target not in (None, package.name):
                        targets.add(target)
            graph[package.name] = targets
        self.assertEqual(_find_cycle(graph), [])

    def test_tests_mirror_source_packages(self) -> None:
        """`tests/` 하위 폴더가 `src` 패키지를 따르고(`tests/tools` 제외), 바로 아래에는 공용 도우미와 `src` 바로 아래 모듈의 테스트만 있음을 검증"""
        tests_root = _REPO_ROOT / "tests"
        unmatched: list[str] = []
        for init in sorted(tests_root.rglob("__init__.py")):
            rel = init.parent.relative_to(tests_root)
            if rel.parts and rel.parts[0] != "tools" and not (_SRC / rel / "__init__.py").is_file():
                unmatched.append(rel.as_posix())
        missing_init = sorted(
            path.parent.relative_to(tests_root).as_posix()
            for path in tests_root.rglob("test_*.py")
            if not (path.parent / "__init__.py").is_file()
        )
        top_level_tests = sorted(
            path.name
            for path in tests_root.glob("test_*.py")
            if not (_SRC / path.name.removeprefix("test_")).is_file()
        )
        self.assertEqual((unmatched, missing_init, top_level_tests), ([], [], []))


class TestLazyLoading(unittest.TestCase):
    """상위 계층 모듈이 필요하지 않은 임포트 시점에 로드되지 않는지 검증"""

    def test_core_import_does_not_load_facade_or_cui(self) -> None:
        """core 프로버 임포트가 파사드, CUI, tabulate를 로드하지 않음을 검증"""
        loaded = _loaded_modules_after("import src.core.analysis.prober")
        self.assertTrue({"src.classifier", "src.cui", "tabulate"}.isdisjoint(loaded))

    def test_gui_import_does_not_load_cui(self) -> None:
        """GUI 패키지 임포트가 CUI 계층을 로드하지 않음을 검증"""
        self.assertNotIn("src.cui", _loaded_modules_after("import src.gui"))


if __name__ == "__main__":
    unittest.main()
