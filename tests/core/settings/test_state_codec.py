import json
import unittest
from typing import Any

from src.core.config import MAX_RECENT_DIRS
from src.core.settings.app_state import AppState
from src.core.settings.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments
from src.core.settings.ui_state import TEXT_MODE_STARTS_WITH, ColumnFilterSnapshot


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
        self.assertEqual(tabs(2, 3), (2, 3))
        self.assertEqual(tabs(3, 0), (3, 0))
        self.assertEqual(tabs(4, 0), (4, 0))
        self.assertEqual(tabs(0, 4), (0, 0))
        self.assertEqual(tabs(-1, -1), (0, 0))
        self.assertEqual(tabs(" 1 ", " 2 "), (1, 2))
        self.assertEqual(tabs("2", "3"), (2, 3))
        self.assertEqual(tabs("x", "x"), (0, 0))
        self.assertEqual(tabs(True, True), (0, 0))
        self.assertEqual(tabs(1.0, None), (0, 0))

    def test_settings_tab_keeps_the_fifth_category(self) -> None:
        """단축키 범주(인덱스 4)가 저장 파일에서 0으로 잘리지 않고 그대로 복원되는지 검증"""

        def tabs(settings: Any, help_: Any) -> tuple[int, int]:
            layout = _parse_ui(layout={"settings_tab": settings, "help_tab": help_}).ui.layout
            return layout.settings_tab, layout.help_tab

        self.assertEqual(tabs(4, 0), (4, 0))
        self.assertEqual(tabs("4", "2"), (4, 2))
        self.assertEqual(tabs(5, 0), (0, 0))
        self.assertEqual(tabs(-1, 0), (0, 0))

        state = _parse_ui(theme_mode=" dark ", analyze_mode="bogus", exception_tokens=[" a ", "", 3, "  "])
        self.assertEqual(state.ui.theme_mode, "dark")
        self.assertEqual(state.ui.analyze_mode, AppState().ui.analyze_mode)
        self.assertEqual(state.ui.exception_tokens, ["a", "3"])
        self.assertEqual(_parse_ui(exception_tokens="not a list").ui.exception_tokens, [])

    def test_legacy_scan_mode_key_is_migrated_to_analyze_mode(self) -> None:
        """이전 저장 키 scan_mode 값을 analyze_mode가 없을 때만 승계함을 검증"""
        self.assertEqual(_parse_ui(scan_mode="full").ui.analyze_mode, "full")
        self.assertEqual(_parse_ui(scan_mode="full", analyze_mode="fast").ui.analyze_mode, "fast")

    def test_legacy_preview_backend_key_is_ignored(self) -> None:
        """구버전 설정의 미리 보기 방식 키를 읽을 때 무시하고 다시 기록하지 않음을 검증"""
        state = _parse_ui(preview_backend="quicklook", preview_seek_seconds=10)
        self.assertEqual(state.ui.preview_seek_seconds, 10)
        self.assertNotIn("preview_backend", render_state_jsonc(state))

    def test_preview_always_on_top_is_sanitized_and_roundtrips(self) -> None:
        """미리 보기 핀 상태가 기본값, 문자열 표기, 저장 후 복원에서 같은 값으로 유지되는지 검증"""
        self.assertTrue(_parse_ui().ui.preview_always_on_top)
        self.assertFalse(_parse_ui(preview_always_on_top=False).ui.preview_always_on_top)
        state = _parse_ui(preview_always_on_top="off")
        self.assertFalse(state.ui.preview_always_on_top)
        text = render_state_jsonc(state)
        self.assertIn('"preview_always_on_top": false', text)
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertFalse(restored.ui.preview_always_on_top)

    def test_preview_background_and_outline_roundtrip_and_unknown_falls_back(self) -> None:
        """배경 색과 영상 테두리가 기본값, 저장 후 복원에서 같은 값으로 유지되고 알 수 없는 색은 검정으로 되돌아가는지 검증"""
        self.assertEqual(_parse_ui().ui.preview_background, "black")
        self.assertFalse(_parse_ui().ui.preview_frame_outline)
        state = _parse_ui(preview_background="white", preview_frame_outline="on")
        self.assertEqual(state.ui.preview_background, "white")
        self.assertTrue(state.ui.preview_frame_outline)
        self.assertEqual(_parse_ui(preview_background="purple").ui.preview_background, "black")
        self.assertEqual(_parse_ui(preview_background=7).ui.preview_background, "black")
        text = render_state_jsonc(state)
        self.assertIn('"preview_background": "white"', text)
        self.assertIn('"preview_frame_outline": true', text)
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertEqual(restored.ui.preview_background, "white")
        self.assertTrue(restored.ui.preview_frame_outline)
        copied = state.ui.copy()
        self.assertEqual(copied.preview_background, "white")
        self.assertTrue(copied.preview_frame_outline)

    def test_compare_top_bar_collapsed_is_sanitized_and_roundtrips(self) -> None:
        """화질 비교 상단 바 접힘 상태가 기본값, 문자열 표기, 저장 후 복원에서 같은 값으로 유지되는지 검증"""
        self.assertFalse(_parse_ui().ui.compare_top_bar_collapsed)
        self.assertTrue(_parse_ui(compare_top_bar_collapsed=True).ui.compare_top_bar_collapsed)
        state = _parse_ui(compare_top_bar_collapsed="on")
        self.assertTrue(state.ui.compare_top_bar_collapsed)
        text = render_state_jsonc(state)
        self.assertIn('"compare_top_bar_collapsed": true', text)
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertTrue(restored.ui.compare_top_bar_collapsed)

    def test_compact_table_defaults_false_and_roundtrips(self) -> None:
        """좁게 보기 설정이 기본값 거짓에서 문자열 표기까지 정규화되고 저장 후 복원되는지 검증"""
        self.assertFalse(_parse_ui().ui.compact_table)
        self.assertTrue(_parse_ui(compact_table=True).ui.compact_table)
        state = _parse_ui(compact_table="on")
        self.assertTrue(state.ui.compact_table)
        self.assertFalse(_parse_ui(compact_table="maybe").ui.compact_table)
        text = render_state_jsonc(state)
        self.assertIn('"compact_table": true', text)
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertTrue(restored.ui.compact_table)

    def test_recent_dirs_capped_at_limit(self) -> None:
        dirs = [f"d{i}" for i in range(MAX_RECENT_DIRS + 3)]
        state = parse_state_mapping({"paths": {"recent_dirs": dirs}})
        self.assertEqual(state.paths.recent_dirs, dirs[:MAX_RECENT_DIRS])
        exact = dirs[:MAX_RECENT_DIRS]
        self.assertEqual(parse_state_mapping({"paths": {"recent_dirs": exact}}).paths.recent_dirs, exact)


class TestTableViewportState(unittest.TestCase):
    """표 가로 스크롤 위치와 포커스 파일명의 기본값, 정규화, 왕복을 검증"""

    def test_defaults_are_zero_and_empty(self) -> None:
        """저장 키가 없는 옛 상태 파일은 0과 빈 파일명으로 복원됨을 검증"""
        layout = _parse_ui(layout={}).ui.layout
        self.assertEqual(layout.table_left_scroll, 0)
        self.assertEqual(layout.table_current_filename, "")

    def test_values_roundtrip_through_jsonc(self) -> None:
        """저장한 가로 위치와 포커스 파일명이 렌더링과 파싱을 거쳐 그대로 유지됨을 검증"""
        state = _parse_ui(
            layout={"table_left_scroll": 120, "table_current_filename": "a.mp4"}
        )
        restored = parse_state_mapping(
            json.loads(strip_jsonc_comments(render_state_jsonc(state)))
        )
        self.assertEqual(restored.ui.layout.table_left_scroll, 120)
        self.assertEqual(restored.ui.layout.table_current_filename, "a.mp4")

    def test_wrong_types_fall_back_to_defaults(self) -> None:
        """숫자 자리에 문자열, 문자열 자리에 목록이 오면 기본값으로 되돌림을 검증"""
        layout = _parse_ui(
            layout={
                "table_left_scroll": "-40",
                "table_current_filename": ["a.mp4"],
            }
        ).ui.layout
        self.assertEqual(layout.table_left_scroll, 0)
        self.assertEqual(layout.table_current_filename, "")

    def test_negative_and_boolean_scroll_fall_back_to_zero(self) -> None:
        """음수, 소수, 참거짓, 없는 값은 가로 위치 0으로 보정됨을 검증"""
        for raw in (-40, 12.5, True, None, "60"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    _parse_ui(layout={"table_left_scroll": raw}).ui.layout.table_left_scroll, 0
                )

    def test_out_of_range_scroll_is_clamped_by_the_view_not_the_state(self) -> None:
        """표 범위를 넘는 가로 위치는 저장값으로 남아 표가 범위 안으로 자르게 함을 검증

        - 현재 행 수와 열 폭에 따라 범위가 달라 상태 계층에서는 상한을 알 수 없음
        """
        self.assertEqual(
            _parse_ui(layout={"table_left_scroll": 999_999}).ui.layout.table_left_scroll, 999_999
        )

class TestManualTierState(unittest.TestCase):
    """출력 해상도 직접 지정 맵의 정규화와 옛 설정 파일 호환을 검증"""

    def test_only_string_values_of_valid_keys_are_kept(self) -> None:
        """규격 키와 keep 외 값, 문자열이 아닌 값, 항목이 비는 작업 폴더는 제거해 자동 판정으로 되돌림"""
        state = _parse_ui(
            manual_target_tiers={
                "C:/work": {"a.mp4": "720p", "b.mp4": "keep", "c.mp4": "8K", "d.mp4": 3},
                "C:/empty": {"e.mp4": None},
                "C:/broken": "not-a-dict",
            }
        )
        self.assertEqual(state.ui.manual_target_tiers, {"C:/work": {"a.mp4": "720p", "b.mp4": "keep"}})

    def test_legacy_file_without_manual_field_loads_empty(self) -> None:
        """직접 지정 필드가 없는 옛 설정 파일은 빈 맵으로 복원됨을 검증"""
        self.assertEqual(_parse_ui(show_setting_hints=True).ui.manual_target_tiers, {})
        self.assertEqual(parse_state_mapping({}).ui.manual_target_tiers, {})

    def test_manual_tiers_are_not_written_to_state_file(self) -> None:
        """직접 지정 맵은 전용 DB가 원본이므로 상태 파일에 기록하지 않고, 기록 후 읽어도 비어 있음을 검증"""
        state = AppState()
        state.ui.manual_target_tiers = {"C:/work": {"a.mp4": "720p", "b.mp4": "keep"}}
        text = render_state_jsonc(state)
        self.assertNotIn("manual_target_tiers", text)
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertEqual(restored.ui.manual_target_tiers, {})


class TestColumnFilterState(unittest.TestCase):
    """열별 필터 조건과 정렬 우선순위의 JSONC 왕복 복원, 잘못된 항목 정규화를 검증"""

    def test_column_filters_roundtrip_through_jsonc(self) -> None:
        """열 ID별 선택 값, 수치 범위, 텍스트 조건이 저장 후에도 같은 값으로 복원됨을 검증"""
        state = AppState()
        state.ui.filters.columns = {
            "codec": ColumnFilterSnapshot(selected_values=("H.264", "HEVC"), include_null=False),
            "bitrate": ColumnFilterSnapshot(min_value=1.5, max_value=None, null_only=False),
            "name": ColumnFilterSnapshot(text_query="sample", text_mode=TEXT_MODE_STARTS_WITH),
        }
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(render_state_jsonc(state))))
        self.assertEqual(restored.ui.filters.columns, state.ui.filters.columns)

    def test_sort_keys_roundtrip_in_priority_order(self) -> None:
        """정렬 우선순위와 오름차순 여부가 저장 후에도 같은 순서로 복원됨을 검증"""
        state = AppState()
        state.ui.filters.sort_keys = [("bitrate", False), ("name", True)]
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(render_state_jsonc(state))))
        self.assertEqual(restored.ui.filters.sort_keys, [("bitrate", False), ("name", True)])

    def test_invalid_column_entries_are_dropped(self) -> None:
        """알 수 없는 열 ID, 객체가 아닌 값, 허용되지 않은 텍스트 모드와 잘못된 정렬 항목은 버리거나 기본값으로 되돌림"""
        raw = {
            "ui": {
                "filters": {
                    "columns": {
                        "bogus": {"text_query": "x"},
                        "bitrate": "not-a-dict",
                        "codec": {"min_value": True, "text_mode": "regex", "text_query": 7},
                    },
                    "sort_keys": [["bogus", True], ["name", "yes"], ["ratio", False]],
                }
            }
        }
        filters = parse_state_mapping(raw).ui.filters
        self.assertEqual(list(filters.columns), ["codec"])
        self.assertEqual(filters.columns["codec"], ColumnFilterSnapshot())
        self.assertEqual(filters.sort_keys, [("ratio", False)])


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
