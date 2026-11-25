"""단축키 기본 표의 인코딩 항목(그룹, 시작 Ctrl+G, 설정은 키 없음), 화질 비교 전환 키와 기본 키 중복 없는 시작 키를 검증"""

import unittest

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_GROUPS, SHORTCUT_SPECS


class EncodeShortcutTests(unittest.TestCase):
    """인코딩 단축키 항목의 등록 상태를 검증"""

    def test_encode_group_exists_after_analysis(self) -> None:
        """인코딩 그룹이 분석 그룹 뒤에 있는지 검증"""
        keys = [group.key for group in SHORTCUT_GROUPS]
        self.assertIn("encode", keys)
        self.assertEqual(keys.index("encode"), keys.index("analyze") + 1)

    def test_window_item_uses_ctrl_g_and_is_the_only_owner_of_that_key(self) -> None:
        """인코딩 창 열기가 Ctrl+G를 쓰고, 다른 명령은 같은 기본 키를 쓰지 않는지 검증"""
        self.assertEqual(DEFAULT_SHORTCUTS["encode.start"], "Ctrl+G")
        owners = [action_id for action_id, key in DEFAULT_SHORTCUTS.items() if key == "Ctrl+G"]
        self.assertEqual(owners, ["encode.start"])

    def test_duplicate_progress_shortcut_is_removed(self) -> None:
        """중복이던 인코딩 진행 창 열기 명령과 Ctrl+Shift+G 기본 키가 사라졌는지 검증"""
        self.assertNotIn("encode.open_progress", DEFAULT_SHORTCUTS)
        self.assertNotIn("Ctrl+Shift+G", DEFAULT_SHORTCUTS.values())

    def test_analyze_mode_frame_command_is_registered(self) -> None:
        """기본 분석 모드: 화질 분석 명령이 빈 기본 키로 등록되는지 검증"""
        spec = next(spec for spec in SHORTCUT_SPECS if spec.action_id == "analyze.mode_frame")
        self.assertEqual(spec.group_key, "analyze")
        self.assertEqual(spec.default_key, "")

    def test_settings_item_is_registered_without_a_default_key(self) -> None:
        """인코딩 설정 항목이 등록되어 있고 기본 키는 비어 있는지 검증"""
        spec = next(spec for spec in SHORTCUT_SPECS if spec.action_id == "encode.open_settings")
        self.assertEqual(spec.group_key, "encode")
        self.assertEqual(spec.default_key, "")


class CompareShortcutTests(unittest.TestCase):
    """화질 비교 전환 단축키 항목의 등록 상태를 검증"""

    def test_compare_toggle_uses_ctrl_b_right_after_preview(self) -> None:
        """화질 비교 전환이 미리 보기 바로 뒤 선택 그룹에 Ctrl+B로 등록되는지 검증"""
        action_ids = [spec.action_id for spec in SHORTCUT_SPECS]
        self.assertEqual(
            action_ids.index("classify.compare_selected"),
            action_ids.index("classify.preview_selected") + 1,
        )
        spec = SHORTCUT_SPECS[action_ids.index("classify.compare_selected")]
        self.assertEqual((spec.group_key, spec.label), ("selection", "화질 비교 전환"))
        self.assertEqual(spec.default_key, "Ctrl+B")
        self.assertEqual(spec.description, "현재 영상의 미리 보기와 화질 비교 창을 전환하거나 화질 비교 창을 엽니다.")
        owners = [action_id for action_id, key in DEFAULT_SHORTCUTS.items() if key == "Ctrl+B"]
        self.assertEqual(owners, ["classify.compare_selected"])


class CompareWindowShortcutTests(unittest.TestCase):
    """화질 비교 창 전용 그룹과 미리 보기 1프레임 명령 등록 상태"""

    def test_compare_group_follows_preview(self) -> None:
        keys = [group.key for group in SHORTCUT_GROUPS]
        self.assertIn("compare", keys)
        self.assertEqual(keys.index("compare"), keys.index("preview") + 1)
        group = SHORTCUT_GROUPS[keys.index("compare")]
        self.assertEqual((group.title, group.icon_name), ("화질 비교 창", "compare"))

    def test_compare_commands_use_the_approved_defaults(self) -> None:
        expected = {
            "compare.mode_split": "1",
            "compare.mode_toggle": "2",
            "compare.mode_side": "3",
            "compare.toggle_original": "C",
            "compare.zoom_fit": "0",
            "compare.zoom_in": "=",
            "compare.zoom_out": "-",
            "compare.toggle_controls": "H",
        }
        for action_id, key in expected.items():
            self.assertEqual(DEFAULT_SHORTCUTS[action_id], key)
        owners = {
            key: [action_id for action_id, value in DEFAULT_SHORTCUTS.items() if value == key]
            for key in expected.values()
        }
        for action_id, key in expected.items():
            self.assertEqual(owners[key], [action_id])

    def test_preview_frame_commands_exist_with_comma_and_period(self) -> None:
        self.assertEqual(DEFAULT_SHORTCUTS["preview.frame_backward"], ",")
        self.assertEqual(DEFAULT_SHORTCUTS["preview.frame_forward"], ".")
        specs = {spec.action_id: spec for spec in SHORTCUT_SPECS}
        self.assertEqual(specs["preview.frame_backward"].label, "이전 프레임")
        self.assertEqual(specs["preview.frame_forward"].label, "다음 프레임")
        self.assertEqual(
            specs["preview.frame_forward"].description,
            "재생을 일시중지하고 한 프레임 앞으로 이동합니다.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
