"""작업 경로 입력 칸 아래에 하위 폴더 목록을 띄우는 자동완성 드롭다운 모듈

- 폴더 읽기는 워커 스레드의 순수 함수가 맡고, Qt 객체는 GUI 스레드에서만 다룸
- 목록에는 폴더만 넣고, 상위 폴더가 있으면 첫 줄에 `..`를 둠
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from src.core.sorting import natural_sort_key
from src.core.workspace_paths import PATH_SPEC_SEP, normalize_path_separators
from src.gui.constants import FILTER_DEBOUNCE_MS, call_later
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.floating_popup import FloatingPopupFrame

# - 하위 폴더가 수천 개인 경로에서도 목록을 끊어 읽도록 두는 표시 상한
PATH_AC_MAX_FOLDERS: int = 200
PARENT_ROW_TEXT: str = ".."
READING_NOTICE: str = "폴더를 읽는 중입니다"
TRUNCATED_NOTICE: str = "일부만 표시합니다"
KEY_HINT: str = "Up Down 이동, Tab/Enter 채우기, 선택 없이 Enter 확정"
_FOLDER_ICON_NAME: str = "folder"
_FOLDER_ROW_ICON_SIZE_PX: int = 14
_LIST_MAX_HEIGHT_PX: int = 232
_CARD_PADDING_PX: int = 4
_POPUP_GAP_PX: int = 2
_POPUP_MIN_WIDTH_PX: int = 180
_COUNT_TEMPLATE: str = "{count}개 폴더"
_TRUNCATED_SEPARATOR: str = ", "


@dataclass(frozen=True)
class FolderListing:
    """하위 폴더 목록 한 번의 결과. `folders`는 확정에 바로 쓰는 전체 경로"""

    base_dir: str
    folders: tuple[str, ...]
    parent: str | None
    truncated: bool

    @property
    def count(self) -> int:
        """목록에 담은 하위 폴더 수"""
        return len(self.folders)


def split_folder_input(text: str) -> tuple[str, str]:
    """입력 경로를 목록을 읽을 상위 경로와 이름 접두어로 나눔

    - 여러 작업 폴더를 세미콜론으로 이어 입력하면 마지막 항목만 대상으로 함
    - 구분자로 끝나면 하위 폴더 전체를, 아니면 그 이름으로 시작하는 폴더만 고름
    """
    last_spec = normalize_path_separators(text).split(PATH_SPEC_SEP)[-1].strip()
    if not last_spec:
        return "", ""
    cut = max(last_spec.rfind("\\"), last_spec.rfind("/"))
    if cut < 0:
        return "", last_spec
    return last_spec[: cut + 1], last_spec[cut + 1 :]


def parent_folder(path: str) -> str | None:
    """상위 폴더 경로 반환. 드라이브, UNC 공유 루트처럼 더 올라갈 곳이 없으면 None"""
    trimmed = path.rstrip("\\/")
    cut = max(trimmed.rfind("\\"), trimmed.rfind("/"))
    if not trimmed or cut < 0:
        return None
    head = trimmed[:cut]
    if head.endswith(":"):
        return f"{head}\\"
    # - `\\\\서버\\공유` 위의 서버 목록은 폴더가 아니므로 상위 항목을 두지 않음
    if head.startswith("\\\\") and head.count("\\") <= 2:
        return None
    return head or None


def read_folder_names(
    base_dir: str,
    prefix: str,
    limit: int,
    is_cancelled: Callable[[], bool],
) -> tuple[tuple[str, ...], bool]:
    """`base_dir` 바로 아래 폴더 이름을 이름순으로 `limit`개까지 읽음

    - 파일과 이름이 접두어로 시작하지 않는 항목은 넣지 않음
    - 반환은 (이름 목록, 상한을 넘겨 더 있음 여부). 상한을 넘으면 그 자리에서 읽기를 멈춤
    """
    folded = prefix.casefold()
    names: list[str] = []
    truncated = False
    with os.scandir(base_dir) as entries:
        for entry in entries:
            if is_cancelled():
                return (), False
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if not is_dir or not entry.name.casefold().startswith(folded):
                continue
            names.append(entry.name)
            if len(names) > limit:
                truncated = True
                break
    names.sort(key=natural_sort_key)
    return tuple(names[:limit]), truncated


def resolve_listing_base(text: str) -> tuple[str, str]:
    """하위 목록을 읽을 기준 경로와 이름 접두어 반환

    - 마지막 항목이 구분자 없이 존재하는 폴더면 그 폴더 자체의 하위 목록을 읽도록 기준을 옮김
    - 그 밖에는 입력한 이름으로 시작하는 폴더를 고르도록 상위 경로와 접두어를 그대로 씀
    """
    base_dir, prefix = split_folder_input(text)
    if prefix and os.path.isdir(f"{base_dir}{prefix}"):
        return f"{base_dir}{prefix}", ""
    return base_dir, prefix


def build_listing(
    text: str,
    limit: int,
    is_cancelled: Callable[[], bool],
) -> FolderListing | None:
    """입력 경로에 대한 하위 폴더 목록. 폴더로 읽을 수 없거나 취소되면 None"""
    base_dir, prefix = resolve_listing_base(text)
    if not base_dir or not os.path.isdir(base_dir):
        return None
    try:
        names, truncated = read_folder_names(base_dir, prefix, limit, is_cancelled)
    except OSError:
        return None
    if is_cancelled():
        return None
    return FolderListing(
        base_dir=base_dir,
        folders=tuple(os.path.join(base_dir, name) for name in names),
        parent=parent_folder(base_dir),
        truncated=truncated,
    )


class FolderListLoader(QObject):
    """입력 경로의 하위 폴더를 워커 스레드에서 읽어 GUI 스레드로 전달

    - 워커는 Qt 객체를 만지지 않고 폴더 읽기 함수만 호출
    - 새 요청은 이전 읽기를 취소하고, 늦게 도착한 이전 결과는 요청 번호로 걸러 냄
    """

    finished = pyqtSignal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._request_id: int = 0
        self._cancel: threading.Event = threading.Event()

    def request(self, text: str, limit: int) -> int:
        """새 읽기를 시작하고 요청 번호 반환"""
        self._cancel.set()
        self._cancel = threading.Event()
        self._request_id += 1
        threading.Thread(
            target=self._run,
            args=(self._request_id, text, limit, self._cancel),
            name="path-autocomplete",
            daemon=True,
        ).start()
        return self._request_id

    def cancel(self) -> None:
        """진행 중인 읽기 취소"""
        self._cancel.set()

    def _run(self, request_id: int, text: str, limit: int, cancel: threading.Event) -> None:
        """워커 스레드: 폴더 목록을 읽어 요청 번호와 함께 전달"""
        listing = build_listing(text, limit, cancel.is_set)
        if not cancel.is_set():
            self.finished.emit(request_id, listing)


class FolderAutocompletePopup(FloatingPopupFrame):
    """경로 입력 칸 아래에 붙는 폴더 전용 드롭다운

    - 키 입력은 입력 칸의 이벤트 필터가 맡으므로 팝업은 포커스를 받지 않음
    - 읽는 동안에는 목록을 숨기고 `폴더를 읽는 중입니다`만 표시
    """

    folder_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self._anchor: QWidget | None = None
        self._palette: ThemePalette = get_active_palette()
        card, layout = self._build_card("PathAutocompleteCard", _CARD_PADDING_PX)
        layout.setSpacing(0)
        self.list_folders = QListWidget(card)
        self.list_folders.setObjectName("PathAutocompleteList")
        # - 팝업과 목록이 포커스를 가져가면 입력 칸이 키 입력을 받지 못하므로 포커스는 입력 칸에 둠
        self.list_folders.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.list_folders.setUniformItemSizes(True)
        self.list_folders.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_folders.setMaximumHeight(_LIST_MAX_HEIGHT_PX)
        self.list_folders.itemClicked.connect(self._on_item_clicked)
        footer = QHBoxLayout()
        footer.setContentsMargins(6, 2, 6, 1)
        self.lbl_count = QLabel(card)
        self.lbl_count.setObjectName("PathAutocompleteFooter")
        self.lbl_keys = QLabel(KEY_HINT, card)
        self.lbl_keys.setObjectName("PathAutocompleteFooter")
        footer.addWidget(self.lbl_count)
        footer.addStretch(1)
        footer.addWidget(self.lbl_keys)
        layout.addWidget(self.list_folders)
        layout.addLayout(footer)

    def show_for(self, anchor: QWidget) -> None:
        """앵커 입력 칸 바로 아래에 같은 폭으로 표시"""
        self._anchor = anchor
        self.setFixedWidth(max(anchor.width(), _POPUP_MIN_WIDTH_PX))
        self.show_clamped_at(anchor.mapToGlobal(QPoint(0, anchor.height() + _POPUP_GAP_PX)))

    def show_reading(self, anchor: QWidget) -> None:
        """목록을 비우고 읽는 중 안내만 두고 표시"""
        self.list_folders.clear()
        self.list_folders.setVisible(False)
        self.lbl_count.setText(READING_NOTICE)
        self.show_for(anchor)

    def show_listing(self, listing: FolderListing) -> None:
        """읽은 폴더 목록과 개수, 키 안내를 채우고 선택은 비워 둠"""
        self.list_folders.clear()
        self.list_folders.setVisible(True)
        if listing.parent is not None:
            self._add_row(PARENT_ROW_TEXT, listing.parent)
        for folder in listing.folders:
            self._add_row(os.path.basename(folder), folder)
        self.list_folders.setCurrentRow(-1)
        count_text = _COUNT_TEMPLATE.format(count=listing.count)
        if listing.truncated:
            count_text = f"{count_text}{_TRUNCATED_SEPARATOR}{TRUNCATED_NOTICE}"
        self.lbl_count.setText(count_text)
        # - 읽는 동안 숨겨 두었던 목록이 보이면 높이가 바뀌므로 폭과 위치를 다시 맞춤
        if self._anchor is not None:
            self.show_for(self._anchor)

    @property
    def count_text(self) -> str:
        """지금 하단에 표시 중인 개수, 안내 문구"""
        return self.lbl_count.text()

    @property
    def theme_palette(self) -> ThemePalette:
        """항목 아이콘에 쓰는 현재 테마 팔레트"""
        return self._palette

    def selected_folder(self) -> str | None:
        """방향키로 고른 항목의 전체 경로. 고른 항목이 없으면 None"""
        item = self.list_folders.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def current_row_text(self) -> str:
        """지금 고른 행의 표시 문구. 고른 행이 없으면 빈 문자열"""
        item = self.list_folders.currentItem()
        return "" if item is None else item.text()

    def move_selection(self, delta: int) -> None:
        """현재 행을 delta만큼 옮김. 아직 고르지 않았으면 진행 방향 끝에서 시작"""
        count = self.list_folders.count()
        if count == 0:
            return
        row = self.list_folders.currentRow()
        row = (0 if delta > 0 else count - 1) if row < 0 else max(0, min(count - 1, row + delta))
        self.list_folders.setCurrentRow(row)

    def apply_theme(self, palette: ThemePalette) -> None:
        """테마 팔레트를 저장하고 표시 중인 항목 아이콘 색상을 갱신"""
        self._palette = palette
        for row in range(self.list_folders.count()):
            item = self.list_folders.item(row)
            if item is not None:
                item.setIcon(self._folder_icon())

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """입력 칸 클릭은 바깥 클릭으로 보지 않고, 입력 칸의 Esc는 닫기만 한 뒤 편집 취소에 넘김"""
        if (
            a0 is self._anchor
            and isinstance(a1, QKeyEvent)
            and a1.type() == QEvent.Type.KeyPress
            and a1.key() == Qt.Key.Key_Escape
        ):
            # - Esc를 소비하면 입력 칸이 편집을 취소하지 못하므로 닫기만 하고 넘김
            self.close()
            return False
        if (
            a1 is not None
            and a1.type() == QEvent.Type.MouseButtonPress
            and isinstance(a1, QMouseEvent)
            and self.isVisible()
            and self._anchor is not None
        ):
            pos = a1.globalPosition().toPoint()
            if not self.rect().contains(self.mapFromGlobal(pos)):
                hit = QApplication.widgetAt(pos)
                if hit is not None and (hit is self._anchor or self._anchor.isAncestorOf(hit)):
                    return False
        return super().eventFilter(a0, a1)

    def _folder_icon(self) -> QIcon:
        """목록 행에 쓰는 폴더 아이콘"""
        return get_icon(_FOLDER_ICON_NAME, palette=self._palette, size=_FOLDER_ROW_ICON_SIZE_PX)

    def _add_row(self, text: str, folder: str) -> None:
        """표시 문구와 확정 경로를 담은 행 추가"""
        item = QListWidgetItem(self._folder_icon(), text)
        item.setData(Qt.ItemDataRole.UserRole, folder)
        item.setToolTip(folder)
        self.list_folders.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """행 클릭은 그 폴더로 확정"""
        value = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(value, str):
            self.folder_activated.emit(value)

    def _on_hidden(self) -> None:
        """숨김 시 선택을 비워 다시 열 때 Enter가 입력 텍스트로 확정되게 함"""
        self.list_folders.setCurrentRow(-1)


class PathAutocomplete(QObject):
    """경로 입력 칸의 하위 폴더 자동완성 조율

    - 입력이 멈추면 `FILTER_DEBOUNCE_MS` 뒤에 폴더 읽기를 시작
    - Up/Down 이동, Tab/Enter 채우기, 선택 없는 Enter 확정은 입력 칸 이벤트 필터가 처리
    """

    def __init__(self, edit: QLineEdit, parent: QWidget | None = None) -> None:
        owner = parent if parent is not None else edit
        super().__init__(owner)
        self._edit = edit
        self._popup = FolderAutocompletePopup(owner)
        self._loader = FolderListLoader(self)
        self._request_id: int = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FILTER_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_reading)
        self._loader.finished.connect(self._on_listing_ready)
        self._popup.folder_activated.connect(self._on_folder_activated)
        edit.textEdited.connect(self._on_text_edited)
        edit.installEventFilter(self)

    @property
    def popup(self) -> FolderAutocompletePopup:
        """자동완성 드롭다운"""
        return self._popup

    @property
    def is_open(self) -> bool:
        """드롭다운이 열려 있는지"""
        return self._popup.isVisible()

    @property
    def request_id(self) -> int:
        """지금 반영을 기다리는 폴더 읽기 요청 번호"""
        return self._request_id

    def apply_theme(self, palette: ThemePalette) -> None:
        """드롭다운 아이콘 색상을 테마에 맞춤"""
        self._popup.apply_theme(palette)

    def close(self) -> None:
        """디바운스와 진행 중인 읽기를 멈추고 드롭다운을 닫음"""
        self._timer.stop()
        self._loader.cancel()
        self._drop_pending_result()
        self._popup.close()

    def _drop_pending_result(self) -> None:
        """취소 확인을 이미 지난 읽기 결과가 늦게 도착해도 버리도록 대기 번호를 비움

        - 로더 요청 번호는 1부터 시작하므로 0은 어떤 결과와도 일치하지 않음
        """
        self._request_id = 0

    def _on_text_edited(self, _text: str) -> None:
        """입력이 바뀌면 디바운스를 다시 시작하고, 열려 있으면 읽는 중으로 표시"""
        # - 이전 입력으로 시작한 읽기 결과가 지금 입력과 다른 목록을 띄우지 않게 함
        self._loader.cancel()
        self._drop_pending_result()
        if self._popup.isVisible():
            self._popup.show_reading(self._edit)
        self._timer.start()

    def _start_reading(self) -> None:
        """입력 경로의 하위 폴더 읽기를 워커에 요청. 읽을 상위 경로가 없으면 닫음"""
        base_dir, _prefix = resolve_listing_base(self._edit.text())
        if not base_dir:
            self.close()
            return
        self._request_id = self._loader.request(self._edit.text(), PATH_AC_MAX_FOLDERS)
        self._popup.show_reading(self._edit)

    def _on_listing_ready(self, request_id: int, listing: object) -> None:
        """워커 결과를 드롭다운에 반영. 늦은 결과와 읽기 실패는 버림"""
        if request_id != self._request_id:
            return
        if not isinstance(listing, FolderListing):
            self._popup.close()
            return
        # - 고를 항목이 하나도 없으면 빈 목록을 띄우지 않음
        if not listing.folders and listing.parent is None:
            self._popup.close()
            return
        self._popup.show_listing(listing)

    def _on_folder_activated(self, folder: str) -> None:
        """목록에서 고른 폴더를 입력 칸에 넣고 그 폴더로 확정"""
        self.close()
        self._edit.setText(folder)
        self._edit.returnPressed.emit()

    def _close_if_focus_moved(self) -> None:
        """포커스가 다른 위젯으로 옮겨 갔으면 대기 중인 디바운스까지 멈추고 닫음. 창 비활성화는 기반 팝업이 처리"""
        focused = QApplication.focusWidget()
        if focused is None or focused is self._edit:
            return
        if focused is self._popup or self._popup.isAncestorOf(focused):
            return
        self.close()

    def _fill_folder(self, folder: str) -> None:
        """고른 폴더를 입력 칸에 채우고 그 아래 목록을 이어서 읽음"""
        # - 드라이브 루트처럼 이미 구분자로 끝나는 경로에 구분자를 겹쳐 붙이지 않음
        suffix = "" if folder.endswith(("\\", "/")) else os.sep
        self._edit.setText(f"{folder}{suffix}")
        self._start_reading()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """드롭다운이 열린 동안 Up/Down/Tab/Enter를 가로채 목록을 조작하고, 포커스 이탈 시 닫음"""
        if a0 is not self._edit or a1 is None:
            return super().eventFilter(a0, a1)
        if a1.type() == QEvent.Type.FocusOut:
            # - 창 비활성화는 기반 팝업이 처리하므로, 실제로 다른 위젯이 포커스를 받았을 때만 닫음
            call_later(self, 0, self._close_if_focus_moved)
            return False
        if a1.type() != QEvent.Type.KeyPress:
            return super().eventFilter(a0, a1)
        if not isinstance(a1, QKeyEvent) or not self._popup.isVisible():
            return False
        key = a1.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._popup.move_selection(-1 if key == Qt.Key.Key_Up else 1)
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            folder = self._popup.selected_folder()
            if folder is not None:
                # - Tab과 Enter 모두 고른 폴더를 채우고 그 아래 목록을 이어서 보여 줌
                self._fill_folder(folder)
                return True
            if key == Qt.Key.Key_Tab:
                return False
            # - 고른 항목이 없으면 입력 텍스트 그대로 확정하도록 기본 처리에 맡김
            self.close()
            return False
        return False
