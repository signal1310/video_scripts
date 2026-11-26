"""통합 설정, GUI 상태 파일(data/config.json) 영속화 및 data/rootdir.txt 기준 정의를 검증하는 단위 테스트"""

import json
import os
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    AnalysisConfig,
    BitrateModelConfig,
    ClassifierConfig,
    DisplayConfig,
    EncodeConfig,
    PathsConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.core.paths import DEFAULT_ROOT_DIR, ROOTDIR_FILENAME, get_root_dir
from src.core.settings.app_state import (
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
from src.core.settings.state_codec import strip_jsonc_comments
from src.core.settings.ui_state import UiFilterState, UiLayoutState, UiState
from src.service.analysis_store import load_manual_target_store, save_manual_target_store


@contextmanager
def _isolated_data_paths(tmpdir: str) -> Generator[tuple[str, str]]:
    root_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
    state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)
    with (
        patch("src.core.paths.get_rootdir_path", return_value=root_path),
        patch("src.core.settings.app_state.get_state_path", return_value=state_path),
    ):
        yield root_path, state_path


class TestAppStateModule(unittest.TestCase):
    """data/config.json 저장, 로드, data/rootdir.txt 기준 정의 및 기본값 복원을 검증"""

    def test_portable_state_path_uses_app_base_dir(self) -> None:
        """통합 설정 파일 경로가 실행 기준 폴더의 data/config.json임을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("src.core.paths.get_app_base_dir", return_value=tmpdir),
        ):
            self.assertEqual(
                get_state_path(),
                os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME),
            )

    def test_save_and_load_roundtrip(self) -> None:
        """save_app_state가 data/*에 전체 UI, 분석, 레이아웃 상태를 왕복 저장함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (root_path, state_path):
            state = AppState(
                paths=PathsConfig(
                    recent_dirs=[os.path.normpath(tmpdir)],
                ),
                analysis=AnalysisConfig(
                    thresholds=ThresholdsConfig(
                        over_bitrate_ratio=1.55,
                        keyframe_interval_sec=3.5,
                    ),
                    savings=SavingsConfig(
                        min_saved_mb=12.0,
                        min_mb_per_min=4.5,
                        exempt_saved_mb=80.0,
                    ),
                    encode=EncodeConfig(force_legacy_compat=True),
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
                    analyze_mode="full",
                    show_setting_hints=False,
                    exception_tokens=["v.codec == 'hevc'", "v.duration < 90"],
                    disabled_exception_tokens=["v.fps < 24", "legacy_tag"],
                    filters=UiFilterState(
                        search="intro",
                    ),
                    layout=UiLayoutState(
                        window_geometry="AAABBB==",
                        table_header_state="CCCDDD==",
                        settings_tab=1,
                        help_tab=2,
                        top_area_collapsed=True,
                    ),
                ),
            )
            save_app_state(state, root_dir=tmpdir)

            self.assertTrue(os.path.isfile(root_path))
            self.assertTrue(os.path.isfile(state_path))
            self.assertEqual(load_app_state().to_dict(), state.to_dict())
            self.assertTrue(load_app_state().analysis.encode.force_legacy_compat)
            self.assertEqual(get_root_dir(), tmpdir)
            self.assertEqual(resolve_effective_root_dir(), tmpdir)
            with open(state_path, encoding="utf-8") as f:
                parsed = json.loads(strip_jsonc_comments(f.read()))
            self.assertNotIn("root_dir", parsed["paths"])

    def test_column_preset_layouts_round_trip_and_reset(self) -> None:
        """프리셋 키별 열 배치와 활성 프리셋이 설정 파일 왕복에서 유지되고 초기화 뒤 기본값으로 돌아가는지 검증"""
        from src.core.settings.ui_state import DEFAULT_COLUMN_PRESET

        quality_layout = '{"version": 2, "columns": {"name": {"order": 0, "width": 300, "hidden": false}}}'
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (_root_path, _state_path):
            save_app_state(
                AppState(
                    ui=UiState(
                        layout=UiLayoutState(
                            column_preset="quality",
                            column_preset_layouts={"quality": quality_layout},
                        )
                    )
                ),
                root_dir=tmpdir,
            )

            loaded = load_app_state().ui.layout
            self.assertEqual(loaded.column_preset, "quality")
            self.assertEqual(loaded.column_preset_layouts, {"quality": quality_layout})

            reset = reset_app_state(
                preserve_root_dir=True, preserve_recent_dirs=True, preserve_layout=False
            )
            self.assertEqual(reset.ui.layout.column_preset, DEFAULT_COLUMN_PRESET)
            self.assertEqual(reset.ui.layout.column_preset_layouts, {})

            kept = reset_app_state(preserve_root_dir=True, preserve_recent_dirs=True)
            self.assertEqual(kept.ui.layout.column_preset, DEFAULT_COLUMN_PRESET)

    def test_rootdir_file_is_single_source_and_ignores_legacy_json_root_dir(self) -> None:
        """data/rootdir.txt만 작업 경로 기준 정의로 사용되고 구형 config.json의 root_dir은 무시, 정리됨을 검증"""
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

    def test_manual_tiers_are_saved_copied_and_reset(self) -> None:
        """직접 지정 맵은 전용 DB에 저장되고, 복사본은 독립적이며, 저장 전 상태 파일에는 기록되지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir):
            work = os.path.normpath(tmpdir)
            state = AppState()
            state.ui.manual_target_tiers = {work: {"a.mp4": "720p"}}
            save_manual_target_store(state.ui.manual_target_tiers)
            self.assertEqual(load_manual_target_store({}).get(work), {"a.mp4": "720p"})
            self.assertEqual(load_app_state().ui.manual_target_tiers, {})

            copied = state.copy()
            copied.ui.manual_target_tiers[work]["a.mp4"] = "keep"
            self.assertEqual(state.ui.manual_target_tiers, {work: {"a.mp4": "720p"}})

            save_manual_target_store({})
            self.assertNotIn(work, load_manual_target_store({}))

    def test_shortcut_overrides_are_saved_copied_and_reset(self) -> None:
        """단축키 오버라이드가 저장과 불러오기에서 유지되고, 복사본은 독립적이며, 초기화하면 비워짐을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir):
            state = AppState()
            state.ui.shortcuts = {"file.open_dir": "F5", "analyze.run_default": ""}
            save_app_state(state)
            self.assertEqual(
                load_app_state().ui.shortcuts,
                {"file.open_dir": "F5", "analyze.run_default": ""},
            )

            copied = state.copy()
            copied.ui.shortcuts["file.open_dir"] = "Ctrl+P"
            self.assertEqual(state.ui.shortcuts["file.open_dir"], "F5")

            self.assertEqual(reset_app_state().ui.shortcuts, {})
            self.assertEqual(load_app_state().ui.shortcuts, {})

    def test_shortcut_tab_index_persists_and_invalid_shortcut_entries_are_dropped(self) -> None:
        """단축키 탭 인덱스 2가 유지되고 저장 파일의 잘못된 단축키 항목은 불러올 때 제거됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir) as (_root, state_path):
            state = AppState()
            state.ui.layout.settings_tab = 2
            save_app_state(state)
            self.assertEqual(load_app_state().ui.layout.settings_tab, 2)

            with open(state_path, encoding="utf-8") as f:
                parsed = json.loads(strip_jsonc_comments(f.read()))
            parsed["ui"]["shortcuts"] = {"unknown.action": "Ctrl+X", "file.open_dir": 7}
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f)
            self.assertEqual(load_app_state().ui.shortcuts, {})

    def test_manual_tier_keys_of_one_folder_in_different_spellings_merge_in_session(self) -> None:
        """표기가 다른 옛 키로 저장된 같은 폴더의 직접 지정이 세션에 적용되면 한 항목으로 합쳐짐을 검증"""
        from src.core.config import ClassifierConfig
        from src.service.cache import CacheRegistry
        from src.service.session import ClassifierSession, normalize_root_key

        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir):
            work = os.path.normpath(tmpdir)
            save_manual_target_store(
                {
                    work: {"a.mp4": "720p"},
                    work.replace(os.sep, "/"): {"b.mp4": "keep"},
                }
            )

            session = ClassifierSession(work, ClassifierConfig(), registry=CacheRegistry())
            session.set_manual_target_store(load_manual_target_store({}))
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p", "b.mp4": "keep"})
            self.assertEqual(list(session.manual_target_store), [normalize_root_key(work)])

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
                        analyze_mode="full",
                        exception_tokens=["sample"],
                        filters=UiFilterState(
                            search="abc",
                        ),
                        layout=UiLayoutState(
                            window_geometry="GEOM_B64",
                            table_header_state="HDR_B64",
                            settings_tab=1,
                            help_tab=2,
                            table_left_scroll=90,
                            table_current_filename="a.mp4",
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
            self.assertEqual(reset.ui.analyze_mode, "fast")
            self.assertEqual(reset.ui.filters.search, "")
            self.assertEqual(reset.ui.theme_mode, "system")
            self.assertEqual(reset.ui.layout.window_geometry, "GEOM_B64")
            self.assertEqual(reset.ui.layout.table_header_state, "HDR_B64")
            self.assertEqual(reset.ui.layout.settings_tab, 1)
            self.assertEqual(reset.ui.layout.help_tab, 2)
            self.assertEqual(reset.ui.layout.table_left_scroll, 90)
            self.assertEqual(reset.ui.layout.table_current_filename, "a.mp4")

            with open(state_path, "w", encoding="utf-8") as f:
                f.write("{corrupted json")
            recovered = load_app_state()
            self.assertEqual(get_root_dir(), tmpdir)
            self.assertEqual(recovered.ui.analyze_mode, "fast")

            reset_app_state(preserve_root_dir=False)
            self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)

    def test_first_run_auto_creates_rootdir_and_config_json_from_defaults(self) -> None:
        """초기 실행 시 미존재 data/*, classify.ipynb를 기본값 기반으로 자동 생성함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notebook_path = os.path.join(tmpdir, NOTEBOOK_FILENAME)
            with (
                _isolated_data_paths(tmpdir) as (root_path, state_path),
                patch("src.core.settings.app_state.get_notebook_path", return_value=notebook_path),
            ):
                self.assertFalse(os.path.exists(root_path))
                self.assertFalse(os.path.exists(state_path))
                self.assertFalse(os.path.exists(notebook_path))

                # 기본 인자 호출은 읽기 전용이어야 하며 기본 파일을 생성하지 않아야 함
                load_app_state()
                self.assertFalse(os.path.exists(root_path))
                self.assertFalse(os.path.exists(state_path))
                self.assertFalse(os.path.exists(notebook_path))

                state = ensure_default_config_files()
                self.assertTrue(os.path.isfile(root_path))
                self.assertTrue(os.path.isfile(state_path))
                self.assertTrue(os.path.isfile(notebook_path))
                self.assertEqual(get_root_dir(), DEFAULT_ROOT_DIR)
                self.assertEqual(state.ui.analyze_mode, "fast")
                self.assertEqual(state.ui.filters.search, "")
                self.assertEqual(state.ui.theme_mode, "system")
                self.assertEqual(state.to_classifier_config().to_dict(), ClassifierConfig().to_dict())

                # JSONC 파일에 섹션, 숫자 타입, // 주석이 포함되고 중첩 깊이 3 이하를 유지하는지 검증
                with open(state_path, encoding="utf-8") as f:
                    raw_text = f.read()
                self.assertIn("// 1. 경로", raw_text)
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
        """JSONC 주석, 후행 쉼표 파싱 시 문자열 리터럴 내부의 //와 블록 주석 기호가 보존됨을 검증"""
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


class TestRecursiveScanAndMultiRootPersistence(unittest.TestCase):
    """하위 폴더 포함 설정의 저장 왕복과 data/rootdir.txt 다중 작업 폴더 왕복을 검증"""

    def test_recursive_scan_round_trips_through_config_file(self) -> None:
        """하위 폴더 포함 설정이 data/config.json 저장 후 다시 읽을 때 유지됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "data", CONFIG_FILENAME)
            with patch("src.core.settings.app_state.get_state_path", return_value=state_path):
                state = AppState()
                state.ui.recursive_scan = True
                save_app_state(state)
                loaded = load_app_state()
            self.assertTrue(loaded.ui.recursive_scan)

    def test_multi_line_rootdir_round_trips_as_list(self) -> None:
        """data/rootdir.txt에 여러 작업 폴더를 쓰면 같은 목록과 세미콜론 문자열로 읽힘을 검증"""
        from src.core.paths import get_root_dir, get_root_dirs, set_root_dirs

        with tempfile.TemporaryDirectory() as tmpdir:
            first = os.path.join(tmpdir, "드라마")
            second = os.path.join(tmpdir, "예능")
            os.makedirs(first)
            os.makedirs(second)
            rootdir_path = os.path.join(tmpdir, "data", "rootdir.txt")
            with patch("src.core.paths.get_rootdir_path", return_value=rootdir_path):
                set_root_dirs([first, second, first.upper()])
                self.assertEqual(get_root_dirs(), [first, second])
                self.assertEqual(get_root_dir(), f"{first}; {second}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
