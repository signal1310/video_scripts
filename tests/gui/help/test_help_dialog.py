"""사용 가이드의 (?) 앵커 이동이 실제로 본문을 스크롤하는지 끝까지 검증

- 설정 카드의 (?) 버튼은 조율자를 거쳐 판정 원리 탭의 앵커를 요청하므로, 같은 공개 경로로 창을 연 뒤 스크롤 값을 잼
- 창을 숨긴 동안에는 본문 배치가 없어 스크롤 범위가 0이므로, 표시한 뒤 앵커 절이 본문 맨 위에 오는지 확인
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import ClassifierConfig
from src.gui.controllers.dialog_coordinator import DialogCoordinator
from src.gui.help.help_concepts_page import build_concepts_html
from src.gui.help.help_dialog import HelpDialog
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase, isolate_app_state

_APP = QApplication.instance() or QApplication([])
_SRC_DIR = Path(__file__).resolve().parents[3] / "src"
# - (?) 카드가 넘기는 앵커는 설정 모듈의 인자와 상수에만 있으므로 코드에서 그대로 뽑아 씀
_ANCHOR_ARG_RE = re.compile(r'help_anchor="([A-Za-z0-9-]+)"')
_ANCHOR_CONST_RE = re.compile(r'_INTERNAL_REFERENCE_ANCHOR:\s*str\s*=\s*"([A-Za-z0-9-]+)"')
# - 본문 맨 위 절은 스크롤 값이 0이므로 아래쪽 절만 스크롤 값을 검사
_MIN_SCROLL_FOR_LOWER_SECTION: int = 500
# - 앵커 절 제목이 본문 맨 위에 오면 커서 사각형의 윗변이 0에 붙음, 배치 여백만큼만 허용
_TOP_TOLERANCE_PX: int = 2
_EVENT_PUMPS: int = 6


def _pump() -> None:
    """대기 중인 타이머와 배치 이벤트를 정해진 횟수만큼 처리"""
    for _ in range(_EVENT_PUMPS):
        QCoreApplication.processEvents()


def _anchor_headings() -> dict[str, str]:
    """판정 원리 본문의 앵커 이름과 그 절 제목(태그 제거)"""
    html = build_concepts_html(LIGHT_PALETTE, ClassifierConfig())
    pairs = re.findall(r'<a name="([A-Za-z0-9-]+)"></a><h3>(.+?)</h3>', html)
    return {anchor: re.sub(r"<[^>]+>", "", title) for anchor, title in pairs}


def _anchors_used_by_help_buttons() -> set[str]:
    """설정 화면 (?) 버튼이 넘기는 앵커 이름을 소스에서 모음"""
    anchors: set[str] = set()
    for path in sorted(_SRC_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        anchors.update(_ANCHOR_ARG_RE.findall(text))
        anchors.update(_ANCHOR_CONST_RE.findall(text))
    return anchors


def _scroll_state(dialog: HelpDialog, heading: str) -> tuple[int, int]:
    """판정 원리 본문의 스크롤 값과 앵커 절 제목이 놓인 세로 위치"""
    browser = dialog.browser_concepts
    document = browser.document()
    assert document is not None
    scroll_bar = browser.verticalScrollBar()
    assert scroll_bar is not None
    cursor = document.find(heading)
    assert not cursor.isNull(), f"본문에서 절 제목을 찾지 못함: {heading}"
    return scroll_bar.value(), browser.cursorRect(cursor).top()


class HelpAnchorScrollTests(GuiTestCase):
    """(?) 버튼이 요청한 앵커로 본문이 실제로 스크롤되는지 검증"""

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        isolate_app_state(self, self.work.name)
        self.headings = _anchor_headings()

    def _assert_anchor_at_top(self, dialog: HelpDialog, anchor: str) -> None:
        """앵커 절이 본문 맨 위에 오는지, 아래쪽 절이면 스크롤 값도 충분한지 검증"""
        heading = self.headings[anchor]
        value, top = _scroll_state(dialog, heading)
        with self.subTest(anchor=anchor):
            self.assertGreaterEqual(value, _MIN_SCROLL_FOR_LOWER_SECTION)
            self.assertLessEqual(
                abs(top), _TOP_TOLERANCE_PX, f"{anchor} 절이 맨 위에 오지 않음: {top}"
            )
            self.assertEqual(dialog.tab_widget.currentIndex(), HelpDialog.TAB_CONCEPTS)
            self.assertEqual(dialog.current_anchor(), "")

    def _new_coordinator(self) -> DialogCoordinator:
        """부모 위젯을 둔 조율자. 테스트가 만든 창은 정리 단계에서 함께 지워짐"""
        parent = QWidget()
        self.addCleanup(parent.close)
        coordinator = DialogCoordinator(
            parent=parent,
            on_status=lambda _text: None,
            on_settings_applied=lambda _config, _root: None,
        )
        self.addCleanup(coordinator.close_all)
        return coordinator

    def test_every_help_button_anchor_resolves_to_a_section(self) -> None:
        """코드의 모든 (?) 앵커가 판정 원리 본문의 절 이름과 맞는지 검증"""
        anchors = _anchors_used_by_help_buttons()
        self.assertGreaterEqual(len(anchors), 5)
        for anchor in sorted(anchors):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, self.headings)

    def test_question_mark_buttons_scroll_the_help_window(self) -> None:
        """설정 카드의 (?) 버튼을 눌러 연 가이드가 해당 절로 스크롤되는지 검증"""
        coordinator = self._new_coordinator()
        settings = coordinator.open_settings(ClassifierConfig(), self.work.name)
        _pump()
        cards = [
            card
            for card in settings.findChildren(SettingsGroupCard)
            if card.help_anchor and card.btn_help is not None
        ]
        self.assertGreaterEqual(len({card.help_anchor for card in cards}), 3)
        for card in cards:
            anchor = card.help_anchor
            btn = card.btn_help
            assert btn is not None
            btn.click()
            _pump()
            dialog = coordinator.help_dialog
            assert dialog is not None
            self._assert_anchor_at_top(dialog, anchor)
            dialog.close()
            _pump()

    def test_hidden_dialog_keeps_anchor_until_shown(self) -> None:
        """숨긴 채 앵커를 요청하고 이벤트를 처리해도, 표시한 뒤 그 절로 이동하는지 검증"""
        for anchor in ("optimal-bitrate", "savings", "internal-thresholds"):
            with self.subTest(anchor=anchor):
                dialog = HelpDialog(ClassifierConfig())
                self.addCleanup(dialog.close)
                dialog.open_anchor(anchor)
                _pump()
                self.assertFalse(dialog.isVisible())
                dialog.show()
                _pump()
                self._assert_anchor_at_top(dialog, anchor)
                dialog.close()
                _pump()

    def test_visible_dialog_moves_to_each_requested_anchor(self) -> None:
        """창을 띄운 상태에서 다른 앵커를 요청하면 그 절로 다시 이동하는지 검증"""
        dialog = HelpDialog(ClassifierConfig())
        self.addCleanup(dialog.close)
        dialog.show()
        _pump()
        for anchor in ("internal-thresholds", "ratio", "savings"):
            dialog.open_anchor(anchor)
            _pump()
            self._assert_anchor_at_top(dialog, anchor)

    def test_coordinator_reuses_hidden_dialog_at_the_new_anchor(self) -> None:
        """닫아 둔 가이드 창을 다른 앵커로 다시 열면 그 절로 이동하는지 검증"""
        coordinator = self._new_coordinator()
        first = coordinator.show_help_dialog(ClassifierConfig(), anchor="optimal-bitrate")
        _pump()
        self._assert_anchor_at_top(first, "optimal-bitrate")
        first.close()
        _pump()
        dialog = coordinator.show_help_dialog(
            ClassifierConfig(), anchor="internal-thresholds"
        )
        self.assertIs(dialog, first)
        _pump()
        self._assert_anchor_at_top(dialog, "internal-thresholds")


if __name__ == "__main__":
    unittest.main()
