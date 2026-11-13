"""통합 설정·GUI 상태 파일(data/config.json) 영속화 및 .rootdir 우선순위를 검증하는 단위 테스트"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.app_state import (
    CONFIG_FILENAME,
    DATA_DIR_NAME,
    MAX_RECENT_DIRS,
    AppState,
    ensure_default_config_files,
    get_state_path,
    load_app_state,
    record_recent_dir,
    reset_app_state,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    DEFAULT_ROOT_DIR,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    PathsConfig,
    SmallFootageConfig,
    ThresholdsConfig,
    UiFilterState,
    UiLayoutState,
    UiState,
)
from src.core.state_codec import strip_jsonc_comments


class TestAppStateModule(unittest.TestCase):
    """data/config.json 저장·로드, .rootdir 우선순위 및 기본값 복원을 검증"""

    def test_portable_state_path_uses_app_base_dir(self) -> None:
        """통합 설정 파일 경로가 실행 기준 폴더의 data/config.json임을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("src.core.config.get_app_base_dir", return_value=tmpdir),
        ):
            self.assertEqual(
                get_state_path(),
                os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME),
            )

    def test_save_and_load_roundtrip(self) -> None:
        """save_app_state가 data/config.json과 .rootdir에 전체 UI·분석·레이아웃 상태를 왕복 저장함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                state = AppState(
                    paths=PathsConfig(
                        root_dir=tmpdir,
                        recent_dirs=[tmpdir],
                    ),
                    analysis=AnalysisConfig(
                        thresholds=ThresholdsConfig(
                            over_bitrate_ratio=1.55,
                            keyframe_interval_sec=3.5,
                        ),
                        small_footage=SmallFootageConfig(size_mb=42.0),
                        bitrate_model=BitrateModelConfig(base_target_kbps=2400),
                    ),
                    ui=UiState(
                        theme_mode="dark",
                        scan_mode="full",
                        exception_tokens=["v.codec == 'hevc'", "v.duration < 90"],
                        disabled_exception_tokens=["v.fps < 24", "legacy_tag"],
                        filters=UiFilterState(
                            search="intro",
                            zone="OVER",
                            target="ENCODE_ONLY",
                        ),
                        layout=UiLayoutState(
                            window_geometry="AAABBB==",
                            table_header_state="CCCDDD==",
                        ),
                    ),
                )
                save_app_state(state, sync_rootdir=True)

                self.assertTrue(os.path.isfile(root_path))
                self.assertTrue(os.path.isfile(state_path))

                reloaded = load_app_state()
                self.assertAlmostEqual(reloaded.analysis.thresholds.over_bitrate_ratio, 1.55)
                self.assertAlmostEqual(reloaded.analysis.thresholds.keyframe_interval_sec, 3.5)
                self.assertAlmostEqual(reloaded.analysis.small_footage.size_mb, 42.0)
                self.assertEqual(reloaded.analysis.bitrate_model.base_target_kbps, 2400)
                self.assertEqual(reloaded.paths.root_dir, tmpdir)
                self.assertEqual(reloaded.paths.recent_dirs, [os.path.normpath(tmpdir)])
                self.assertEqual(
                    reloaded.ui.exception_tokens, ["v.codec == 'hevc'", "v.duration < 90"]
                )
                self.assertEqual(
                    reloaded.ui.disabled_exception_tokens, ["v.fps < 24", "legacy_tag"]
                )
                self.assertEqual(reloaded.ui.scan_mode, "full")
                self.assertEqual(reloaded.ui.filters.search, "intro")
                self.assertEqual(reloaded.ui.filters.zone, "OVER")
                self.assertEqual(reloaded.ui.filters.target, "ENCODE_ONLY")
                self.assertEqual(reloaded.ui.theme_mode, "dark")
                self.assertEqual(reloaded.ui.layout.window_geometry, "AAABBB==")
                self.assertEqual(reloaded.ui.layout.table_header_state, "CCCDDD==")

    def test_rootdir_file_takes_priority_over_json_root_dir(self) -> None:
        """.rootdir 파일에 경로가 있으면 data/config.json의 root_dir보다 항상 우선 적용됨을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            tempfile.TemporaryDirectory() as json_dir,
            tempfile.TemporaryDirectory() as rootfile_dir,
        ):
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                save_app_state(
                    AppState(paths=PathsConfig(root_dir=json_dir)),
                    sync_rootdir=False,
                )
                with open(root_path, "w", encoding="utf-8") as f:
                    f.write(rootfile_dir)

                self.assertEqual(resolve_effective_root_dir(json_dir), rootfile_dir)
                self.assertEqual(load_app_state().paths.root_dir, rootfile_dir)

                with open(root_path, "w", encoding="utf-8") as f:
                    f.write("   \n")
                self.assertEqual(load_app_state().paths.root_dir, json_dir)

    def test_record_recent_dir_deduplicates_and_caps_limit(self) -> None:
        """record_recent_dir가 중복 제거, 최신 선두 배치 및 최대 보관 개수를 준수함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dirs = [os.path.join(tmpdir, f"dir_{i}") for i in range(MAX_RECENT_DIRS + 3)]
            recent: list[str] = []
            for d in dirs:
                recent = record_recent_dir(recent, d)

            self.assertEqual(len(recent), MAX_RECENT_DIRS)
            self.assertEqual(recent[0], os.path.normpath(dirs[-1]))

            readded = dirs[-3]
            recent = record_recent_dir(recent, readded)
            self.assertEqual(len(recent), MAX_RECENT_DIRS)
            self.assertEqual(recent[0], os.path.normpath(readded))

    def test_reset_app_state_and_corrupted_json_fallback(self) -> None:
        """reset_app_state 기본값 복원 및 손상된 상태 JSON 안전 폴백을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                save_app_state(
                    AppState(
                        paths=PathsConfig(
                            root_dir=tmpdir,
                            recent_dirs=[tmpdir],
                        ),
                        analysis=AnalysisConfig(
                            bitrate_model=BitrateModelConfig(base_target_kbps=3800),
                        ),
                        ui=UiState(
                            theme_mode="light",
                            scan_mode="full",
                            exception_tokens=["sample"],
                            filters=UiFilterState(
                                search="abc",
                                zone="OVER",
                                target="PASS_ONLY",
                            ),
                            layout=UiLayoutState(
                                window_geometry="GEOM_B64",
                                table_header_state="HDR_B64",
                            ),
                        ),
                    ),
                    sync_rootdir=True,
                )

                reset = reset_app_state(preserve_root_dir=True, preserve_recent_dirs=True)
                self.assertEqual(
                    reset.analysis.bitrate_model.base_target_kbps,
                    DEFAULT_BITRATE_MODEL.base_target_kbps,
                )
                self.assertEqual(reset.paths.root_dir, tmpdir)
                self.assertEqual(reset.paths.recent_dirs, [tmpdir])
                self.assertEqual(reset.ui.exception_tokens, [])
                self.assertEqual(reset.ui.scan_mode, "fast")
                self.assertEqual(reset.ui.filters.search, "")
                self.assertEqual(reset.ui.filters.zone, "ALL")
                self.assertEqual(reset.ui.filters.target, "ALL")
                self.assertEqual(reset.ui.theme_mode, "system")
                self.assertEqual(reset.ui.layout.window_geometry, "GEOM_B64")
                self.assertEqual(reset.ui.layout.table_header_state, "HDR_B64")

                with open(state_path, "w", encoding="utf-8") as f:
                    f.write("{corrupted json")
                recovered = load_app_state()
                self.assertEqual(recovered.paths.root_dir, tmpdir)
                self.assertEqual(recovered.ui.scan_mode, "fast")

    def test_first_run_auto_creates_rootdir_and_config_json_from_defaults(self) -> None:
        """초기 실행 시 미존재 .rootdir과 data/config.json을 기본값 기반으로 자동 생성함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                self.assertFalse(os.path.exists(root_path))
                self.assertFalse(os.path.exists(state_path))

                state = ensure_default_config_files()
                self.assertTrue(os.path.isfile(root_path))
                self.assertTrue(os.path.isfile(state_path))
                self.assertEqual(state.paths.root_dir, DEFAULT_ROOT_DIR)
                self.assertEqual(state.ui.scan_mode, "fast")
                self.assertEqual(state.ui.filters.zone, "ALL")
                self.assertEqual(state.ui.filters.target, "ALL")
                self.assertEqual(state.ui.theme_mode, "system")
                self.assertEqual(state.to_classifier_config().to_dict(), ClassifierConfig().to_dict())

                # JSONC 파일에 섹션·숫자 타입·// 주석이 포함되고 중첩 깊이 3 이하를 유지하는지 검증
                with open(state_path, encoding="utf-8") as f:
                    raw_text = f.read()
                self.assertIn("// 1. 경로 및 분류 폴더", raw_text)
                self.assertIn("// 2. 화질 및 키프레임 분석 기준", raw_text)
                parsed = json.loads(strip_jsonc_comments(raw_text))

                def _max_dict_depth(obj: object, depth: int = 0) -> int:
                    if isinstance(obj, dict):
                        return max((_max_dict_depth(v, depth + 1) for v in obj.values()), default=depth)
                    return depth

                self.assertLessEqual(_max_dict_depth(parsed), 3)
                self.assertNotIn("version", parsed)
                self.assertIs(type(parsed["analysis"]["bitrate_model"]["base_width"]), int)
                self.assertIs(type(parsed["analysis"]["thresholds"]["over_bitrate_ratio"]), float)

    def test_jsonc_comments_and_string_literal_preservation(self) -> None:
        """JSONC 주석·후행 쉼표 파싱 시 문자열 리터럴 내부의 //와 블록 주석 기호가 보존됨을 검증"""
        raw_jsonc = """
        // 파일 상단 주석
        {
          "paths": {
            "root_dir": "http://example.com//keep", /* 인라인 블록 주석 */
            "recent_dirs": ["D:\\\\Clips",],
          },
          "analysis": {
            "thresholds": {
              "over_bitrate_ratio": 1.45, // 수동 편집 값
            },
          },
          "ui": {
            "exception_tokens": ["https://host//skip"],
            "theme_mode": "dark",
          },
        }
        """
        cleaned = strip_jsonc_comments(raw_jsonc)
        loaded_dict = json.loads(cleaned)
        self.assertEqual(loaded_dict["paths"]["root_dir"], "http://example.com//keep")
        self.assertEqual(loaded_dict["ui"]["exception_tokens"], ["https://host//skip"])

        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                f.write(raw_jsonc)

            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                loaded = load_app_state()
                self.assertAlmostEqual(loaded.analysis.thresholds.over_bitrate_ratio, 1.45)
                self.assertEqual(loaded.ui.theme_mode, "dark")
                self.assertEqual(loaded.ui.exception_tokens, ["https://host//skip"])
                self.assertEqual(loaded.paths.root_dir, "http://example.com//keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
