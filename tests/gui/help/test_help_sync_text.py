"""F1 도움말의 용어 사전 자리표시자, 필수 용어, 금지 문자를 검증하는 동기화 테스트"""

import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.settings.shortcuts import SHORTCUT_SPECS, WINDOW_SHORTCUT_GROUPS
from src.gui.help.glossary_data import GLOSSARY_TERMS
from src.gui.help.glossary_view import build_glossary_html, glossary_placeholder_values
from src.gui.help.help_concepts_page import build_concepts_html
from src.gui.help.help_guide_pages import build_guide_html, build_syntax_shortcuts_html
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE

_APP = QApplication.instance() or QApplication([])

# - AGENTS.md 4절: 문서, 주석, 사용자 문자열에 가운뎃점, 화살표, 절 기호, 줄표 금지
_FORBIDDEN_CHARS: tuple[tuple[str, str], ...] = (
    ("가운뎃점", "\u00b7"),
    ("화살표", "\u2192"),
    ("역화살표", "\u2190"),
    ("겹화살표", "\u21d2"),
    ("절 기호", "\u00a7"),
    ("줄표", "\u2014"),
    ("반각 줄표", "\u2013"),
    ("꺾쇠", "\u203a"),
)
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _strip_tags(html_text: str) -> str:
    """HTML 태그를 지워 화면에 보이는 글자만 남김"""
    return re.sub(r"<[^>]+>", "", html_text)


class GlossaryPlaceholderTests(unittest.TestCase):
    """용어 사전의 자리표시자가 모두 현재 설정으로 치환되는지 검증"""

    def test_all_placeholders_resolve(self) -> None:
        """모든 용어 설명, 예시, 값 뜻의 자리표시자가 해석되는지 검증"""
        values = glossary_placeholder_values(ClassifierConfig())
        for term in GLOSSARY_TERMS:
            fields = [term.desc, term.example, *(value.meaning for value in term.values)]
            for field in fields:
                with self.subTest(term=term.term):
                    self.assertIsNone(_PLACEHOLDER.search(field.format_map(values)))

    def test_rendered_glossary_has_no_placeholder(self) -> None:
        """렌더링된 용어 사전 본문에 중괄호 자리표시자가 남지 않는지 검증"""
        html = build_glossary_html(GLOSSARY_TERMS, ClassifierConfig(), LIGHT_PALETTE)
        self.assertIsNone(_PLACEHOLDER.search(_strip_tags(html)))

    def test_required_terms_exist(self) -> None:
        """5, 6단계에서 추가한 용어가 사전에 있는지 검증"""
        names = {term.term for term in GLOSSARY_TERMS}
        for name in (
            "활성 화면",
            "품질값 자동 조정",
            "품질값 정밀 결정",
            "최대 비트레이트",
            "보호 품질값 연속 규칙",
            "FFmpeg 명령 기록",
            "크기 조절 방식",
        ):
            with self.subTest(term=name):
                self.assertIn(name, names)


class ShortcutTableCoverageTests(unittest.TestCase):
    """고급 활용 탭 단축키 표가 창 전용 명령을 빠짐없이 담는지 검증"""

    def test_preview_and_compare_groups_are_complete(self) -> None:
        """미리 보기, 화질 비교 창의 모든 기본 단축키가 표에 있는지 검증"""
        text = _strip_tags(build_syntax_shortcuts_html(LIGHT_PALETTE))
        for spec in SHORTCUT_SPECS:
            if spec.group_key not in WINDOW_SHORTCUT_GROUPS:
                continue
            with self.subTest(action=spec.action_id):
                self.assertIn(spec.label, text)
                if spec.default_key:
                    self.assertIn(spec.default_key, text)

    def test_crop_preview_shortcut_is_documented(self) -> None:
        """여백 미리 보기 B 키가 표에 있는지 검증"""
        text = _strip_tags(build_syntax_shortcuts_html(LIGHT_PALETTE))
        self.assertIn("여백 미리 보기", text)
        self.assertIn("B", text)


class HelpForbiddenCharacterTests(unittest.TestCase):
    """모든 도움말 본문에 금지 문자가 없는지 검증"""

    def test_no_forbidden_characters_in_help_text(self) -> None:
        """가운뎃점, 화살표, 절 기호, 줄표가 도움말 본문에 없는지 검증"""
        config = ClassifierConfig()
        pages = {
            "guide": build_guide_html(LIGHT_PALETTE, None, config),
            "concepts": build_concepts_html(LIGHT_PALETTE, config),
            "syntax": build_syntax_shortcuts_html(LIGHT_PALETTE),
            "guide_dark": build_guide_html(DARK_PALETTE, None, config),
            "concepts_dark": build_concepts_html(DARK_PALETTE, config),
            "glossary": build_glossary_html(GLOSSARY_TERMS, config, DARK_PALETTE),
        }
        for page, html_text in pages.items():
            text = _strip_tags(html_text)
            for label, char in _FORBIDDEN_CHARS:
                with self.subTest(page=page, char=label):
                    self.assertNotIn(char, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
