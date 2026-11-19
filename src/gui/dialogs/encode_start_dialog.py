"""재인코딩 시작 전 태그 기록 방식, 내부 요소 집계, 원본 교체 경고를 보여주고 시작을 확인하는 대화상자 모듈

- 집계 항목은 summarize_encode_impact 결과를 그대로 받아 표시하고, 0개 항목은 숨김
- 문구는 TERMINOLOGY.md 6.6절이 기준 정의
"""

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from src.core.config import RESULT_POLICY_REPLACE
from src.core.encode.encode_impact import IMPACT_BITMAP_SUB_MKV, EncodeImpactSummary
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin, build_stylesheet
from src.service.encode.encode_job import EncodeSettings

_DIALOG_TITLE: str = "재인코딩 시작"
_DIALOG_MIN_WIDTH: int = 520
_DIALOG_MIN_HEIGHT: int = 360
_BUTTON_SIZE: int = 36
_BUTTON_ICON_SIZE: int = 16
_START_NAME: str = "재인코딩 시작"
_START_TOOLTIP: str = "재인코딩을 시작합니다."
_CANCEL_NAME: str = "취소"
_CANCEL_TOOLTIP: str = "재인코딩을 시작하지 않고 닫습니다."
_SECTION_TITLE: str = "재인코딩으로 바뀌는 항목"
_BITMAP_TOOLTIP: str = "MKV로 출력하면 탐색기 태그는 옮기지만 별점은 옮기지 않습니다."
_WARNING_TEMPLATE: str = "원본 {count}개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다."
_TAG_NOTICES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "결과에 재인코딩 태그를 기록합니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_HIDDEN: "결과에 숨은 기록만 남깁니다. 탐색기 태그 칸에는 보이지 않습니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_OFF: (
        "결과에 재인코딩 태그를 기록하지 않습니다. 이미 재인코딩한 영상은 재인코딩 이력으로만 가려냅니다. "
        "설정에서 바꿀 수 있습니다."
    ),
}


class EncodeStartDialog(QDialog, ThemeAwareMixin):
    """재인코딩 배치 시작 전 영향 집계와 경고를 확인받는 대화상자. 시작은 accept, 취소는 reject"""

    def __init__(
        self,
        summary: EncodeImpactSummary,
        settings: EncodeSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle(_DIALOG_TITLE)
        self.setModal(True)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.lbl_tag = QLabel(_TAG_NOTICES[settings.encode_tag_mode], self)
        self.lbl_tag.setWordWrap(True)
        layout.addWidget(self.lbl_tag)

        has_items = any(bucket.count > 0 for bucket in summary.buckets)
        self.lbl_section = QLabel(_SECTION_TITLE, self)
        self.lbl_section.setObjectName("GroupHeaderTitle")
        self.lbl_section.setVisible(has_items)
        layout.addWidget(self.lbl_section)
        self.tree_items = self._build_items_tree(summary)
        self.tree_items.setVisible(has_items)
        layout.addWidget(self.tree_items, stretch=1)

        self.lbl_warning = QLabel(_WARNING_TEMPLATE.format(count=summary.total_count), self)
        self.lbl_warning.setObjectName("WarningHintLabel")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setVisible(settings.result_policy == RESULT_POLICY_REPLACE)
        layout.addWidget(self.lbl_warning)

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
            self.accept,
            accessible_name=_START_NAME,
            fixed_size=_BUTTON_SIZE,
            object_name="PrimaryButton",
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
        tree = QTreeWidget(self)
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        for bucket in summary.buckets:
            if bucket.count == 0:
                continue
            parent = QTreeWidgetItem(tree, [f"{bucket.label} ({bucket.count}개)"])
            if bucket.key == IMPACT_BITMAP_SUB_MKV:
                parent.setToolTip(0, _BITMAP_TOOLTIP)
            for filename in bucket.filenames:
                QTreeWidgetItem(parent, [filename])
        return tree

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 스타일시트와 버튼 아이콘 색을 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        set_button_icon(self.btn_cancel, "x", palette=palette, size=_BUTTON_ICON_SIZE, pad_right=0)
        set_button_icon(self.btn_start, "play", palette=palette, size=_BUTTON_ICON_SIZE, pad_right=0)
