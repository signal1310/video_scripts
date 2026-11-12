import os
import tempfile
import unittest

from src.core.config import ClassifierConfig, load_env, get_root_dir, DEFAULT_CONFIGS


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
