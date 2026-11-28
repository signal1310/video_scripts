"""인코딩 창 요약 탭: 시작 전 브리핑, 진행 중 대조, 완료 후 성적표와 영상 변환 트리

- 시작 전에는 배치 요약 카드(강조 타일 하나와 보조 두 줄), 원본 교체 경고, 인코딩 태그 안내, 영상 변환 트리, 설정 링크 하나를 보여 줌
- 진행 중과 완료 후에는 상태 분포 칩, 완료분 절약 대조, 현재/평균 속도, 주의 안내, 진행 배지를 보여 줌
- 색은 팔레트 토큰만 쓰고, 문구는 TERMINOLOGY.md 6.6절, 6.17절이 기준 정의
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.config import RESULT_POLICY_REPLACE
from src.core.encode.encode_batch_book import EncodeState
from src.core.encode.encode_impact import EncodeImpactSummary
from src.core.encode.encode_progress_values import (
    ResultRow,
    saved_totals,
    total_size_change_text,
)
from src.core.encode.encode_progress_view import speed_text
from src.core.encode.encode_summary import (
    BRIEF_NET_CAPTION,
    BRIEF_TITLE,
    BriefValues,
    attention_text,
    brief_values,
    delta_text,
    designation_caution_line,
    designation_counts,
    designation_line,
    designation_summary_line,
    done_badge,
    done_count,
    final_speed_text,
    net_saved_grew,
    saving_text,
    status_chips,
    status_counts,
    target_count,
    target_line,
)
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.models import VideoEvaluation
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.dialogs.encode_impact_tree import EncodeImpactTree, SummaryMedia
from src.gui.dialogs.encode_progress_chips import FilterChip
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import build_stylesheet
from src.service.encode.encode_job import EncodeSettings

SECTION_TITLE: str = "인코딩으로 바뀌는 항목"
SECTION_SUBTITLE: str = "중복 포함, 해당 영상만 표시"
TAG_NOTICES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "결과에 인코딩 태그를 기록합니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_HIDDEN: "결과에 숨은 기록만 남깁니다. 탐색기 태그 칸에는 보이지 않습니다. 설정에서 바꿀 수 있습니다.",
    ENCODE_TAG_MODE_OFF: (
        "결과에 인코딩 태그를 기록하지 않습니다. 이미 인코딩한 영상은 인코딩 이력으로만 가려냅니다. "
        "설정에서 바꿀 수 있습니다."
    ),
}
TAG_RECORD_LINES: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "이 방식에서는 탐색기 태그와 숨은 기록, 인코딩 이력이 남습니다.",
    ENCODE_TAG_MODE_HIDDEN: "이 방식에서는 숨은 기록과 인코딩 이력이 남습니다. 탐색기 태그는 남지 않습니다.",
    ENCODE_TAG_MODE_OFF: "이 방식에서는 인코딩 이력만 남습니다. 탐색기 태그와 숨은 기록은 남지 않습니다.",
}
TAG_CHIP_TEXTS: dict[str, str] = {
    ENCODE_TAG_MODE_VISIBLE: "인코딩 태그 {tag_name}",
    ENCODE_TAG_MODE_HIDDEN: "인코딩 태그 숨은 기록만",
    ENCODE_TAG_MODE_OFF: "인코딩 태그 기록 안 함",
}
TAG_TERM_LINES: tuple[str, ...] = (
    "인코딩 태그: 이 프로그램이 인코딩한 결과임을 알리는 표시입니다. 같은 영상을 다시 인코딩하지 않도록 막는 데 씁니다.",
    "탐색기 태그: 파일 속성 창(Alt+Enter)의 태그 칸에 보이는 키워드입니다. 현재 이름은 {tag_name}입니다. 탐색기에서 검색하고 묶어 볼 수 있습니다.",
    "숨은 기록: 파일 안에 남기는 보이지 않는 기록입니다. 인코더, 품질값, 시각, 프로그램 버전이 들어 있으며, 탐색기에서 태그를 고쳐도 남습니다.",
    "인코딩 이력: 이 PC의 프로그램 데이터에 항상 남는 기록입니다.",
)
WARNING_TEMPLATE: str = "원본 {count}개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다."
WARNING_ICON_NAME: str = "alert-triangle"
WARNING_ICON_SIZE: int = 18
SUMMARY_ICON_SIZE: int = 16
BUTTON_PX: int = 28
BRIEF_CARD_RADIUS_PX: int = 8
BRIEF_CARD_MARGIN_H_PX: int = 10
BRIEF_CARD_MARGIN_V_PX: int = 8
BRIEF_CARD_SPACING_PX: int = 6
BRIEF_TILE_MARGIN_H_PX: int = 14
BRIEF_TILE_MARGIN_V_PX: int = 6
BRIEF_CONTENT_SPACING_PX: int = 12
BRIEF_NET_VALUE_PX: int = 20
FINAL_NET_SAVED_CAPTION: str = "최종 순 절약"
BRIEF_NET_TIP: str = "이번 인코딩으로 줄어들 것으로 예상하는 용량입니다. 음수는 오히려 늘어남을 뜻합니다."
BRIEF_TARGET_TIP: str = "이번 인코딩 대상 영상 수와, 대상 영상의 현재 용량 합계 및 판정 단계의 예상 용량입니다."
BRIEF_ENCODER_TIP: str = (
    "이번 배치에 쓸 목표 인코딩 방식과 화질 기준값입니다. 환경 설정의 인코딩 탭에서 바꿀 수 있습니다."
)
SETTINGS_CHIP_TIP: str = "환경 설정의 인코딩 탭을 엽니다."
DESIGNATION_SUMMARY_TIP: str = "이번 배치 대상을 용량 절약과 변환 우선으로 나눈 개수입니다."
ATTENTION_ACCESSIBLE_NAME: str = "결과 탭 열기"
ATTENTION_TIP: str = "결과 탭으로 이동해 주의가 필요한 영상을 확인합니다."
RESULT_TAB_ICON: str = "file-text"
SETTINGS_LINK_TEXT: str = "인코딩 설정"


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


class EncodeSummaryView(QWidget):
    """요약 탭 한 페이지. 시작 전 브리핑과 진행/완료 상태를 한 위젯에서 갱신"""

    settings_requested = pyqtSignal()
    results_requested = pyqtSignal()
    status_requested = pyqtSignal(str)

    def __init__(
        self,
        media: SummaryMedia,
        parent: QWidget | None = None,
        *,
        get_shortcuts: Callable[[], Mapping[str, str]],
    ) -> None:
        super().__init__(parent)
        self._media: SummaryMedia = media
        self._palette: ThemePalette | None = None
        self._plan: EncodeStartPlan | None = None
        self._status_chips: dict[str, FilterChip] = {}
        self._net_saved_grew: bool = False
        self._target_text: str = ""
        self._size_change_text: str = ""
        self._designation_tip_suffix: str = ""
        (
            self.brief_card,
            self.brief_tile,
            self.brief_net_caption,
            self.brief_net_value,
            self.brief_target_line,
            self.brief_encoder_line,
            self.designation_line,
            self.designation_caution,
            self.btn_settings,
        ) = self._build_brief_card()
        self.live_saving = self._build_live_label()
        self.live_speed = self._build_live_label()
        self.status_host, self.status_flow = self._build_chip_host()
        self.attention, self.lbl_attention_icon, self.lbl_attention, self.btn_attention = self._build_attention()
        self.warning_box, self.lbl_warning_icon, self.lbl_warning = self._build_warning_box()
        self.tag_label = self._build_tag_label()
        self.tree_head, self.lbl_section, self.lbl_subtitle, self.badge = self._build_tree_head()
        self.tree = EncodeImpactTree(self._media, self, get_shortcuts=get_shortcuts)
        self.tree.setVisible(False)
        self._layout_body()

    def _layout_body(self) -> None:
        """브리핑 카드, 상태 칩, 경고, 태그, 트리를 위에서 아래로 배치"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
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
        ):
            layout.addWidget(widget)
        self.tree_head.setVisible(False)

    def _build_live_label(self) -> QLabel:
        """진행 중과 완료 후에만 보이는 한 줄 값 라벨"""
        label = QLabel(self)
        label.setObjectName("EncodeSummaryLive")
        label.setWordWrap(True)
        label.setVisible(False)
        return label

    def _build_chip_host(self) -> tuple[QWidget, FlowLayout]:
        """칩을 담는 칸과 칸 폭에 맞춘 흐름 배치. 칩이 없으면 숨김"""
        host = QWidget(self)
        host.setVisible(False)
        return host, FlowLayout(host)

    def _build_tag_label(self) -> QLabel:
        """인코딩 태그 안내 문장 라벨"""
        label = QLabel(self)
        label.setWordWrap(True)
        return label

    def _build_tree_head(self) -> tuple[QWidget, QLabel, QLabel, QLabel]:
        """영상 변환 트리 제목 줄: 제목, 부제, 진행 배지"""
        head = QWidget(self)
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        section = QLabel(SECTION_TITLE, head)
        section.setObjectName("GroupHeaderTitle")
        subtitle = QLabel(SECTION_SUBTITLE, head)
        subtitle.setObjectName("EncodeSummarySubtitle")
        badge = QLabel(head)
        badge.setObjectName("EncodeSummaryBadge")
        row.addWidget(section)
        row.addWidget(subtitle)
        row.addStretch(1)
        row.addWidget(badge)
        return head, section, subtitle, badge

    def _build_attention(self) -> tuple[QFrame, QLabel, QLabel, QPushButton]:
        """주의 필요 배너와 그 안의 아이콘, 문구, 결과 탭 버튼을 만듦"""
        banner = QFrame(self)
        banner.setObjectName("EncodeAttentionBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(6)
        icon = QLabel(banner)
        icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        text = QLabel(banner)
        text.setWordWrap(True)
        button = create_icon_button(
            banner,
            ATTENTION_TIP,
            self.results_requested.emit,
            accessible_name=ATTENTION_ACCESSIBLE_NAME,
            fixed_size=BUTTON_PX,
        )
        set_button_icon(button, RESULT_TAB_ICON, pad_right=0)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        row.addWidget(button)
        banner.setVisible(False)
        return banner, icon, text, button

    def _build_brief_card(self) -> tuple[QFrame, QFrame, QLabel, QLabel, QLabel, QLabel, QLabel, QLabel, QPushButton]:
        """배치 요약 카드. 제목 줄 아래 한 줄에 강조 타일 하나와 보조 두 줄을 나란히 둠"""
        card = QFrame(self)
        card.setObjectName("EncodeBriefCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(
            BRIEF_CARD_MARGIN_H_PX, BRIEF_CARD_MARGIN_V_PX, BRIEF_CARD_MARGIN_H_PX, BRIEF_CARD_MARGIN_V_PX
        )
        outer.setSpacing(BRIEF_CARD_SPACING_PX)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel(BRIEF_TITLE, card)
        title.setObjectName("GroupHeaderTitle")
        btn_settings = QPushButton(SETTINGS_LINK_TEXT, card)
        btn_settings.setObjectName("EncodeSettingsChip")
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.setAccessibleName(SETTINGS_LINK_TEXT)
        btn_settings.setToolTip(SETTINGS_CHIP_TIP)
        btn_settings.clicked.connect(self.settings_requested.emit)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(btn_settings)
        outer.addLayout(head)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(BRIEF_CONTENT_SPACING_PX)
        tile, net_caption, net_value = self._brief_net_tile(card)
        lines = QVBoxLayout()
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(2)
        target_line_label = self._brief_secondary(label_parent=card, tip=BRIEF_TARGET_TIP)
        encoder_line_label = self._brief_secondary(label_parent=card, tip=BRIEF_ENCODER_TIP)
        designation_label = self._brief_secondary(label_parent=card, tip=DESIGNATION_SUMMARY_TIP)
        designation_caution = QLabel(card)
        designation_caution.setObjectName("EncodeBriefCaution")
        designation_caution.setWordWrap(True)
        designation_caution.setTextFormat(Qt.TextFormat.PlainText)
        designation_caution.setVisible(False)
        lines.addStretch(1)
        lines.addWidget(target_line_label)
        lines.addWidget(encoder_line_label)
        lines.addWidget(designation_label)
        lines.addWidget(designation_caution)
        lines.addStretch(1)
        content.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
        content.addLayout(lines, 1)
        outer.addLayout(content)
        return (
            card,
            tile,
            net_caption,
            net_value,
            target_line_label,
            encoder_line_label,
            designation_label,
            designation_caution,
            btn_settings,
        )

    def _brief_net_tile(self, parent: QWidget) -> tuple[QFrame, QLabel, QLabel]:
        """강조 타일 하나. 예상 순 절약 이름과 큰 굵은 값을 가운데에 둠"""
        tile = QFrame(parent)
        tile.setObjectName("EncodeBriefTile")
        tile.setToolTip(BRIEF_NET_TIP)
        column = QVBoxLayout(tile)
        column.setContentsMargins(
            BRIEF_TILE_MARGIN_H_PX, BRIEF_TILE_MARGIN_V_PX, BRIEF_TILE_MARGIN_H_PX, BRIEF_TILE_MARGIN_V_PX
        )
        column.setSpacing(0)
        caption_label = QLabel(BRIEF_NET_CAPTION, tile)
        caption_label.setObjectName("EncodeBriefCaption")
        caption_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        value_label = QLabel(tile)
        value_label.setObjectName("EncodeBriefValue")
        value_label.setWordWrap(True)
        font = QFont(value_label.font())
        font.setPixelSize(BRIEF_NET_VALUE_PX)
        font.setBold(True)
        value_label.setFont(font)
        for label in (caption_label, value_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setToolTip(BRIEF_NET_TIP)
        column.addWidget(caption_label, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(value_label, 0, Qt.AlignmentFlag.AlignHCenter)
        return tile, caption_label, value_label

    def _brief_secondary(self, *, label_parent: QWidget, tip: str) -> QLabel:
        """타일 옆 보조 줄. 좁은 폭에서 줄바꿈해 가로 스크롤을 만들지 않음"""
        label = QLabel(label_parent)
        label.setObjectName("EncodeBriefLine")
        label.setWordWrap(True)
        label.setToolTip(tip)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _build_warning_box(self) -> tuple[QFrame, QLabel, QLabel]:
        """원본 교체 경고 상자와 그 안의 아이콘, 문구를 만듦"""
        box = QFrame(self)
        box.setObjectName("EncodeWarningBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        icon = QLabel(box)
        icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        text = QLabel(box)
        text.setObjectName("WarningHintLabel")
        text.setWordWrap(True)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        box.setVisible(False)
        return box, icon, text

    def set_plan(self, plan: EncodeStartPlan) -> None:
        """시작 전 브리핑으로 되돌리고 새 배치 값을 채움"""
        self._plan = plan
        values = brief_values(
            plan.evaluations,
            baseline_quality=plan.baseline_quality,
            encoder_label=plan.target_codec_label,
            result_policy_label=result_policy_label(plan.settings.result_policy),
        )
        self._set_brief(values, plan.evaluations)
        self.brief_card.setVisible(True)
        self.tree.set_summary(plan.summary, plan.evaluations, plan.root_dir, plan.target_codec_label)
        has_items = plan.summary.unknown_count > 0 or any(bucket.count > 0 for bucket in plan.summary.buckets)
        self.tree.setVisible(has_items)
        self.tree_head.setVisible(has_items)
        self._set_tag(plan.settings)
        self._set_warning(plan.settings, len(plan.evaluations))
        self._update_settings_tip(plan)
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

    def set_live(
        self,
        items: Sequence[tuple[str, EncodeState, str]],
        results: Mapping[str, ResultRow],
        speed_summary: str,
        average_speed: float | None,
        elapsed_text: str,
        *,
        finished: bool,
    ) -> None:
        """진행 중 또는 완료 후 상태 분포, 절약 대조, 속도, 주의 안내, 진행 배지를 갱신

        - 완료 후에는 원본 교체 경고를 숨기고 최종 성적에 최종 용량 변화를 함께 씀
        """
        counts = status_counts(items)
        self._refresh_status_chips(counts)
        self.status_host.setVisible(True)
        actual_mb, expected_mb = saved_totals(results.values())
        size_change = total_size_change_text(results.values()) if finished else ""
        self.live_saving.setText(saving_text(actual_mb, expected_mb, finished=finished, size_change=size_change))
        self.live_speed.setText(
            final_speed_text(elapsed_text, speed_text(average_speed)) if finished else speed_summary
        )
        self.live_saving.setVisible(True)
        self.live_speed.setVisible(True)
        text = attention_text(counts)
        self.attention.setVisible(bool(text))
        self.lbl_attention.setText(text)
        if finished:
            self.warning_box.setVisible(False)
        self._set_net_saved(finished, results.values())
        if self._plan is not None:
            self.badge.setText(done_badge(done_count(items), target_count(items)))
            self.badge.setVisible(True)

    @property
    def target_text(self) -> str:
        """브리핑 대상 줄에 보이는 대상 수 문구(예: 12개, 1/2개)"""
        return self._target_text

    def set_excluded_count(self, included: int, total: int) -> None:
        """시작 전 대기열에서 제외한 영상 수를 반영해 대상 수를 다시 표시"""
        self._target_text = f"{total}개" if included >= total else f"{included}/{total}개"
        self.brief_target_line.setText(target_line(self._target_text, self._size_change_text))
        self._apply_target_tip()

    def _apply_target_tip(self) -> None:
        """대상 줄 툴팁을 지금 포함 대상의 직접 지정 개수까지 반영해 다시 씀"""
        self.brief_target_line.setToolTip(
            f"{BRIEF_TARGET_TIP}\n{self._designation_tip_suffix}"
        )

    def compare_current(self) -> None:
        """현재 트리 영상의 화질 비교를 요청"""
        self.tree.compare_current()

    def apply_theme(self, palette: ThemePalette) -> None:
        """팔레트 토큰으로 카드, 목록, 칩, 설정 링크를 칠하고 경고 아이콘을 다시 그림"""
        self._palette = palette
        self.setStyleSheet(build_stylesheet(palette))
        icon = get_icon(WARNING_ICON_NAME, color=palette.fg_maxrate_warn, size=WARNING_ICON_SIZE)
        pixmap = icon.pixmap(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        self.lbl_warning_icon.setPixmap(pixmap)
        self.lbl_attention_icon.setPixmap(pixmap)
        set_button_icon(self.btn_attention, RESULT_TAB_ICON, palette=palette, size=SUMMARY_ICON_SIZE, pad_right=0)
        self.btn_settings.setStyleSheet(_settings_chip_style(palette))
        for chip in self._status_chips.values():
            chip.apply_palette(palette)
        self._paint_net_saved()

    def _set_brief(self, values: BriefValues, evaluations: tuple[VideoEvaluation, ...]) -> None:
        """브리핑 타일 이름과 값, 보조 두 줄을 채움"""
        self._target_text = values.target
        self._size_change_text = values.size_change
        self.brief_net_caption.setText(BRIEF_NET_CAPTION)
        self.brief_net_value.setText(values.net_saved)
        self.brief_target_line.setText(target_line(values.target, values.size_change))
        self.brief_encoder_line.setText(values.encoder)
        counts = designation_counts(evaluations)
        self._designation_tip_suffix = designation_summary_line(counts)
        self._apply_target_tip()
        self.designation_line.setText(designation_line(counts))
        self.designation_line.setVisible(bool(designation_line(counts)))
        caution = designation_caution_line(counts)
        self.designation_caution.setText(caution)
        self.designation_caution.setVisible(bool(caution))
        # - 다시 시작한 배치는 완료 후 바뀐 타일 이름과 값을 예상값으로 되돌림
        self._net_saved_grew = values.net_saved_grew
        self._paint_net_saved()

    def _set_net_saved(self, finished: bool, rows: Iterable[ResultRow]) -> None:
        """완료 후에는 순 절약 타일을 최종 값으로 바꾸고, 그 밖에는 예상 값을 그대로 둠"""
        completed = list(rows)
        if not finished or not completed:
            return
        actual_mb, _expected_mb = saved_totals(completed)
        delta_mb = 0.0 if actual_mb is None else actual_mb
        self.brief_net_caption.setText(FINAL_NET_SAVED_CAPTION)
        self.brief_net_value.setText(delta_text(delta_mb))
        self._net_saved_grew = net_saved_grew(delta_mb)
        self._paint_net_saved()

    def _paint_net_saved(self) -> None:
        """순 절약 타일 값만 절약 초록색 또는 증가 주의색으로 칠함"""
        palette = self._palette
        if palette is None:
            return
        color = palette.fg_maxrate_warn if self._net_saved_grew else palette.fg_saved_pos
        self.brief_net_value.setStyleSheet(f"color: {color};")
        self._paint_designation_caution()

    def _paint_designation_caution(self) -> None:
        """직접 지정 주의 줄을 주의색으로 칠함. 팔레트가 없으면 문구만 둠"""
        palette = self._palette
        if palette is None:
            return
        self.designation_caution.setStyleSheet(f"color: {palette.fg_maxrate_warn};")

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

    def _update_settings_tip(self, plan: EncodeStartPlan) -> None:
        """설정 링크 버튼의 툴팁에 지금 설정 값을 요약해 넣음"""
        tag_text = TAG_CHIP_TEXTS[plan.settings.encode_tag_mode].format(tag_name=plan.settings.encode_tag_name)
        self.btn_settings.setToolTip(
            SETTINGS_CHIP_TIP
            + f"\n화질 기준값 {plan.baseline_quality:.1f}, 목표 인코딩 방식 {plan.target_codec_label}, "
            + f"{result_policy_label(plan.settings.result_policy)}, {tag_text}"
        )

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


def _settings_chip_style(palette: ThemePalette) -> str:
    """설정 요약 칩 스타일"""
    return (
        f"QPushButton#EncodeSettingsChip {{ background: {palette.bg_elevated};"
        f" border: 1px solid {palette.border_subtle}; border-radius: 10px;"
        f" color: {palette.text_secondary}; padding: 3px 10px; }}"
        f"QPushButton#EncodeSettingsChip:hover {{ background: {palette.bg_hover};"
        f" border: 1px solid {palette.accent_blue}; color: {palette.text_primary}; }}"
    )
