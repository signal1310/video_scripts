import dataclasses
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.core import paths as paths_mod
from src.core.config import (
    CODEC_WEIGHT_GROUPS,
    CORE_SETTING_COUNT,
    DEFAULT_PATHS,
    LEGACY_COMPAT_CODECS,
    SAVINGS_BASIS_ENCODE,
    SAVINGS_BASIS_PLAYBACK,
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
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
    is_valid_downscale_prefix,
    set_root_dir,
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
            "paths": {"dir_standard_cq22": '"_1_custom"'},
            "analysis": {"bitrate_model": {"base_target_kbps": 2200}},
        })
        self.assertEqual(parsed.bitrate_model.base_target_kbps, 2200)
        self.assertEqual(parsed.dir_standard_cq22, "_1_custom")
        self.assertEqual(parsed.bitrate_model.base_width, 1280)

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
        self.assertIn("paths", cfg_dict)
        self.assertIn("analysis", cfg_dict)
        self.assertIn("cui", cfg_dict)
        self.assertEqual(cfg_dict["analysis"]["bitrate_model"]["base_target_kbps"], 2000)
        self.assertEqual(cfg_dict["analysis"]["thresholds"]["over_bitrate_ratio"], 1.35)

        reloaded = ClassifierConfig.from_dict(cfg_dict)
        self.assertEqual(reloaded.bitrate_model.base_target_kbps, 2000)
        self.assertAlmostEqual(reloaded.thresholds.over_bitrate_ratio, 1.35)

    def test_codec_weight_groups_single_source_of_truth(self) -> None:
        """CodecWeightsConfig와 CODEC_WEIGHT_GROUPS가 코덱 별칭 가중치를 기준 정의로 제공함을 검증"""
        cfg = ClassifierConfig()
        default_legacy = CodecWeightsConfig().legacy
        self.assertAlmostEqual(cfg.get_codec_weight("mpeg4"), default_legacy)
        self.assertAlmostEqual(cfg.get_codec_weight("vp8"), default_legacy)

        custom = CodecWeightsConfig(av1=0.55)
        for alias in CODEC_WEIGHT_GROUPS["av1"]:
            self.assertAlmostEqual(custom.get_weight(alias), 0.55)

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
        self.assertEqual(CORE_SETTING_COUNT, 36)
        self.assertFalse(hasattr(DEFAULT_PATHS, "root_dir"))
        self.assertTrue(bool(DEFAULT_PATHS.dir_standard_cq22))
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
        fit_cfg = ClassifierConfig(encode=EncodeConfig(auto_downscale_by_bitrate=True))
        self.assertTrue(ClassifierConfig.from_dict(fit_cfg.to_dict()).encode.auto_downscale_by_bitrate)
        self.assertTrue(ClassifierConfig.from_dict({"analysis": {"encode": {"auto_downscale_by_bitrate": "true"}}}).encode.auto_downscale_by_bitrate)
        self.assertFalse(ClassifierConfig.from_dict({"analysis": {"encode": {}}}).encode.auto_downscale_by_bitrate)
        self.assertTrue({"wmv3", "vc1", "vp8", "flv1", "theora"}.issubset(LEGACY_COMPAT_CODECS))

        for valid_unit in ("mb", "gb", "auto"):
            loaded = ClassifierConfig.from_dict({"display": {"size_unit": valid_unit}})
            self.assertEqual(loaded.display.size_unit, valid_unit)

    def test_savings_selection_basis_roundtrip_and_invalid_fallback(self) -> None:
        """절약 선별 기준은 저장값을 보존하고, 알 수 없는 값은 재생 기준(기본값)으로 되돌리는지 검증"""
        self.assertEqual(ClassifierConfig().savings.selection_basis, SAVINGS_BASIS_PLAYBACK)
        encode_cfg = ClassifierConfig.from_dict({"analysis": {"savings": {"selection_basis": SAVINGS_BASIS_ENCODE}}})
        self.assertEqual(encode_cfg.savings.selection_basis, SAVINGS_BASIS_ENCODE)
        self.assertEqual(ClassifierConfig.from_dict(encode_cfg.to_dict()).savings.selection_basis, SAVINGS_BASIS_ENCODE)
        invalid_cfg = ClassifierConfig.from_dict({"analysis": {"savings": {"selection_basis": "unknown"}}})
        self.assertEqual(invalid_cfg.savings.selection_basis, SAVINGS_BASIS_PLAYBACK)

    def test_class_dirname_with_forbidden_chars_falls_back_to_default(self) -> None:
        """금지 문자나 빈 값이 든 표준 인코딩, 저화질 폴더명은 로드 시 기본값으로 복귀"""
        loaded = ClassifierConfig.from_dict(
            {"paths": {"dir_standard_cq22": "a/b", "dir_low_quality_keyframe": "  "}}
        )
        self.assertEqual(loaded.dir_standard_cq22, "_1_표준인코딩_CQ22")
        self.assertEqual(loaded.dir_low_quality_keyframe, "_2_저화질_키프레임교정")

    def test_downscale_prefix_and_max_resolution_roundtrip_and_fallback(self) -> None:
        """축소 폴더명 앞부분과 최대 해상도 설정의 왕복 저장, 잘못된 값의 기본값 복귀, 폴더명 검증 기준을 검증"""
        default = ClassifierConfig()
        self.assertEqual(default.encode.max_resolution_tier, "none")
        self.assertEqual(default.dir_downscale_prefix, "_3_해상도축소_")

        custom = ClassifierConfig(
            dir_downscale_prefix="_9_축소_",
            encode=EncodeConfig(max_resolution_tier="720p"),
        )
        reloaded = ClassifierConfig.from_dict(custom.to_dict())
        self.assertEqual(reloaded, custom)
        dirnames = reloaded.class_dirnames()
        self.assertEqual(len(dirnames), 10)
        self.assertEqual(dirnames[-1], "_9_축소_2160p")

        invalid = ClassifierConfig.from_dict(
            {
                "paths": {"dir_downscale_prefix": "   "},
                "analysis": {"encode": {"max_resolution_tier": "8K"}},
            }
        )
        self.assertEqual(invalid.dir_downscale_prefix, "_3_해상도축소_")
        self.assertEqual(invalid.encode.max_resolution_tier, "none")
        forbidden = ClassifierConfig.from_dict({"paths": {"dir_downscale_prefix": "_3/축소_"}})
        self.assertEqual(forbidden.dir_downscale_prefix, "_3_해상도축소_")

        reserved = (default.dir_standard_cq22, default.dir_low_quality_keyframe)
        self.assertTrue(is_valid_downscale_prefix("_9_축소_", reserved))
        self.assertFalse(is_valid_downscale_prefix("  ", reserved))
        self.assertFalse(is_valid_downscale_prefix("_3/축소_", reserved))
        self.assertFalse(is_valid_downscale_prefix("_1_표준인코딩_CQ22", ("_1_표준인코딩_CQ22720p",)))
        self.assertFalse(is_valid_downscale_prefix("_1_", ("_1_720p",)))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
