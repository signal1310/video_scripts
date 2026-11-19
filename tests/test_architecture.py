"""계층 간 임포트 방향과 지연 로딩 경계 검증"""

import ast
import json
import os
import pathlib
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


def _imported_layers(path: pathlib.Path) -> set[str]:
    """파일이 절대 경로로 임포트하는 src 하위 계층 이름 집합"""
    layers: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "src" and len(parts) > 1:
                layers.add(parts[1])
    return layers


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


class TestLazyLoading(unittest.TestCase):
    """상위 계층 모듈이 필요하지 않은 임포트 시점에 로드되지 않는지 검증"""

    def test_core_import_does_not_load_facade_or_cui(self) -> None:
        """core 프로버 임포트가 파사드, CUI, tabulate를 로드하지 않음을 검증"""
        loaded = _loaded_modules_after("import src.core.prober")
        self.assertTrue({"src.classifier", "src.cui", "tabulate"}.isdisjoint(loaded))

    def test_gui_import_does_not_load_cui(self) -> None:
        """GUI 패키지 임포트가 CUI 계층을 로드하지 않음을 검증"""
        self.assertNotIn("src.cui", _loaded_modules_after("import src.gui"))


if __name__ == "__main__":
    unittest.main()
