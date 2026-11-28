"""이어서 인코딩 확인 대화상자와 교체 포기 확인 창

- 기록 밖 임시 파일은 목록으로만 보여 주고 삭제하지 않음. 자동 삭제는 이 프로그램이 만든 파일임을 증명할 수 없어 하지 않음
- 닫기와 Esc는 나중에로 봄(배치를 남겨 둠)
"""

from collections.abc import Sequence
from enum import Enum

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_progress_panels import REVEAL_ACCESSIBLE_NAME, REVEAL_TIP
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_actions import reveal_paths
from src.gui.theme import ThemeAwareMixin, build_stylesheet
from src.service.encode.encode_resume import ResumeCount

ICON_BUTTON_PX: int = 28
LIST_MAX_HEIGHT_PX: int = 160
RESUME_TITLE: str = "이어서 인코딩 확인"
RESUME_BUTTON_TEXT: str = "이어서 인코딩"
LATER_BUTTON_TEXT: str = "나중에"
DISCARD_BUTTON_TEXT: str = "이어 하지 않음"
LATER_TIP: str = "배치를 그대로 두고 닫습니다. 다음 실행 때 다시 묻습니다."
DISCARD_TIP: str = "이 배치를 닫습니다. 이 배치가 기록한 임시 파일은 휴지통으로 옮기고, 원본 파일은 바뀌지 않습니다. 기록에 없는 파일은 그대로 둡니다."
CLEANUP_ACCESSIBLE_NAME: str = "임시 파일 정리"
CLEANUP_TIP: str = "기록에 없는 임시 파일을 정리 창에서 골라 휴지통으로 옮깁니다."
RESUME_LINE: str = "끝나지 않은 인코딩이 있습니다. 영상 {count}개를 이어서 인코딩할 수 있습니다."
REPLACE_LINE: str = "교체 대기 영상 {count}개가 있습니다. 진행 창의 결과 목록에서 다시 시도하거나 포기할 수 있습니다."
EXCLUDED_LINE: str = "원본이 바뀌어 제외하는 영상 {count}개는 건너뜀으로 표시합니다."
RESUME_SCOPE_LINE: str = "이어서 인코딩하면 기록에 있는 임시 파일만 지웁니다. 기록에 없는 파일은 그대로 둡니다."
UNRECORDED_NOTICE: str = (
    "기록에 없는 임시 파일 {count}개는 삭제하지 않았습니다. "
    "이 프로그램이 만든 파일인지 확인한 뒤 지워 주세요. "
    "같은 이름의 결과 임시 파일이 남아 있는 영상은 인코딩에 실패합니다."
)
FORFEIT_TITLE: str = "교체를 포기할까요?"
FORFEIT_BODY: str = "결과 파일을 원본 자리에 옮기지 않고 이 영상을 실패로 둡니다. 원본과 결과 파일은 모두 그대로 남습니다."
FORFEIT_BUTTON_TEXT: str = "교체 포기"
FORFEIT_CANCEL_TEXT: str = "취소"
DISCARD_TITLE: str = "결과를 폐기할까요?"
DISCARD_BODY: str = (
    "이 영상이 쓰려던 임시 결과 파일을 지웁니다. 지운 결과는 되돌릴 수 없으며 원본 영상 파일은 그대로 남습니다."
)
DISCARD_REPLACE_BUTTON_TEXT: str = "폐기"
DISCARD_CANCEL_TEXT: str = "취소"


class ResumeChoice(Enum):
    """이어서 인코딩 확인의 선택. CLEANUP은 정리 창을 연 뒤 같은 확인을 다시 묻는 신호"""

    RESUME = "resume"
    LATER = "later"
    DISCARD = "discard"
    CLEANUP = "cleanup"


def resume_body_lines(count: ResumeCount) -> list[str]:
    """재개 대상, 교체 대기, 원본 변경 제외, 임시 파일 범위 문장

    - 재개 대상이 없고 교체 대기만 있으면 재개 문장 대신 교체 대기 문장만 씀
    - 제외가 없으면 제외 문장은 뺌
    """
    lines: list[str] = []
    if count.resumable > 0 or count.replace_pending == 0:
        lines.append(RESUME_LINE.format(count=count.resumable))
    if count.replace_pending > 0:
        lines.append(REPLACE_LINE.format(count=count.replace_pending))
    if count.excluded > 0:
        lines.append(EXCLUDED_LINE.format(count=count.excluded))
    lines.append(RESUME_SCOPE_LINE)
    return lines


def _reveal(paths: list[str]) -> None:
    """위치 열기 결과(열린 폴더 수)는 버튼 동작에 필요 없으므로 버림"""
    reveal_paths(paths)


class ResumeDialog(QDialog, ThemeAwareMixin):
    """이어서 인코딩 확인 대화상자. 기록 밖 임시 파일이 있으면 목록을 스크롤로 보여 줌"""

    def __init__(self, count: ResumeCount, unrecorded: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(RESUME_TITLE)
        self._choice: ResumeChoice = ResumeChoice.LATER
        layout = QVBoxLayout(self)
        for line in resume_body_lines(count):
            label = QLabel(line)
            label.setWordWrap(True)
            layout.addWidget(label)
        buttons = QHBoxLayout()
        if unrecorded:
            notice = QLabel(UNRECORDED_NOTICE.format(count=len(unrecorded)))
            notice.setWordWrap(True)
            layout.addWidget(notice)
            listing = QListWidget()
            listing.setMaximumHeight(LIST_MAX_HEIGHT_PX)
            listing.addItems(list(unrecorded))
            listing.itemDoubleClicked.connect(lambda item: _reveal([item.text()]))
            layout.addWidget(listing)
            reveal = create_icon_button(
                self,
                REVEAL_TIP,
                lambda: _reveal([item.text() for item in listing.selectedItems()]),
                accessible_name=REVEAL_ACCESSIBLE_NAME,
                fixed_size=ICON_BUTTON_PX,
            )
            set_button_icon(reveal, "folder-open", pad_right=0)
            cleanup = create_icon_button(
                self,
                CLEANUP_TIP,
                lambda: self.choose(ResumeChoice.CLEANUP),
                accessible_name=CLEANUP_ACCESSIBLE_NAME,
                fixed_size=ICON_BUTTON_PX,
            )
            set_button_icon(cleanup, "trash", pad_right=0)
            buttons.addWidget(reveal)
            buttons.addWidget(cleanup)
        buttons.addStretch(1)
        resume = QPushButton(RESUME_BUTTON_TEXT)
        resume.setDefault(True)
        resume.clicked.connect(lambda: self.choose(ResumeChoice.RESUME))
        later = QPushButton(LATER_BUTTON_TEXT)
        later.setToolTip(LATER_TIP)
        later.clicked.connect(lambda: self.choose(ResumeChoice.LATER))
        discard = QPushButton(DISCARD_BUTTON_TEXT)
        discard.setToolTip(DISCARD_TIP)
        discard.clicked.connect(lambda: self.choose(ResumeChoice.DISCARD))
        for button in (resume, later, discard):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.init_theme_listener()

    def apply_theme(self, palette: ThemePalette) -> None:
        """독립 대화상자에서도 확인 창과 툴팁이 앱 테마 QSS를 쓰도록 스타일시트를 적용"""
        self.setStyleSheet(build_stylesheet(palette))

    @property
    def choice(self) -> ResumeChoice:
        """사용자가 고른 선택. 고르지 않고 닫으면 나중에"""
        return self._choice

    def choose(self, choice: ResumeChoice) -> None:
        """선택을 기록하고 창을 닫음. 이어서 인코딩만 수락으로 닫음"""
        self._choice = choice
        if choice is ResumeChoice.RESUME:
            self.accept()
        else:
            self.reject()


def ask_resume(parent: QWidget, count: ResumeCount, unrecorded: Sequence[str]) -> ResumeChoice:
    """이어서 인코딩 확인을 모달로 띄우고 선택을 돌려줌"""
    dialog = ResumeDialog(count, unrecorded, parent)
    dialog.exec()
    return dialog.choice


def ask_forfeit(parent: QWidget) -> bool:
    """교체 포기 확인. 교체 포기를 고르면 참, 취소나 닫기는 거짓(기본 포커스는 취소)"""
    box = QMessageBox(parent)
    box.setWindowTitle(FORFEIT_TITLE)
    box.setText(FORFEIT_BODY)
    forfeit = box.addButton(FORFEIT_BUTTON_TEXT, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(FORFEIT_CANCEL_TEXT, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is forfeit


def ask_discard_replace(parent: QWidget) -> bool:
    """결과 폐기 확인. 폐기를 고르면 참, 취소나 닫기는 거짓(기본 포커스는 취소)"""
    box = QMessageBox(parent)
    box.setWindowTitle(DISCARD_TITLE)
    box.setText(DISCARD_BODY)
    discard = box.addButton(DISCARD_REPLACE_BUTTON_TEXT, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(DISCARD_CANCEL_TEXT, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is discard
