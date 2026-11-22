"""재인코딩 시작 전 태그 기록 방식, 내부 요소 집계, 원본 교체 경고를 보여주고 시작을 확인하는 대화상자 모듈

- 집계 항목은 summarize_encode_impact 결과를 그대로 받아 표시하고, 0개 항목은 숨김
- 영상 행은 메인 표와 같이 툴팁, 더블클릭 재생, 스페이스바 미리 보기를 제공
- 문구는 TERMINOLOGY.md 6.6절이 기준 정의
"""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.config import RESULT_POLICY_REPLACE
from src.core.encode.encode_impact import (
    IMPACT_ATTACHMENT_DROPPED,
    IMPACT_AUDIO_AAC,
    IMPACT_BITMAP_SUB_MKV,
    IMPACT_CONTAINER_CHANGED,
    IMPACT_COVER_KEPT,
    IMPACT_DATA_DROPPED,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    IMPACT_EXTRA_VIDEO_DROPPED,
    IMPACT_SUBTITLE_MOV_TEXT,
    IMPACT_TEN_BIT_TO_EIGHT,
    IMPACT_VR_STEREO_LOST,
    EncodeImpactSummary,
)
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.models import VideoEvaluation
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.table.table_actions import count_missing_video_files, launch_video_file, missing_notice, row_file_state
from src.gui.table.table_tooltips import format_row_tooltip
from src.gui.theme import ThemeAwareMixin, build_stylesheet
from src.service.encode.encode_job import EncodeSettings

_DIALOG_TITLE: str = "재인코딩 시작"
_DIALOG_MIN_WIDTH: int = 520
_DIALOG_MIN_HEIGHT: int = 360
_BUTTON_SIZE: int = 36
_BUTTON_ICON_SIZE: int = 16
_WARNING_ICON_NAME: str = "alert-triangle"
_WARNING_ICON_SIZE: int = 18
_START_NAME: str = "재인코딩 시작"
_START_TOOLTIP: str = "재인코딩을 시작합니다."
_START_OBJECT_NAME: str = "EncodeStartButton"
_CANCEL_NAME: str = "취소"
_CANCEL_TOOLTIP: str = "재인코딩을 시작하지 않고 닫습니다."
_SECTION_TITLE: str = "재인코딩으로 바뀌는 항목"
_UNKNOWN_LABEL: str = "정보 없음"
_UNKNOWN_TOOLTIP: str = "분석 정보가 없어 집계하지 못했습니다."
_BITMAP_REASON: str = "비트맵 자막(PGS, VobSub 등)은 MP4에 저장할 수 없어 MKV로 출력합니다."
_BITMAP_TOOLTIP: str = "MKV로 출력하면 탐색기 태그는 옮기지만 별점은 옮기지 않습니다."
_WARNING_TEMPLATE: str = "원본 {count}개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다."
_REPLACE_CONFIRM_TEMPLATE: str = (
    "원본 {count}개를 결과 파일로 바꿉니다. 원본은 보관되지 않으며 되돌릴 수 없습니다. 그래도 시작하시겠습니까?"
)
_IMPACT_TOOLTIPS: dict[str, str] = {
    IMPACT_COVER_KEPT: "앞면 커버 이미지를 다시 인코딩하지 않고 결과 파일에 그대로 복사합니다.",
    IMPACT_EXPLORER_TAGS_PRESERVED: (
        "원본의 탐색기 태그와 별점을 결과 파일에 옮깁니다. 태그나 별점이 없는 영상도 이 목록에 포함됩니다."
    ),
    IMPACT_SUBTITLE_MOV_TEXT: (
        "텍스트 자막을 MP4용 형식으로 바꿉니다. 글꼴, 색, 위치 같은 자막 서식은 사라지고 글자만 남습니다."
    ),
    IMPACT_BITMAP_SUB_MKV: f"{_BITMAP_REASON} {_BITMAP_TOOLTIP}",
    IMPACT_ATTACHMENT_DROPPED: "MP4는 첨부 파일을 저장하지 않아 글꼴 같은 첨부 파일을 결과에서 뺍니다.",
    IMPACT_EXTRA_VIDEO_DROPPED: "주 영상 밖의 영상 스트림을 결과에서 뺍니다. 커버 이미지는 이 항목에 들지 않습니다.",
    IMPACT_DATA_DROPPED: "데이터 스트림은 결과에 넣지 않습니다. 원본에 든 타임코드 같은 기록 트랙이 여기에 속합니다.",
    IMPACT_AUDIO_AAC: (
        "결과 형식에 맞지 않는 오디오를 AAC로 다시 인코딩합니다. AAC도 손실 압축이라 음질이 조금 달라질 수 있습니다."
    ),
    IMPACT_CONTAINER_CHANGED: (
        "원본과 다른 형식으로 저장합니다. 원본 형식을 유지할 수 없는 형식(WMV 등)은 MP4로 저장하고, 출력 형식이 항상 MP4이면 MKV 원본도 MP4로 저장합니다."
    ),
    IMPACT_TEN_BIT_TO_EIGHT: (
        "10비트 영상을 8비트로 줄여 저장합니다. 색 정보가 줄어 밝기 단계나 그라데이션이 달라질 수 있습니다."
    ),
    IMPACT_VR_STEREO_LOST: (
        "MP4로 출력하면 입체 3D(Stereo 3D) 같은 VR 부가 정보가 사라집니다. 원본 형식 유지(MKV)를 권장합니다."
    ),
}
_TAG_NOTICES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "결과에 재인코딩 태그를 기록합니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_HIDDEN: "결과에 숨은 기록만 남깁니다. 탐색기 태그 칸에는 보이지 않습니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_OFF: (
        "결과에 재인코딩 태그를 기록하지 않습니다. 이미 재인코딩한 영상은 재인코딩 이력으로만 가려냅니다. "
        "설정에서 바꿀 수 있습니다."
    ),
}
_TAG_RECORD_LINES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "이 방식에서는 탐색기 태그와 숨은 기록, 재인코딩 이력이 남습니다.",
    ENCODE_TAG_MODE_HIDDEN: "이 방식에서는 숨은 기록과 재인코딩 이력이 남습니다. 탐색기 태그는 남지 않습니다.",
    ENCODE_TAG_MODE_OFF: "이 방식에서는 재인코딩 이력만 남습니다. 탐색기 태그와 숨은 기록은 남지 않습니다.",
}
_TAG_TERM_LINES: tuple[str, ...] = (
    "재인코딩 태그: 이 프로그램이 재인코딩한 결과임을 알리는 표시입니다. 같은 영상을 다시 재인코딩하지 않도록 막는 데 씁니다.",
    "탐색기 태그: 파일 속성 창(Alt+Enter)의 태그 칸에 보이는 키워드입니다. 현재 이름은 {tag_name}입니다. 탐색기에서 검색하고 묶어 볼 수 있습니다.",
    "숨은 기록: 파일 안에 남기는 보이지 않는 기록입니다. 인코더, 품질값, 시각, 프로그램 버전이 들어 있으며, 탐색기에서 태그를 고쳐도 남습니다.",
    "재인코딩 이력: 이 PC의 프로그램 데이터에 항상 남는 기록입니다.",
)


def _tag_tooltip(settings: EncodeSettings) -> str:
    """태그 안내 줄의 툴팁. 현재 방식에서 남는 기록을 먼저 쓰고 용어를 풀이"""
    terms = [line.format(tag_name=settings.encode_tag_name) for line in _TAG_TERM_LINES]
    return "\n".join([_TAG_RECORD_LINES[settings.encode_tag_mode], "", *terms])


class _ImpactItemTree(QTreeWidget):
    """집계 목록 트리. 스페이스바는 미리 보기 요청으로 넘기고, 항목 줄은 한 번 클릭으로 펼치거나 접음"""

    preview_key_pressed = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() == Qt.Key.Key_Space:
            self.preview_key_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """화살표 클릭은 기본 동작이 펼치거나 접으므로 상태가 바뀌었으면 더 바꾸지 않아 두 번 토글되지 않게 함"""
        if event is None:
            return
        item = self.itemAt(event.position().toPoint())
        was_expanded = item is not None and item.isExpanded()
        super().mousePressEvent(event)
        if item is not None and item.childCount() > 0 and item.isExpanded() == was_expanded:
            item.setExpanded(not was_expanded)


class _ImpactRowDelegate(QStyledItemDelegate):
    """최상위 항목 줄(집계 제목)은 선택 배경과 포커스 테두리를 그리지 않고, 영상 줄은 기본대로 그림

    - 선택과 현재 항목 상태는 그대로 두고 그리기 상태만 지워 키보드 이동이 바뀌지 않게 함
    """

    def initStyleOption(self, option: QStyleOptionViewItem | None, index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        if option is not None and not index.parent().isValid():
            option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)


class EncodeStartDialog(QDialog, ThemeAwareMixin):
    """재인코딩 배치 시작 전 영향 집계와 경고를 확인받는 대화상자. 시작은 accept, 취소는 reject

    - 영상 행의 동작(툴팁, 더블클릭, 스페이스바)은 메인 표와 같고, 미리 보기는 preview_path 콜백에 넘김
    - 원본 교체 배치는 시작 때 한 번 더 확인하며 기본 선택은 취소
    """

    def __init__(
        self,
        summary: EncodeImpactSummary,
        settings: EncodeSettings,
        evaluations: Sequence[VideoEvaluation],
        root_dir: str,
        target_codec_label: str,
        preview_path: Callable[[VideoEvaluation, str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle(_DIALOG_TITLE)
        self.setModal(True)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self._root_dir = root_dir
        self._target_codec_label = target_codec_label
        self._preview_path = preview_path
        self._evals: dict[str, VideoEvaluation] = {ev.video.filename: ev for ev in evaluations}
        self._replace_confirm_needed = settings.result_policy == RESULT_POLICY_REPLACE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.lbl_tag = QLabel(_TAG_NOTICES[settings.encode_tag_mode], self)
        self.lbl_tag.setWordWrap(True)
        self.lbl_tag.setToolTip(_tag_tooltip(settings))
        layout.addWidget(self.lbl_tag)

        has_items = summary.unknown_count > 0 or any(bucket.count > 0 for bucket in summary.buckets)
        self.lbl_section = QLabel(_SECTION_TITLE, self)
        self.lbl_section.setObjectName("GroupHeaderTitle")
        self.lbl_section.setVisible(has_items)
        layout.addWidget(self.lbl_section)
        self.tree_items = self._build_items_tree(summary)
        self.tree_items.setVisible(has_items)
        layout.addWidget(self.tree_items, stretch=1)

        self.warning_box = QFrame(self)
        self.warning_box.setObjectName("EncodeWarningBox")
        warning_row = QHBoxLayout(self.warning_box)
        warning_row.setContentsMargins(10, 8, 10, 8)
        warning_row.setSpacing(8)
        self.lbl_warning_icon = QLabel(self.warning_box)
        self.lbl_warning_icon.setFixedSize(_WARNING_ICON_SIZE, _WARNING_ICON_SIZE)
        self.lbl_warning = QLabel(_WARNING_TEMPLATE.format(count=summary.total_count), self.warning_box)
        self.lbl_warning.setObjectName("WarningHintLabel")
        self.lbl_warning.setWordWrap(True)
        warning_row.addWidget(self.lbl_warning_icon, 0, Qt.AlignmentFlag.AlignTop)
        warning_row.addWidget(self.lbl_warning, 1)
        self.warning_box.setVisible(self._replace_confirm_needed)
        layout.addWidget(self.warning_box)
        self._replace_confirm_text = _REPLACE_CONFIRM_TEMPLATE.format(count=summary.total_count)

        self.btn_cancel = create_icon_button(
            self,
            _CANCEL_TOOLTIP,
            self.reject,
            accessible_name=_CANCEL_NAME,
            fixed_size=_BUTTON_SIZE,
        )
        self.btn_start = create_icon_button(
            self,
            _START_TOOLTIP,
            self._on_start_clicked,
            accessible_name=_START_NAME,
            fixed_size=_BUTTON_SIZE,
            object_name=_START_OBJECT_NAME,
        )
        self.btn_start.setDefault(True)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch(1)
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_start)
        layout.addLayout(footer)
        self.init_theme_listener()

    def _build_items_tree(self, summary: EncodeImpactSummary) -> QTreeWidget:
        """0개 항목을 뺀 집계 항목을 접을 수 있는 트리로 만듦. 자식 노드는 영상 이름"""
        tree = _ImpactItemTree(self)
        tree.setObjectName("EncodeImpactTree")
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        tree.setExpandsOnDoubleClick(False)
        tree.setItemDelegate(_ImpactRowDelegate(tree))
        tree.preview_key_pressed.connect(self._on_preview_key)
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        for bucket in summary.buckets:
            if bucket.count == 0:
                continue
            self._add_group(tree, f"{bucket.label} ({bucket.count}개)", _IMPACT_TOOLTIPS[bucket.key], bucket.filenames)
        if summary.unknown_count > 0:
            self._add_group(
                tree,
                f"{_UNKNOWN_LABEL} ({summary.unknown_count}개)",
                _UNKNOWN_TOOLTIP,
                summary.unknown_filenames,
            )
        return tree

    def _add_group(self, tree: QTreeWidget, title: str, tooltip: str, filenames: Sequence[str]) -> None:
        """집계 항목 하나를 추가하고 그 아래에 영상 행을 메인 표와 같은 툴팁으로 나열"""
        group = QTreeWidgetItem(tree, [title])
        group.setToolTip(0, tooltip)
        for filename in filenames:
            child = QTreeWidgetItem(group, [filename])
            child.setData(0, Qt.ItemDataRole.UserRole, filename)
            ev = self._evals.get(filename)
            if ev is not None:
                child.setToolTip(0, format_row_tooltip(ev, self._root_dir, self._target_codec_label))

    def _evaluation_of(self, item: QTreeWidgetItem | None) -> VideoEvaluation | None:
        """영상 행이면 그 평가 결과, 집계 항목 제목이거나 없으면 None"""
        if item is None or item.parent() is None:
            return None
        filename = item.data(0, Qt.ItemDataRole.UserRole)
        return self._evals.get(filename) if isinstance(filename, str) else None

    def _on_preview_key(self) -> None:
        """스페이스바: 현재 영상 행을 미리 보기 콜백에 넘김"""
        ev = self._evaluation_of(self.tree_items.currentItem())
        if ev is None:
            return
        _state, path = row_file_state(self._root_dir, ev)
        if path is None:
            self._notify_missing(ev)
            return
        self._preview_path(ev, path)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """더블클릭: 영상 행을 기본 플레이어로 재생하고 파일이 없으면 안내"""
        ev = self._evaluation_of(item)
        if ev is not None and not launch_video_file(self._root_dir, ev):
            self._notify_missing(ev)

    def _notify_missing(self, ev: VideoEvaluation) -> None:
        """영상 파일이 없을 때 메인 표와 같은 안내 문구를 보여줌"""
        notice = missing_notice(count_missing_video_files(self._root_dir, [ev]), 1)
        if notice is not None:
            QMessageBox.warning(self, _DIALOG_TITLE, notice)

    def _on_start_clicked(self) -> None:
        """원본 교체 배치는 한 번 더 묻고, 기본 선택은 취소"""
        if self._replace_confirm_needed and not self._confirm_replace():
            return
        self.accept()

    def _confirm_replace(self) -> bool:
        answer = QMessageBox.question(
            self,
            _DIALOG_TITLE,
            self._replace_confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 스타일시트, 경고 아이콘, 버튼 아이콘 색을 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        warning_pixmap = get_icon(_WARNING_ICON_NAME, color=palette.fg_maxrate_warn, size=_WARNING_ICON_SIZE)
        self.lbl_warning_icon.setPixmap(warning_pixmap.pixmap(_WARNING_ICON_SIZE, _WARNING_ICON_SIZE))
        set_button_icon(self.btn_cancel, "x", palette=palette, size=_BUTTON_ICON_SIZE, pad_right=0)
        set_button_icon(self.btn_start, "play", palette=palette, size=_BUTTON_ICON_SIZE, pad_right=0)
