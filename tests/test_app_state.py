"""통합 설정·GUI 상태 파일(data/config.json) 영속화 및 data/rootdir.txt 단일 원천을 검증하는 단위 테스트"""

import json
import os
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

from src.core.app_state import (
    CONFIG_FILENAME,
    DATA_DIR_NAME,
    MAX_RECENT_DIRS,
    NOTEBOOK_FILENAME,
    AppState,
    ensure_default_config_files,
    ensure_default_notebook_file,
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
    ROOTDIR_FILENAME,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    DisplayConfig,
    EfficiencyConfig,
    PathsConfig,
    ThresholdsConfig,
    UiFilterState,
    UiLayoutState,
    UiState,
    get_root_dir,
)
from src.core.state_codec import strip_jsonc_comments


@contextmanager
def _isolated_data_paths(tmpdir: str) -> Generator[tuple[str, str]]:
    root_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
    state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
    with (
        patch("src.core.config.get_rootdir_path", return_value=root_path),
        patch("src.core.app_state.get_state_path", return_value=state_path),
    ):
        yield root_path, state_path


class TestAppStateModule(unittest.TestCase):
    """data/config.json 저장·로드, data/rootdir.txt 단일 원천 및 기본값 복원을 검증"""

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
        """save_app_state가 data/*에 전체 UI·분석·레이아웃 상태를 왕복 저장함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (root_path, state_path):
            state = AppState(
                paths=PathsConfig(
                    recent_dirs=[tmpdir],
                ),
                analysis=AnalysisConfig(
                    thresholds=ThresholdsConfig(
                        over_bitrate_ratio=1.55,
                        keyframe_interval_sec=3.5,
                    ),
                    efficiency=EfficiencyConfig(
                        min_saved_mb=12.0,
                        min_mb_per_min=4.5,
                        exempt_saved_mb=80.0,
                    ),
                    bitrate_model=BitrateModelConfig(base_target_kbps=2400),
                ),
                display=DisplayConfig(
                    fps_decimals=3,
                    fps_trim_whole=False,
                    ratio_decimals=4,
                    size_unit="auto",
                ),
                ui=UiState(
                    theme_mode="dark",
                    scan_mode="full",
                    exception_tokens=["v.codec == 'hevc'", "v.duration < 90"],
                    disabled_exception_tokens=["v.fps < 24", "legacy_tag"],
                    filters=UiFilterState(
                        search="intro",
                    ),
                    layout=UiLayoutState(
                        window_geometry="AAABBB==",
                        table_header_state="CCCDDD==",
                        settings_tab=1,
                    ),
                ),
            )
            save_app_state(state, root_dir=tmpdir)

            self.assertTrue(os.path.isfile(root_path))
            self.assertTrue(os.path.isfile(state_path))

            reloaded = load_app_state()
            self.assertAlmostEqual(reloaded.analysis.thresholds.over_bitrate_ratio, 1.55)
            self.assertAlmostEqual(reloaded.analysis.thresholds.keyframe_interval_sec, 3.5)
            self.assertAlmostEqual(reloaded.analysis.efficiency.min_saved_mb, 12.0)
            self.assertAlmostEqual(reloaded.analysis.efficiency.min_mb_per_min, 4.5)
            self.assertAlmostEqual(reloaded.analysis.efficiency.exempt_saved_mb, 80.0)
            self.assertEqual(reloaded.analysis.bitrate_model.base_target_kbps, 2400)
            self.assertEqual(reloaded.display.fps_decimals, 3)
            self.assertFalse(reloaded.display.fps_trim_whole)
            self.assertEqual(reloaded.display.ratio_decimals, 4)
            self.assertEqual(reloaded.display.size_unit, "auto")
            self.assertEqual(get_root_dir(), tmpdir)
            self.assertEqual(resolve_effective_root_dir(), tmpdir)
            with open(state_path, encoding="utf-8") as f:
                parsed = json.loads(strip_jsonc_comments(f.read()))
            self.assertNotIn("root_dir", parsed["paths"])
            self.assertEqual(reloaded.paths.recent_dirs, [os.path.normpath(tmpdir)])
            self.assertEqual(
                reloaded.ui.exception_tokens, ["v.codec == 'hevc'", "v.duration < 90"]
            )
            self.assertEqual(
                reloaded.ui.disabled_exception_tokens, ["v.fps < 24", "legacy_tag"]
            )
            self.assertEqual(reloaded.ui.scan_mode, "full")
            self.assertEqual(reloaded.ui.filters.search, "intro")
            self.assertEqual(reloaded.ui.theme_mode, "dark")
            self.assertEqual(reloaded.ui.layout.window_geometry, "AAABBB==")
            self.assertEqual(reloaded.ui.layout.table_header_state, "CCCDDD==")
            self.assertEqual(reloaded.ui.layout.settings_tab, 1)

            for invalid_tab in ("bad", 99, -1, True):
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump({"ui": {"layout": {"settings_tab": invalid_tab}}}, f)
                self.assertEqual(load_app_state().ui.layout.settings_tab, 0)

    def test_rootdir_file_is_single_source_and_ignores_legacy_json_root_dir(self) -> None:
        """data/rootdir.txt만 작업 경로 원천으로 사용되고 구형 config.json의 root_dir은 무시·정리됨을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            tempfile.TemporaryDirectory() as legacy_dir,
            tempfile.TemporaryDirectory() as rootfile_dir,_isolated_data_paths(tmpdir) as (root_path, state_path)
        ):
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"paths": {"root_dir": legacy_dir}}, f)
            with open(root_path, "w", encoding="utf-8") as f:
                f.write(rootfile_dir)

            self.assertEqual(resolve_effective_root_dir(), rootfile_dir)
            loaded = load_app_state()
            save_app_state(loaded)
            with open(state_path, encoding="utf-8") as f:
                parsed = json.loads(strip_jsonc_comments(f.read()))
            self.assertNotIn("root_dir", parsed["paths"])

            with open(root_path, "w", encoding="utf-8") as f:
                f.write("   \n")
            self.assertEqual(resolve_effective_root_dir(), "")

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
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (_root_path, state_path):
            save_app_state(
                AppState(
                    paths=PathsConfig(
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
                        ),
                        layout=UiLayoutState(
                            window_geometry="GEOM_B64",
                            table_header_state="HDR_B64",
                            settings_tab=1,
                        ),
                    ),
                ),
                root_dir=tmpdir,
            )

            reset = reset_app_state(preserve_root_dir=True, preserve_recent_dirs=True)
            self.assertEqual(
                reset.analysis.bitrate_model.base_target_kbps,
                DEFAULT_BITRATE_MODEL.base_target_kbps,
            )
            self.assertEqual(get_root_dir(), tmpdir)
            self.assertEqual(reset.paths.recent_dirs, [tmpdir])
            self.assertEqual(reset.ui.exception_tokens, [])
            self.assertEqual(reset.ui.scan_mode, "fast")
            self.assertEqual(reset.ui.filters.search, "")
            self.assertEqual(reset.ui.theme_mode, "system")
            self.assertEqual(reset.ui.layout.window_geometry, "GEOM_B64")
            self.assertEqual(reset.ui.layout.table_header_state, "HDR_B64")
            self.assertEqual(reset.ui.layout.settings_tab, 1)

            with open(state_path, "w", encoding="utf-8") as f:
                f.write("{corrupted json")
            recovered = load_app_state()
            self.assertEqual(get_root_dir(), tmpdir)
            self.assertEqual(recovered.ui.scan_mode, "fast")

            reset_app_state(preserve_root_dir=False)
            self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)

    def test_first_run_auto_creates_rootdir_and_config_json_from_defaults(self) -> None:
        """초기 실행 시 미존재 data/*·classify.ipynb를 기본값 기반으로 자동 생성함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notebook_path = os.path.join(tmpdir, NOTEBOOK_FILENAME)
            with (
                _isolated_data_paths(tmpdir) as (root_path, state_path),
                patch("src.core.app_state.get_notebook_path", return_value=notebook_path),
            ):
                self.assertFalse(os.path.exists(root_path))
                self.assertFalse(os.path.exists(state_path))
                self.assertFalse(os.path.exists(notebook_path))

                # create_missing=False 경로에서는 기본 파일을 생성하지 않아야 함
                load_app_state(create_missing=False)
                self.assertFalse(os.path.exists(root_path))
                self.assertFalse(os.path.exists(state_path))
                self.assertFalse(os.path.exists(notebook_path))

                state = ensure_default_config_files()
                self.assertTrue(os.path.isfile(root_path))
                self.assertTrue(os.path.isfile(state_path))
                self.assertTrue(os.path.isfile(notebook_path))
                self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)
                self.assertEqual(state.ui.scan_mode, "fast")
                self.assertEqual(state.ui.filters.search, "")
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
                self.assertNotIn("root_dir", parsed["paths"])
                self.assertIs(type(parsed["analysis"]["bitrate_model"]["base_width"]), int)
                self.assertIs(type(parsed["analysis"]["thresholds"]["over_bitrate_ratio"]), float)

                # 기존 classify.ipynb가 있으면 덮어쓰지 않고 보존함
                custom_nb = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}\n'
                with open(notebook_path, "w", encoding="utf-8") as f:
                    f.write(custom_nb)
                ensure_default_notebook_file()
                with open(notebook_path, encoding="utf-8") as f:
                    self.assertEqual(f.read(), custom_nb)

    def test_jsonc_comments_and_string_literal_preservation(self) -> None:
        """JSONC 주석·후행 쉼표 파싱 시 문자열 리터럴 내부의 //와 블록 주석 기호가 보존됨을 검증"""
        raw_jsonc = """
        // 파일 상단 주석
        {
          "paths": {
            "recent_dirs": ["http://example.com//keep", "D:\\\\Clips",], /* 인라인 블록 주석 */
          },
          "analysis": {
            "thresholds": {
              "over_bitrate_ratio": 1.45, // 수동 편집 값
            },
          },
          "ui": {
            "exception_tokens": ["https://host//skip"],
            "theme_mode": "dark",
            "filters": {
              "search": "clip",
            },
          },
        }
        """
        cleaned = strip_jsonc_comments(raw_jsonc)
        loaded_dict = json.loads(cleaned)
        self.assertEqual(loaded_dict["paths"]["recent_dirs"][0], "http://example.com//keep")
        self.assertEqual(loaded_dict["ui"]["exception_tokens"], ["https://host//skip"])

        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (_root_path, state_path):
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                f.write(raw_jsonc)

            loaded = load_app_state()
            self.assertAlmostEqual(loaded.analysis.thresholds.over_bitrate_ratio, 1.45)
            self.assertEqual(loaded.ui.theme_mode, "dark")
            self.assertEqual(loaded.ui.filters.search, "clip")
            self.assertEqual(loaded.ui.exception_tokens, ["https://host//skip"])
            self.assertEqual(loaded.paths.recent_dirs[0], "http://example.com//keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
