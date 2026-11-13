import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.core import config as config_mod
from src.core.config import (
    CODEC_WEIGHT_GROUPS,
    CORE_SETTING_COUNT,
    DEFAULT_PATHS,
    DEFAULT_ROOT_DIR,
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    ThresholdsConfig,
    collect_setting_bounds,
    ensure_default_rootdir_file,
    get_root_dir,
    get_rootdir_path,
    set_root_dir,
)
from tests._sandbox import get_orig_app_base_dir


class TestConfigModule(unittest.TestCase):
    """실행 기준 경로, .rootdir 파일 파싱 및 ClassifierConfig 스키마·직렬화 동작 검증"""

    def test_app_base_dir_and_frozen_executable_mode(self) -> None:
        """PyInstaller 등 동결(frozen) 실행 환경에서는 실행 파일(.exe) 폴더를 기준으로 삼음을 검증"""
        fake_exe = os.path.join(tempfile.gettempdir(), "PortableApp", "VideoClassifier.exe")
        with (
            patch(
                "src.core.config.get_app_base_dir",
                side_effect=get_orig_app_base_dir(),
            ),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", fake_exe),
        ):
            self.assertEqual(
                config_mod.get_app_base_dir(), os.path.dirname(os.path.abspath(fake_exe))
            )
            self.assertEqual(
                get_rootdir_path(),
                os.path.join(os.path.dirname(os.path.abspath(fake_exe)), ".rootdir"),
            )

    def test_get_root_dir_parsing_and_fallback(self) -> None:
        """.rootdir 파일에서 주석(#)과 공백을 건너뛰고 첫 번째 유효 경로를 정확히 읽어오는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_rootdir = os.path.join(tmpdir, ".rootdir")
            expected_path = os.path.join(tmpdir, "Archive")
            with open(dummy_rootdir, "w", encoding="utf-8") as f:
                f.write("# comment line\n\n")
                f.write(f"  {expected_path}  \n")
            with patch("src.core.config.get_rootdir_path", return_value=dummy_rootdir):
                self.assertEqual(get_root_dir(), expected_path)

    def test_classifier_config_defaults_and_from_dict(self) -> None:
        """ClassifierConfig 섹션 기본값 및 from_dict() 계층 복원·기본 폴백을 검증"""
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
        """set_root_dir가 파일에 경로를 정상 기록하고 get_root_dir가 이를 정확히 읽어오는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_rootdir = os.path.join(tmpdir, ".rootdir")
            with patch("src.core.config.get_rootdir_path", return_value=dummy_rootdir):
                target_path = os.path.join(tmpdir, "VideoArchive")
                set_root_dir(target_path)
                read_path = get_root_dir()
                self.assertEqual(read_path, target_path)

    def test_get_root_dir_missing_raises_file_not_found(self) -> None:
        """.rootdir 파일이 존재하지 않으면 get_root_dir()가 FileNotFoundError를 발생시키는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_rootdir = os.path.join(tmpdir, "missing.rootdir")
            with (
                patch("src.core.config.get_rootdir_path", return_value=missing_rootdir),
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
        """CodecWeightsConfig와 CODEC_WEIGHT_GROUPS가 코덱 별칭 가중치를 단일 원천으로 제공함을 검증"""
        cfg = ClassifierConfig()
        default_legacy = CodecWeightsConfig().legacy
        self.assertAlmostEqual(cfg.get_codec_weight("mpeg4"), default_legacy)
        self.assertAlmostEqual(cfg.get_codec_weight("vp8"), default_legacy)

        custom = CodecWeightsConfig(av1=0.55)
        for alias in CODEC_WEIGHT_GROUPS["av1"]:
            self.assertAlmostEqual(custom.get_weight(alias), 0.55)

    def test_ensure_default_rootdir_file_creates_missing_and_preserves_existing(self) -> None:
        """.rootdir 미존재 시 기본값 템플릿으로 자동 생성하고 기존 파일은 보존함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            rootdir_path = os.path.join(tmpdir, ".rootdir")
            with patch("src.core.config.get_rootdir_path", return_value=rootdir_path):
                created = ensure_default_rootdir_file()
                self.assertEqual(created, rootdir_path)
                self.assertTrue(os.path.isfile(rootdir_path))
                self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)

                custom_dir = os.path.join(tmpdir, "Media")
                set_root_dir(custom_dir)
                ensure_default_rootdir_file()
                self.assertEqual(get_root_dir(), custom_dir)

    def test_dataclass_metadata_bounds_and_core_setting_count(self) -> None:
        """데이터클래스 메타데이터 경계와 CORE_SETTING_COUNT·DEFAULT_PATHS 단일 원천을 검증"""
        bounds = collect_setting_bounds()
        self.assertEqual(CORE_SETTING_COUNT, 22)
        self.assertEqual(DEFAULT_PATHS.root_dir, DEFAULT_ROOT_DIR)
        self.assertIn("over_bitrate_ratio", bounds)
        self.assertIn("base_target_kbps", bounds)
        self.assertIn("av1", bounds)
        self.assertIn("filename_maxlen", bounds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
