"""재인코딩 시작 확인 대화상자의 태그 안내, 집계 목록, 원본 교체 경고, 버튼 결과와 테마 대비 검증"""

import dataclasses
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QTreeWidgetItem

from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_REPLACE, ClassifierConfig
from src.core.encode.encode_impact import (
    IMPACT_BITMAP_SUB_MKV,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    IMPACT_LABELS,
    EncodeImpactSummary,
    ImpactBucket,
)
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.gui.dialogs.encode_start_dialog import EncodeStartDialog
from src.gui.theme import DARK_PALETTE, LIGHT_PALETTE
from src.service.encode.encode_job import EncodeSettings
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
_BITMAP_TOOLTIP: str = "MKV로 출력하면 탐색기 태그는 옮기지만 별점은 옮기지 않습니다."


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


def _summary(total_count: int, filenames: dict[str, tuple[str, ...]]) -> EncodeImpactSummary:
    """지정한 항목만 영상이 있는 집계 결과 생성"""
    buckets = tuple(
        ImpactBucket(key=key, label=label, filenames=filenames.get(key, ())) for key, label in IMPACT_LABELS
    )
    return EncodeImpactSummary(
        total_count=total_count,
        analyzed_count=total_count,
        unknown_filenames=(),
        buckets=buckets,
    )


def _settings(*, result_policy: str = RESULT_POLICY_ARCHIVE, tag_mode: str = ENCODE_TAG_MODE_VISIBLE) -> EncodeSettings:
    """기본 설정 사본에서 확인 창과 관련된 두 값만 바꾼 설정"""
    base = EncodeSettings.from_config(ClassifierConfig())
    return dataclasses.replace(base, result_policy=result_policy, encode_tag_mode=tag_mode)


class EncodeStartDialogTests(GuiTestCase):
    """확인 창의 문구 분기, 집계 목록 구성, 버튼 결과와 테마별 대비를 검증"""

    def test_tag_notice_follows_the_tag_mode(self) -> None:
        """태그 기록 방식 3가지마다 승인된 안내 문장이 나오는지 검증"""
        expected = {
            ENCODE_TAG_MODE_VISIBLE: _TAG_NOTICE_VISIBLE,
            ENCODE_TAG_MODE_HIDDEN: _TAG_NOTICE_HIDDEN,
            ENCODE_TAG_MODE_OFF: _TAG_NOTICE_OFF,
        }
        for mode, text in expected.items():
            with self.subTest(mode=mode):
                dialog = EncodeStartDialog(_summary(0, {}), _settings(tag_mode=mode))
                self.assertEqual(dialog.lbl_tag.text(), text)

    def test_replace_policy_shows_the_irreversible_warning_with_count(self) -> None:
        """원본 교체일 때만 경고를 보이고 원본 개수를 넣는지 검증"""
        replace = EncodeStartDialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_REPLACE))
        archive = EncodeStartDialog(_summary(2, {}), _settings(result_policy=RESULT_POLICY_ARCHIVE))

        self.assertFalse(replace.lbl_warning.isHidden())
        self.assertEqual(replace.lbl_warning.text(), _WARNING_TEXT)
        self.assertTrue(archive.lbl_warning.isHidden())

    def test_zero_count_items_are_hidden_and_files_sit_under_their_label(self) -> None:
        """0개 항목은 목록에서 빠지고, 남은 항목 아래에 영상 이름이 나열되는지 검증"""
        summary = _summary(2, {IMPACT_EXPLORER_TAGS_PRESERVED: ("a.mp4", "sub/b.mov")})

        dialog = EncodeStartDialog(summary, _settings())

        self.assertEqual(dialog.tree_items.topLevelItemCount(), 1)
        parent = dialog.tree_items.topLevelItem(0)
        assert parent is not None
        self.assertEqual(parent.text(0), "탐색기 태그와 별점 보존 대상 (2개)")
        children = [parent.child(i) for i in range(parent.childCount())]
        self.assertEqual([child.text(0) for child in children if child is not None], ["a.mp4", "sub/b.mov"])
        self.assertFalse(dialog.lbl_section.isHidden())

    def test_no_items_hides_the_section(self) -> None:
        """집계 항목이 하나도 없으면 제목과 목록을 모두 숨기는지 검증"""
        dialog = EncodeStartDialog(_summary(0, {}), _settings())

        self.assertTrue(dialog.lbl_section.isHidden())
        self.assertTrue(dialog.tree_items.isHidden())
        self.assertEqual(dialog.tree_items.topLevelItemCount(), 0)

    def test_bitmap_item_tooltip_says_star_rating_is_not_kept(self) -> None:
        """비트맵 자막 항목 툴팁에 별점 비보존 안내가 있는지 검증"""
        summary = _summary(1, {IMPACT_BITMAP_SUB_MKV: ("bitmap.wmv",)})

        dialog = EncodeStartDialog(summary, _settings())

        items = [dialog.tree_items.topLevelItem(i) for i in range(dialog.tree_items.topLevelItemCount())]
        bitmap_items = [item for item in items if isinstance(item, QTreeWidgetItem) and "비트맵" in item.text(0)]
        self.assertEqual(len(bitmap_items), 1)
        self.assertEqual(bitmap_items[0].toolTip(0), _BITMAP_TOOLTIP)

    def test_start_button_accepts_and_cancel_button_rejects(self) -> None:
        """시작은 수락, 취소는 거절로 끝나며 두 아이콘 버튼 모두 접근성 이름을 가지는지 검증"""
        start = EncodeStartDialog(_summary(0, {}), _settings())
        cancel = EncodeStartDialog(_summary(0, {}), _settings())
        self.assertEqual(start.btn_start.accessibleName(), "재인코딩 시작")
        self.assertEqual(cancel.btn_cancel.accessibleName(), "취소")
        self.assertTrue(start.btn_start.toolTip())
        self.assertTrue(cancel.btn_cancel.toolTip())

        start.btn_start.click()
        cancel.btn_cancel.click()

        self.assertEqual(start.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(cancel.result(), int(QDialog.DialogCode.Rejected))

    def test_warning_text_keeps_contrast_in_both_themes(self) -> None:
        """다크와 라이트 팔레트 모두에서 경고 글자색이 대비 기준을 넘고 스타일시트에 팔레트 토큰으로 반영되는지 검증"""
        dialog = EncodeStartDialog(_summary(1, {}), _settings(result_policy=RESULT_POLICY_REPLACE))
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                dialog.apply_theme(palette)
                self.assertIn(palette.fg_maxrate_warn, dialog.styleSheet())
                self.assertGreaterEqual(_contrast(palette.fg_maxrate_warn, palette.bg_card), _MIN_TEXT_CONTRAST)


if __name__ == "__main__":
    unittest.main(verbosity=2)
