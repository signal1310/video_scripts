"""재인코딩 진행 창의 영상 카드 탭: 순서 번호와 줄임 제목, 전체 파일 이름 툴팁, 탭 막대 표시, 팔레트 꾸밈

- 카드 생성과 신호 연결은 진행 창이 맡고, 이 위젯은 탭 추가와 제거만 맡음
- 화면 스레드에서만 쓴다
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTabWidget, QWidget

from src.gui.dialogs.encode_progress_card import tab_title
from src.gui.styles.palettes import ThemePalette


class CardTabs(QTabWidget):
    """영상마다 카드 하나를 탭으로 두는 위젯. 카드가 없으면 탭 영역 전체를 숨김"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDocumentMode(True)
        tab_bar = self.tabBar()
        if tab_bar is not None:
            tab_bar.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setVisible(False)

    def set_tab_bar_visible(self, visible: bool) -> None:
        """동시 실행이 1개면 탭 막대를 숨김. 카드 영역 자체는 카드 수에 따라 따로 정함"""
        tab_bar = self.tabBar()
        if tab_bar is not None:
            tab_bar.setVisible(visible)

    def add_card(self, card: QWidget, order: int, file_name: str) -> None:
        """카드를 탭으로 추가하고 탭 툴팁에 순서와 전체 파일 이름을 보여 줌"""
        index = self.addTab(card, tab_title(order, file_name))
        self.setTabToolTip(index, f"{order}. {file_name}")
        self.setVisible(True)

    def remove_card(self, card: QWidget) -> None:
        """카드 탭을 제거하고, 남은 탭이 없으면 탭 영역을 숨김"""
        index = self.indexOf(card)
        if index >= 0:
            self.removeTab(index)
        card.deleteLater()
        self.setVisible(self.count() > 0)

    def apply_palette(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 꾸밈: 선택 탭은 카드 바탕과 강조 밑줄, 나머지는 흐린 글자와 호버 바탕"""
        self.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabWidget, QTabBar, QTabWidget > QWidget#qt_tabwidget_stackedwidget { background: transparent; }"
            f"QTabBar::tab {{ background: transparent; color: {palette.text_muted};"
            " padding: 6px 12px; margin-right: 4px; border: none;"
            " border-top-left-radius: 6px; border-top-right-radius: 6px; }"
            f"QTabBar::tab:hover {{ background: {palette.bg_hover}; color: {palette.text_primary}; }}"
            f"QTabBar::tab:selected {{ background: {palette.bg_card}; color: {palette.text_primary};"
            f" border-bottom: 2px solid {palette.progress_fill}; }}"
        )
