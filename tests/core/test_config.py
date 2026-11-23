import dataclasses
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.core import paths as paths_mod
from src.core.config import (
    CORE_SETTING_COUNT,
    DEFAULT_FRAME_ANALYSIS_MODE,
    DEFAULT_PATHS,
    SAVINGS_BASIS_ENCODE,
    SAVINGS_BASIS_PLAYBACK,
    BitrateModelConfig,
    ClassifierConfig,
    EncodeConfig,
    ThresholdsConfig,
    collect_setting_bounds,
)
from src.core.paths import (
    DATA_DIR_NAME,
    DEFAULT_ROOT_DIR,
    ROOTDIR_FILENAME,
    ensure_default_rootdir_file,
    get_root_dir,
    get_rootdir_path,
    set_root_dir,
)
from src.core.resolution_tiers import RESOLUTION_TIERS
from src.core.settings.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_GROUPS,
    SHORTCUT_SPEC_BY_ID,
    SHORTCUT_SPECS,
    diff_shortcut_overrides,
    find_conflict_action,
    format_tooltip_with_shortcut,
    normalize_shortcut,
    resolve_shortcuts,
    sanitize_shortcut_overrides,
)
from tests._sandbox import get_orig_app_base_dir


class TestConfigModule(unittest.TestCase):
    """실행 기준 경로, data/rootdir.txt 파일 파싱 및 ClassifierConfig 스키마, 직렬화 동작 검증"""

    def test_app_base_dir_and_frozen_executable_mode(self) -> None:
        """PyInstaller 등 동결(frozen) 실행 환경에서는 실행 파일(.exe) 폴더를 기준으로 삼음을 검증"""
        fake_exe = os.path.join(tempfile.gettempdir(), "PortableApp", "VideoClassifier.exe")
        with (
            patch(
                "src.core.paths.get_app_base_dir",
                side_effect=get_orig_app_base_dir(),
            ),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", fake_exe),
        ):
            self.assertEqual(
                paths_mod.get_app_base_dir(), os.path.dirname(os.path.abspath(fake_exe))
            )
            self.assertEqual(
                get_rootdir_path(),
                os.path.join(
                    os.path.dirname(os.path.abspath(fake_exe)),
                    DATA_DIR_NAME,
                    ROOTDIR_FILENAME,
                ),
            )

    def test_get_root_dir_parsing_and_fallback(self) -> None:
        """data/rootdir.txt 파일에서 BOM, 따옴표, 인라인 주석, ~, 환경변수를 정리하고 첫 유효 경로를 반환함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_rootdir = os.path.join(tmpdir, ROOTDIR_FILENAME)
            expected_path = os.path.join(tmpdir, "Archive")
            with open(dummy_rootdir, "w", encoding="utf-8") as f:
                f.write("# comment line\n\n")
                f.write(f"  {expected_path}  \n")
            with patch("src.core.paths.get_rootdir_path", return_value=dummy_rootdir):
                self.assertEqual(get_root_dir(), expected_path)

            # UTF-8 BOM + 빈 따옴표 줄 스킵 + 따옴표, 인라인 주석, 내부 '#' 보존
            hash_dir = os.path.join(tmpdir, "#Archive Folder")
            with open(dummy_rootdir, "w", encoding="utf-8-sig") as f:
                f.write('  ""  # empty quoted line\n')
                f.write(f'  "{hash_dir}"   # inline comment\n')
            with patch("src.core.paths.get_rootdir_path", return_value=dummy_rootdir):
                self.assertEqual(get_root_dir(), hash_dir)

            # 따옴표 없는 경로 내 '#' 보존 및 환경변수, 홈 디렉터리 확장
            with open(dummy_rootdir, "w", encoding="utf-8") as f:
                f.write("$TEST_VID_ROOT/#raw # comment\n")
            with (
                patch("src.core.paths.get_rootdir_path", return_value=dummy_rootdir),
                patch.dict(os.environ, {"TEST_VID_ROOT": tmpdir}),
            ):
                self.assertEqual(get_root_dir(), f"{tmpdir}/#raw")

            with open(dummy_rootdir, "w", encoding="utf-8") as f:
                f.write("'~/Videos' # home path\n")
            with patch("src.core.paths.get_rootdir_path", return_value=dummy_rootdir):
                self.assertEqual(get_root_dir(), os.path.expanduser("~/Videos"))

    def test_classifier_config_defaults_and_from_dict(self) -> None:
        """ClassifierConfig 섹션 기본값 및 from_dict() 계층 복원, 기본 폴백을 검증"""
        cfg = ClassifierConfig()
        self.assertEqual(cfg.bitrate_model.base_width, 1280)
        self.assertEqual(cfg.bitrate_model.base_height, 720)
        self.assertEqual(cfg.bitrate_model.base_target_kbps, 1500)
        self.assertEqual(cfg.thresholds.over_bitrate_ratio, 1.20)
        self.assertEqual(cfg.thresholds.low_bitrate_ratio, 0.80)
        self.assertEqual(cfg.thresholds.keyframe_interval_sec, 2.2)

        parsed = ClassifierConfig.from_dict({
            "analysis": {"bitrate_model": {"base_target_kbps": 2200}},
        })
        self.assertEqual(parsed.bitrate_model.base_target_kbps, 2200)
        self.assertEqual(parsed.bitrate_model.base_width, 1280)
        self.assertEqual(cfg.encode.baseline_cq, 22.0)
        baseline_parsed = ClassifierConfig.from_dict({"analysis": {"encode": {"baseline_cq": 23.9}}})
        self.assertEqual(baseline_parsed.encode.baseline_cq, 23.9)
        self.assertEqual(ClassifierConfig.from_dict({"analysis": {"encode": {}}}).encode.baseline_cq, 22.0)
        for raw_value, expected in (
            (1e9, 30.0),
            (-1e9, 16.0),
            ("nan", 22.0),
            ("inf", 22.0),
        ):
            with self.subTest(raw=raw_value):
                clamped = ClassifierConfig.from_dict({"analysis": {"encode": {"baseline_cq": raw_value}}})
                self.assertEqual(clamped.encode.baseline_cq, expected)

    def test_get_codec_weight_lookup(self) -> None:
        """대소문자, 공백 및 미등록 코덱에 대해 적절한 상대 비트레이트 가중치를 반환하는지 검증"""
        cfg = ClassifierConfig()
        self.assertEqual(cfg.get_codec_weight("av1"), 0.65)
        self.assertEqual(cfg.get_codec_weight("hevc"), 0.80)
        self.assertEqual(cfg.get_codec_weight("h264"), 1.00)
        self.assertEqual(cfg.get_codec_weight(" HEVC "), 0.80)
        self.assertEqual(cfg.get_codec_weight("UNKNOWN_CODEC"), 1.00)
        self.assertEqual(cfg.get_codec_weight(""), 1.00)

    def test_set_root_dir_and_get_root_dir(self) -> None:
        """set_root_dir가 CUI 안내 주석과 함께 경로를 기록하고 get_root_dir가 이를 정확히 읽어오는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_rootdir = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with patch("src.core.paths.get_rootdir_path", return_value=dummy_rootdir):
                target_path = os.path.join(tmpdir, "VideoArchive")
                set_root_dir(target_path)
                with open(dummy_rootdir, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("#", content)
                self.assertIn("data/rootdir.txt", content)
                read_path = get_root_dir()
                self.assertEqual(read_path, target_path)

    def test_get_root_dir_missing_raises_file_not_found(self) -> None:
        """data/rootdir.txt 파일이 존재하지 않으면 get_root_dir()가 FileNotFoundError를 발생시키는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_rootdir = os.path.join(tmpdir, "missing_rootdir.txt")
            with (
                patch("src.core.paths.get_rootdir_path", return_value=missing_rootdir),
                self.assertRaises(FileNotFoundError),
            ):
                _ = get_root_dir()

    def test_to_dict_roundtrip(self) -> None:
        """ClassifierConfig.to_dict() 계층 구조 직렬화 및 from_dict() 왕복 복원을 검증"""
        cfg = ClassifierConfig(
            thresholds=ThresholdsConfig(over_bitrate_ratio=1.35),
            bitrate_model=BitrateModelConfig(base_target_kbps=2000),
        )
        cfg_dict = cfg.to_dict()
        self.assertNotIn("paths", cfg_dict)
        self.assertIn("analysis", cfg_dict)
        self.assertIn("cui", cfg_dict)
        self.assertEqual(cfg_dict["analysis"]["bitrate_model"]["base_target_kbps"], 2000)
        self.assertEqual(cfg_dict["analysis"]["thresholds"]["over_bitrate_ratio"], 1.35)

        reloaded = ClassifierConfig.from_dict(cfg_dict)
        self.assertEqual(reloaded.bitrate_model.base_target_kbps, 2000)
        self.assertAlmostEqual(reloaded.thresholds.over_bitrate_ratio, 1.35)

    def test_ensure_default_rootdir_file_creates_missing_and_preserves_existing(self) -> None:
        """data/rootdir.txt 미존재 시 CUI 안내 주석 템플릿으로 자동 생성하고 기존 파일은 보존함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            rootdir_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            with patch("src.core.paths.get_rootdir_path", return_value=rootdir_path):
                created = ensure_default_rootdir_file()
                self.assertEqual(created, rootdir_path)
                self.assertTrue(os.path.isfile(rootdir_path))
                with open(rootdir_path, encoding="utf-8") as f:
                    raw_template = f.read()
                self.assertIn("#", raw_template)
                self.assertIn("data/rootdir.txt", raw_template)
                self.assertIn("Jupyter", raw_template)
                self.assertIn("# 예시:", raw_template)
                self.assertNotIn("hygiene: ignore", raw_template)
                self.assertNotIn("data/config.json", raw_template)
                self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)

                custom_dir = os.path.join(tmpdir, "Media")
                set_root_dir(custom_dir)
                ensure_default_rootdir_file()
                self.assertEqual(get_root_dir(), custom_dir)

    def test_dataclass_metadata_bounds_and_core_setting_count(self) -> None:
        """데이터클래스 메타데이터 경계와 CORE_SETTING_COUNT, DEFAULT_PATHS 기준 정의를 검증"""
        bounds = collect_setting_bounds()
        self.assertEqual(CORE_SETTING_COUNT, 50)
        self.assertFalse(hasattr(DEFAULT_PATHS, "root_dir"))
        self.assertEqual(DEFAULT_PATHS.recent_dirs, [])
        self.assertIn("over_bitrate_ratio", bounds)
        self.assertIn("base_target_kbps", bounds)
        self.assertIn("av1", bounds)
        self.assertIn("fps_decimals", bounds)
        self.assertIn("ratio_decimals", bounds)
        self.assertIn("size_decimals", bounds)
        self.assertIn("filename_maxlen", bounds)
        self.assertIn("min_saved_mb", bounds)
        self.assertIn("min_mb_per_min", bounds)
        self.assertIn("exempt_saved_mb", bounds)
        self.assertIn("encode_speed_factor", bounds)

    def test_to_dict_covers_all_section_fields_via_dataclasses(self) -> None:
        """ClassifierConfig.to_dict가 모든 하위 설정 데이터클래스 필드를 누락 없이 직렬화, 역직렬화함을 검증"""
        cfg = ClassifierConfig()
        dumped = cfg.to_dict()
        self.assertEqual(
            set(dumped["analysis"]["thresholds"]),
            {f.name for f in dataclasses.fields(cfg.thresholds)},
        )
        self.assertEqual(
            set(dumped["analysis"]["savings"]),
            {f.name for f in dataclasses.fields(cfg.savings)},
        )
        self.assertEqual(
            set(dumped["analysis"]["encode"]),
            {f.name for f in dataclasses.fields(cfg.encode)},
        )
        self.assertEqual(
            set(dumped["analysis"]["codec_weights"]),
            {f.name for f in dataclasses.fields(cfg.codec_weights)},
        )
        self.assertEqual(
            set(dumped["analysis"]["bitrate_model"]),
            {f.name for f in dataclasses.fields(cfg.bitrate_model)},
        )
        self.assertEqual(
            set(dumped["display"]),
            {f.name for f in dataclasses.fields(cfg.display)},
        )
        self.assertEqual(
            set(dumped["cui"]),
            {f.name for f in dataclasses.fields(cfg.cui)},
        )
        self.assertEqual(ClassifierConfig.from_dict(dumped), cfg)

        raw_custom = {
            "display": {
                "fps_decimals": 3,
                "fps_trim_whole": "false",
                "size_decimals": 1,
                "size_unit": "auto",
            }
        }
        custom_cfg = ClassifierConfig.from_dict(raw_custom)
        self.assertEqual(custom_cfg.display.fps_decimals, 3)
        self.assertFalse(custom_cfg.display.fps_trim_whole)
        self.assertEqual(custom_cfg.display.size_decimals, 1)
        self.assertEqual(custom_cfg.display.size_unit, "auto")

        for legacy_or_invalid in ("tb", "mb_gb"):
            invalid_unit = ClassifierConfig.from_dict(
                {"display": {"size_unit": legacy_or_invalid}}
            )
            self.assertEqual(invalid_unit.display.size_unit, "auto")

        invalid_codec = ClassifierConfig.from_dict(
            {"analysis": {"encode": {"encode_codec": "unknown_codec"}}}
        )
        self.assertEqual(invalid_codec.encode.encode_codec, "hevc_nvenc")
        self.assertFalse(ClassifierConfig().encode.force_legacy_compat)
        compat_cfg = ClassifierConfig(encode=EncodeConfig(force_legacy_compat=True))
        self.assertTrue(ClassifierConfig.from_dict(compat_cfg.to_dict()).encode.force_legacy_compat)
        self.assertTrue(ClassifierConfig.from_dict({"analysis": {"encode": {"force_legacy_compat": "true"}}}).encode.force_legacy_compat)
        self.assertFalse(ClassifierConfig().encode.auto_downscale_by_bitrate)
        self.assertEqual(ClassifierConfig().encode.frame_analysis_mode, DEFAULT_FRAME_ANALYSIS_MODE)
        mode_cfg = ClassifierConfig(encode=EncodeConfig(frame_analysis_mode="precise"))
        self.assertEqual(ClassifierConfig.from_dict(mode_cfg.to_dict()).encode.frame_analysis_mode, "precise")
        self.assertEqual(
            ClassifierConfig.from_dict({"analysis": {"encode": {"frame_analysis_mode": "unknown"}}}).encode.frame_analysis_mode,
            DEFAULT_FRAME_ANALYSIS_MODE,
        )
        fit_cfg = ClassifierConfig(encode=EncodeConfig(auto_downscale_by_bitrate=True))
        self.assertTrue(ClassifierConfig.from_dict(fit_cfg.to_dict()).encode.auto_downscale_by_bitrate)
        self.assertTrue(ClassifierConfig.from_dict({"analysis": {"encode": {"auto_downscale_by_bitrate": "true"}}}).encode.auto_downscale_by_bitrate)
        self.assertFalse(ClassifierConfig.from_dict({"analysis": {"encode": {}}}).encode.auto_downscale_by_bitrate)
        self.assertTrue(ClassifierConfig().encode.crop_black_bars)
        crop_off = ClassifierConfig(encode=EncodeConfig(crop_black_bars=False))
        self.assertFalse(ClassifierConfig.from_dict(crop_off.to_dict()).encode.crop_black_bars)
        self.assertTrue(
            ClassifierConfig.from_dict({"analysis": {"encode": {"crop_black_bars": "true"}}}).encode.crop_black_bars
        )
        self.assertFalse(
            ClassifierConfig.from_dict({"analysis": {"encode": {"crop_black_bars": "false"}}}).encode.crop_black_bars
        )

        for valid_unit in ("mb", "gb", "auto"):
            loaded = ClassifierConfig.from_dict({"display": {"size_unit": valid_unit}})
            self.assertEqual(loaded.display.size_unit, valid_unit)

        self.assertEqual(ClassifierConfig().display.filename_align, "left")
        for valid_align in ("left", "right"):
            loaded = ClassifierConfig.from_dict({"display": {"filename_align": valid_align}})
            self.assertEqual(loaded.display.filename_align, valid_align)
        self.assertEqual(
            ClassifierConfig.from_dict({"display": {"filename_align": "center"}}).display.filename_align,
            "left",
        )

    def test_savings_selection_basis_roundtrip_and_invalid_fallback(self) -> None:
        """절약 선별 기준은 저장값을 보존하고, 알 수 없는 값은 재생 시간 기준(기본값)으로 되돌리는지 검증"""
        self.assertEqual(ClassifierConfig().savings.selection_basis, SAVINGS_BASIS_PLAYBACK)
        encode_cfg = ClassifierConfig.from_dict({"analysis": {"savings": {"selection_basis": SAVINGS_BASIS_ENCODE}}})
        self.assertEqual(encode_cfg.savings.selection_basis, SAVINGS_BASIS_ENCODE)
        self.assertEqual(ClassifierConfig.from_dict(encode_cfg.to_dict()).savings.selection_basis, SAVINGS_BASIS_ENCODE)
        invalid_cfg = ClassifierConfig.from_dict({"analysis": {"savings": {"selection_basis": "unknown"}}})
        self.assertEqual(invalid_cfg.savings.selection_basis, SAVINGS_BASIS_PLAYBACK)

    def test_legacy_efficiency_section_and_overhead_key_are_migrated(self) -> None:
        """이전 저장 형식의 efficiency 섹션과 overhead 키를 신규 섹션과 키로 승계하고, 신규 값을 우선함을 검증"""
        legacy = ClassifierConfig.from_dict(
            {
                "analysis": {
                    "thresholds": {"keyframe_1s_overhead_rate": 0.09},
                    "efficiency": {"min_saved_mb": 7.0, "encode_codec": "libx265"},
                }
            }
        )
        self.assertAlmostEqual(legacy.thresholds.keyframe_1s_increase_rate, 0.09)
        self.assertAlmostEqual(legacy.savings.min_saved_mb, 7.0)
        self.assertEqual(legacy.encode.encode_codec, "libx265")

        current = ClassifierConfig.from_dict(
            {
                "analysis": {
                    "savings": {"min_saved_mb": 8.0},
                    "efficiency": {"min_saved_mb": 7.0},
                }
            }
        )
        self.assertAlmostEqual(current.savings.min_saved_mb, 8.0)


class TestShortcutSchema(unittest.TestCase):
    """단축키 카탈로그, 오버라이드 정규화, 충돌 해소, 툴팁 표기 규칙 검증"""

    def test_catalog_has_84_unique_commands_in_10_groups(self) -> None:
        """명령 84종이 액션 ID 중복 없이 10개 기능 그룹에 모두 속함을 검증"""
        self.assertEqual(len(SHORTCUT_SPECS), 84)
        self.assertEqual(len(SHORTCUT_SPEC_BY_ID), len(SHORTCUT_SPECS))
        self.assertEqual(len(SHORTCUT_GROUPS), 10)
        group_keys = {group.key for group in SHORTCUT_GROUPS}
        self.assertEqual({spec.group_key for spec in SHORTCUT_SPECS}, group_keys)

    def test_non_empty_default_keys_are_unique_ignoring_case(self) -> None:
        """기본 단축키 문자열이 대소문자 무시로 서로 겹치지 않음을 검증"""
        keys = [spec.default_key.lower() for spec in SHORTCUT_SPECS if spec.default_key]
        self.assertEqual(len(keys), len(set(keys)))

    def test_resolution_commands_follow_tier_order_with_ctrl_digits(self) -> None:
        """출력 해상도 명령이 규격 오름차순으로 Ctrl+1부터 배정되고 표시명을 따름을 검증"""
        for index, tier in enumerate(RESOLUTION_TIERS, start=1):
            with self.subTest(tier=tier.key):
                spec = SHORTCUT_SPEC_BY_ID[f"manual_target.{tier.key}"]
                self.assertEqual(spec.default_key, f"Ctrl+{index}")
                self.assertIn(tier.display, spec.label)
        self.assertEqual(
            SHORTCUT_SPEC_BY_ID["manual_target.2160p"].label,
            "출력 해상도 지정: 4K (3840x2160)",
        )

    def test_normalize_unifies_standalone_enter_and_trims(self) -> None:
        """단독 Enter를 Return으로 통일하고 앞뒤 공백을 제거함을 검증"""
        self.assertEqual(normalize_shortcut("  Enter "), "Return")
        self.assertEqual(normalize_shortcut(" Ctrl+K "), "Ctrl+K")
        self.assertEqual(normalize_shortcut(""), "")

    def test_shift_digit_symbols_normalize_to_digits_and_bind_symbol_variant(self) -> None:
        """Shift 조합의 기호 입력은 숫자 표기로 저장되고, 숫자 표기 단축키는 기호 바인딩 표기를 함께 가짐을 검증"""
        from src.core.settings.shortcuts import shifted_symbol_variant

        self.assertEqual(normalize_shortcut("Ctrl+Shift+)"), "Ctrl+Shift+0")
        self.assertEqual(normalize_shortcut("Ctrl+Shift+("), "Ctrl+Shift+9")
        self.assertEqual(normalize_shortcut("Ctrl+("), "Ctrl+(")
        self.assertEqual(shifted_symbol_variant("Ctrl+Shift+0"), "Ctrl+Shift+)")
        self.assertEqual(shifted_symbol_variant("Ctrl+Shift+9"), "Ctrl+Shift+(")
        self.assertIsNone(shifted_symbol_variant("Ctrl+0"))
        self.assertIsNone(shifted_symbol_variant("Ctrl+Shift+F"))

    def test_sanitize_keeps_only_known_non_default_overrides(self) -> None:
        """저장값 정리 시 알 수 없는 명령, 비문자열, 기본값과 같은 항목을 버림을 검증"""
        self.assertEqual(sanitize_shortcut_overrides(None), {})
        self.assertEqual(sanitize_shortcut_overrides("Ctrl+P"), {})
        raw = {
            "unknown.action": "Ctrl+P",
            "file.exit": 3,
            "file.open_dir": "Ctrl+O",
            "file.edit_path": " Ctrl+P ",
            "classify.play_selected": "Enter",
            "file.export_csv": "",
        }
        self.assertEqual(
            sanitize_shortcut_overrides(raw),
            {"file.edit_path": "Ctrl+P", "file.export_csv": ""},
        )

    def test_sanitize_keeps_first_claim_of_duplicate_keys_and_allows_empty(self) -> None:
        """같은 키가 여러 명령에 중복되면 먼저 등장한 명령만 유지하고 빈 값 중복은 허용함을 검증"""
        self.assertEqual(
            sanitize_shortcut_overrides({"file.open_dir": "Ctrl+P", "file.edit_path": "ctrl+p"}),
            {"file.open_dir": "Ctrl+P"},
        )
        self.assertEqual(
            sanitize_shortcut_overrides({"file.open_dir": "", "file.edit_path": ""}),
            {"file.open_dir": "", "file.edit_path": ""},
        )

    def test_resolve_without_overrides_returns_defaults(self) -> None:
        """오버라이드가 없으면 기본값과 같고 차이 사전이 비어 있음을 검증"""
        self.assertEqual(resolve_shortcuts(), DEFAULT_SHORTCUTS)
        self.assertEqual(resolve_shortcuts({}), DEFAULT_SHORTCUTS)
        self.assertEqual(diff_shortcut_overrides(DEFAULT_SHORTCUTS), {})

    def test_resolve_clears_default_owner_of_reassigned_key(self) -> None:
        """새로 배정된 키의 기존 기본 소유 명령을 비워 중복을 방지함을 검증"""
        effective = resolve_shortcuts({"file.open_dir": "F5"})
        self.assertEqual(effective["file.open_dir"], "F5")
        self.assertEqual(effective["analyze.run_default"], "")
        self.assertEqual(effective["file.export_csv"], DEFAULT_SHORTCUTS["file.export_csv"])

    def test_resolve_keeps_owner_that_has_its_own_override(self) -> None:
        """기존 소유 명령에도 오버라이드가 있으면 비우지 않음을 검증"""
        effective = resolve_shortcuts({"file.open_dir": "F5", "analyze.run_default": "Shift+F6"})
        self.assertEqual(effective["analyze.run_default"], "Shift+F6")

    def test_diff_roundtrips_through_resolve(self) -> None:
        """유효 단축키의 차이 사전을 다시 해석하면 같은 유효 단축키가 복원됨을 검증"""
        effective = resolve_shortcuts({"file.open_dir": "F5"})
        diff = diff_shortcut_overrides(effective)
        self.assertEqual(diff, {"file.open_dir": "F5", "analyze.run_default": ""})
        self.assertEqual(resolve_shortcuts(diff), effective)

    def test_find_conflict_ignores_target_and_matches_case_insensitively(self) -> None:
        """충돌 검색이 대상 명령 자신을 제외하고 대소문자와 단독 Enter를 같은 키로 취급함을 검증"""
        conflict = find_conflict_action(DEFAULT_SHORTCUTS, "file.open_dir", "f5")
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.action_id, "analyze.run_default")
        self.assertIsNone(find_conflict_action(DEFAULT_SHORTCUTS, "file.open_dir", "Ctrl+O"))
        self.assertIsNone(find_conflict_action(DEFAULT_SHORTCUTS, "file.open_dir", ""))
        enter_conflict = find_conflict_action(DEFAULT_SHORTCUTS, "file.open_dir", "Enter")
        self.assertIsNotNone(enter_conflict)
        assert enter_conflict is not None
        self.assertEqual(enter_conflict.action_id, "classify.play_selected")

    def test_find_conflict_sees_reassigned_key_and_skips_cleared_owner(self) -> None:
        """유효 단축키 기준으로 재배정된 키를 찾고, 비워진 이전 소유 명령은 충돌로 보지 않음을 검증"""
        effective = resolve_shortcuts({"file.open_dir": "F5"})
        conflict = find_conflict_action(effective, "file.edit_path", "F5")
        assert conflict is not None
        self.assertEqual(conflict.action_id, "file.open_dir")
        self.assertIsNone(find_conflict_action(effective, "file.edit_path", "Shift+F5x"))

    def test_tooltip_appends_shortcut_in_requested_bracket_style(self) -> None:
        """툴팁에 단축키를 대괄호 또는 소괄호로 덧붙이며 빈 단축키는 문구만 남김을 검증"""
        self.assertEqual(format_tooltip_with_shortcut("작업 폴더 열기", "Ctrl+O"), "작업 폴더 열기 [Ctrl+O]")
        self.assertEqual(
            format_tooltip_with_shortcut("작업 폴더 열기", "Ctrl+O", bracket="round"),
            "작업 폴더 열기 (Ctrl+O)",
        )
        self.assertEqual(format_tooltip_with_shortcut("작업 폴더 열기", ""), "작업 폴더 열기")


if __name__ == "__main__":
    unittest.main(verbosity=2)
