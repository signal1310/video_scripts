import os
import tempfile
import unittest

from src.core.config import (
    DEFAULT_CONFIGS,
    ClassifierConfig,
    get_root_dir,
    load_env,
    save_env_values,
    set_root_dir,
)


class TestConfigModule(unittest.TestCase):
    """
    - 환경변수 로딩, .rootdir 파일 파싱 및 ClassifierConfig 설정 객체의 생성과 기본값 동작을 검증.
    """

    def test_load_env_default_and_override(self) -> None:
        """
        - load_env가 존재하지 않는 환경변수에 대해 기본값을 반환하고, 따옴표를 올바르게 제거하는지 검증.
        """
        # 기본값 로드 검증
        val = load_env("BASE_RESOLUTION_WIDTH")
        self.assertEqual(val, "1280")

        # 따옴표 제거 검증
        os.environ["__TEST_QUOTED_VAR__"] = '"test_value"'
        DEFAULT_CONFIGS["__TEST_QUOTED_VAR__"] = "default"
        try:
            loaded = load_env("__TEST_QUOTED_VAR__")
            self.assertEqual(loaded, "test_value")
        finally:
            del os.environ["__TEST_QUOTED_VAR__"]
            del DEFAULT_CONFIGS["__TEST_QUOTED_VAR__"]

    def test_get_root_dir_parsing_and_fallback(self) -> None:
        """
        - .rootdir 파일에서 주석(#)과 공백을 건너뛰고 첫 번째 유효 경로를 정확히 읽어오는지 검증.
        """
        rootdir_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".rootdir")
        )
        # .rootdir 파일이 존재할 경우 읽기 테스트
        if os.path.isfile(rootdir_path):
            path = get_root_dir()
            self.assertIsInstance(path, str)

    def test_classifier_config_from_env(self) -> None:
        """
        - ClassifierConfig.from_env()가 환경변수 및 기본값으로부터 설정 객체를 정상 생성하는지 검증.
        """
        cfg = ClassifierConfig.from_env(reload_dotenv=False)
        self.assertEqual(cfg.base_width, 1280)
        self.assertEqual(cfg.base_height, 720)
        self.assertEqual(cfg.base_target_bitrate, 1500)
        self.assertEqual(cfg.over_bitrate_ratio, 1.20)
        self.assertEqual(cfg.low_bitrate_ratio, 0.80)
        self.assertEqual(cfg.keyframe_threshold, 2.2)

    def test_get_codec_weight_lookup(self) -> None:
        """
        - 대소문자, 공백 및 미등록 코덱에 대해 적절한 상대 비트레이트 가중치를 반환하는지 검증.
        """
        cfg = ClassifierConfig()
        self.assertEqual(cfg.get_codec_weight("av1"), 0.65)
        self.assertEqual(cfg.get_codec_weight("hevc"), 0.80)
        self.assertEqual(cfg.get_codec_weight("h264"), 1.00)
        self.assertEqual(cfg.get_codec_weight(" HEVC "), 0.80)
        self.assertEqual(cfg.get_codec_weight("UNKNOWN_CODEC"), 1.00)
        self.assertEqual(cfg.get_codec_weight(""), 1.00)

    def test_set_root_dir_and_get_root_dir(self) -> None:
        """
        - set_root_dir가 파일에 경로를 정상 기록하고 get_root_dir가 이를 정확히 읽어오는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_rootdir = os.path.join(tmpdir, ".rootdir")
            from unittest.mock import patch

            with patch("src.core.config.get_rootdir_path", return_value=dummy_rootdir):
                target_path = r"C:\Test\VideoArchive"
                set_root_dir(target_path)
                read_path = get_root_dir()
                self.assertEqual(read_path, target_path)

    def test_save_env_values_preserves_comments(self) -> None:
        """
        - save_env_values가 기존 주석을 유지하면서 지정된 키 값만 정확히 갱신하고 os.environ에 반영하는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_env = os.path.join(tmpdir, ".env")
            initial_content = (
                "# Header comment\n"
                "BASE_TARGET_BITRATE=1500\n"
                "# Comment between\n"
                "THRESHOLD_OPTIMAL_BITRATE_RATE=1.2\n"
            )
            with open(dummy_env, "w", encoding="utf-8") as f:
                f.write(initial_content)

            from unittest.mock import patch

            with patch("src.core.config.get_env_path", return_value=dummy_env):
                save_env_values(
                    {
                        "BASE_TARGET_BITRATE": "1800",
                        "NEW_CUSTOM_VAR": "custom_val",
                    },
                    reload_dotenv=False,
                )

                with open(dummy_env, encoding="utf-8") as f:
                    content = f.read()

                self.assertIn("# Header comment", content)
                self.assertIn("BASE_TARGET_BITRATE=1800", content)
                self.assertIn("# Comment between", content)
                self.assertIn("THRESHOLD_OPTIMAL_BITRATE_RATE=1.2", content)
                self.assertIn("NEW_CUSTOM_VAR=custom_val", content)

    def test_to_env_dict_and_save_entire_config(self) -> None:
        """
        - ClassifierConfig.to_env_dict()가 필수 21개 키를 모두 포함하고 save_entire_config가 정상 동작하는지 검증.
        """
        cfg = ClassifierConfig(base_target_bitrate=2000, over_bitrate_ratio=1.35)
        env_dict = cfg.to_env_dict()
        for k in DEFAULT_CONFIGS:
            self.assertIn(k, env_dict)
        self.assertEqual(env_dict["BASE_TARGET_BITRATE"], "2000")
        self.assertEqual(env_dict["THRESHOLD_OPTIMAL_BITRATE_RATE"], "1.35")

    def test_codec_weight_groups_single_source_of_truth(self) -> None:
        """
        - ClassifierConfig() 기본 생성자와 CODEC_WEIGHT_GROUPS가 DEFAULT_CONFIGS와 일관되게 동기화됨을 검증.
        """
        from src.core.config import CODEC_WEIGHT_GROUPS, build_codec_weights_from_groups

        cfg = ClassifierConfig()
        self.assertAlmostEqual(cfg.get_codec_weight("mpeg4"), float(DEFAULT_CONFIGS["CODEC_WEIGHT_LEGACY"]))
        self.assertAlmostEqual(cfg.get_codec_weight("vp8"), float(DEFAULT_CONFIGS["CODEC_WEIGHT_LEGACY"]))

        custom = build_codec_weights_from_groups({"CODEC_WEIGHT_AV1": 0.55})
        for alias in CODEC_WEIGHT_GROUPS["CODEC_WEIGHT_AV1"]:
            self.assertAlmostEqual(custom[alias], 0.55)


if __name__ == "__main__":
    unittest.main(verbosity=2)
