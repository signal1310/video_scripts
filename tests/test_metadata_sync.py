import ast
import fnmatch
import json
import pathlib
import re
import unittest
from collections import Counter
from dataclasses import fields

from src.core.config import DEFAULT_CONFIGS
from src.core.file_manager import VIDEO_EXTENSIONS
from src.core.models import VideoProps
from src.gui.table_model import VideoTableModel

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DOC_FILES = ("README.md", "ARCHITECTURE.md", "AGENTS.md", "TODO.md")


def _collect_module_top_symbols(py_path: pathlib.Path) -> set[str]:
    """AST 파싱으로 모듈 최상위 정의 및 re-export 심볼 이름 집합 수집"""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                symbols.add(alias.asname or alias.name)
    return symbols


def _extract_string_collection(node: ast.AST) -> set[str]:
    """AST 상수 컨테이너(dict/set/frozenset 호출)에서 문자열 리터럴 집합 추출"""
    target = node
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
        and node.args
    ):
        target = node.args[0]

    values: set[str] = set()
    if isinstance(target, ast.Dict):
        for key in target.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                values.add(key.value)
    elif isinstance(target, (ast.Set, ast.List, ast.Tuple)):
        for elt in target.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.add(elt.value)
    return values


def _extract_exception_rule_whitelists() -> tuple[set[str], set[str], set[str]]:
    """exception_rules.py에서 허용 함수·문자열 메서드·VideoProps 속성 집합 추출"""
    tree = ast.parse(
        (_REPO_ROOT / "src" / "core" / "exception_rules.py").read_text(encoding="utf-8")
    )
    extracted: dict[str, set[str]] = {}
    targets = {"_SAFE_FUNCTIONS", "_SAFE_STR_METHODS", "_SAFE_VIDEO_ATTRS"}
    for node in tree.body:
        name: str | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
            value = node.value

        if name in targets and value is not None:
            extracted[name] = _extract_string_collection(value)

    return (
        extracted.get("_SAFE_FUNCTIONS", set()),
        extracted.get("_SAFE_STR_METHODS", set()),
        extracted.get("_SAFE_VIDEO_ATTRS", set()),
    )


class TestMetadataSync(unittest.TestCase):
    """문서·메타파일·IDE 설정과 실제 코드베이스 간 일관성을 자동 검증"""

    def test_env_example_matches_default_configs(self) -> None:
        """.env_example과 문서 내 설정 개수가 DEFAULT_CONFIGS와 1:1 일치하는지 검증"""
        env_example = (_REPO_ROOT / ".env_example").read_text(encoding="utf-8")
        example_pairs: dict[str, str] = {}
        for raw_line in env_example.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            example_pairs[key.strip()] = val.strip()

        self.assertEqual(set(example_pairs.keys()), set(DEFAULT_CONFIGS.keys()))
        self.assertEqual(example_pairs, DEFAULT_CONFIGS)

        expected_count_token = f"{len(DEFAULT_CONFIGS)}개"
        for doc_name in ("README.md", "ARCHITECTURE.md"):
            doc_text = (_REPO_ROOT / doc_name).read_text(encoding="utf-8")
            self.assertIn(
                expected_count_token,
                doc_text,
                f"{doc_name}의 .env 설정 키 개수 표기가 {expected_count_token}와 다름",
            )

    def test_shana_presets_match_target_directory_names(self) -> None:
        """shana_presets 디렉터리 내 XML 파일 접두사가 기본 분류 폴더명과 일치하는지 검증"""
        preset_dir = _REPO_ROOT / "shana_presets"
        if not preset_dir.is_dir():
            self.skipTest("shana_presets 디렉터리가 존재하지 않음")

        xml_names = [p.name for p in preset_dir.glob("*.xml")]
        for key in ("DIR_STANDARD_CQ22", "DIR_LOW_QUALITY_KEYFRAME"):
            dir_name = DEFAULT_CONFIGS[key]
            self.assertTrue(
                any(name.startswith(dir_name) for name in xml_names),
                f"shana_presets 내 '{dir_name}' 접두사 XML 프리셋 누락",
            )

    def test_src_modules_documented_in_architecture_and_readme(self) -> None:
        """src 모듈과 문서 간 추가 누락·삭제된 유령 참조·동명 모듈 표기를 양방향 검증"""
        arch_text = (_REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        basename_counts: Counter[str] = Counter()

        for py_path in sorted((_REPO_ROOT / "src").rglob("*.py")):
            rel_posix = py_path.relative_to(_REPO_ROOT).as_posix()
            if py_path.name == "__init__.py":
                continue

            with self.subTest(module=rel_posix):
                self.assertIn(
                    rel_posix,
                    arch_text,
                    f"ARCHITECTURE.md에 모듈 링크 누락: {rel_posix}",
                )
                if not py_path.name.startswith("__"):
                    basename_counts[py_path.name] += 1
                    self.assertIn(
                        py_path.name,
                        readme_text,
                        f"README.md 구조 안내에 모듈명 누락: {py_path.name}",
                    )

        for name, count in basename_counts.items():
            if count > 1:
                self.assertGreaterEqual(
                    readme_text.count(name),
                    count,
                    f"README.md에 동명 모듈 '{name}' 기재 횟수가 {count}회 미만임",
                )

        # 문서 전반의 src/**/*.py 참조가 삭제·개명된 유령 경로가 아닌지 검증
        src_ref_re = re.compile(r"(?<![A-Za-z0-9_])(src/[A-Za-z0-9_/]+\.py)\b")
        for doc_name in _DOC_FILES:
            doc_text = (_REPO_ROOT / doc_name).read_text(encoding="utf-8")
            for rel_ref in set(src_ref_re.findall(doc_text)):
                with self.subTest(doc=doc_name, ghost_check=rel_ref):
                    self.assertTrue(
                        (_REPO_ROOT / rel_ref).is_file(),
                        f"{doc_name}에 존재하지 않는 모듈 참조 잔존: {rel_ref}",
                    )

    def test_architecture_module_symbols_exist_in_source(self) -> None:
        """ARCHITECTURE.md 모듈 맵에 기재된 주요 심볼이 실제 소스 AST에 존재하는지 검증"""
        arch_text = (_REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        row_re = re.compile(r"^\| \[[^\]]+\]\((src/[^)]+\.py)\) \| ([^|]+) \|", re.M)

        for rel_path, symbols_cell in row_re.findall(arch_text):
            mod_path = _REPO_ROOT / rel_path
            self.assertTrue(mod_path.is_file(), f"존재하지 않는 모듈: {rel_path}")
            top_symbols = _collect_module_top_symbols(mod_path)
            tokens = re.findall(r"`([A-Za-z_][A-Za-z0-9_*]*)`", symbols_cell)
            for token in tokens:
                with self.subTest(module=rel_path, symbol=token):
                    matched = (
                        any(fnmatch.fnmatchcase(sym, token) for sym in top_symbols)
                        if "*" in token
                        else token in top_symbols
                    )
                    self.assertTrue(
                        matched,
                        f"ARCHITECTURE.md에 기재된 '{token}' 심볼이 {rel_path}에 없음",
                    )

    def test_markdown_local_links_resolve_to_existing_paths(self) -> None:
        """문서(README, ARCHITECTURE, AGENTS, TODO)의 상대 파일 링크가 실존 경로인지 검증"""
        link_re = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)")
        for doc_name in _DOC_FILES:
            doc_text = (_REPO_ROOT / doc_name).read_text(encoding="utf-8")
            for target in link_re.findall(doc_text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = _REPO_ROOT / target
                with self.subTest(doc=doc_name, target=target):
                    self.assertTrue(
                        resolved.exists(),
                        f"{doc_name}의 링크 대상이 존재하지 않음: {target}",
                    )

    def test_deprecated_terms_absent_from_docs_and_example_notebook(self) -> None:
        """전체 문서 및 예제 노트북에 폐기된 구용어(소형 클립, 가분류)가 잔존하지 않는지 검증"""
        deprecated = ("소형 클립", "가분류")
        for rel_path in (*_DOC_FILES, "classify_example.ipynb"):
            content = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
            if rel_path.endswith(".ipynb"):
                nb = json.loads(content)
                content = "\n".join(
                    "".join(cell.get("source", [])) for cell in nb.get("cells", [])
                )
            for term in deprecated:
                self.assertNotIn(
                    term,
                    content,
                    f"{rel_path}에 폐기 용어 '{term}' 잔존",
                )

    def test_docs_match_code_contracts_and_ui_bindings(self) -> None:
        """README·ARCHITECTURE의 확장자, 열 수, 단축키, 예외 AST 허용 목록, 버튼명이 코드와 일치하는지 검증"""
        readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        arch_text = (_REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

        # 1) 지원 비디오 확장자 목록 일치
        ext_match = re.search(r"\(지원 확장자: ([^)]+)\)", readme_text)
        self.assertIsNotNone(ext_match, "README.md에서 지원 확장자 목록을 찾을 수 없음")
        assert ext_match is not None
        listed_exts = set(re.findall(r"`(\.[a-z0-9]+)`", ext_match.group(1)))
        self.assertEqual(listed_exts, VIDEO_EXTENSIONS)

        # 2) 테이블 모델 열 수 일치
        expected_col_token = f"{len(VideoTableModel.HEADERS)}열"
        self.assertIn(expected_col_token, arch_text)

        # 3) GUI 단축키 표와 window_layout.py / main_window.py 바인딩 양방향 일치
        layout_src = (_REPO_ROOT / "src" / "gui" / "window_layout.py").read_text(
            encoding="utf-8"
        )
        main_win_src = (_REPO_ROOT / "src" / "gui" / "main_window.py").read_text(
            encoding="utf-8"
        )
        code_shortcuts = set(re.findall(r'QKeySequence\("([^"]+)"\)', layout_src))
        if "Key_Escape" in main_win_src:
            code_shortcuts.add("Esc")

        shortcut_section = re.search(
            r"### 주요 단축키\s*\n\| 단축키 \| 동작 \|\s*\n\| --- \| --- \|\s*\n((?:\| `[^`]+` \|[^\n]*\n?)+)",
            readme_text,
        )
        self.assertIsNotNone(shortcut_section, "README.md 단축키 표를 찾을 수 없음")
        assert shortcut_section is not None
        doc_shortcuts = set(re.findall(r"^\| `([^`]+)` \|", shortcut_section.group(1), re.M))
        self.assertEqual(doc_shortcuts, code_shortcuts)

        # 4) 예외 규칙 AST 허용 심볼·VideoProps 속성 일치
        safe_funcs, safe_methods, safe_attrs = _extract_exception_rule_whitelists()
        video_props_names = {f.name for f in fields(VideoProps)} | {
            name
            for name, member in vars(VideoProps).items()
            if isinstance(member, property)
        }
        self.assertTrue(
            safe_attrs.issubset(video_props_names),
            f"exception_rules 허용 속성 중 VideoProps에 없는 항목: {safe_attrs - video_props_names}",
        )

        attrs_line = re.search(r"사용 가능한 `VideoProps` 속성[^\n]+", readme_text)
        self.assertIsNotNone(attrs_line)
        assert attrs_line is not None
        doc_attrs = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", attrs_line.group(0).split(":", 1)[1]))
        self.assertEqual(doc_attrs, safe_attrs)

        funcs_line = re.search(r"허용 함수 · 메서드\*\*:\s*내장 함수([^\n]+)/\s*문자열 메서드([^\n]+)", readme_text)
        self.assertIsNotNone(funcs_line)
        assert funcs_line is not None
        doc_funcs = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", funcs_line.group(1)))
        doc_methods = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", funcs_line.group(2)))
        self.assertEqual(doc_funcs, safe_funcs)
        self.assertEqual(doc_methods, safe_methods)

        # 5) 푸터 바 주요 액션 버튼 문구 실존 확인
        footer_src = (_REPO_ROOT / "src" / "gui" / "widgets" / "footer_bar.py").read_text(
            encoding="utf-8"
        )
        for label in ("폴더 분류 이동", "분류 복원"):
            self.assertIn(label, readme_text)
            self.assertIn(label, footer_src)

    def test_pyproject_and_vscode_settings_stay_aligned(self) -> None:
        """pyproject.toml과 .vscode/settings.json의 엄격 타입·린트 설정 일치를 검증"""
        pyproject_text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("[tool.pyright]", pyproject_text)
        self.assertIn('typeCheckingMode = "strict"', pyproject_text)

        vscode_settings = json.loads(
            (_REPO_ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(vscode_settings.get("python.analysis.typeCheckingMode"), "strict")
        self.assertEqual(
            vscode_settings.get("basedpyright.analysis.typeCheckingMode"), "strict"
        )
        self.assertTrue(vscode_settings.get("ruff.enable"))
        self.assertTrue(vscode_settings.get("ruff.lint.enable"))

        overrides = vscode_settings.get("python.analysis.diagnosticSeverityOverrides", {})
        self.assertEqual(overrides.get("reportImportCycles"), "error")
        self.assertEqual(overrides.get("reportMissingParameterType"), "error")

    def test_shared_pre_commit_hook_enforces_gates(self) -> None:
        """공유 pre-commit 훅이 린트·타입·위생·메타 동기화 게이트를 모두 호출하는지 검증"""
        hook_path = _REPO_ROOT / "scripts" / "hooks" / "pre-commit"
        self.assertTrue(hook_path.is_file(), "scripts/hooks/pre-commit 파일이 없음")
        hook_text = hook_path.read_text(encoding="utf-8")
        for token in (
            "ruff",
            "basedpyright",
            "scripts/verify_hygiene.py --staged",
            "tests.test_metadata_sync",
        ):
            self.assertIn(token, hook_text)

    def test_agent_skills_documented_in_readme_and_architecture(self) -> None:
        """프로젝트 에이전트 스킬(.agents/skills)의 프런트매터와 문서 목록 일치를 검증"""
        skills_dir = _REPO_ROOT / ".agents" / "skills"
        skill_files = sorted(skills_dir.glob("*/SKILL.md"))
        self.assertTrue(skill_files, ".agents/skills/*/SKILL.md 파일이 없음")

        skill_names: set[str] = set()
        for skill_file in skill_files:
            dir_name = skill_file.parent.name
            skill_names.add(dir_name)
            text = skill_file.read_text(encoding="utf-8")
            self.assertRegex(
                text,
                rf"(?ms)^---\r?\nname:\s*{re.escape(dir_name)}\r?\ndescription:\s*.+?\r?\n---",
                f"스킬 프런트매터 누락 또는 불일치: {skill_file}",
            )

        readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        arch_text = (_REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

        readme_table = re.search(
            r"\| 스킬 \| 호출 상황 \| 주요 역할 \|\s*\r?\n\| --- \| --- \| --- \|\s*\r?\n((?:\| `[^`]+` \|[^\r\n]*(?:\r?\n|$))+)",
            readme_text,
        )
        self.assertIsNotNone(readme_table, "README.md 스킬 표를 찾을 수 없음")
        assert readme_table is not None
        documented_in_readme = set(
            re.findall(r"^\| `([^`]+)` \|", readme_table.group(1), re.M)
        )
        self.assertEqual(documented_in_readme, skill_names)

        for name in skill_names:
            self.assertIn(name, arch_text, f"ARCHITECTURE.md에 스킬명 누락: {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
