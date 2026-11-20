"""재인코딩 임시 파일 정리 창과 휴지통 이동 확인 대화상자

- 묶음별 트리에서 고를 수 있는 파일만 체크 상자를 두고, 고를 수 없는 파일은 이유를 툴팁으로 보임
- 옮기기 결과와 영구 삭제 확인은 조정자가 맡고, 이 창은 고르기, 위치 열기, 확인 창 한 번과 목록 갱신을 맡음
- 화면 스레드에서만 쓴다
"""

import os
from collections.abc import Callable, Sequence
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_leftovers import BlockReason, LeftoverGroup
from src.core.encode.encode_progress_values import bytes_to_mb, size_text
from src.gui.dialogs.encode_progress_panels import REVEAL_ACCESSIBLE_NAME, REVEAL_TIP
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.table.table_actions import reveal_paths
from src.service.encode.encode_leftovers import CleanupOutcome, LeftoverItem

WINDOW_TITLE: str = "임시 파일 정리"
WINDOW_MIN_WIDTH_PX: int = 680
WINDOW_MIN_HEIGHT_PX: int = 440
ACTION_BUTTON_PX: int = 28
TOOLBAR_BUTTON_PX: int = 32
COLUMN_HEADERS: tuple[str, ...] = ("파일 이름", "폴더", "크기", "수정 시각", "조작")
ACTION_COLUMN: int = 4
MTIME_FORMAT: str = "%Y-%m-%d %H:%M"
NS_PER_SEC: float = 1_000_000_000.0
GROUP_ORDER: tuple[LeftoverGroup, ...] = (
    LeftoverGroup.RECORDED,
    LeftoverGroup.OWN_RESULT,
    LeftoverGroup.UNKNOWN,
    LeftoverGroup.BLOCKED,
)
GROUP_TITLES: dict[LeftoverGroup, str] = {
    LeftoverGroup.RECORDED: "기록된 임시 파일",
    LeftoverGroup.OWN_RESULT: "결과로 보이는 파일",
    LeftoverGroup.UNKNOWN: "출처를 알 수 없는 파일",
    LeftoverGroup.BLOCKED: "고를 수 없는 파일",
}
PRESELECTED_GROUPS: frozenset[LeftoverGroup] = frozenset({LeftoverGroup.RECORDED, LeftoverGroup.OWN_RESULT})
BLOCK_TEXTS: dict[BlockReason, str] = {
    BlockReason.REPLACE: "교체 대기 결과라 옮길 수 없습니다. 교체를 마치거나 포기한 뒤 정리해 주세요.",
    BlockReason.RESUME_TARGET: "이어서 재인코딩 때 정리되는 임시 파일이라 옮길 수 없습니다. 이어서 재인코딩하거나 이어 하지 않음을 고른 뒤 정리해 주세요.",
    BlockReason.RECENT: "최근 10분 안에 바뀌어 다른 프로그램이 쓰는 중일 수 있습니다. 잠시 뒤 다시 확인해 주세요.",
}
SUMMARY_TEMPLATE: str = "선택 {count}개, 용량 {size}"
EMPTY_TEXT: str = "정리할 임시 파일이 없습니다."
TRASH_ACCESSIBLE_NAME: str = "휴지통으로 이동"
TRASH_TIP: str = "선택한 임시 파일을 휴지통으로 옮깁니다. 확인 창을 한 번 거칩니다."
CONFIRM_BODY_TEMPLATE: str = "임시 파일 {count}개, 약 {size}를 휴지통으로 옮길까요? 원본 영상 파일은 바뀌지 않습니다."
CONFIRM_TRASH_TEXT: str = "휴지통으로 이동"
CONFIRM_CANCEL_TEXT: str = "취소"
PART_CONFIRM_BODY_TEMPLATE: str = "이 영상이 쓰려던 임시 파일 {name}을 휴지통으로 옮길까요? 원본 영상 파일은 바뀌지 않습니다."
PERMANENT_TITLE: str = "영구 삭제 확인"
PERMANENT_BODY: str = "이 드라이브에는 휴지통이 없거나 확인할 수 없습니다. 선택한 파일은 휴지통을 거치지 않고 영구 삭제됩니다. 계속할까요?"
PERMANENT_TEXT: str = "영구 삭제"
SKIPPED_TEMPLATE: str = "건너뛴 임시 파일 {count}개가 있습니다. 작업 기록에서 사유를 확인해 주세요."
REVEAL_LIMIT_TEMPLATE: str = "한 번에 열 수 있는 탐색기 창 수를 넘어 {count}개 폴더는 열지 못했습니다."
PATH_ROLE: int = int(Qt.ItemDataRole.UserRole)


def _ask_two_buttons(parent: QWidget | None, title: str, body: str, confirm_text: str, destructive: bool) -> bool:
    """확인과 취소 두 버튼 모달. 기본 포커스는 취소, Esc도 취소로 봄"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(body)
    role = QMessageBox.ButtonRole.DestructiveRole if destructive else QMessageBox.ButtonRole.AcceptRole
    confirm = box.addButton(confirm_text, role)
    cancel = box.addButton(CONFIRM_CANCEL_TEXT, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is confirm


def ask_trash(parent: QWidget | None, count: int, total_bytes: int) -> bool:
    """휴지통 이동 확인. 확인을 고르면 참"""
    body = CONFIRM_BODY_TEMPLATE.format(count=count, size=size_text(bytes_to_mb(total_bytes)))
    return _ask_two_buttons(parent, WINDOW_TITLE, body, CONFIRM_TRASH_TEXT, destructive=False)


def ask_permanent(parent: QWidget | None) -> bool:
    """휴지통을 거치지 않는 영구 삭제 확인. 확인을 고르면 참"""
    return _ask_two_buttons(parent, PERMANENT_TITLE, PERMANENT_BODY, PERMANENT_TEXT, destructive=True)


def ask_part_trash(parent: QWidget | None, file_name: str) -> bool:
    """같은 자리 임시 파일 하나를 휴지통으로 옮기기 전 확인. 확인을 고르면 참"""
    body = PART_CONFIRM_BODY_TEMPLATE.format(name=file_name)
    return _ask_two_buttons(parent, WINDOW_TITLE, body, CONFIRM_TRASH_TEXT, destructive=False)


def _mtime_text(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / NS_PER_SEC).strftime(MTIME_FORMAT)


class CleanupWindow(QDialog):
    """임시 파일 목록 창. 옮기기는 on_trash가 맡고, 돌아온 결과에서 옮긴 파일만 목록에서 뺌"""

    def __init__(
        self,
        items: Sequence[LeftoverItem],
        on_trash: Callable[[list[LeftoverItem]], list[CleanupOutcome]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH_PX, WINDOW_MIN_HEIGHT_PX)
        self._items: list[LeftoverItem] = list(items)
        self._on_trash: Callable[[list[LeftoverItem]], list[CleanupOutcome]] = on_trash
        self._checked: dict[str, QTreeWidgetItem] = {}
        self._tree: QTreeWidget = QTreeWidget()
        self._tree.setHeaderLabels(list(COLUMN_HEADERS))
        self._tree.setRootIsDecorated(True)
        self._tree.itemChanged.connect(lambda _item, _column: self._update_summary())
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(ACTION_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        self._summary: QLabel = QLabel()
        reveal_btn = create_icon_button(
            self,
            REVEAL_TIP,
            self._reveal_checked,
            accessible_name=REVEAL_ACCESSIBLE_NAME,
            fixed_size=TOOLBAR_BUTTON_PX,
        )
        set_button_icon(reveal_btn, "folder-open", pad_right=0)
        trash_btn = create_icon_button(
            self,
            TRASH_TIP,
            self._trash_checked,
            accessible_name=TRASH_ACCESSIBLE_NAME,
            fixed_size=TOOLBAR_BUTTON_PX,
        )
        set_button_icon(trash_btn, "trash", pad_right=0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._summary, stretch=1)
        toolbar.addWidget(reveal_btn)
        toolbar.addWidget(trash_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tree, stretch=1)
        layout.addLayout(toolbar)
        layout.addWidget(buttons)
        self._rebuild()

    def checked_items(self) -> list[LeftoverItem]:
        """체크된 항목. 고를 수 없는 항목은 체크할 수 없으므로 항상 고를 수 있는 항목만 나옴"""
        by_path = {item.path: item for item in self._items}
        return [by_path[path] for path, child in self._checked.items() if child.checkState(0) == Qt.CheckState.Checked]

    def _rebuild(self) -> None:
        self._tree.clear()
        self._checked = {}
        for group in GROUP_ORDER:
            members = [item for item in self._items if item.verdict.group is group]
            if not members:
                continue
            group_row = QTreeWidgetItem(self._tree, [f"{GROUP_TITLES[group]} {len(members)}개"])
            group_row.setFlags(group_row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            group_row.setFirstColumnSpanned(True)
            group_row.setExpanded(True)
            for item in members:
                self._add_row(group_row, item)
        if self._tree.topLevelItemCount() == 0:
            QTreeWidgetItem(self._tree, [EMPTY_TEXT])
        self._update_summary()

    def _add_row(self, parent: QTreeWidgetItem, item: LeftoverItem) -> None:
        folder = os.path.relpath(os.path.dirname(item.path), item.root_dir)
        row = QTreeWidgetItem(
            parent,
            [os.path.basename(item.path), folder, size_text(bytes_to_mb(item.size)), _mtime_text(item.mtime_ns), ""],
        )
        row.setData(0, PATH_ROLE, item.path)
        if item.verdict.selectable:
            checked = item.verdict.group in PRESELECTED_GROUPS
            row.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._checked[item.path] = row
        else:
            row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if item.verdict.block is not None:
                row.setToolTip(0, BLOCK_TEXTS[item.verdict.block])
        reveal = create_icon_button(
            self._tree,
            REVEAL_TIP,
            lambda path=item.path: self._reveal([path]),
            accessible_name=REVEAL_ACCESSIBLE_NAME,
            fixed_size=ACTION_BUTTON_PX,
        )
        set_button_icon(reveal, "folder-open", pad_right=0)
        self._tree.setItemWidget(row, ACTION_COLUMN, reveal)

    def _on_double_clicked(self, row: QTreeWidgetItem, _column: int) -> None:
        path = row.data(0, PATH_ROLE)
        if isinstance(path, str):
            self._reveal([path])

    def _reveal(self, paths: list[str]) -> None:
        skipped = reveal_paths(paths)
        if skipped > 0:
            QMessageBox.information(self, WINDOW_TITLE, REVEAL_LIMIT_TEMPLATE.format(count=skipped))

    def _reveal_checked(self) -> None:
        paths = [item.path for item in self.checked_items()]
        if paths:
            self._reveal(paths)

    def _trash_checked(self) -> None:
        selected = self.checked_items()
        if not selected:
            return
        if not ask_trash(self, len(selected), sum(item.size for item in selected)):
            return
        outcomes = self._on_trash(selected)
        moved = {outcome.path for outcome in outcomes if outcome.moved}
        self._items = [item for item in self._items if item.path not in moved]
        self._rebuild()
        skipped = len(outcomes) - len(moved)
        if skipped > 0:
            QMessageBox.information(self, WINDOW_TITLE, SKIPPED_TEMPLATE.format(count=skipped))

    def _update_summary(self) -> None:
        selected = self.checked_items()
        total = sum(item.size for item in selected)
        self._summary.setText(SUMMARY_TEMPLATE.format(count=len(selected), size=size_text(bytes_to_mb(total))))
