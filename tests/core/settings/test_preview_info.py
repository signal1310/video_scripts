"""미리 보기 정보 표시 검증: 항목 키 기준, 설정 저장과 복원, 표 열과의 이름과 값 일치, 재생기 항목 서식, 오버레이 배치와 끌기, 설정 패널"""

import json
import os
import tempfile
import unittest
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QListWidget,
    QSlider,
    QToolButton,
    QWidget,
)

from src.core.config import ClassifierConfig
from src.core.messages import COLUMN_LABELS, ColumnId
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.settings.app_state import AppState, load_app_state, save_app_state
from src.core.settings.preview_info import (
    ALL_INFO_KEYS,
    DEFAULT_DETAIL_INFO_KEYS,
    DEFAULT_INFO_ANCHOR_X,
    DEFAULT_INFO_ANCHOR_Y,
    DEFAULT_INFO_BOX_HEIGHT,
    DEFAULT_INFO_BOX_WIDTH,
    DEFAULT_INFO_SCALE,
    DEFAULT_SUMMARY_INFO_KEYS,
    INFO_KEY_RESOLUTION,
    INFO_LAYOUT_MULTI_LINE,
    INFO_LAYOUT_SINGLE_LINE,
    INFO_MODE_DETAIL,
    INFO_MODE_OFF,
    INFO_MODE_SUMMARY,
    INFO_SCALE_MAX,
    INFO_SCALE_MIN,
    JUDGMENT_INFO_KEYS,
    PLAYER_INFO_KEYS,
    VIDEO_INFO_KEYS,
    normalize_info_keys,
)
from src.core.settings.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments
from src.core.settings.ui_state import (
    PREVIEW_BACKEND_QUICKLOOK,
    UiPreviewInfoState,
    UiState,
)
from src.gui.preview.preview_info_items import (
    INFO_EMPTY_TEXT,
    INFO_GROUPS,
    RESOLUTION_INFO_COLUMNS,
    TABLE_INFO_COLUMNS,
    PlayerSnapshot,
    PreviewVideoInfo,
    format_clock_ms,
    info_label,
    info_value,
    info_value_color,
    info_value_colors,
)
from src.gui.preview.preview_settings_panel import PreviewOrderList, PreviewSettingsPanel, order_keys
from src.gui.preview.video_preview_overlay import (
    PreviewInfoOverlay,
    anchor_to_pos,
    build_info_html,
    pos_to_anchor,
)
from src.gui.styles.palettes import DARK_PALETTE
from src.gui.table_columns import COL_DEF_MAP
from src.gui.table_model import VideoTableModel
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase
from tests.core.settings.test_app_state import _isolated_data_paths

_APP = QApplication.instance() or QApplication([])

_PLAYER_SNAPSHOT = PlayerSnapshot(
    decoded_size=(320, 240),
    view_size=(960, 540),
    pixel_format="Format_NV12",
    play_fps=25.24,
    position_ms=920,
    duration_ms=3000,
    frame_time_us=960_000,
)
_EMPTY_SNAPSHOT = PlayerSnapshot(
    decoded_size=None,
    view_size=(0, 0),
    pixel_format="",
    play_fps=None,
    position_ms=0,
    duration_ms=0,
    frame_time_us=None,
)


class PreviewInfoKeysTests(unittest.TestCase):
    """항목 키의 중복 없는 그룹 구성, 정규화, 기본 항목의 표시 순서를 검증"""

    def test_groups_partition_all_keys_without_duplicates(self) -> None:
        grouped = [key for _title, keys in INFO_GROUPS for key in keys]
        self.assertEqual(grouped, list(ALL_INFO_KEYS))
        self.assertEqual(len(set(ALL_INFO_KEYS)), len(ALL_INFO_KEYS))
        self.assertEqual(
            ALL_INFO_KEYS, VIDEO_INFO_KEYS + JUDGMENT_INFO_KEYS + PLAYER_INFO_KEYS
        )

    def test_normalize_drops_unknown_and_duplicates_and_keeps_the_user_order(self) -> None:
        self.assertEqual(
            normalize_info_keys(["zone", "bogus", "codec", "zone", INFO_KEY_RESOLUTION]),
            ["zone", "codec", INFO_KEY_RESOLUTION],
        )

    def test_defaults_are_valid_and_already_in_display_order(self) -> None:
        for keys in (DEFAULT_SUMMARY_INFO_KEYS, DEFAULT_DETAIL_INFO_KEYS):
            self.assertEqual(normalize_info_keys(keys), list(keys))


class PreviewInfoPersistenceTests(unittest.TestCase):
    """정보 표시 설정이 설정 파일에 저장되고 재시작 뒤에도 같은 값으로 복원됨을 검증"""

    @staticmethod
    def _custom_state() -> AppState:
        state = AppState()
        state.ui.preview_backend = PREVIEW_BACKEND_QUICKLOOK
        state.ui.preview_seek_seconds = 10
        state.ui.preview_info = UiPreviewInfoState(
            mode=INFO_MODE_DETAIL,
            layout=INFO_LAYOUT_SINGLE_LINE,
            summary_items=["codec", "zone"],
            detail_items=["name", "frame_time"],
            anchor_x=0.25,
            anchor_y=0.75,
            scale=150,
            box_width=0.25,
            box_height=0.5,
        )
        return state

    def test_values_survive_json_render_and_parse(self) -> None:
        text = render_state_jsonc(self._custom_state())
        restored = parse_state_mapping(json.loads(strip_jsonc_comments(text)))
        self.assertEqual(restored.ui.preview_info, self._custom_state().ui.preview_info)
        self.assertEqual(restored.ui.preview_backend, PREVIEW_BACKEND_QUICKLOOK)
        self.assertEqual(restored.ui.preview_seek_seconds, 10)

    def test_values_survive_save_and_load_through_the_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, _isolated_data_paths(tmpdir):
            save_app_state(self._custom_state())
            loaded = load_app_state()
        self.assertEqual(loaded.ui.preview_info, self._custom_state().ui.preview_info)

    def test_missing_section_falls_back_to_defaults(self) -> None:
        info = parse_state_mapping({"ui": {}}).ui.preview_info
        self.assertEqual(info, UiPreviewInfoState())
        self.assertEqual(info.mode, INFO_MODE_OFF)
        self.assertEqual(info.layout, INFO_LAYOUT_MULTI_LINE)
        self.assertEqual(info.summary_items, list(DEFAULT_SUMMARY_INFO_KEYS))
        self.assertEqual(info.detail_items, list(DEFAULT_DETAIL_INFO_KEYS))

    def test_invalid_values_fall_back_per_field(self) -> None:
        raw: dict[str, Any] = {
            "mode": "bogus",
            "layout": 3,
            "summary_items": "codec",
            "detail_items": ["bogus", "zone", "zone", "codec"],
            "anchor_x": 1.5,
            "anchor_y": True,
            "scale": 999,
        }
        info = parse_state_mapping({"ui": {"preview_info": raw}}).ui.preview_info
        self.assertEqual(info.mode, INFO_MODE_OFF)
        self.assertEqual(info.layout, INFO_LAYOUT_MULTI_LINE)
        self.assertEqual(info.summary_items, list(DEFAULT_SUMMARY_INFO_KEYS))
        self.assertEqual(info.detail_items, ["zone", "codec"])
        self.assertEqual(info.anchor_x, DEFAULT_INFO_ANCHOR_X)
        self.assertEqual(info.anchor_y, DEFAULT_INFO_ANCHOR_Y)
        self.assertEqual(info.scale, DEFAULT_INFO_SCALE)

    def test_non_integer_scale_falls_back_to_default(self) -> None:
        for raw in (True, 100.5, "100", None, INFO_SCALE_MIN - 1):
            with self.subTest(raw=raw):
                info = parse_state_mapping({"ui": {"preview_info": {"scale": raw}}}).ui.preview_info
                self.assertEqual(info.scale, DEFAULT_INFO_SCALE)

    def test_box_size_ratios_are_kept_in_range_and_fall_back_per_field(self) -> None:
        info = parse_state_mapping(
            {"ui": {"preview_info": {"box_width": 0.4, "box_height": 1.5}}}
        ).ui.preview_info
        self.assertEqual(info.box_width, 0.4)
        self.assertEqual(info.box_height, DEFAULT_INFO_BOX_HEIGHT)
        self.assertEqual(parse_state_mapping({}).ui.preview_info.box_width, DEFAULT_INFO_BOX_WIDTH)

    def test_empty_item_list_means_nothing_selected_not_defaults(self) -> None:
        info = parse_state_mapping(
            {"ui": {"preview_info": {"summary_items": [], "mode": INFO_MODE_SUMMARY}}}
        ).ui.preview_info
        self.assertEqual(info.summary_items, [])

    def test_ui_state_copy_is_independent(self) -> None:
        original = UiState()
        duplicate = original.copy()
        duplicate.preview_info.summary_items.append("size")
        duplicate.preview_info.anchor_x = 0.9
        self.assertNotIn("size", original.preview_info.summary_items)
        self.assertEqual(original.preview_info.anchor_x, DEFAULT_INFO_ANCHOR_X)


def _display(model: VideoTableModel, col: int) -> str:
    value = model.data(model.index(0, col))
    return INFO_EMPTY_TEXT if value is None else str(value)


class PreviewInfoTableConsistencyTests(unittest.TestCase):
    """표 열과 같은 정보 표시 항목의 이름과 값이 표의 정의와 어긋나지 않음을 검증"""

    def setUp(self) -> None:
        self.config = ClassifierConfig()
        self.evaluations = [
            make_eval(),
            make_eval(
                make_video(
                    filename="rotated, clip.mp4", rotate_type=90, keyframe_interval=None, fps=29.97
                ),
                bitrate_ratio=0.4,
            ),
            make_eval(make_video(filename="hdr.mp4", width=3840, height=2160, is_hdr=True)),
        ]

    def _model(self, ev: Any) -> VideoTableModel:
        model = VideoTableModel(config=self.config)
        model.set_data_source([ev], root_dir="", config=self.config)
        return model

    def test_every_table_column_is_covered_by_an_info_item(self) -> None:
        """표에 열이 추가되면 정보 표시 항목도 함께 정해야 하도록 열 전체의 대응을 검증"""
        covered = set(TABLE_INFO_COLUMNS.values()) | set(RESOLUTION_INFO_COLUMNS)
        self.assertEqual(covered, set(COL_DEF_MAP))
        self.assertEqual(len(covered), len(TABLE_INFO_COLUMNS) + len(RESOLUTION_INFO_COLUMNS))

    def test_info_keys_match_table_column_ids(self) -> None:
        column_ids = {column.value for column in ColumnId}
        merged = {ColumnId.WIDTH.value, ColumnId.HEIGHT.value}
        self.assertEqual(set(TABLE_INFO_COLUMNS), column_ids - merged)
        table_backed = set(VIDEO_INFO_KEYS) | set(JUDGMENT_INFO_KEYS)
        self.assertEqual(table_backed, set(TABLE_INFO_COLUMNS) | {INFO_KEY_RESOLUTION})

    def test_labels_are_the_table_header_full_names(self) -> None:
        for key, col in TABLE_INFO_COLUMNS.items():
            with self.subTest(key=key):
                label = info_label(key, self.config)
                col_def = COL_DEF_MAP[col]
                expected = (
                    col_def.header_factory(self.config)
                    if col_def.header_factory is not None
                    else col_def.menu_title
                )
                self.assertEqual(label, expected)
                self.assertTrue(label.startswith(COLUMN_LABELS[ColumnId(key)].full))

    def test_values_equal_the_table_cell_text(self) -> None:
        for ev in self.evaluations:
            model = self._model(ev)
            video = PreviewVideoInfo(ev, self.config, model.pending_output_tiers, 1)
            for key, col in TABLE_INFO_COLUMNS.items():
                with self.subTest(file=ev.video.filename, key=key):
                    self.assertEqual(
                        info_value(key, video, _PLAYER_SNAPSHOT), _display(model, col)
                    )

    def test_resolution_joins_the_width_and_height_cells(self) -> None:
        for ev in self.evaluations:
            model = self._model(ev)
            video = PreviewVideoInfo(ev, self.config, model.pending_output_tiers, 1)
            width, height = (_display(model, col) for col in RESOLUTION_INFO_COLUMNS)
            self.assertEqual(
                info_value(INFO_KEY_RESOLUTION, video, _PLAYER_SNAPSHOT), f"{width}x{height}"
            )

    def test_pending_output_resolution_is_shown_like_the_table_does(self) -> None:
        ev = self.evaluations[0]
        model = self._model(ev)
        model.set_pending_output_tiers({ev.video.filename: RESOLUTION_TIER_BY_KEY["720p"]})
        video = PreviewVideoInfo(ev, self.config, model.pending_output_tiers, 1)
        column = TABLE_INFO_COLUMNS["output_res"]
        self.assertEqual(info_value("output_res", video, _PLAYER_SNAPSHOT), _display(model, column))
        self.assertEqual(_display(model, column), "720p")

    def test_short_labels_are_the_table_headers_as_shown(self) -> None:
        """영상 위 표시는 표에 보이는 헤더(약어)를 그대로 쓰고, 풀네임은 설정 화면 몫임을 검증"""
        for key, col in TABLE_INFO_COLUMNS.items():
            with self.subTest(key=key):
                col_def = COL_DEF_MAP[col]
                expected = (
                    col_def.header_factory(self.config)
                    if col_def.header_factory is not None
                    else col_def.header
                )
                self.assertEqual(info_label(key, self.config, short=True), expected)
                self.assertTrue(expected.startswith(COLUMN_LABELS[ColumnId(key)].short))
        self.assertEqual(info_label("bitrate", self.config, short=True), "BR")
        self.assertEqual(info_label("bitrate", self.config), "비트레이트")
        self.assertEqual(info_label("keyframe", self.config, short=True), "KF 간격")
        self.assertEqual(info_label("keyframe", self.config), "키프레임 간격")

    def test_labels_are_unique_and_player_items_do_not_reuse_table_names(self) -> None:
        for short in (False, True):
            labels = [info_label(key, self.config, short=short) for key in ALL_INFO_KEYS]
            self.assertEqual(len(set(labels)), len(labels), f"short={short}")

    def test_table_values_are_empty_markers_without_a_video(self) -> None:
        for key in set(VIDEO_INFO_KEYS) | set(JUDGMENT_INFO_KEYS):
            self.assertEqual(info_value(key, None, _PLAYER_SNAPSHOT), INFO_EMPTY_TEXT)


class PlayerInfoValueTests(unittest.TestCase):
    """재생기 항목의 값 서식과 값이 없을 때의 표기를 검증"""

    def test_player_values_use_compact_formats(self) -> None:
        values = {
            key: info_value(key, None, _PLAYER_SNAPSHOT) for key in PLAYER_INFO_KEYS
        }
        self.assertEqual(
            values,
            {
                "play_resolution": "320x240",
                "view_size": "960x540",
                "pixel_format": "NV12",
                "play_fps": "25.2",
                "position": "00:00.920 / 00:03.000",
                "frame_time": "0.960초",
            },
        )

    def test_missing_player_values_show_empty_marker(self) -> None:
        for key in ("play_resolution", "view_size", "pixel_format", "play_fps", "frame_time"):
            with self.subTest(key=key):
                self.assertEqual(info_value(key, None, _EMPTY_SNAPSHOT), INFO_EMPTY_TEXT)

    def test_clock_uses_hours_only_when_needed(self) -> None:
        self.assertEqual(format_clock_ms(0), "00:00.000")
        self.assertEqual(format_clock_ms(61_005), "01:01.005")
        self.assertEqual(format_clock_ms(3_723_400), "1:02:03.400")
        self.assertEqual(format_clock_ms(-5), "00:00.000")


class OverlayGeometryTests(GuiTestCase):
    """오버레이 위치 비율 환산, 문구 배치, 끌기 동작을 검증"""

    def test_anchor_and_position_are_inverse_inside_the_area(self) -> None:
        area, size = QRect(10, 10, 200, 100), QSize(50, 20)
        self.assertEqual(anchor_to_pos(area, size, 0.0, 0.0), QPoint(10, 10))
        self.assertEqual(anchor_to_pos(area, size, 1.0, 1.0), QPoint(160, 90))
        self.assertEqual(anchor_to_pos(area, size, 0.5, 1.0), QPoint(85, 90))
        self.assertEqual(pos_to_anchor(area, size, QPoint(85, 90)), (0.5, 1.0))

    def test_position_is_clamped_to_the_area(self) -> None:
        area, size = QRect(0, 0, 100, 100), QSize(40, 40)
        self.assertEqual(pos_to_anchor(area, size, QPoint(-30, 500)), (0.0, 1.0))

    def test_widget_larger_than_area_stays_at_the_top_left(self) -> None:
        area, size = QRect(5, 6, 30, 30), QSize(80, 80)
        self.assertEqual(anchor_to_pos(area, size, 0.7, 0.7), QPoint(5, 6))
        self.assertEqual(pos_to_anchor(area, size, QPoint(5, 6)), (0.0, 0.0))

    def test_html_layout_follows_single_or_multi_line_choice(self) -> None:
        rows = [("코덱", "h264"), ("FPS", "30")]
        single = build_info_html(rows, single_line=True, note="", muted_color="#888")
        multi = build_info_html(rows, single_line=False, note="선택 3개 중 재생 중", muted_color="#888")
        self.assertNotIn("<table", single)
        self.assertIn("코덱", single)
        self.assertIn("h264", single)
        self.assertIn("<table", multi)
        self.assertIn("선택 3개 중 재생 중", multi)

    def test_single_line_keeps_each_name_and_value_pair_together(self) -> None:
        """줄이 바뀌어도 이름과 값이 갈라지지 않도록 쌍 안의 공백은 줄바꿈 불가 공백임을 검증"""
        html_text = build_info_html(
            [("화질 등급", "과화질"), ("위치", "00:00.920 / 00:03.000")],
            single_line=True,
            note="",
            muted_color="#888",
        )
        self.assertIn("화질&nbsp;등급</span>&nbsp;과화질", html_text)
        self.assertIn("00:00.920&nbsp;/&nbsp;00:03.000", html_text)
        self.assertEqual(html_text.count(" / "), 1)

    def test_html_escapes_markup_in_values(self) -> None:
        html_text = build_info_html(
            [("이름", "<b>a&b</b>.mp4")], single_line=False, note="", muted_color="#888"
        )
        self.assertNotIn("<b>", html_text)
        self.assertIn("&lt;b&gt;a&amp;b&lt;/b&gt;.mp4", html_text)

    @staticmethod
    def _send_mouse(overlay: PreviewInfoOverlay, kind: QEvent.Type, global_pos: QPointF) -> None:
        """실제 입력처럼 커서 아래의 가장 안쪽 위젯에 이벤트를 보냄, 안쪽 라벨이 이벤트를 소비하면 끌기가 끊김"""
        local = global_pos - QPointF(overlay.pos())
        receiver = overlay.childAt(local.toPoint()) or overlay
        QApplication.sendEvent(
            receiver,
            QMouseEvent(
                kind,
                local,
                global_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    @staticmethod
    def _center(overlay: PreviewInfoOverlay) -> QPointF:
        return QPointF(overlay.width() // 2, overlay.height() // 2)

    @staticmethod
    def _edge_point(
        overlay: PreviewInfoOverlay,
        *,
        left: bool = False,
        right: bool = False,
        top: bool = False,
        bottom: bool = False,
    ) -> QPointF:
        """지정한 변의 가장자리 여백 안, 지정하지 않은 축은 가운데인 점의 전역 좌표"""
        x = 2 if left else overlay.width() - 2 if right else overlay.width() // 2
        y = 2 if top else overlay.height() - 2 if bottom else overlay.height() // 2
        return QPointF(overlay.pos()) + QPointF(x, y)

    def _overlay(self, area: QRect) -> PreviewInfoOverlay:
        host = QWidget()
        self.addCleanup(host.close)
        overlay = PreviewInfoOverlay(host, lambda: area)
        self.addCleanup(overlay.close)
        overlay.set_content([("코덱", "h264")], single_line=True, note="", anchor=(0.0, 0.0))
        overlay.show()
        return overlay

    def test_overlay_is_a_translucent_tool_window_that_never_takes_focus(self) -> None:
        """영상 표면이 같은 창의 위젯을 덮으므로 소유 창 위에 뜨는 투명 도구 창이며 포커스를 받지 않음을 검증"""
        overlay = self._overlay(QRect(0, 0, 400, 300))
        flags = overlay.windowFlags()
        self.assertTrue(overlay.isWindow())
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

    def test_text_label_lets_mouse_input_reach_the_window_that_handles_the_drag(self) -> None:
        overlay = self._overlay(QRect(0, 0, 400, 300))
        self.assertIsNone(overlay.childAt(QPoint(5, 5)))

    def test_single_line_stays_on_one_line_while_it_fits_and_wraps_only_when_it_does_not(self) -> None:
        rows = [("해상도", "3840x1920"), ("코덱", "h264"), ("FPS", "59.94"), ("BR", "18142")]
        wide = self._overlay(QRect(0, 0, 1200, 600))
        wide.set_content(rows, single_line=True, note="", anchor=(0.0, 0.0))
        narrow = self._overlay(QRect(0, 0, 160, 600))
        narrow.set_content(rows, single_line=True, note="", anchor=(0.0, 0.0))
        multi = self._overlay(QRect(0, 0, 1200, 600))
        multi.set_content(rows, single_line=False, note="", anchor=(0.0, 0.0))
        self.assertLess(wide.height(), narrow.height())
        self.assertLess(wide.height(), multi.height())
        self.assertLessEqual(narrow.width(), 160)

    def test_font_scale_changes_the_overlay_size(self) -> None:
        overlay = self._overlay(QRect(0, 0, 800, 600))
        rows = [("코덱", "h264"), ("FPS", "30")]
        overlay.set_content(rows, single_line=False, note="", anchor=(0.0, 0.0), scale=100)
        base = overlay.size()
        overlay.set_content(rows, single_line=False, note="", anchor=(0.0, 0.0), scale=200)
        self.assertGreater(overlay.width(), base.width())
        self.assertGreater(overlay.height(), base.height())
        overlay.set_content(rows, single_line=False, note="", anchor=(0.0, 0.0), scale=60)
        self.assertLess(overlay.width(), base.width())

    @staticmethod
    def _wheel(overlay: PreviewInfoOverlay, delta: int) -> None:
        QApplication.sendEvent(
            overlay,
            QWheelEvent(
                QPointF(5, 5),
                QPointF(5, 5),
                QPoint(0, 0),
                QPoint(0, delta),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            ),
        )

    def test_wheel_changes_scale_one_step_per_notch_within_limits(self) -> None:
        overlay = self._overlay(QRect(0, 0, 800, 600))
        seen: list[int] = []
        overlay.scale_changed.connect(seen.append)
        self._wheel(overlay, 120)
        self._wheel(overlay, -120)
        self.assertEqual(seen, [110, 90])
        content = [("코덱", "h264")]
        overlay.set_content(content, single_line=True, note="", anchor=(0.0, 0.0), scale=INFO_SCALE_MAX)
        self._wheel(overlay, 120)
        overlay.set_content(content, single_line=True, note="", anchor=(0.0, 0.0), scale=INFO_SCALE_MIN)
        self._wheel(overlay, -120)
        self.assertEqual(seen[2:], [INFO_SCALE_MAX, INFO_SCALE_MIN])

    def test_small_touchpad_wheel_deltas_accumulate_into_one_step(self) -> None:
        overlay = self._overlay(QRect(0, 0, 800, 600))
        seen: list[int] = []
        overlay.scale_changed.connect(seen.append)
        self._wheel(overlay, 60)
        self.assertEqual(seen, [])
        self._wheel(overlay, 60)
        self.assertEqual(seen, [110])

    def test_overlay_is_placed_at_global_area_coordinates(self) -> None:
        area = QRect(120, 80, 400, 300)
        overlay = self._overlay(area)
        self.assertEqual(overlay.pos(), QPoint(120, 80))

    def test_drag_moves_the_overlay_and_reports_the_released_ratio(self) -> None:
        area = QRect(100, 100, 400, 300)
        overlay = self._overlay(area)
        reported: list[tuple[float, float]] = []
        overlay.anchor_changed.connect(lambda x, y: reported.append((x, y)))
        start = overlay.pos()
        press = QPointF(start) + self._center(overlay)

        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, press + QPointF(100, 50))
        moved = overlay.pos()
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, press + QPointF(100, 50))

        self.assertEqual(moved, start + QPoint(100, 50))
        self.assertEqual(reported, [pos_to_anchor(area, overlay.size(), moved)])

    def test_drag_is_clamped_to_the_area(self) -> None:
        area = QRect(100, 100, 400, 300)
        overlay = self._overlay(area)
        press = QPointF(overlay.pos()) + self._center(overlay)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, QPointF(5000, 5000))
        self.assertEqual(
            overlay.pos(), QPoint(area.right() - overlay.width() + 1, area.bottom() - overlay.height() + 1)
        )

    def _record_box(self, overlay: PreviewInfoOverlay) -> tuple[list[tuple[float, float]], list[int]]:
        boxes: list[tuple[float, float]] = []
        scales: list[int] = []
        overlay.box_changed.connect(lambda w, h: boxes.append((w, h)))
        overlay.scale_changed.connect(scales.append)
        return boxes, scales

    def test_dragging_the_right_edge_changes_only_the_width_and_keeps_the_font(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        origin = overlay.pos()
        base = overlay.size()
        boxes, scales = self._record_box(overlay)
        press = self._edge_point(overlay, right=True)
        end = press + QPointF(200, 0)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, end)
        self.assertEqual(overlay.pos(), origin)
        self.assertEqual(overlay.width(), base.width() + 200)
        self.assertEqual(overlay.height(), base.height())
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, end)
        self.assertEqual(scales, [])
        self.assertEqual(len(boxes), 1)
        self.assertAlmostEqual(boxes[0][0], (base.width() + 200) / 600)
        self.assertAlmostEqual(boxes[0][1], base.height() / 400)

    def test_dragging_the_left_edge_keeps_the_right_edge_fixed(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        overlay.set_content([("코덱", "h264")], single_line=True, note="", anchor=(0.5, 0.5), scale=100)
        right = overlay.geometry().right()
        press = self._edge_point(overlay, left=True)
        end = press - QPointF(overlay.width(), 0)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, end)
        self.assertEqual(overlay.geometry().right(), right)
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, end)

    def test_dragging_the_corner_changes_width_and_height_together(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        base = overlay.size()
        boxes, scales = self._record_box(overlay)
        press = self._edge_point(overlay, right=True, bottom=True)
        end = press + QPointF(100, 60)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, end)
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, end)
        self.assertEqual(overlay.size(), QSize(base.width() + 100, base.height() + 60))
        self.assertEqual(scales, [])
        self.assertAlmostEqual(boxes[0][0], (base.width() + 100) / 600)
        self.assertAlmostEqual(boxes[0][1], (base.height() + 60) / 400)

    def test_box_width_is_limited_to_the_area_and_the_minimum_width(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        base = overlay.size()
        boxes, _scales = self._record_box(overlay)
        press = self._edge_point(overlay, right=True)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, press + QPointF(5000, 0))
        self.assertEqual(overlay.width(), 600)
        self._send_mouse(overlay, QEvent.Type.MouseMove, press - QPointF(5000, 0))
        self.assertEqual(overlay.width(), base.width())
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, press - QPointF(5000, 0))
        self.assertGreater(boxes[0][0], 0)

    def test_shrinking_height_stops_at_the_content_height(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        base = overlay.size()
        press = self._edge_point(overlay, bottom=True)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, press - QPointF(0, 5000))
        self.assertEqual(overlay.height(), base.height())
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, press - QPointF(0, 5000))

    def test_box_ratio_sets_the_size_and_the_text_wraps_inside_it(self) -> None:
        overlay = self._overlay(QRect(0, 0, 600, 400))
        overlay.set_content(
            [("해상도", "3840x1920"), ("코덱", "h264"), ("FPS", "59.94"), ("BR", "18142")],
            single_line=True,
            note="",
            anchor=(0.0, 0.0),
            box=(0.5, 0.5),
        )
        self.assertEqual(overlay.width(), 300)
        self.assertEqual(overlay.height(), 200)

    def test_content_update_keeps_the_dragged_box_while_resizing(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        press = self._edge_point(overlay, right=True)
        end = press + QPointF(100, 0)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, end)
        resized = overlay.size()
        overlay.set_content([("코덱", "h264")], single_line=True, note="", anchor=(0.0, 0.0), box=(0.0, 0.0))
        self.assertEqual(overlay.size(), resized)
        self._send_mouse(overlay, QEvent.Type.MouseButtonRelease, end)

    def test_cursor_follows_the_edge_under_the_mouse(self) -> None:
        overlay = self._overlay(QRect(100, 100, 600, 400))
        cases = [
            (self._edge_point(overlay, right=True), Qt.CursorShape.SizeHorCursor),
            (self._edge_point(overlay, right=True, bottom=True), Qt.CursorShape.SizeFDiagCursor),
            (self._edge_point(overlay, left=True, bottom=True), Qt.CursorShape.SizeBDiagCursor),
            (QPointF(overlay.pos()) + self._center(overlay), Qt.CursorShape.SizeAllCursor),
        ]
        for point, shape in cases:
            with self.subTest(shape=shape):
                local = point - QPointF(overlay.pos())
                QApplication.sendEvent(
                    overlay,
                    QMouseEvent(
                        QEvent.Type.MouseMove,
                        local,
                        point,
                        Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self.assertEqual(overlay.cursor().shape(), shape)

    def test_content_update_does_not_snap_back_while_dragging(self) -> None:
        overlay = self._overlay(QRect(100, 100, 400, 300))
        press = QPointF(overlay.pos()) + self._center(overlay)
        self._send_mouse(overlay, QEvent.Type.MouseButtonPress, press)
        self._send_mouse(overlay, QEvent.Type.MouseMove, press + QPointF(100, 50))
        dragged = overlay.pos()
        overlay.set_content([("코덱", "h264")], single_line=True, note="", anchor=(0.0, 0.0))
        self.assertEqual(overlay.pos(), dragged)


def _list_keys(order_list: QListWidget) -> list[str]:
    return order_keys(order_list)


class PreviewSettingsPanelTests(GuiTestCase):
    """설정 패널이 모든 미리 보기 설정을 UI 상태에 바로 쓰고 변경을 알리는지 검증"""

    def setUp(self) -> None:
        self.ui = UiState()
        self.panel = PreviewSettingsPanel(lambda: self.ui, ClassifierConfig)
        self.addCleanup(self.panel.close)
        self.changes: list[int] = []
        self.panel.changed.connect(lambda: self.changes.append(1))

    def _combo(self, index: int) -> QComboBox:
        return self.panel.findChildren(QComboBox)[index]

    def _select(self, combo: QComboBox, data: object) -> None:
        combo.setCurrentIndex(combo.findData(data))

    def test_panel_lists_every_info_item_for_both_levels(self) -> None:
        boxes = self.panel.findChildren(QCheckBox)
        self.assertEqual(len(boxes), len(ALL_INFO_KEYS) * 2)

    def test_backend_and_seek_changes_are_written_to_state(self) -> None:
        self._select(self._combo(0), PREVIEW_BACKEND_QUICKLOOK)
        self._select(self._combo(1), 30)
        self.assertEqual(self.ui.preview_backend, PREVIEW_BACKEND_QUICKLOOK)
        self.assertEqual(self.ui.preview_seek_seconds, 30)
        self.assertEqual(len(self.changes), 2)

    def test_mode_and_layout_changes_are_written_to_state(self) -> None:
        self._select(self._combo(2), INFO_MODE_DETAIL)
        self._select(self._combo(3), INFO_LAYOUT_SINGLE_LINE)
        self.assertEqual(self.ui.preview_info.mode, INFO_MODE_DETAIL)
        self.assertEqual(self.ui.preview_info.layout, INFO_LAYOUT_SINGLE_LINE)

    def _summary_box_by_key(self) -> dict[str, QCheckBox]:
        summary_boxes = self.panel.findChildren(QCheckBox)[0::2]
        keys = [key for _title, group in INFO_GROUPS for key in group]
        return dict(zip(keys, summary_boxes, strict=True))

    def test_item_checks_keep_the_existing_order_and_append_new_items(self) -> None:
        self.ui.preview_info.summary_items = ["zone", "name"]
        self.panel.refresh()
        by_key = self._summary_box_by_key()
        by_key["codec"].setChecked(True)
        self.assertEqual(self.ui.preview_info.summary_items, ["zone", "name", "codec"])
        by_key["zone"].setChecked(False)
        self.assertEqual(self.ui.preview_info.summary_items, ["name", "codec"])
        self.assertEqual(self.ui.preview_info.detail_items, list(DEFAULT_DETAIL_INFO_KEYS))

    def test_order_lists_show_the_checked_items_in_state_order(self) -> None:
        self.ui.preview_info.summary_items = ["zone", "codec"]
        self.panel.refresh()
        summary_list, detail_list = self.panel.findChildren(PreviewOrderList)
        self.assertEqual(_list_keys(summary_list), ["zone", "codec"])
        self.assertEqual(_list_keys(detail_list), list(DEFAULT_DETAIL_INFO_KEYS))

    def test_dropping_an_order_list_item_writes_the_new_order_to_state(self) -> None:
        self.ui.preview_info.summary_items = ["zone", "codec", "name"]
        self.panel.refresh()
        summary_list = self.panel.findChildren(PreviewOrderList)[0]
        moved = summary_list.takeItem(0)
        summary_list.insertItem(2, moved)
        summary_list.reordered.emit()
        self.assertEqual(self.ui.preview_info.summary_items, ["codec", "name", "zone"])
        self.assertEqual(len(self.changes), 1)

    def test_panel_uses_full_names_and_scale_slider_writes_state(self) -> None:
        names = {label.text() for label in self.panel.findChildren(QLabel)}
        self.assertIn("비트레이트", names)
        self.assertIn("키프레임 간격", names)
        self.assertNotIn("BR", names)
        slider = self.panel.findChildren(QSlider)[0]
        slider.setValue(15)
        self.assertEqual(self.ui.preview_info.scale, 150)
        self.assertIn("150%", {label.text() for label in self.panel.findChildren(QLabel)})
        self.assertEqual(len(self.changes), 1)

    def test_reset_position_restores_default_anchor(self) -> None:
        self.ui.preview_info.anchor_x = 0.8
        self.ui.preview_info.anchor_y = 0.6
        self.ui.preview_info.box_width = 0.4
        self.ui.preview_info.box_height = 0.3
        self.ui.preview_info.scale = 150
        buttons = [b for b in self.panel.findChildren(QToolButton) if b.accessibleName() == "위치와 크기 초기화"]
        self.assertEqual(len(buttons), 1)
        self.assertFalse(buttons[0].text())
        buttons[0].click()
        info = self.ui.preview_info
        self.assertEqual(info.anchor_x, DEFAULT_INFO_ANCHOR_X)
        self.assertEqual(info.anchor_y, DEFAULT_INFO_ANCHOR_Y)
        self.assertEqual(info.box_width, DEFAULT_INFO_BOX_WIDTH)
        self.assertEqual(info.box_height, DEFAULT_INFO_BOX_HEIGHT)
        self.assertEqual(info.scale, DEFAULT_INFO_SCALE)
        self.assertIn("100%", {label.text() for label in self.panel.findChildren(QLabel)})

    def test_refresh_reflects_external_state_without_emitting_changes(self) -> None:
        self.ui.preview_info.mode = INFO_MODE_SUMMARY
        self.ui.preview_seek_seconds = 10
        self.panel.refresh()
        self.assertEqual(self._combo(2).currentData(), INFO_MODE_SUMMARY)
        self.assertEqual(self._combo(1).currentData(), 10)
        self.assertEqual(self.changes, [])


class OverlayValueColorTests(unittest.TestCase):
    """영상 위 값 색이 표 셀의 전경색, 배지 글자색과 같고 흐린 기본색은 기본 글자색으로 두는지 검증"""

    def test_html_colors_only_the_values_that_have_a_state_color(self) -> None:
        colored = build_info_html(
            [("화질 등급", "과화질"), ("코덱", "h264")],
            single_line=False,
            note="",
            muted_color="#888",
            value_colors=["#FF0000", None],
        )
        self.assertIn('<span style="color:#FF0000">과화질</span>', colored)
        self.assertEqual(colored.count("<span"), 1)
        plain = build_info_html(
            [("코덱", "h264")], single_line=True, note="", muted_color="#888", value_colors=[None]
        )
        self.assertEqual(plain.count("<span"), 1)

    def test_zone_and_status_colors_are_the_table_badge_text_colors(self) -> None:
        info = PreviewVideoInfo(make_eval(), ClassifierConfig(), {}, 1)
        badge = COL_DEF_MAP[VideoTableModel.COL_ZONE].badge_getter
        assert badge is not None
        expected = badge(info.ev, DARK_PALETTE)[1]
        self.assertEqual(info_value_color("zone", info), expected)
        self.assertEqual(info_value_color("zone", None), None)

    def test_keyframe_color_follows_the_table_and_unmeasured_values_use_the_default_color(self) -> None:
        exceeded = PreviewVideoInfo(
            make_eval(make_video(keyframe_interval=9.0), is_keyframe_exceeded=True), ClassifierConfig(), {}, 1
        )
        self.assertEqual(info_value_color("keyframe", exceeded), DARK_PALETTE.fg_kf_exceeded)
        unmeasured = PreviewVideoInfo(make_eval(make_video(keyframe_interval=None)), ClassifierConfig(), {}, 1)
        self.assertIsNone(info_value_color("keyframe", unmeasured))

    def test_values_without_a_table_color_use_the_default_color(self) -> None:
        info = PreviewVideoInfo(make_eval(), ClassifierConfig(), {}, 1)
        self.assertIsNone(info_value_color("name", info))
        self.assertEqual(info_value_colors(["name", "zone"], info)[0], None)
        self.assertEqual(len(info_value_colors(["name", "zone"], info)), 2)


if __name__ == "__main__":
    unittest.main()
