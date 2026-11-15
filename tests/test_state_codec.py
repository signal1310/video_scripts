import json
import unittest
from typing import Any

from src.core.app_state import AppState
from src.core.config import MAX_RECENT_DIRS
from src.core.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments


def _parse_ui(**ui: Any) -> AppState:
    return parse_state_mapping({"ui": ui})


class TestStripJsonc(unittest.TestCase):
    """JSONC 주석, 후행 쉼표 제거가 문자열 리터럴을 보존하는지 검증"""

    def test_line_and_block_comments_removed_keeping_line_structure(self) -> None:
        self.assertEqual(strip_jsonc_comments('{"a": 1} // tail'), '{"a": 1} ')
        self.assertEqual(strip_jsonc_comments('{"a": /* x */ 1}'), '{"a":  1}')
        self.assertEqual(strip_jsonc_comments('{"a": /* x\ny */ 1}'), '{"a": \n 1}')
        self.assertEqual(strip_jsonc_comments('{"a": 1} /'), '{"a": 1} /')
        self.assertEqual(strip_jsonc_comments('1 /* open'), "1 ")

    def test_block_comment_closing_requires_exact_pair(self) -> None:
        self.assertEqual(strip_jsonc_comments("1 /* * / */ 2"), "1  2")
        self.assertEqual(strip_jsonc_comments("1 /* a */"), "1 ")
        self.assertEqual(strip_jsonc_comments("1 /**/ 2"), "1  2")

    def test_comment_markers_inside_strings_are_preserved(self) -> None:
        text = '{"url": "http://x/*y*/", "q": "a\\"//b"}'
        self.assertEqual(strip_jsonc_comments(text), text)

    def test_trailing_commas_removed_only_before_closers(self) -> None:
        self.assertEqual(strip_jsonc_comments('{"a": [1, 2,\n ], "b": 3,\t}'), '{"a": [1, 2\n ], "b": 3\t}')
        self.assertEqual(strip_jsonc_comments('{"a": 1, "b": 2}'), '{"a": 1, "b": 2}')
        self.assertEqual(strip_jsonc_comments('{"a": ",}"}'), '{"a": ",}"}')
        self.assertEqual(strip_jsonc_comments("[1,"), "[1,")


class TestParseStateMapping(unittest.TestCase):
    """손상, 비정형 값이 안전한 기본값으로 정규화되는지 검증"""

    def test_bool_strings_are_interpreted_and_unknown_falls_back_to_default(self) -> None:
        for raw, expected in [
            (True, True), (False, False),
            ("true", True), ("1", True), ("yes", True), ("on", True), (" ON ", True),
            ("false", False), ("0", False), ("no", False), ("off", False), (" Off ", False),
            ("maybe", True), (2, True), (None, True),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(_parse_ui(show_setting_hints=raw).ui.show_setting_hints, expected)

    def test_tab_index_accepts_only_in_range_ints(self) -> None:
        def tabs(settings: Any, help_: Any) -> tuple[int, int]:
            layout = _parse_ui(layout={"settings_tab": settings, "help_tab": help_}).ui.layout
            return layout.settings_tab, layout.help_tab

        self.assertEqual(tabs(1, 2), (1, 2))
        self.assertEqual(tabs(0, 0), (0, 0))
        self.assertEqual(tabs(2, 3), (0, 3))
        self.assertEqual(tabs(0, 4), (0, 0))
        self.assertEqual(tabs(-1, -1), (0, 0))
        self.assertEqual(tabs(" 1 ", " 2 "), (1, 2))
        self.assertEqual(tabs("2", "3"), (0, 3))
        self.assertEqual(tabs("x", "x"), (0, 0))
        self.assertEqual(tabs(True, True), (0, 0))
        self.assertEqual(tabs(1.0, None), (0, 0))

    def test_choice_and_string_list_sanitizing(self) -> None:
        state = _parse_ui(theme_mode=" dark ", scan_mode="bogus", exception_tokens=[" a ", "", 3, "  "])
        self.assertEqual(state.ui.theme_mode, "dark")
        self.assertEqual(state.ui.scan_mode, AppState().ui.scan_mode)
        self.assertEqual(state.ui.exception_tokens, ["a", "3"])
        self.assertEqual(_parse_ui(exception_tokens="not a list").ui.exception_tokens, [])

    def test_recent_dirs_capped_at_limit(self) -> None:
        dirs = [f"d{i}" for i in range(MAX_RECENT_DIRS + 3)]
        state = parse_state_mapping({"paths": {"recent_dirs": dirs}})
        self.assertEqual(state.paths.recent_dirs, dirs[:MAX_RECENT_DIRS])
        exact = dirs[:MAX_RECENT_DIRS]
        self.assertEqual(parse_state_mapping({"paths": {"recent_dirs": exact}}).paths.recent_dirs, exact)

    def test_empty_directory_names_fall_back_to_defaults(self) -> None:
        blank = parse_state_mapping({"paths": {"dir_standard_cq22": "", "dir_low_quality_keyframe": ""}})
        default = AppState()
        self.assertEqual(blank.paths.dir_standard_cq22, default.paths.dir_standard_cq22)
        self.assertEqual(blank.paths.dir_low_quality_keyframe, default.paths.dir_low_quality_keyframe)
        custom = parse_state_mapping({"paths": {"dir_standard_cq22": "_custom"}})
        self.assertEqual(custom.paths.dir_standard_cq22, "_custom")


class TestRenderStateJsonc(unittest.TestCase):
    """렌더링된 JSONC가 주석 제거 후 원본 상태로 복원되고 서식 규칙을 지키는지 검증"""

    def test_render_roundtrips_through_strip_and_parse(self) -> None:
        state = AppState()
        state.ui.exception_tokens = ["a", "b"]
        text = render_state_jsonc(state)
        self.assertEqual(json.loads(strip_jsonc_comments(text)), state.to_dict())
        self.assertEqual(parse_state_mapping(json.loads(strip_jsonc_comments(text))).to_dict(), state.to_dict())

    def test_last_field_of_each_object_has_no_trailing_comma(self) -> None:
        lines = render_state_jsonc(AppState()).splitlines()
        for idx, line in enumerate(lines[:-1]):
            if lines[idx + 1].strip() in ("}", "},"):
                self.assertFalse(line.rstrip().endswith(","), line)

    def test_comments_are_column_aligned(self) -> None:
        commented = [line for line in render_state_jsonc(AppState()).splitlines() if line.lstrip().startswith('"') and "//" in line]
        self.assertTrue(commented)
        short = [line for line in commented if line.index("//") == len(line.split("//")[0])]
        self.assertTrue(all(line.index("//") >= 54 for line in short))


if __name__ == "__main__":
    unittest.main()
