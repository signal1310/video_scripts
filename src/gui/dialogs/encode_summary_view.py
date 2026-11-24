"""재인코딩 창 요약 탭: 시작 전 브리핑, 진행 중 대조, 완료 후 성적표와 영상 변환 트리

- 시작 전에는 배치 요약 카드, 원본 교체 경고, 재인코딩 태그 안내, 영상 변환 트리, 설정 요약 칩을 보여 줌
- 진행 중과 완료 후에는 상태 분포 칩, 완료분 절약 대조, 현재/평균 속도, 주의 안내, 진행 배지를 보여 줌
- 색은 팔레트 토큰만 쓰고, 문구는 TERMINOLOGY.md 6.6절, 6.17절이 기준 정의
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import RESULT_POLICY_REPLACE
from src.core.encode.encode_batch_book import EncodeState
from src.core.encode.encode_impact import EncodeImpactSummary
from src.core.encode.encode_progress_values import ResultRow
from src.core.encode.encode_summary import (
    BRIEF_CAPTIONS,
    BRIEF_POLICY_CAPTION,
    BRIEF_SIZE_CAPTION,
    BRIEF_TARGET_CAPTION,
    BRIEF_TITLE,
    BriefValues,
    attention_text,
    brief_values,
    done_badge,
    done_count,
    final_speed_text,
    saving_text,
    status_chips,
    status_counts,
    target_count,
)
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.models import VideoEvaluation
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.encode_impact_tree import EncodeImpactTree, SummaryMedia
from src.gui.dialogs.encode_progress_chips import FilterChip
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin, build_stylesheet
from src.service.encode.encode_job import EncodeSettings

SECTION_TITLE: str = "재인코딩으로 바뀌는 항목"
SECTION_SUBTITLE: str = "중복 포함, 해당 영상만 표시"
TAG_NOTICES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "결과에 재인코딩 태그를 기록합니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_HIDDEN: "결과에 숨은 기록만 남깁니다. 탐색기 태그 칸에는 보이지 않습니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_OFF: (
        "결과에 재인코딩 태그를 기록하지 않습니다. 이미 재인코딩한 영상은 재인코딩 이력으로만 가려냅니다. "
        "설정에서 바꿀 수 있습니다."
    ),
}
TAG_RECORD_LINES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "이 방식에서는 탐색기 태그와 숨은 기록, 재인코딩 이력이 남습니다.",
    ENCODE_TAG_MODE_HIDDEN: "이 방식에서는 숨은 기록과 재인코딩 이력이 남습니다. 탐색기 태그는 남지 않습니다.",
    ENCODE_TAG_MODE_OFF: "이 방식에서는 재인코딩 이력만 남습니다. 탐색기 태그와 숨은 기록은 남지 않습니다.",
}
TAG_TERM_LINES: tuple[str, ...] = (
    "재인코딩 태그: 이 프로그램이 재인코딩한 결과임을 알리는 표시입니다. 같은 영상을 다시 재인코딩하지 않도록 막는 데 씁니다.",
    "탐색기 태그: 파일 속성 창(Alt+Enter)의 태그 칸에 보이는 키워드입니다. 현재 이름은 {tag_name}입니다. 탐색기에서 검색하고 묶어 볼 수 있습니다.",
    "숨은 기록: 파일 안에 남기는 보이지 않는 기록입니다. 인코더, 품질값, 시각, 프로그램 버전이 들어 있으며, 탐색기에서 태그를 고쳐도 남습니다.",
    "재인코딩 이력: 이 PC의 프로그램 데이터에 항상 남는 기록입니다.",
)
WARNING_TEMPLATE: str = "원본 {count}개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다."
WARNING_ICON_NAME: str = "alert-triangle"
WARNING_ICON_SIZE: int = 18
SUMMARY_ICON_SIZE: int = 16
BUTTON_PX: int = 28
SETTINGS_CHIP_TIP: str = "환경 설정의 재인코딩 탭을 엽니다."
ATTENTION_ACCESSIBLE_NAME: str = "결과 탭 열기"
ATTENTION_TIP: str = "결과 탭으로 이동해 주의가 필요한 영상을 확인합니다."
RESULT_TAB_ICON: str = "file-text"
BRIEF_ROW_ROLES: dict[str, str] = {
    BRIEF_TARGET_CAPTION: "이번 재인코딩 대상 영상 수입니다.",
    BRIEF_SIZE_CAPTION: "대상 영상의 현재 용량 합계와 판정 단계의 예상 용량입니다.",
    "예상 순 절약": "판정 단계의 예상 절약 합계입니다. 음수는 용량이 늘어남을 뜻합니다.",
    "인코더/화질 기준값": "이번 배치에 쓸 목표 인코딩 방식과 화질 기준값입니다.",
    BRIEF_POLICY_CAPTION: "검사를 통과한 결과 파일을 원본과 어떻게 놓을지 정한 설정입니다.",
}


@dataclass(frozen=True, slots=True)
class EncodeStartPlan:
    """시작 전 브리핑과 영상 변환 트리에 필요한 값 묶음"""

    evaluations: tuple[VideoEvaluation, ...]
    paths: tuple[str, ...]
    summary: EncodeImpactSummary
    settings: EncodeSettings
    root_dir: str
    target_codec_label: str
    baseline_quality: float


class EncodeSummaryView(QWidget, ThemeAwareMixin):
    """요약 탭 한 페이지. 시작 전 브리핑과 진행/완료 상태를 한 위젯에서 갱신"""

    settings_requested = pyqtSignal()
    results_requested = pyqtSignal()
    status_requested = pyqtSignal(str)

    def __init__(self, media: SummaryMedia, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._media: SummaryMedia = media
        self._palette: ThemePalette | None = None
        self._plan: EncodeStartPlan | None = None
        self._status_chips: dict[str, FilterChip] = {}
        self._settings_chips: list[QPushButton] = []
        # - 빌더가 만든 위젯을 받기 전에 형식 검사를 통과하도록 자리 위젯을 두고 바로 바꿈
        self.lbl_attention_icon: QLabel = QLabel()
        self.lbl_attention: QLabel = QLabel()
        self.btn_attention: QPushButton = QPushButton()
        self.brief_values: dict[str, QLabel] = {}
        self.lbl_warning_icon: QLabel = QLabel()
        self.lbl_warning: QLabel = QLabel()
        self._build()
        self.init_theme_listener()

    def _build(self) -> None:
        """브리핑 카드, 상태 칩, 경고, 태그, 트리, 설정 칩을 배치"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.brief_card = self._build_brief_card()
        self.live_saving = QLabel(self)
        self.live_saving.setObjectName("EncodeSummaryLive")
        self.live_saving.setWordWrap(True)
        self.live_speed = QLabel(self)
        self.live_speed.setObjectName("EncodeSummaryLive")
        self.status_host = QWidget(self)
        self.status_flow = FlowLayout(self.status_host)
        self.attention = self._build_attention()
        self.warning_box = self._build_warning_box()
        self.tag_label = QLabel(self)
        self.tag_label.setWordWrap(True)
        self.tree_head = QWidget(self)
        head_row = QHBoxLayout(self.tree_head)
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(6)
        self.lbl_section = QLabel(SECTION_TITLE, self.tree_head)
        self.lbl_section.setObjectName("GroupHeaderTitle")
        self.lbl_subtitle = QLabel(SECTION_SUBTITLE, self.tree_head)
        self.lbl_subtitle.setObjectName("EncodeSummarySubtitle")
        self.badge = QLabel(self.tree_head)
        self.badge.setObjectName("EncodeSummaryBadge")
        head_row.addWidget(self.lbl_section)
        head_row.addWidget(self.lbl_subtitle)
        head_row.addStretch(1)
        head_row.addWidget(self.badge)
        self.tree = EncodeImpactTree(self._media, self)
        self.settings_host = QWidget(self)
        self.settings_flow = FlowLayout(self.settings_host)
        for widget in (
            self.brief_card,
            self.live_saving,
            self.live_speed,
            self.status_host,
            self.attention,
            self.warning_box,
            self.tag_label,
            self.tree_head,
            self.tree,
            self.settings_host,
        ):
            layout.addWidget(widget)
        self.live_saving.setVisible(False)
        self.live_speed.setVisible(False)
        self.status_host.setVisible(False)
        self.attention.setVisible(False)
        self.tree.setVisible(False)
        self.tree_head.setVisible(False)
        self.settings_host.setVisible(False)

    def _build_attention(self) -> QFrame:
        """주의 필요 배너. 오른쪽 아이콘 버튼으로 결과 탭을 엶"""
        banner = QFrame(self)
        banner.setObjectName("EncodeAttentionBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(6)
        self.lbl_attention_icon = QLabel(banner)
        self.lbl_attention_icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        self.lbl_attention = QLabel(banner)
        self.lbl_attention.setWordWrap(True)
        self.btn_attention = create_icon_button(
            banner,
            ATTENTION_TIP,
            self.results_requested.emit,
            accessible_name=ATTENTION_ACCESSIBLE_NAME,
            fixed_size=BUTTON_PX,
        )
        set_button_icon(self.btn_attention, RESULT_TAB_ICON, pad_right=0)
        row.addWidget(self.lbl_attention_icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.lbl_attention, 1)
        row.addWidget(self.btn_attention)
        banner.setVisible(False)
        return banner

    def _build_brief_card(self) -> QFrame:
        """배치 요약 카드의 이름과 값 줄을 만듦"""
        card = QFrame(self)
        card.setObjectName("EncodeBriefCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        title = QLabel(BRIEF_TITLE, card)
        title.setObjectName("GroupHeaderTitle")
        outer.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.brief_values = {}
        for row, caption in enumerate(BRIEF_CAPTIONS):
            caption_label = QLabel(caption, card)
            caption_label.setObjectName("EncodeBriefCaption")
            caption_label.setToolTip(BRIEF_ROW_ROLES[caption])
            value_label = QLabel(card)
            value_label.setObjectName("EncodeBriefValue")
            value_label.setWordWrap(True)
            value_label.setToolTip(BRIEF_ROW_ROLES[caption])
            grid.addWidget(caption_label, row, 0)
            grid.addWidget(value_label, row, 1)
            self.brief_values[caption] = value_label
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        return card

    def _build_warning_box(self) -> QFrame:
        """원본 교체 경고 상자"""
        box = QFrame(self)
        box.setObjectName("EncodeWarningBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.lbl_warning_icon = QLabel(box)
        self.lbl_warning_icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        self.lbl_warning = QLabel(box)
        self.lbl_warning.setObjectName("WarningHintLabel")
        self.lbl_warning.setWordWrap(True)
        row.addWidget(self.lbl_warning_icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.lbl_warning, 1)
        box.setVisible(False)
        return box

    def set_plan(self, plan: EncodeStartPlan) -> None:
        """시작 전 브리핑으로 되돌리고 새 배치 값을 채움"""
        self._plan = plan
        values = brief_values(
            plan.evaluations,
            baseline_quality=plan.baseline_quality,
            encoder_label=plan.target_codec_label,
            result_policy_label=result_policy_label(plan.settings.result_policy),
        )
        self._set_brief(values)
        self.brief_card.setVisible(True)
        self.tree.set_summary(plan.summary, plan.evaluations, plan.root_dir, plan.target_codec_label)
        has_items = plan.summary.unknown_count > 0 or any(bucket.count > 0 for bucket in plan.summary.buckets)
        self.tree.setVisible(has_items)
        self.tree_head.setVisible(has_items)
        self._set_tag(plan.settings)
        self._set_warning(plan.settings, len(plan.evaluations))
        self._rebuild_settings_chips(plan)
        self.live_saving.setVisible(False)
        self.live_speed.setVisible(False)
        self.status_host.setVisible(False)
        self.attention.setVisible(False)
        self.badge.setVisible(False)

    def set_resumed(self) -> None:
        """작업 기록에서 이어서 시작한 배치는 브리핑 없이 상태 칩과 성적만 보여 줌"""
        self._plan = None
        self.brief_card.setVisible(False)
        self.warning_box.setVisible(False)
        self.tag_label.setVisible(False)
        self.tree.setVisible(False)
        self.tree_head.setVisible(False)
        self.badge.setVisible(False)
        self._clear_settings_chips()

    def set_live(
        self,
        items: Sequence[tuple[str, EncodeState, str]],
        results: Mapping[str, ResultRow],
        speed_summary: str,
        elapsed_text: str,
        *,
        finished: bool,
    ) -> None:
        """진행 중 또는 완료 후 상태 분포, 절약 대조, 속도, 주의 안내, 진행 배지를 갱신"""
        counts = status_counts(items)
        self._refresh_status_chips(counts)
        self.status_host.setVisible(True)
        actual_mb, expected_mb = _saved_totals(results)
        self.live_saving.setText(saving_text(actual_mb, expected_mb, finished=finished))
        average = speed_summary.split("평균 ", 1)[-1]
        self.live_speed.setText(
            final_speed_text(elapsed_text, average) if finished else speed_summary
        )
        self.live_saving.setVisible(True)
        self.live_speed.setVisible(True)
        text = attention_text(counts)
        self.attention.setVisible(bool(text))
        self.lbl_attention.setText(text)
        if self._plan is not None:
            self.badge.setText(done_badge(done_count(items), target_count(items)))
            self.badge.setVisible(True)

    def set_excluded_count(self, included: int, total: int) -> None:
        """시작 전 대기열에서 제외한 영상 수를 반영해 대상 수를 다시 표시"""
        if BRIEF_TARGET_CAPTION in self.brief_values:
            text = f"{total}개" if included >= total else f"{included}/{total}개"
            self.brief_values[BRIEF_TARGET_CAPTION].setText(text)

    def compare_current(self) -> None:
        """현재 트리 영상의 화질 비교를 요청"""
        self.tree.compare_current()

    def apply_theme(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 카드, 목록, 칩을 칠하고 경고 아이콘을 다시 그림"""
        self._palette = palette
        self.setStyleSheet(build_stylesheet(palette))
        icon = get_icon(WARNING_ICON_NAME, color=palette.fg_maxrate_warn, size=WARNING_ICON_SIZE)
        pixmap = icon.pixmap(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        self.lbl_warning_icon.setPixmap(pixmap)
        self.lbl_attention_icon.setPixmap(pixmap)
        set_button_icon(self.btn_attention, RESULT_TAB_ICON, palette=palette, size=SUMMARY_ICON_SIZE, pad_right=0)
        for chip in self._status_chips.values():
            chip.apply_palette(palette)
        for chip in self._settings_chips:
            chip.setStyleSheet(_settings_chip_style(palette))

    def _set_brief(self, values: BriefValues) -> None:
        """브리핑 카드 값 5개를 채움"""
        text_by_caption = {
            BRIEF_TARGET_CAPTION: values.target,
            BRIEF_SIZE_CAPTION: values.size_change,
            "예상 순 절약": values.net_saved,
            "인코더/화질 기준값": values.encoder,
            BRIEF_POLICY_CAPTION: values.result_policy,
        }
        for caption, text in text_by_caption.items():
            self.brief_values[caption].setText(text)

    def _set_tag(self, settings: EncodeSettings) -> None:
        """태그 안내 문장과 툴팁을 기록 방식에 맞춤"""
        self.tag_label.setText(TAG_NOTICES[settings.encode_tag_mode])
        terms = [line.format(tag_name=settings.encode_tag_name) for line in TAG_TERM_LINES]
        self.tag_label.setToolTip("\n".join([TAG_RECORD_LINES[settings.encode_tag_mode], "", *terms]))
        self.tag_label.setVisible(True)

    def _set_warning(self, settings: EncodeSettings, count: int) -> None:
        """원본 교체일 때만 경고 상자를 보이고 원본 개수를 넣음"""
        self.lbl_warning.setText(WARNING_TEMPLATE.format(count=count))
        self.warning_box.setVisible(settings.result_policy == RESULT_POLICY_REPLACE)

    def _rebuild_settings_chips(self, plan: EncodeStartPlan) -> None:
        """설정 요약 칩을 다시 만듦. 칩을 누르면 환경 설정 재인코딩 탭을 엶"""
        self._clear_settings_chips()
        chips: list[tuple[str, str]] = [
            ("화질 기준값", f"화질 기준값 {plan.baseline_quality:.1f}"),
            ("목표 인코딩 방식", f"목표 인코딩 방식 {plan.target_codec_label}"),
            (BRIEF_POLICY_CAPTION, result_policy_label(plan.settings.result_policy)),
            ("재인코딩 태그", f"재인코딩 태그 {plan.settings.encode_tag_name}"),
        ]
        for caption, text in chips:
            chip = QPushButton(text, self.settings_host)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setObjectName("EncodeSettingsChip")
            chip.setAccessibleName(caption)
            chip.setToolTip(SETTINGS_CHIP_TIP)
            chip.clicked.connect(self.settings_requested.emit)
            if self._palette is not None:
                chip.setStyleSheet(_settings_chip_style(self._palette))
            self.settings_flow.addWidget(chip)
            self._settings_chips.append(chip)
        self.settings_host.setVisible(True)

    def _clear_settings_chips(self) -> None:
        """설정 요약 칩을 모두 지움"""
        for chip in self._settings_chips:
            self.settings_flow.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self._settings_chips = []
        self.settings_host.setVisible(False)

    def _refresh_status_chips(self, counts: Mapping[str, int]) -> None:
        """0개가 된 상태 칩을 지우고 남은 칩의 개수와 순서를 맞춤"""
        wanted = dict(status_chips(counts))
        for status in list(self._status_chips):
            if status not in wanted:
                chip = self._status_chips.pop(status)
                self.status_flow.removeWidget(chip)
                chip.hide()
                chip.deleteLater()
        for chip in self._status_chips.values():
            self.status_flow.removeWidget(chip)
        for status, count in wanted.items():
            chip = self._status_chips.get(status)
            if chip is None:
                chip = self._make_status_chip(status)
                self._status_chips[status] = chip
            chip.setText(f"{status} {count}")
            self.status_flow.addWidget(chip)

    def _make_status_chip(self, status: str) -> FilterChip:
        """상태 칩 하나를 만들고 누르면 그 상태의 탭이나 필터로 이동하게 함"""
        chip = FilterChip(status, self.status_host)
        chip.setCheckable(False)
        chip.setToolTip(f"{status} 상태 영상으로 이동합니다.")
        chip.clicked.connect(lambda _checked=False, key=status: self.status_requested.emit(key))
        if self._palette is not None:
            chip.apply_palette(self._palette)
        return chip


def result_policy_label(policy: str) -> str:
    """결과 파일 처리 설정의 화면 표기"""
    if policy == RESULT_POLICY_REPLACE:
        return "원본 교체"
    if policy == "separate_output":
        return "별도 폴더에 저장"
    return "원본 보관 후 교체"


def _saved_totals(results: Mapping[str, ResultRow]) -> tuple[float | None, float | None]:
    """완료 결과의 실제 절약과 예상 절약 합계"""
    rows = list(results.values())
    actual = sum((row.source_bytes - row.output_bytes) / (1024 * 1024) for row in rows)
    expected_values = [row.expected_delta_mb for row in rows if row.expected_delta_mb is not None]
    expected = sum(expected_values) if expected_values else None
    return (actual, expected) if rows else (None, None)


def _settings_chip_style(palette: ThemePalette) -> str:
    """설정 요약 칩 스타일"""
    return (
        f"QPushButton#EncodeSettingsChip {{ background: {palette.bg_elevated};"
        f" border: 1px solid {palette.border_subtle}; border-radius: 10px;"
        f" color: {palette.text_secondary}; padding: 3px 10px; }}"
        f"QPushButton#EncodeSettingsChip:hover {{ background: {palette.bg_hover};"
        f" border: 1px solid {palette.accent_blue}; color: {palette.text_primary}; }}"
    )
