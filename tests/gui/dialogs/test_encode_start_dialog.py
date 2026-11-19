"""재인코딩 시작 확인 대화상자의 태그 안내, 집계 목록과 툴팁, 영상 행 동작, 원본 교체 경고와 재확인, 테마 대비 검증"""

import dataclasses
import os
import tempfile
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QStyle, QStyleOptionViewItem, QTreeWidgetItem, QWidget

from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_REPLACE, ClassifierConfig
from src.core.encode.encode_impact import (
    IMPACT_BITMAP_SUB_MKV,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    IMPACT_LABELS,
    EncodeImpactSummary,
    ImpactBucket,
)
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.gui.dialogs import encode_start_dialog as dialog_module
from src.gui.dialogs.encode_start_dialog import _IMPACT_TOOLTIPS, EncodeStartDialog, _ImpactRowDelegate
from src.gui.theme import DARK_PALETTE, LIGHT_PALETTE
from src.service.encode.encode_job import EncodeSettings
from tests._factories import make_eval, make_video
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])
_MIN_TEXT_CONTRAST: float = 4.5
_SRGB_LINEAR_THRESHOLD: float = 0.03928
_SRGB_LINEAR_DIVISOR: float = 12.92
_SRGB_GAMMA_OFFSET: float = 0.055
_SRGB_GAMMA_SCALE: float = 1.055
_SRGB_GAMMA: float = 2.4
_CONTRAST_OFFSET: float = 0.05
_TAG_NOTICE_VISIBLE: str = "결과에 재인코딩 태그를 기록합니다. 설정에서 바꿀 수 있습니다."
_TAG_NOTICE_HIDDEN: str = "결과에 숨은 기록만 남깁니다. 탐색기 태그 칸에는 보이지 않습니다. 설정에서 바꿀 수 있습니다."
_TAG_NOTICE_OFF: str = "결과에 재인코딩 태그를 기록하지 않습니다. 이미 재인코딩한 영상은 재인코딩 이력으로만 가려냅니다. 설정에서 바꿀 수 있습니다."
_WARNING_TEXT: str = "원본 2개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다."
_REPLACE_CONFIRM_TEXT: str = "원본 2개를 결과 파일로 바꿉니다. 원본은 보관되지 않으며 되돌릴 수 없습니다. 그래도 시작하시겠습니까?"
_BITMAP_TOOLTIP: str = "MKV로 출력하면 탐색기 태그는 옮기지만 별점은 옮기지 않습니다."
_ROOT_DIR: str = "C:/work"
_TARGET_LABEL: str = "HEVC"


def _relative_luminance(hex_color: str) -> float:
    """sRGB 16진수 색의 상대 휘도(WCAG 기준)"""
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [_linearize(c) for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _linearize(channel: float) -> float:
    """sRGB 채널 값을 선형 값으로 변환"""
    if channel <= _SRGB_LINEAR_THRESHOLD:
        return channel / _SRGB_LINEAR_DIVISOR
    return ((channel + _SRGB_GAMMA_OFFSET) / _SRGB_GAMMA_SCALE) ** _SRGB_GAMMA


def _contrast(first: str, second: str) -> float:
    """두 색의 대비 비율"""
    lum_a, lum_b = _relative_luminance(first), _relative_luminance(second)
    hi, lo = max(lum_a, lum_b), min(lum_a, lum_b)
    return (hi + _CONTRAST_OFFSET) / (lo + _CONTRAST_OFFSET)


def _summary(
    total_count: int,
    filenames: dict[str, tuple[str, ...]],
    unknown: tuple[str, ...] = (),
) -> EncodeImpactSummary:
    """지정한 항목만 영상이 있는 집계 결과 생성"""
    buckets = tuple(
        ImpactBucket(key=key, label=label, filenames=filenames.get(key, ())) for key, label in IMPACT_LABELS
    )
    return EncodeImpactSummary(
        total_count=total_count,
        analyzed_count=total_count - len(unknown),
        unknown_filenames=unknown,
        buckets=buckets,
    )


def _settings(*, result_policy: str = RESULT_POLICY_ARCHIVE, tag_mode: str = ENCODE_TAG_MODE_VISIBLE) -> EncodeSettings:
    """기본 설정 사본에서 확인 창과 관련된 두 값만 바꾼 설정"""
    base = EncodeSettings.from_config(ClassifierConfig())
    return dataclasses.replace(base, result_policy=result_policy, encode_tag_mode=tag_mode)


def _dialog(
    summary: EncodeImpactSummary,
    settings: EncodeSettings | None = None,
    filenames: tuple[str, ...] = (),
    root_dir: str = _ROOT_DIR,
    preview: Callable[[str], None] | None = None,
) -> EncodeStartDialog:
    """테스트용 확인 창. 영상 행 동작에 필요한 평가 결과는 파일 이름 목록으로 만듦"""
    evaluations = [make_eval(make_video(filename=name)) for name in filenames]
    return EncodeStartDialog(
        summary,
        settings or _settings(),
        evaluations,
        root_dir,
        _TARGET_LABEL,
        preview or (lambda _path: None),
    )


def _top_items(dialog: EncodeStartDialog) -> list[QTreeWidgetItem]:
    """집계 트리의 최상위 항목 목록"""
    items = [dialog.tree_items.topLevelItem(i) for i in range(dialog.tree_items.topLevelItemCount())]
    return [item for item in items if item is not None]


def _space_event() -> QKeyEvent:
    """스페이스바 누름 이벤트"""
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)


def _left_click(widget: QWidget, pos: QPoint) -> None:
    """위젯의 지정 위치에 왼쪽 버튼 누름과 놓음 이벤트를 차례로 보냄"""
    local = QPointF(pos)
    global_pos = QPointF(widget.mapToGlobal(pos))
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, local, global_pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, local, global_pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, press)
    QApplication.sendEvent(widget, release)


def _child_item(parent: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    """부모 항목의 자식 항목(없으면 테스트 실패)"""
    child = parent.child(index)
    assert child is not None
    return child


class EncodeStartDialogTests(GuiTestCase):
    """확인 창의 문구 분기, 집계 목록 구성, 툴팁, 영상 행 동작, 버튼 결과, 원본 교체 재확인과 테마별 대비를 검증"""

    def test_tag_notice_follows_the_tag_mode(self) -> None:
        """태그 기록 방식 3가지마다 승인된 안내 문장이 나오는지 검증"""
        expected = {
            ENCODE_TAG_MODE_VISIBLE: _TAG_NOTICE_VISIBLE,
            ENCODE_TAG_MODE_HIDDEN: _TAG_NOTICE_HIDDEN,
            ENCODE_TAG_MODE_OFF: _TAG_NOTICE_OFF,
        }
        for mode, text in expected.items():
            with self.subTest(mode=mode):
                dialog = _dialog(_summary(0, {}), _settings(tag_mode=mode))
                self.assertEqual(dialog.lbl_tag.text(), text)

    def test_tag_tooltip_explains_terms_and_the_current_tag_name(self) -> None:
        """태그 안내 툴팁 첫 줄이 방식별 남는 기록을 말하고, 네 용어를 풀이하며 현재 태그 이름을 넣는지 검증"""
        expected_first_line = {
            ENCODE_TAG_MODE_VISIBLE: "이 방식에서는 탐색기 태그와 숨은 기록, 재인코딩 이력이 남습니다.",
            ENCODE_TAG_MODE_HIDDEN: "이 방식에서는 숨은 기록과 재인코딩 이력이 남습니다. 탐색기 태그는 남지 않습니다.",
            ENCODE_TAG_MODE_OFF: "이 방식에서는 재인코딩 이력만 남습니다. 탐색기 태그와 숨은 기록은 남지 않습니다.",
        }
        for mode, first_line in expected_first_line.items():
            with self.subTest(mode=mode):
                settings = dataclasses.replace(_settings(tag_mode=mode), encode_tag_name="sample_tag")
                dialog = _dialog(_summary(0, {}), settings)
                tooltip = dialog.lbl_tag.toolTip()
                self.assertEqual(tooltip.split("\n")[0], first_line)
                for term in ("재인코딩 태그:", "탐색기 태그:", "숨은 기록:", "재인코딩 이력:"):
                    self.assertIn(term, tooltip)
                self.assertIn("현재 이름은 sample_tag입니다.", tooltip)

    def test_replace_policy_shows_the_irreversible_warning_with_count(self) -> None:
        """원본 교체일 때만 경고 상자를 보이고 원본 개수를 넣는지 검증"""
        replace = _dialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_REPLACE))
        archive = _dialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_ARCHIVE))

        self.assertFalse(replace.warning_box.isHidden())
        self.assertEqual(replace.lbl_warning.text(), _WARNING_TEXT)
        self.assertTrue(archive.warning_box.isHidden())

    def test_zero_count_items_are_hidden_and_files_sit_under_their_label(self) -> None:
        """0개 항목은 목록에서 빠지고, 남은 항목 아래에 영상 이름이 나열되는지 검증"""
        summary = _summary(2, {IMPACT_EXPLORER_TAGS_PRESERVED: ("a.mp4", "sub/b.mov")})

        dialog = _dialog(summary, filenames=("a.mp4", "sub/b.mov"))

        self.assertEqual(dialog.tree_items.topLevelItemCount(), 1)
        parent = _top_items(dialog)[0]
        self.assertEqual(parent.text(0), "탐색기 태그와 별점 보존 대상 (2개)")
        children = [_child_item(parent, i) for i in range(parent.childCount())]
        self.assertEqual([child.text(0) for child in children], ["a.mp4", "sub/b.mov"])
        self.assertTrue(children[0].toolTip(0).startswith("파일명: a.mp4"))
        self.assertFalse(dialog.lbl_section.isHidden())

    def test_no_items_hides_the_section(self) -> None:
        """집계 항목이 하나도 없으면 제목과 목록을 모두 숨기는지 검증"""
        dialog = _dialog(_summary(0, {}))

        self.assertTrue(dialog.lbl_section.isHidden())
        self.assertTrue(dialog.tree_items.isHidden())
        self.assertEqual(dialog.tree_items.topLevelItemCount(), 0)

    def test_every_impact_item_has_a_specific_tooltip(self) -> None:
        """집계 항목 10개마다 툴팁이 있고, 비트맵 자막 툴팁은 이유와 승인된 별점 안내를 함께 담는지 검증"""
        self.assertEqual(set(_IMPACT_TOOLTIPS), {key for key, _label in IMPACT_LABELS})
        for key, tooltip in _IMPACT_TOOLTIPS.items():
            with self.subTest(key=key):
                self.assertGreater(len(tooltip), 10)
        self.assertIn("MP4에 저장할 수 없어 MKV로 출력합니다.", _IMPACT_TOOLTIPS[IMPACT_BITMAP_SUB_MKV])
        self.assertTrue(_IMPACT_TOOLTIPS[IMPACT_BITMAP_SUB_MKV].endswith(_BITMAP_TOOLTIP))

    def test_bitmap_item_tooltip_says_star_rating_is_not_kept(self) -> None:
        """비트맵 자막 항목 툴팁에 MKV 출력 이유와 별점 비보존 안내가 있는지 검증"""
        summary = _summary(1, {IMPACT_BITMAP_SUB_MKV: ("bitmap.wmv",)})

        dialog = _dialog(summary)

        bitmap_items = [item for item in _top_items(dialog) if "비트맵" in item.text(0)]
        self.assertEqual(len(bitmap_items), 1)
        self.assertIn(_BITMAP_TOOLTIP, bitmap_items[0].toolTip(0))

    def test_unknown_stream_summary_is_listed_as_unknown_information(self) -> None:
        """스트림 요약이 없는 영상은 정보 없음 항목으로 묶고 그 툴팁을 달며, 0개면 숨기는지 검증"""
        dialog = _dialog(_summary(1, {}, unknown=("broken.mp4",)), filenames=("broken.mp4",))

        top = _top_items(dialog)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0].text(0), "정보 없음 (1개)")
        self.assertEqual(top[0].toolTip(0), "분석 정보가 없어 집계하지 못했습니다.")
        self.assertEqual(_child_item(top[0], 0).text(0), "broken.mp4")
        self.assertFalse(dialog.lbl_section.isHidden())

    def test_start_button_accepts_and_cancel_button_rejects(self) -> None:
        """시작은 수락, 취소는 거절로 끝나며 두 아이콘 버튼 모두 접근성 이름과 툴팁을 가지는지 검증"""
        start = _dialog(_summary(0, {}))
        cancel = _dialog(_summary(0, {}))
        self.assertEqual(start.btn_start.accessibleName(), "재인코딩 시작")
        self.assertEqual(cancel.btn_cancel.accessibleName(), "취소")
        self.assertEqual(start.btn_start.toolTip(), "재인코딩을 시작합니다.")
        self.assertEqual(cancel.btn_cancel.toolTip(), "재인코딩을 시작하지 않고 닫습니다.")

        start.btn_start.click()
        cancel.btn_cancel.click()

        self.assertEqual(start.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(cancel.result(), int(QDialog.DialogCode.Rejected))

    def test_replace_start_asks_again_with_cancel_as_default(self) -> None:
        """원본 교체 배치는 시작 때 확인을 한 번 더 묻고, 기본 선택이 취소이며, 아니요면 창이 열린 채 남는지 검증"""
        dialog = _dialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_REPLACE))

        with mock.patch.object(
            dialog_module.QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ) as question:
            dialog.btn_start.click()

        self.assertEqual(question.call_args.args[2], _REPLACE_CONFIRM_TEXT)
        self.assertEqual(question.call_args.args[4], QMessageBox.StandardButton.No)
        self.assertEqual(dialog.result(), 0)

        with mock.patch.object(dialog_module.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.btn_start.click()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))

    def test_archive_start_does_not_ask_again(self) -> None:
        """결과 파일 보관 방식은 시작 때 추가 확인을 묻지 않는지 검증"""
        dialog = _dialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_ARCHIVE))

        with mock.patch.object(dialog_module.QMessageBox, "question") as question:
            dialog.btn_start.click()

        question.assert_not_called()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))

    def test_space_key_requests_preview_of_the_current_video(self) -> None:
        """스페이스바가 집계 트리의 현재 영상 절대 경로를 미리 보기 콜백에 넘기는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, "clip.mp4"), "wb").close()
            requested: list[str] = []
            dialog = _dialog(
                _summary(1, {IMPACT_EXPLORER_TAGS_PRESERVED: ("clip.mp4",)}),
                root_dir=root,
                filenames=("clip.mp4",),
                preview=requested.append,
            )
            parent = _top_items(dialog)[0]
            dialog.tree_items.setCurrentItem(_child_item(parent, 0))
            dialog.tree_items.keyPressEvent(_space_event())

        self.assertEqual(requested, [os.path.join(root, "clip.mp4")])

    def test_space_key_on_missing_file_shows_notice_instead_of_preview(self) -> None:
        """영상 파일이 없으면 미리 보기를 요청하지 않고 메인 표와 같은 안내를 내는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            requested: list[str] = []
            dialog = _dialog(
                _summary(1, {IMPACT_EXPLORER_TAGS_PRESERVED: ("gone.mp4",)}),
                root_dir=root,
                filenames=("gone.mp4",),
                preview=requested.append,
            )
            dialog.tree_items.setCurrentItem(_child_item(_top_items(dialog)[0], 0))

            with mock.patch.object(dialog_module.QMessageBox, "warning") as warning:
                dialog.tree_items.keyPressEvent(_space_event())

        self.assertEqual(requested, [])
        warning.assert_called_once()

    def test_double_click_plays_the_video_or_warns_when_missing(self) -> None:
        """영상 행 더블클릭은 기본 플레이어 재생을 호출하고, 실행하지 못하면 안내하는지 검증"""
        dialog = _dialog(
            _summary(1, {IMPACT_EXPLORER_TAGS_PRESERVED: ("clip.mp4",)}),
            filenames=("clip.mp4",),
        )
        child = _child_item(_top_items(dialog)[0], 0)

        with mock.patch.object(dialog_module, "launch_video_file", return_value=True) as launch:
            dialog.tree_items.itemDoubleClicked.emit(child, 0)
        launch.assert_called_once()

        with (
            mock.patch.object(dialog_module, "launch_video_file", return_value=False),
            mock.patch.object(dialog_module.QMessageBox, "warning") as warning,
        ):
            dialog.tree_items.itemDoubleClicked.emit(child, 0)
        warning.assert_called_once()

    def test_warning_keeps_contrast_and_start_button_is_toned_down_in_both_themes(self) -> None:
        """다크와 라이트 팔레트 모두에서 경고 글자, 시작 버튼 글자가 대비 기준을 넘고, 스타일시트에 팔레트 토큰이 반영되는지 검증"""
        dialog = _dialog(_summary(1, {}), _settings(result_policy=RESULT_POLICY_REPLACE))
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                dialog.apply_theme(palette)
                style = dialog.styleSheet()
                self.assertIn(palette.fg_maxrate_warn, style)
                self.assertIn(palette.action_primary_border, style)
                self.assertGreaterEqual(_contrast(palette.fg_maxrate_warn, palette.bg_card), _MIN_TEXT_CONTRAST)
                self.assertGreaterEqual(_contrast(palette.text_primary, palette.bg_elevated), _MIN_TEXT_CONTRAST)
                self.assertFalse(dialog.lbl_warning_icon.pixmap().isNull())


    def test_hover_and_selection_colors_keep_contrast_in_both_themes(self) -> None:
        """시작 버튼 호버와 집계 목록 선택 색이 팔레트 토큰으로 적용되고, 양쪽 테마에서 글자 대비 기준을 넘는지 검증"""
        dialog = _dialog(_summary(1, {}), _settings())
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                dialog.apply_theme(palette)
                self.assertIn("EncodeImpactTree::item:selected:!active", dialog.styleSheet())
                self.assertIn(f"background-color: {palette.bg_selected}", dialog.styleSheet())
                self.assertGreaterEqual(_contrast(palette.text_primary, palette.bg_selected), _MIN_TEXT_CONTRAST)

    def test_single_click_toggles_a_group_once_and_arrow_click_does_not_toggle_twice(self) -> None:
        """항목 줄 한 번 클릭은 펼침을 한 번만 바꾸고, 화살표 클릭도 기본 동작 한 번으로 끝나는지 검증"""
        summary = _summary(1, {IMPACT_EXPLORER_TAGS_PRESERVED: ("a.mp4",)})
        dialog = _dialog(summary, filenames=("a.mp4",))
        dialog.show()
        self.addCleanup(dialog.close)
        tree = dialog.tree_items
        group = _top_items(dialog)[0]
        self.assertEqual(tree.selectionMode().name, "SingleSelection")
        viewport = tree.viewport()
        assert viewport is not None
        center = tree.visualItemRect(group).center()

        _left_click(viewport, center)
        self.assertTrue(group.isExpanded())
        _left_click(viewport, center)
        self.assertFalse(group.isExpanded())

        arrow = QPoint(tree.indentation() // 2, center.y())
        _left_click(viewport, arrow)
        self.assertTrue(group.isExpanded())
        _left_click(viewport, arrow)
        self.assertFalse(group.isExpanded())

    def test_group_row_draws_no_selection_or_focus_while_video_row_keeps_them(self) -> None:
        """항목 줄은 선택 배경과 포커스 테두리를 그리지 않고, 영상 줄은 선택과 포커스 상태를 그대로 쓰는지 검증"""
        summary = _summary(1, {IMPACT_EXPLORER_TAGS_PRESERVED: ("a.mp4",)})
        dialog = _dialog(summary, filenames=("a.mp4",))
        tree = dialog.tree_items
        group = _top_items(dialog)[0]
        group.setExpanded(True)
        video = _child_item(group, 0)
        delegate = tree.itemDelegate()
        self.assertIsInstance(delegate, _ImpactRowDelegate)
        assert isinstance(delegate, _ImpactRowDelegate)
        active = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus

        group_option = QStyleOptionViewItem()
        group_option.state = active
        delegate.initStyleOption(group_option, tree.indexFromItem(group))
        self.assertFalse(group_option.state & QStyle.StateFlag.State_Selected)
        self.assertFalse(group_option.state & QStyle.StateFlag.State_HasFocus)

        video_option = QStyleOptionViewItem()
        video_option.state = active
        delegate.initStyleOption(video_option, tree.indexFromItem(video))
        self.assertTrue(video_option.state & QStyle.StateFlag.State_Selected)
        self.assertTrue(video_option.state & QStyle.StateFlag.State_HasFocus)


if __name__ == "__main__":
    unittest.main(verbosity=2)
