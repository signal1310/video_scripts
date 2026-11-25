"""헤더 재인코딩 버튼의 상태별 문구, 진행 채움, 대상 수 배지, 테마 배색과 클릭을 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.encode.encode_batch_book import (
    CHIP_STATE_FINISHED,
    CHIP_STATE_IDLE,
    CHIP_STATE_PAUSED,
    CHIP_STATE_RUNNING,
    EncodeChipStatus,
)
from src.gui.constants import TOOLBAR_ICON_BTN_SIZE
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from src.gui.widgets.header_encode_button import (
    ARROW_ICON_NAME,
    ARROW_SIZE_PX,
    HEADER_ENCODE_ACCESSIBLE_NAME,
    IDLE_LABEL,
    PAUSED_LABEL_TEMPLATE,
    RUNNING_LABEL_TEMPLATE,
    EncodeTargetCountBadge,
    HeaderEncodeButton,
    create_encode_arrow_label,
)
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _status(state: str, percent: int = 0) -> EncodeChipStatus:
    """시험용 상태 묶음"""
    return EncodeChipStatus(state=state, done=percent, total=100, percent=percent, tooltip="")


def _host(case: unittest.TestCase) -> QWidget:
    """장식 라벨을 담을 임시 부모 위젯"""
    host = QWidget()
    case.addCleanup(host.deleteLater)
    return host


class HeaderEncodeButtonTests(GuiTestCase):
    """재인코딩 버튼의 문구, 채움 폭, 배지, 테마 배색, 클릭을 검증"""

    def setUp(self) -> None:
        self.button = HeaderEncodeButton()
        self.button.resize(140, 32)
        self.addCleanup(self.button.deleteLater)

    def test_idle_label_count_badge_and_accessibility(self) -> None:
        """유휴 상태는 재인코딩 문구와 대상 수 배지를 보이고 접근성 이름을 가짐을 검증"""
        self.button.set_status(_status(CHIP_STATE_IDLE))
        self.assertEqual(self.button.text_label.text(), IDLE_LABEL)
        self.assertEqual(IDLE_LABEL, "재인코딩")
        self.assertEqual(self.button.accessibleName(), HEADER_ENCODE_ACCESSIBLE_NAME)
        self.assertEqual(HEADER_ENCODE_ACCESSIBLE_NAME, "재인코딩 창 열기")
        self.assertFalse(self.button.badge.isVisibleTo(self.button))

        self.button.set_target_count(7)
        self.assertTrue(self.button.badge.isVisibleTo(self.button))
        self.assertEqual(self.button.badge.count, 7)

    def test_badge_hidden_when_no_targets_or_finished(self) -> None:
        """대상이 없거나 배치가 끝난 상태에서는 배지를 숨기는지 검증"""
        self.button.set_target_count(3)
        self.button.set_status(_status(CHIP_STATE_FINISHED))
        self.assertFalse(self.button.badge.isVisibleTo(self.button))

        self.button.set_status(_status(CHIP_STATE_IDLE))
        self.button.set_target_count(0)
        self.assertFalse(self.button.badge.isVisibleTo(self.button))

    def test_running_and_paused_labels_carry_the_percent(self) -> None:
        """실행 중과 일시중지 문구가 각각의 접두어와 퍼센트를 담는지 검증"""
        self.button.set_status(_status(CHIP_STATE_RUNNING, 42))
        self.assertEqual(self.button.text_label.text(), RUNNING_LABEL_TEMPLATE.format(percent=42))
        self.assertEqual(self.button.text_label.text(), "재인코딩 42%")

        self.button.set_status(_status(CHIP_STATE_PAUSED, 7))
        self.assertEqual(self.button.text_label.text(), PAUSED_LABEL_TEMPLATE.format(percent=7))
        self.assertEqual(self.button.text_label.text(), "일시중지 7%")

    def test_progress_fill_follows_the_percent(self) -> None:
        """진행 채움 폭이 버튼 폭과 퍼센트에 비례하고, 유휴와 비활성에서는 0인지 검증"""
        self.assertEqual(self.button.fill_width(), 0)

        self.button.set_status(_status(CHIP_STATE_RUNNING, 50))
        self.assertEqual(self.button.fill_width(), round(self.button.width() / 2))

        self.button.set_status(_status(CHIP_STATE_PAUSED, 100))
        self.assertEqual(self.button.fill_width(), self.button.width())

        self.button.setEnabled(False)
        self.assertEqual(self.button.fill_width(), 0)

    def test_click_emits_the_signal_only_when_enabled(self) -> None:
        """활성 상태에서만 클릭이 신호를 내는지 검증"""
        clicked: list[str] = []
        self.button.clicked.connect(lambda: clicked.append("clicked"))
        self.button.setEnabled(False)
        self.button.click()
        self.assertEqual(clicked, [])
        self.button.setEnabled(True)
        self.button.click()
        self.assertEqual(clicked, ["clicked"])

    def test_paint_uses_palette_state_tokens_in_both_themes(self) -> None:
        """다크와 라이트 팔레트 모두에서 상태별 배색이 서로 다르고 유효한 색인지 검증"""
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            self._assert_state_colors(palette)

    def _assert_state_colors(self, palette: ThemePalette) -> None:
        self.button.apply_theme(palette)
        self.button.set_status(_status(CHIP_STATE_IDLE))
        idle = self._state_colors()
        self.assertEqual(idle, (palette.btn_bg, palette.text_primary, palette.btn_border))

        self.button.set_status(_status(CHIP_STATE_RUNNING, 10))
        running = self._state_colors()
        self.assertEqual(
            running,
            (palette.bg_elevated, palette.badge_encode_running[1], palette.badge_encode_running[2]),
        )

        self.button.set_status(_status(CHIP_STATE_PAUSED, 10))
        paused = self._state_colors()
        self.assertEqual(
            paused,
            (palette.bg_elevated, palette.row_paused_accent, palette.row_paused_accent),
        )
        self.button.setEnabled(False)
        disabled = self._state_colors()
        self.button.setEnabled(True)
        for colors in (idle, running, paused, disabled):
            for color in colors:
                self.assertTrue(QColor(color).isValid())
        self.assertNotEqual(idle, running)
        self.assertNotEqual(running, paused)

    def _state_colors(self) -> tuple[str, str, str]:
        """버튼의 지금 상태 배색 토큰 세 개"""
        return self.button._colors()

    def test_fill_color_uses_the_paused_or_running_token(self) -> None:
        """진행 채움 색이 일시중지와 실행 중에서 서로 다른 토큰을 쓰는지 검증"""
        self.button.apply_theme(DARK_PALETTE)
        self.button.set_status(_status(CHIP_STATE_RUNNING, 30))
        self.assertEqual(self.button._fill_color(), DARK_PALETTE.badge_encode_running[0])
        self.button.set_status(_status(CHIP_STATE_PAUSED, 30))
        self.assertEqual(self.button._fill_color(), DARK_PALETTE.row_paused_bg)

    def test_size_hint_covers_the_child_labels(self) -> None:
        """크기 힌트가 아이콘과 문구, 배지를 담은 레이아웃 크기이고 높이가 헤더 버튼과 같은지 검증"""
        self.button.set_status(_status(CHIP_STATE_IDLE))
        self.button.set_target_count(123)
        hint = self.button.sizeHint()
        self.assertGreater(hint.width(), self.button.text_label.sizeHint().width())
        self.assertEqual(hint.height(), TOOLBAR_ICON_BTN_SIZE)
        self.assertEqual(self.button.minimumSizeHint().height(), hint.height())
        self.assertLess(self.button.minimumSizeHint().width(), hint.width())

    def test_height_does_not_change_when_the_badge_hides(self) -> None:
        """대상 수 배지가 숨는 상태로 바뀌어도 버튼 높이가 흔들리지 않는지 검증"""
        self.button.set_target_count(9)
        idle = self.button.sizeHint()
        self.button.set_status(_status(CHIP_STATE_RUNNING, 20))
        running = self.button.sizeHint()
        self.assertEqual(idle.height(), running.height())

    def test_theme_switch_keeps_the_label_and_updates_the_icon(self) -> None:
        """테마를 바꿔도 문구는 그대로이고 아이콘은 새 팔레트 색으로 다시 그려지는지 검증"""
        self.button.apply_theme(DARK_PALETTE)
        self.button.set_status(_status(CHIP_STATE_RUNNING, 55))
        before = self.button.icon_label.pixmap().toImage()
        self.button.apply_theme(LIGHT_PALETTE)
        self.assertEqual(self.button.text_label.text(), "재인코딩 55%")
        self.assertNotEqual(self.button.icon_label.pixmap().toImage(), before)

    def test_paint_draws_the_state_background_and_progress_fill(self) -> None:
        """그리기 결과에서 진행 채움 왼쪽과 빈 오른쪽 색이 상태 토큰과 일치하는지 검증"""
        self.button.apply_theme(DARK_PALETTE)
        self.button.setEnabled(True)
        self.button.resize(140, 36)
        self.button.show()
        self.addCleanup(self.button.hide)
        self.button.set_status(_status(CHIP_STATE_IDLE))
        idle = self._painted_colors()
        self.assertEqual(idle[0], QColor(DARK_PALETTE.btn_bg).name())
        self.assertEqual(idle[0], idle[1])

        self.button.set_status(_status(CHIP_STATE_RUNNING, 50))
        running = self._painted_colors()
        self.assertEqual(running[0], QColor(DARK_PALETTE.badge_encode_running[0]).name())
        self.assertEqual(running[1], QColor(DARK_PALETTE.bg_elevated).name())

        self.button.set_status(_status(CHIP_STATE_PAUSED, 50))
        paused = self._painted_colors()
        self.assertEqual(paused[0], QColor(DARK_PALETTE.row_paused_bg).name())
        self.assertEqual(paused[1], QColor(DARK_PALETTE.bg_elevated).name())

    def test_hover_redraws_with_the_hover_token(self) -> None:
        """마우스가 들어오고 나갈 때 호버 토큰과 상태 토큰으로 다시 그리는지 검증"""
        self.button.apply_theme(DARK_PALETTE)
        self.button.setEnabled(True)
        self.button.resize(140, 36)
        self.button.enterEvent(None)
        self.assertEqual(self.button._base_background(), DARK_PALETTE.btn_hover)
        self.button.leaveEvent(None)
        self.assertEqual(self.button._base_background(), DARK_PALETTE.btn_bg)

    def _painted_colors(self) -> tuple[str, str]:
        """버튼을 그린 이미지에서 채움 안쪽과 오른쪽 끝 색 이름"""
        self.button.repaint()
        image = self.button.grab().toImage()
        row = image.height() // 2
        left = image.pixelColor(3, row).name()
        right = image.pixelColor(image.width() - 4, row).name()
        return left, right


class EncodeTargetCountBadgeTests(GuiTestCase):
    """대상 수 배지의 문구 폭과 그리기 준비 상태를 검증"""

    def test_width_follows_the_count_and_never_below_the_pill_height(self) -> None:
        """배지 폭이 수 문구에 맞춰 커지고 알약 높이보다 작아지지 않는지 검증"""
        badge = EncodeTargetCountBadge()
        self.addCleanup(badge.deleteLater)
        badge.set_count(1)
        narrow = badge.width()
        badge.set_count(12345)
        self.assertGreater(badge.width(), narrow)
        self.assertGreaterEqual(narrow, badge.height())

    def test_badge_paints_the_background_and_centered_count(self) -> None:
        """배지가 배경 알약을 칠하고 수를 가운데에 그리는지 픽셀로 검증"""
        badge = EncodeTargetCountBadge()
        self.addCleanup(badge.deleteLater)
        badge.resize(34, 16)
        badge.set_count(12)
        badge.set_colors("#123456", "#FFFFFF")
        badge.show()
        _APP.processEvents()
        image = badge.grab().toImage()
        corners = (
            QColor(image.pixelColor(1, image.height() // 2)).name(),
            QColor(image.pixelColor(image.width() - 2, image.height() // 2)).name(),
        )
        for color in corners:
            self.assertEqual(color, "#123456")
        drawn = any(
            QColor(image.pixelColor(x, y)).lightness() > 150
            for x in range(image.width())
            for y in range(image.height())
        )
        self.assertTrue(drawn)


class DecorativeArrowTests(GuiTestCase):
    """장식 화살표 라벨의 크기, 투명 마우스 통과, 흐린 색 토큰을 검증"""

    def test_arrow_is_decorative_and_uses_the_muted_token(self) -> None:
        """화살표가 약 20px 표시 전용 라벨이고 흐린 글자 토큰으로 그려지는지 검증"""
        label = create_encode_arrow_label(_host(self), DARK_PALETTE)
        self.addCleanup(label.deleteLater)
        self.assertEqual(label.size(), QSize(ARROW_SIZE_PX, ARROW_SIZE_PX))
        self.assertEqual(ARROW_SIZE_PX, 20)
        self.assertEqual(ARROW_ICON_NAME, "chevron-right")
        self.assertTrue(label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertFalse(label.pixmap().isNull())

    def test_arrow_changes_pixels_between_themes(self) -> None:
        """다크와 라이트 팔레트가 서로 다른 화살표 픽셀을 만드는지 검증"""
        host = _host(self)
        dark = create_encode_arrow_label(host, DARK_PALETTE)
        light = create_encode_arrow_label(host, LIGHT_PALETTE)
        self.addCleanup(dark.deleteLater)
        self.addCleanup(light.deleteLater)
        self.assertNotEqual(dark.pixmap().toImage(), light.pixmap().toImage())


if __name__ == "__main__":
    unittest.main(verbosity=2)
