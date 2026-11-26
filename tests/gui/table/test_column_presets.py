"""열 프리셋별 기본 순서와 표시 열, 사용자 배치 저장과 복원, 초기화와 이전 형식 승계를 검증"""

import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.settings.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments
from src.core.settings.ui_state import UiLayoutState
from src.gui.constants import (
    COLUMN_PRESETS,
    PRESET_ALL,
    PRESET_DEFAULT,
    PRESET_QUALITY,
    PRESET_SPECS,
)
from src.gui.main_window import MainWindow
from src.gui.table.column_history import (
    COLUMN_PRESET_HISTORY_LABEL,
    COLUMN_PRESET_RESET_HISTORY_LABEL,
)
from src.gui.table.column_layout_state import (
    capture_column_layouts,
    decode_column_layouts,
    encode_column_layouts,
)
from src.gui.table.column_presets import (
    PRESET_COL_ORDERS,
    PRESET_VISIBLE_COLS,
    PRESET_VISIBLE_ORDERS,
    apply_preset_default,
    hidden_columns_for_preset,
    is_preset_default_layout,
    load_preset_layout,
    ordered_columns_for_preset,
    store_preset_layout,
)
from src.gui.table.column_sizing import ALL_COLUMNS, DEFAULT_COL_ORDER, DEFAULT_HIDDEN_COLS
from src.gui.table.table_model import VideoTableModel
from src.gui.table.video_table_view import VideoTableView
from src.gui.window_layout import restore_window_ui_state
from tests._gui_helpers import GuiTestCase, isolate_app_state

_APP = QApplication.instance() or QApplication([])
_UNKNOWN_PRESET: str = "future_preset"


def _visual_columns(view: VideoTableView) -> list[int]:
    """표 헤더의 왼쪽부터 열 번호 목록"""
    header = view.filter_header
    return [header.logicalIndex(index) for index in range(view.table_model.columnCount())]


def _visible_columns(view: VideoTableView) -> list[int]:
    """표 헤더의 왼쪽부터 실제로 보이는 열 번호 목록"""
    return [col for col in _visual_columns(view) if not view.isColumnHidden(col)]


def _hidden_columns(view: VideoTableView) -> frozenset[int]:
    """표에서 숨겨진 열 번호 집합"""
    return frozenset(
        col for col in range(view.table_model.columnCount()) if view.isColumnHidden(col)
    )


class PresetOrderDefinitionTests(unittest.TestCase):
    """프리셋별 기본 순서와 표시 열 정의가 전체 열을 빠짐없이 덮는지 검증"""

    def test_every_preset_lists_each_column_once(self) -> None:
        """프리셋마다 28개 열이 중복 없이 한 번씩만 순서에 들어 있는지 검증"""
        for preset_key in COLUMN_PRESETS:
            with self.subTest(preset=preset_key):
                order = PRESET_COL_ORDERS[preset_key]
                self.assertEqual(len(order), len(ALL_COLUMNS))
                self.assertEqual(set(order), set(ALL_COLUMNS))
                self.assertEqual(
                    PRESET_VISIBLE_COLS[preset_key],
                    frozenset(PRESET_VISIBLE_ORDERS[preset_key]),
                )

    def test_visible_columns_keep_the_preset_order(self) -> None:
        """보이는 열이 전체 순서에서 같은 상대 순서를 지키는지 검증"""
        for preset_key in COLUMN_PRESETS:
            with self.subTest(preset=preset_key):
                order = PRESET_COL_ORDERS[preset_key]
                positions = [order.index(col) for col in PRESET_VISIBLE_ORDERS[preset_key]]
                self.assertEqual(positions, sorted(positions))
                self.assertEqual(
                    set(PRESET_VISIBLE_ORDERS[preset_key]),
                    set(ordered_columns_for_preset(preset_key)) - set(hidden_columns_for_preset(preset_key)),
                )

    def test_default_order_starts_with_decisions_then_effects_then_state(self) -> None:
        """기본 순서가 판정, 효과, 상태, 사양 순서로 이어지는지 검증"""
        order = PRESET_COL_ORDERS[PRESET_DEFAULT]
        self.assertIs(order, DEFAULT_COL_ORDER)
        self.assertEqual(order[0], VideoTableModel.COL_NAME)
        self.assertLess(
            order.index(VideoTableModel.COL_ACTION),
            order.index(VideoTableModel.COL_QUALITY_VALUE),
        )
        self.assertLess(
            order.index(VideoTableModel.COL_QUALITY_VALUE),
            order.index(VideoTableModel.COL_REASON),
        )
        self.assertLess(
            order.index(VideoTableModel.COL_REASON), order.index(VideoTableModel.COL_ZONE)
        )
        # - 효과 묶음은 화질 등급 뒤, 상태 열 앞에 옴
        for col in (
            VideoTableModel.COL_SAVED_SIZE,
            VideoTableModel.COL_SIZE,
            VideoTableModel.COL_DURATION,
            VideoTableModel.COL_EST_ENCODE_TIME,
        ):
            self.assertGreater(order.index(col), order.index(VideoTableModel.COL_ZONE))
            self.assertLess(order.index(col), order.index(VideoTableModel.COL_ENCODE_STATE))
        # - 상태 열은 사양 열보다 앞, FFmpeg 인자 열은 맨 뒤
        self.assertLess(
            order.index(VideoTableModel.COL_ENCODE_STATE),
            order.index(VideoTableModel.COL_CODEC),
        )
        self.assertLess(
            order.index(VideoTableModel.COL_ENCODE_STATE),
            order.index(VideoTableModel.COL_FPS),
        )
        self.assertEqual(order[-1], VideoTableModel.COL_FFMPEG_ARGS)

    def test_quality_preset_follows_the_measurement_pipeline(self) -> None:
        """화질, 해상도 프리셋이 측정 단계 순서로 시작하고 품질값, 판정 사유를 뒤에 두는지 검증"""
        order = PRESET_VISIBLE_ORDERS[PRESET_QUALITY]
        pipeline = (
            VideoTableModel.COL_WIDTH,
            VideoTableModel.COL_HEIGHT,
            VideoTableModel.COL_ACTIVE_FRAME,
            VideoTableModel.COL_DOWNSCALE_CANDIDATE,
            VideoTableModel.COL_QUALITY_METRIC,
            VideoTableModel.COL_OUTPUT_RES,
        )
        positions = [order.index(col) for col in pipeline]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(
            order.index(VideoTableModel.COL_OUTPUT_RES),
            order.index(VideoTableModel.COL_QUALITY_VALUE),
        )
        self.assertLess(
            order.index(VideoTableModel.COL_QUALITY_VALUE),
            order.index(VideoTableModel.COL_REASON),
        )
        self.assertNotIn(VideoTableModel.COL_CODEC, order)

    def test_specs_preset_puts_stream_specs_then_bitrates_then_size(self) -> None:
        """기술 사양 프리셋이 스트림 사양, 비트레이트, 용량 순서인지 검증"""
        order = PRESET_VISIBLE_ORDERS[PRESET_SPECS]
        stream_specs = (
            VideoTableModel.COL_CODEC,
            VideoTableModel.COL_WIDTH,
            VideoTableModel.COL_HEIGHT,
            VideoTableModel.COL_FPS,
        )
        bitrates = (
            VideoTableModel.COL_BITRATE,
            VideoTableModel.COL_OPT_BITRATE,
            VideoTableModel.COL_OUT_BITRATE,
        )
        spec_positions = [order.index(col) for col in stream_specs]
        self.assertEqual(spec_positions, sorted(spec_positions))
        self.assertLess(max(spec_positions), min(order.index(col) for col in bitrates))
        self.assertLess(
            order.index(VideoTableModel.COL_OUT_BITRATE),
            order.index(VideoTableModel.COL_SIZE),
        )
        self.assertNotIn(VideoTableModel.COL_ACTION, order)

    def test_all_preset_shows_every_column_in_default_order(self) -> None:
        """전체 프리셋이 모든 열을 기본 순서로 보이는 상태로 두는지 검증"""
        self.assertEqual(PRESET_VISIBLE_ORDERS[PRESET_ALL], DEFAULT_COL_ORDER)
        self.assertEqual(hidden_columns_for_preset(PRESET_ALL), frozenset())

    def test_hidden_columns_come_from_the_visible_set(self) -> None:
        """숨김 열 집합이 보이는 열의 여집합이고 알 수 없는 프리셋은 기본값을 쓰는지 검증"""
        self.assertEqual(hidden_columns_for_preset(PRESET_DEFAULT), DEFAULT_HIDDEN_COLS)
        self.assertEqual(
            ALL_COLUMNS - PRESET_VISIBLE_COLS[PRESET_SPECS],
            hidden_columns_for_preset(PRESET_SPECS),
        )
        self.assertEqual(hidden_columns_for_preset(_UNKNOWN_PRESET), DEFAULT_HIDDEN_COLS)

    def test_unknown_preset_uses_the_default_column_order(self) -> None:
        """알 수 없는 프리셋 키로 전체 순서를 물으면 기본 열 순서를 돌려주는지 검증"""
        self.assertEqual(ordered_columns_for_preset(_UNKNOWN_PRESET), DEFAULT_COL_ORDER)


class PresetApplyTests(unittest.TestCase):
    """프리셋 적용이 표시 열과 열 순서를 함께 바꾸고, 저장 배치가 없으면 기본 배치를 쓰는지 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.addCleanup(self.view.close)

    def test_apply_preset_default_sets_the_preset_order_and_visibility(self) -> None:
        """기술 사양 기본 배치 적용이 프리셋의 전체 순서와 숨김 열을 표에 그대로 반영하는지 검증"""
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        apply_preset_default(self.view, PRESET_SPECS)
        self.assertEqual(_visual_columns(self.view), list(PRESET_COL_ORDERS[PRESET_SPECS]))
        self.assertEqual(_hidden_columns(self.view), hidden_columns_for_preset(PRESET_SPECS))
        self.assertIn(VideoTableModel.COL_CODEC, _visible_columns(self.view))
        self.assertNotIn(VideoTableModel.COL_ACTION, _visible_columns(self.view))

    def test_unknown_preset_applies_the_default_layout(self) -> None:
        """알 수 없는 프리셋 키를 적용하면 기본 프리셋 배치가 되는지 검증"""
        apply_preset_default(self.view, _UNKNOWN_PRESET)
        self.assertEqual(_visual_columns(self.view), list(DEFAULT_COL_ORDER))
        self.assertEqual(_hidden_columns(self.view), DEFAULT_HIDDEN_COLS)

    def test_store_then_load_restores_the_user_layout(self) -> None:
        """프리셋에 현재 배치를 저장한 뒤 다시 불러오면 그 배치가 복원되는지 검증"""
        layout = UiLayoutState()
        header = self.view.filter_header
        header.moveSection(header.visualIndex(VideoTableModel.COL_REASON), 1)
        self.view.setColumnHidden(VideoTableModel.COL_KEYFRAME, True)
        store_preset_layout(self.view, layout, PRESET_QUALITY)
        user_order = _visual_columns(self.view)
        user_hidden = _hidden_columns(self.view)

        apply_preset_default(self.view, PRESET_SPECS)
        load_preset_layout(self.view, layout, PRESET_QUALITY)
        self.assertEqual(_visual_columns(self.view), user_order)
        self.assertEqual(_hidden_columns(self.view), user_hidden)
        self.assertEqual(layout.column_preset, PRESET_QUALITY)
        self.assertEqual(
            layout.table_header_state, layout.column_preset_layouts[PRESET_QUALITY]
        )
        self.assertEqual(
            decode_column_layouts(layout.column_preset_layouts[PRESET_QUALITY]),
            capture_column_layouts(self.view),
        )

    def test_load_without_stored_layout_falls_back_to_the_preset_default(self) -> None:
        """저장된 배치가 없는 프리셋을 불러오면 그 프리셋의 기본 배치가 되는지 검증"""
        load_preset_layout(self.view, UiLayoutState(), PRESET_SPECS)
        self.assertEqual(_visual_columns(self.view), list(PRESET_COL_ORDERS[PRESET_SPECS]))
        self.assertEqual(_hidden_columns(self.view), hidden_columns_for_preset(PRESET_SPECS))

    def test_load_tolerates_an_unreadable_stored_layout(self) -> None:
        """저장 문자열을 해석할 수 없으면 기본 배치로 되돌아가는지 검증"""
        layout = UiLayoutState(column_preset_layouts={PRESET_DEFAULT: "not a column state"})
        load_preset_layout(self.view, layout, PRESET_DEFAULT)
        self.assertEqual(_visual_columns(self.view), list(DEFAULT_COL_ORDER))
        self.assertEqual(_hidden_columns(self.view), DEFAULT_HIDDEN_COLS)

    def test_is_preset_default_layout_detects_order_and_visibility_only(self) -> None:
        """프리셋 기본 배치 판별이 순서, 표시만 보고 열 폭은 무시하는지 검증"""
        apply_preset_default(self.view, PRESET_SPECS)
        self.assertTrue(is_preset_default_layout(self.view, PRESET_SPECS))
        self.assertFalse(is_preset_default_layout(self.view, PRESET_QUALITY))

        self.view.setColumnWidth(VideoTableModel.COL_CODEC, 155)
        self.assertTrue(is_preset_default_layout(self.view, PRESET_SPECS))

        self.view.filter_header.moveSection(
            self.view.filter_header.visualIndex(VideoTableModel.COL_SIZE), 1
        )
        self.assertFalse(is_preset_default_layout(self.view, PRESET_SPECS))

        apply_preset_default(self.view, PRESET_SPECS)
        self.view.setColumnHidden(VideoTableModel.COL_CODEC, True)
        self.assertFalse(is_preset_default_layout(self.view, PRESET_SPECS))

    def test_stored_layout_ignores_unknown_column_keys(self) -> None:
        """저장 배치에 현재 코드에 없는 열 키가 섞여 있어도 나머지 배치를 그대로 적용하는지 검증"""
        self.view.setColumnHidden(VideoTableModel.COL_ROTATE, True)
        self.view.filter_header.moveSection(
            self.view.filter_header.visualIndex(VideoTableModel.COL_SIZE), 1
        )
        stored = json.loads(encode_column_layouts(capture_column_layouts(self.view)))
        stored["columns"]["future_column"] = {"order": 0, "width": 10, "hidden": False}
        stored["columns"].pop("ffmpeg_args")
        layout = UiLayoutState(column_preset_layouts={PRESET_DEFAULT: json.dumps(stored)})
        load_preset_layout(self.view, layout, PRESET_DEFAULT)
        self.assertTrue(self.view.isColumnHidden(VideoTableModel.COL_ROTATE))
        self.assertEqual(_visual_columns(self.view)[1], VideoTableModel.COL_SIZE)


class LayoutMigrationAndStateTests(unittest.TestCase):
    """창 복원이 이전 형식 열 상태를 활성 프리셋 사용자 배치로 승계하는지, 상태 저장이 왕복하는지 검증"""

    def _window_and_table(self) -> tuple[QMainWindow, VideoTableView]:
        window = QMainWindow()
        table = VideoTableView(parent=window)
        self.addCleanup(window.close)
        return window, table

    def test_previous_format_layout_becomes_the_active_preset_layout(self) -> None:
        """이전 형식의 열 상태만 있으면 활성 프리셋 배치로 승계해 표시와 순서를 잃지 않는지 검증"""
        window, table = self._window_and_table()
        header = table.filter_header
        header.moveSection(header.visualIndex(VideoTableModel.COL_REASON), 1)
        table.setColumnHidden(VideoTableModel.COL_ROTATE, True)
        legacy = encode_column_layouts(capture_column_layouts(table))
        expected_order = _visual_columns(table)

        layout = UiLayoutState(table_header_state=legacy, column_preset=PRESET_SPECS)
        restore_window_ui_state(window, layout, table)

        self.assertEqual(_visual_columns(table), expected_order)
        self.assertTrue(table.isColumnHidden(VideoTableModel.COL_ROTATE))
        self.assertEqual(layout.column_preset_layouts, {PRESET_SPECS: legacy})

    def test_preset_layout_wins_over_the_legacy_copy(self) -> None:
        """활성 프리셋 배치가 있으면 이전 형식 열 상태보다 그 배치를 쓰는지 검증"""
        window, table = self._window_and_table()
        current = encode_column_layouts(capture_column_layouts(table))
        table.setColumnHidden(VideoTableModel.COL_CODEC, True)
        legacy = encode_column_layouts(capture_column_layouts(table))

        layout = UiLayoutState(
            table_header_state=legacy,
            column_preset=PRESET_DEFAULT,
            column_preset_layouts={PRESET_DEFAULT: current},
        )
        restore_window_ui_state(window, layout, table)

        self.assertFalse(table.isColumnHidden(VideoTableModel.COL_CODEC))
        self.assertEqual(layout.column_preset_layouts, {PRESET_DEFAULT: current})

    def test_unreadable_layout_restores_the_active_preset_default(self) -> None:
        """저장 배치를 해석할 수 없으면 활성 프리셋 기본 배치로 복원하는지 검증"""
        window, table = self._window_and_table()
        layout = UiLayoutState(
            table_header_state="stale", column_preset=PRESET_SPECS
        )
        restore_window_ui_state(window, layout, table)
        self.assertEqual(_visual_columns(table), list(PRESET_COL_ORDERS[PRESET_SPECS]))
        self.assertEqual(_hidden_columns(table), hidden_columns_for_preset(PRESET_SPECS))
        self.assertEqual(layout.column_preset_layouts, {})

    def test_preset_layouts_round_trip_through_the_state_file(self) -> None:
        """프리셋 키별 배치와 활성 프리셋이 저장 문자열을 거쳐 그대로 복원되는지 검증"""
        payload = {
            "ui": {
                "layout": {
                    "column_preset": PRESET_QUALITY,
                    "column_preset_layouts": {
                        PRESET_QUALITY: '{"version": 2, "columns": {}}',
                        PRESET_SPECS: '{"version": 2, "columns": {}}',
                    },
                }
            }
        }
        state = parse_state_mapping(payload)
        self.assertEqual(state.ui.layout.column_preset, PRESET_QUALITY)
        self.assertEqual(
            state.ui.layout.column_preset_layouts,
            {
                PRESET_QUALITY: '{"version": 2, "columns": {}}',
                PRESET_SPECS: '{"version": 2, "columns": {}}',
            },
        )
        rendered = render_state_jsonc(state)
        restored = parse_state_mapping(
            json.loads(strip_jsonc_comments(rendered))
        )
        self.assertEqual(restored.ui.layout, state.ui.layout)

    def test_missing_and_unknown_preset_state_falls_back_to_defaults(self) -> None:
        """새 키가 없는 저장 파일과 알 수 없는 프리셋 키가 기본값으로 정규화되는지 검증"""
        legacy = parse_state_mapping({"ui": {"layout": {"table_header_state": "legacy"}}})
        self.assertEqual(legacy.ui.layout.column_preset, PRESET_DEFAULT)
        self.assertEqual(legacy.ui.layout.column_preset_layouts, {})

        unknown = parse_state_mapping(
            {
                "ui": {
                    "layout": {
                        "column_preset": _UNKNOWN_PRESET,
                        "column_preset_layouts": {
                            PRESET_DEFAULT: "",
                            _UNKNOWN_PRESET: "layout",
                            PRESET_ALL: "{}",
                        },
                    }
                }
            }
        )
        self.assertEqual(unknown.ui.layout.column_preset, PRESET_DEFAULT)
        self.assertEqual(unknown.ui.layout.column_preset_layouts, {PRESET_ALL: "{}"})


class PresetUserLayoutWindowTests(GuiTestCase):
    """메인 창에서 프리셋을 오갈 때 프리셋별 사용자 배치가 유지되고 초기화, 되돌리기, 재시작 복원이 되는지 검증"""

    def setUp(self) -> None:
        from tests._sandbox import reset_sandbox_files

        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        # - 창이 연 캐시 DB를 닫은 뒤에 임시 폴더를 지우도록 폴더 정리를 먼저 예약
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        isolate_app_state(self, self.root)

    def _build_window(self) -> MainWindow:
        """격리된 설정 경로와 1개 영상 캐시를 쓰는 메인 창"""
        from src.classifier import VideoClassifier
        from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
        from tests._factories import make_video

        clf = VideoClassifier(root_dir=self.root)
        videos = [make_video(filename="clip.mp4")]
        DEFAULT_CACHE_REGISTRY.put(
            self.root, TableCache(self.root, videos, include_keyframe=True)
        )
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        return mw

    def _edit_layout(self, view: VideoTableView, col: int, visual_index: int) -> None:
        """헤더 드래그와 같은 열 이동 한 단계를 만들어 활성 프리셋 사용자 배치로 저장"""
        header = view.filter_header
        header.layout_edit_started.emit()
        header.moveSection(header.visualIndex(col), visual_index)
        header.layout_edit_finished.emit()

    def test_switching_away_and_back_restores_the_user_layout(self) -> None:
        """기본에서 열 순서를 바꾼 뒤 다른 프리셋을 거쳐 돌아오면 사용자 배치가 그대로 복원되는지 검증"""
        mw = self._build_window()
        view = mw.table_view
        mw.table_display.apply_preset(PRESET_DEFAULT)
        self._edit_layout(view, VideoTableModel.COL_REASON, 1)
        user_order = _visual_columns(view)
        self.assertNotEqual(user_order, list(DEFAULT_COL_ORDER))

        mw.table_display.apply_preset(PRESET_SPECS)
        self.assertEqual(_visual_columns(view), list(PRESET_COL_ORDERS[PRESET_SPECS]))
        mw.table_display.apply_preset(PRESET_DEFAULT)

        self.assertEqual(_visual_columns(view), user_order)
        self.assertEqual(mw._app_state.ui.layout.column_preset, PRESET_DEFAULT)
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")

    def test_window_sync_preset_updates_the_filter_bar_label(self) -> None:
        """숨김 열이 바뀐 뒤 창의 프리셋 동기화가 필터 바 열 프리셋 표시를 현재 배치에 맞추는지 검증"""
        mw = self._build_window()
        mw.table_display.apply_preset(PRESET_SPECS)
        mw.filter_bar.btn_column_preset.setText("열: 임시")

        mw.sync_preset()

        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기술 사양")

    def test_reset_menu_item_is_enabled_only_while_the_layout_differs_from_the_default(self) -> None:
        """프리셋 초기화 항목이 기본 배치에서는 꺼지고 사용자가 배치를 바꾸면 켜지며 초기화 뒤 다시 꺼지는지 검증"""
        mw = self._build_window()
        view = mw.table_view
        mw.table_display.apply_preset(PRESET_QUALITY)
        self.assertFalse(mw.menu.act_reset_column_preset.isEnabled())

        self._edit_layout(view, VideoTableModel.COL_REASON, 1)
        mw.table_display.sync_preset()
        self.assertTrue(mw.menu.act_reset_column_preset.isEnabled())

        mw.menu.act_reset_column_preset.trigger()
        self.assertFalse(mw.menu.act_reset_column_preset.isEnabled())

    def test_menu_column_visibility_change_resyncs_row_grouping_options(self) -> None:
        """보기 메뉴에서 열을 숨기면 행 묶기 기준 열 확인이 다시 도는지 검증"""
        mw = self._build_window()
        grouping = mw.table_display.row_grouping
        assert grouping is not None
        with mock.patch.object(grouping, "sync_options") as sync_options:
            mw.table_display.set_column_visible(VideoTableModel.COL_REASON, False)
        sync_options.assert_called()

    def test_reset_drops_the_user_layout_and_the_menu_item_reports_it(self) -> None:
        """프리셋 초기화 메뉴가 저장한 배치를 지우고 프리셋 기본 배치로 되돌리는지 검증"""
        mw = self._build_window()
        view = mw.table_view
        mw.table_display.apply_preset(PRESET_QUALITY)
        self._edit_layout(view, VideoTableModel.COL_REASON, 1)
        self.assertIn(PRESET_QUALITY, mw._app_state.ui.layout.column_preset_layouts)

        mw.menu.act_reset_column_preset.trigger()

        self.assertEqual(_visual_columns(view), list(PRESET_COL_ORDERS[PRESET_QUALITY]))
        self.assertNotIn(PRESET_QUALITY, mw._app_state.ui.layout.column_preset_layouts)
        self.assertEqual(
            mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_PRESET_RESET_HISTORY_LABEL})"
        )

    def test_preset_change_and_reset_are_undone_through_the_column_history(self) -> None:
        """프리셋 변경과 초기화가 각각 한 단계로 기록되어 되돌리기, 다시 실행이 배치를 함께 되돌리는지 검증"""
        mw = self._build_window()
        view = mw.table_view
        mw.table_display.apply_preset(PRESET_SPECS)
        self._edit_layout(view, VideoTableModel.COL_SIZE, 1)
        specs_user_order = _visual_columns(view)
        mw.table_display.apply_preset(PRESET_DEFAULT)
        self._edit_layout(view, VideoTableModel.COL_REASON, 1)
        user_order = _visual_columns(view)

        mw.table_display.apply_preset(PRESET_SPECS)
        self.assertEqual(
            mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_PRESET_HISTORY_LABEL})"
        )
        self.assertEqual(_visual_columns(view), specs_user_order)

        mw.menu.act_reset_column_preset.trigger()
        self.assertEqual(_visual_columns(view), list(PRESET_COL_ORDERS[PRESET_SPECS]))
        self.assertEqual(
            mw.menu.act_undo.text(), f"되돌리기 ({COLUMN_PRESET_RESET_HISTORY_LABEL})"
        )

        mw.shortcuts.trigger_undo()
        self.assertEqual(_visual_columns(view), specs_user_order)
        self.assertIn(PRESET_SPECS, mw._app_state.ui.layout.column_preset_layouts)

        mw.shortcuts.trigger_undo()
        self.assertEqual(mw._app_state.ui.layout.column_preset, PRESET_DEFAULT)
        self.assertEqual(_visual_columns(view), user_order)
        self.assertEqual(mw.filter_bar.btn_column_preset.text(), "열: 기본")
        self.assertTrue(mw.menu._preset_actions[PRESET_DEFAULT].isChecked())

        mw.shortcuts.trigger_redo()
        self.assertEqual(mw._app_state.ui.layout.column_preset, PRESET_SPECS)
        self.assertEqual(_visual_columns(view), specs_user_order)

    def test_restart_restores_the_active_preset_and_every_user_layout(self) -> None:
        """창을 닫았다 다시 열면 활성 프리셋과 프리셋별 사용자 배치가 모두 복원되는지 검증"""
        mw = self._build_window()
        view = mw.table_view
        mw.table_display.apply_preset(PRESET_QUALITY)
        self._edit_layout(view, VideoTableModel.COL_REASON, 1)
        quality_order = _visual_columns(view)
        mw.table_display.apply_preset(PRESET_SPECS)
        self._edit_layout(view, VideoTableModel.COL_SIZE, 1)
        specs_order = _visual_columns(view)
        stored = dict(mw._app_state.ui.layout.column_preset_layouts)
        mw.close()

        reopened = self._build_window()
        reopened_view = reopened.table_view
        self.assertEqual(reopened._app_state.ui.layout.column_preset, PRESET_SPECS)
        self.assertEqual(reopened.filter_bar.btn_column_preset.text(), "열: 기술 사양")
        self.assertEqual(_visual_columns(reopened_view), specs_order)
        self.assertEqual(
            set(reopened._app_state.ui.layout.column_preset_layouts), set(stored)
        )

        reopened.table_display.apply_preset(PRESET_QUALITY)
        self.assertEqual(_visual_columns(reopened_view), quality_order)
