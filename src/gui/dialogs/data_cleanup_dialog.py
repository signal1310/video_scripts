"""통합 데이터 정리 및 초기화 대화상자 모듈

- 4범주 19개 항목을 설정 카드와 접이식 범주로 묶고, 항목별 건수와 용량을 배지로 미리 보여 줌
- 정리 범위(현재 작업 폴더/전체 저장 데이터)를 바꾸면 저장 데이터 건수는 백그라운드에서 다시 읽어 도착하면 채움
- 화면과 설정 항목의 변경 여부는 Qt 위젯을 읽어야 하므로 화면 스레드에서 계산해 넘겨받음
- 설정 범주를 고르면 되돌릴 수 없음을 아이콘과 경고 상자로 알리고 실행 전에 한 번 더 확인
- 원본 영상 파일은 어떤 항목에서도 지우지 않고, 임시 파일은 휴지통으로 옮긴다는 안내를 항상 표시
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_progress_values import bytes_to_mb
from src.core.formatting import format_size_mb
from src.gui.dialogs.data_cleanup_preview_loader import PreviewLoader
from src.gui.dialogs.encode_flow_layout import FlowLayout
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet, set_state_property
from src.gui.theme import ThemeAwareMixin
from src.service.data_cleanup_service import (
    ALL_CLEANUP_ITEM_KEYS,
    CATEGORY_CLEANUP_ITEM_KEYS,
    CLEANUP_CATEGORY_LABELS,
    CLEANUP_ITEM_LABELS,
    CLEANUP_PRESET_LABELS,
    CLEANUP_PRESETS,
    CLEANUP_SCOPE_ALL,
    CLEANUP_SCOPE_CURRENT,
    CLEANUP_SCOPE_LABELS,
    IRREVERSIBLE_CATEGORY,
    STORAGE_CLEANUP_ITEM_KEYS,
    UI_RESET_CLEANUP_ITEM_KEYS,
    CleanupCategory,
    CleanupItemKey,
    CleanupItemPreview,
    CleanupPresetKey,
    CleanupScope,
    DataCleanupService,
    StoragePreviewFn,
    cleanup_item_category,
)

UiPreviewFn = Callable[[], Mapping[CleanupItemKey, CleanupItemPreview]]
ConfirmFn = Callable[[], bool]

WINDOW_TITLE: str = "데이터 정리 및 초기화"
DIALOG_OBJECT_NAME: str = "SettingsDialog"
DIALOG_DEFAULT_WIDTH: int = 720
DIALOG_DEFAULT_HEIGHT: int = 660
DIALOG_MIN_WIDTH: int = 520
DIALOG_MIN_HEIGHT: int = 460
SIZE_DECIMALS: int = 1
WARNING_ICON_SIZE: int = 18
WARNING_ICON_NAME: str = "alert-triangle"
SAFETY_ICON_NAME: str = "info"
EXECUTE_PRIMARY_OBJECT_NAME: str = "PrimaryButton"
EXECUTE_DANGER_OBJECT_NAME: str = "DangerButton"
EMPTY_BADGE_TEXT: str = "0건"
LOADING_BADGE_TEXT: str = "불러오는 중..."
BADGE_COUNT_TEMPLATE: str = "{count}건"
BADGE_SIZE_TEMPLATE: str = "{count}건 / {size}"
BADGE_TOOLTIP: str = "선택한 범위에서 이 항목을 정리할 때 지우는 기록 건수와 파일 용량입니다."
SELECT_ALL_TEXT: str = "범주 전체 선택"
SELECT_ALL_TOOLTIP: str = "이 범주의 항목을 모두 선택하거나 모두 해제합니다."
EXECUTE_TEXT: str = "선택 항목 정리"
EXECUTE_TOOLTIP: str = "선택한 항목을 정리합니다. 설정 및 규칙, 인코딩 완료 이력, 영상별 직접 지정을 고르면 실행 전에 확인 창을 한 번 거칩니다."
CLOSE_TEXT: str = "닫기"
CLOSE_TOOLTIP: str = "아무것도 정리하지 않고 창을 닫습니다."
SCOPE_HEADING: str = "정리 범위"
PRESET_HEADING: str = "빠른 프리셋"
ITEMS_HEADING: str = "정리할 항목"
PRESET_PILL_OBJECT_NAME: str = "DataCleanupPresetPill"
PRESET_PILL_ICON_SIZE: int = 14
PRESET_ACTIVE_PROPERTY: str = "presetActive"
PRESET_WARNING_PROPERTY: str = "presetWarning"
CURRENT_SCOPE_TOOLTIP: str = "현재 작업 폴더 안의 영상에 해당하는 기록과 캐시만 정리합니다."
CURRENT_SCOPE_DISABLED_TOOLTIP: str = "작업 폴더를 먼저 선택하면 현재 작업 폴더만 정리할 수 있습니다."
ALL_SCOPE_TOOLTIP: str = "저장된 모든 작업 폴더의 기록과 캐시, 화면 배치, 설정을 대상으로 합니다."
CURRENT_SCOPE_HINT_TEMPLATE: str = "현재 작업 폴더: {root}"
ALL_SCOPE_HINT: str = "이 PC에 저장된 모든 작업 기록, 캐시, 화면 배치를 대상으로 합니다."
NO_ROOT_HINT: str = "작업 폴더가 선택되지 않아 현재 작업 폴더 범위를 쓸 수 없습니다."
SAFETY_NOTE_TEXT: str = (
    "원본 영상 파일은 어떤 항목을 선택해도 삭제되지 않으며, 임시 파일(.part)은 휴지통으로 옮겨집니다. "
    "휴지통이 없는 드라이브의 임시 파일은 지우지 않고 남깁니다."
)
SAFETY_NOTE_TOOLTIP: str = (
    "정리 항목은 프로그램이 만든 기록, 캐시, 설정만 지웁니다.\n"
    "영상 원본과 인코딩 결과 파일은 이 창에서 지우지 않습니다.\n"
    "휴지통을 쓸 수 없는 네트워크, 이동식 드라이브에 있는 임시 파일은 그대로 남깁니다."
)
IRREVERSIBLE_WARNING_TEXT: str = "설정은 초기화 후 되돌릴 수 없습니다."
IRREVERSIBLE_WARNING_ACCESSIBLE_NAME: str = "되돌릴 수 없는 설정 초기화 경고"
CONFIRM_TITLE: str = "데이터 정리 확인"
CONFIRM_SETTINGS_TEXT_TEMPLATE: str = "설정 및 규칙 {count}개 항목을 초기화합니다. 초기화한 설정은 되돌릴 수 없습니다."
CONFIRM_HISTORY_TEXT: str = "인코딩 완료 이력을 지웁니다. 지운 기록은 되돌릴 수 없습니다."
CONFIRM_TARGETS_TEXT: str = "영상별 출력 해상도 직접 지정을 지웁니다. 지운 지정은 되돌릴 수 없습니다."
CONFIRM_QUESTION_TEXT: str = "계속할까요?"
CONFIRM_ITEM_KEYS: tuple[CleanupItemKey, ...] = ("encode_output_history", "manual_targets")
CATEGORY_SUBTITLES: dict[CleanupCategory, str] = {
    "encode": "(기록과 임시 파일)",
    "analysis": "(캐시와 영상별 지정)",
    "layout": "(표와 창 배치)",
    "settings": "(되돌릴 수 없음)",
}
# - 범주와 프리셋 순서는 서비스 선언 순서를 그대로 따름(화면 순서와 프리셋 구성의 기준 정의 하나)
CATEGORY_ORDER: tuple[CleanupCategory, ...] = tuple(CATEGORY_CLEANUP_ITEM_KEYS)
PRESET_ORDER: tuple[CleanupPresetKey, ...] = tuple(CLEANUP_PRESETS)
PRESET_WARNING_KEY: CleanupPresetKey = "reset_all"
PRESET_ICONS: dict[CleanupPresetKey, str] = {
    "part_only": "trash", "retry_encode": "rotate-ccw", "frame_only": "bar-chart",
    "reanalyze_fresh": "search", "reset_layout": "panel-top-open", "reset_all": "alert-triangle",
}
PRESET_TOOLTIPS: dict[CleanupPresetKey, str] = {
    "part_only": "분류: 인코딩 기록 및 임시 파일\n중간에 끊긴 인코딩이 남긴 .part 임시 파일을 휴지통으로 옮깁니다. 휴지통이 없는 드라이브의 파일은 남깁니다.",
    "retry_encode": "분류: 인코딩 기록 및 임시 파일\n임시 파일, 이전 폐기 기록, 인코딩 완료 이력을 지워 같은 영상을 다시 인코딩 대상으로 되돌립니다.",
    "frame_only": "분류: 분석 캐시 및 영상별 지정\n화질 분석과 검은 여백 감지 결과를 지워 다음 분석 때 다시 계산합니다.",
    "reanalyze_fresh": "분류: 분석 캐시 및 영상별 지정\n영상 분석 캐시, 화질 분석 캐시, 영상별 출력 해상도, 검은 여백 제외, 수동 VR 보호 지정을 지웁니다.",
    "reset_layout": "분류: 화면 배치 및 기록\n표 열 순서와 너비, 표시 열, 정렬과 그룹, 창 크기와 위치, 최근 폴더 기억을 기본값으로 되돌립니다.",
    "reset_all": "위의 모든 항목과 설정, 단축키, 예외 규칙까지 기본값으로 되돌립니다. 설정은 되돌릴 수 없습니다.",
}
ITEM_TOOLTIPS: dict[CleanupItemKey, str] = {
    "part_files": "인코딩이 중간에 끝나 남은 임시 .part 파일을 휴지통으로 옮깁니다. 휴지통이 없는 드라이브의 파일은 남깁니다.",
    "discarded_history": "절약 효과가 없어 폐기한 인코딩 기록을 지웁니다. 폐기한 영상 파일은 그대로 남습니다.",
    "encode_output_history": "인코딩 결과 지문과 완료 기록을 지워 같은 영상을 다시 대상으로 되돌립니다.",
    "probe_cache": "영상 정보(길이, 해상도, 비트레이트)를 측정해 둔 분석 캐시를 지웁니다. 다음 분석에서 영상을 다시 읽습니다.",
    "frame_cache": "화질 분석과 검은 여백 감지 결과 캐시를 지웁니다.",
    "manual_targets": "영상별로 직접 지정한 출력 해상도 지정을 모두 해제합니다.",
    "crop_exclusions": "검은 여백 잘라내기에서 제외한 영상 지정을 모두 해제합니다.",
    "vr_overrides": "수동으로 VR 보호를 켜 둔 영상 지정을 모두 해제합니다.",
    "column_order": "표 열 순서를 기본 순서로 되돌립니다.",
    "column_widths": "표 열 너비를 기본값으로 되돌립니다.",
    "column_visibility": "표에 보이던 열과 표시 프리셋을 기본값으로 되돌립니다.",
    "table_sort_group": "표의 정렬, 그룹, 필터 조건을 모두 지웁니다.",
    "window_geometry": "메인 창 크기와 위치, 상단 영역 접힘 상태를 기본값으로 되돌립니다.",
    "recent_dirs_selection": "최근 작업 폴더 목록과 표에서 마지막으로 고른 영상 기억을 지웁니다.",
    "criteria_settings": "판별 기준 설정을 기본값으로 되돌립니다. 되돌릴 수 없습니다.",
    "encode_settings": "인코딩 설정을 기본값으로 되돌립니다. 되돌릴 수 없습니다.",
    "general_display_settings": "일반, 표시, 해상도 분석 설정을 기본값으로 되돌립니다. 되돌릴 수 없습니다.",
    "shortcuts_settings": "직접 지정한 단축키를 모두 지우고 기본 단축키로 되돌립니다. 되돌릴 수 없습니다.",
    "exception_rules": "등록한 예외 규칙 목록을 모두 지웁니다. 되돌릴 수 없습니다.",
}
ITEM_TOOLTIP_SUFFIX: str = "원본 영상 파일과 인코딩 결과 파일은 삭제되지 않습니다."


def item_tooltip(key: CleanupItemKey) -> str:
    """항목 툴팁: 그 항목이 지우는 것과 원본 영상이 안전하다는 안내"""
    return f"{ITEM_TOOLTIPS[key]} {ITEM_TOOLTIP_SUFFIX}"


def item_badge_text(preview: CleanupItemPreview | None) -> str:
    """항목 미리보기를 화면 배지 문구로 바꿈"""
    if preview is None or preview.count <= 0:
        return EMPTY_BADGE_TEXT
    if preview.size_bytes <= 0:
        return BADGE_COUNT_TEMPLATE.format(count=preview.count)
    return BADGE_SIZE_TEMPLATE.format(
        count=preview.count,
        size=format_size_mb(bytes_to_mb(preview.size_bytes), SIZE_DECIMALS, unit="auto"),
    )


def is_irreversible_key(key: CleanupItemKey) -> bool:
    """되돌릴 수 없는 설정 범주 항목인지"""
    return cleanup_item_category(key) == IRREVERSIBLE_CATEGORY


def needs_confirm_key(key: CleanupItemKey) -> bool:
    """실행 전 확인 창을 한 번 더 거쳐야 하는 항목인지(설정 범주, 완료 이력, 영상별 직접 지정)"""
    return is_irreversible_key(key) or key in CONFIRM_ITEM_KEYS


def confirm_text(keys: Sequence[CleanupItemKey]) -> str:
    """확인 창 본문: 고른 항목 종류별 문장을 모아 한 상자에 담음"""
    settings_count = sum(1 for key in keys if is_irreversible_key(key))
    lines = [CONFIRM_SETTINGS_TEXT_TEMPLATE.format(count=settings_count)] if settings_count else []
    kinds = (("encode_output_history", CONFIRM_HISTORY_TEXT), ("manual_targets", CONFIRM_TARGETS_TEXT))
    lines.extend(text for key, text in kinds if key in keys)
    return "\n".join([*lines, CONFIRM_QUESTION_TEXT])


def _empty_ui_preview() -> dict[CleanupItemKey, CleanupItemPreview]:
    """화면/설정 항목의 빈 미리보기. 화면 상태 확인 함수를 받지 못했을 때 씀"""
    return {key: CleanupItemPreview(key, 0, 0, True) for key in UI_RESET_CLEANUP_ITEM_KEYS}


def _empty_storage_preview(_scope: CleanupScope) -> dict[CleanupItemKey, CleanupItemPreview]:
    """저장 데이터 미리보기를 받지 못했을 때 쓰는 빈 미리보기. 아무것도 지우지 않음"""
    return {key: CleanupItemPreview(key, 0, 0, True) for key in STORAGE_CLEANUP_ITEM_KEYS}


@dataclass(frozen=True, slots=True)
class DataCleanupRequest:
    """정리 실행 요청: 정리 범위와 고른 항목 목록"""

    scope: CleanupScope
    keys: tuple[CleanupItemKey, ...]

    @property
    def irreversible_keys(self) -> tuple[CleanupItemKey, ...]:
        """되돌릴 수 없는 설정 범주 항목만 골라 반환"""
        return tuple(key for key in self.keys if is_irreversible_key(key))

    @property
    def confirm_keys(self) -> tuple[CleanupItemKey, ...]:
        """실행 전 확인을 받아야 하는 항목만 골라 반환"""
        return tuple(key for key in self.keys if needs_confirm_key(key))


class DataCleanupDialog(QDialog, ThemeAwareMixin):
    """정리 범위, 빠른 프리셋, 4범주 19개 항목을 한 화면에서 고르는 대화상자

    - 창은 저장 데이터를 읽기 전에 먼저 뜨고, 건수와 용량은 백그라운드 결과가 도착하면 채움
    - 불러오는 동안 실행 버튼을 잠가 화면에 보이는 건수와 실행이 어긋나지 않게 함
    """

    def __init__(
        self,
        *,
        storage_preview_fn: StoragePreviewFn | None = None,
        ui_preview_fn: UiPreviewFn | None = None,
        service: DataCleanupService | None = None,
        current_root: str = "",
        has_current_root: bool | None = None,
        confirm_fn: ConfirmFn | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(DIALOG_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(DIALOG_DEFAULT_WIDTH, DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT)

        self._current_root: str = current_root
        self._has_current_root: bool = bool(current_root.strip()) if has_current_root is None else has_current_root
        self._storage_preview_fn: StoragePreviewFn = self._resolve_storage_preview_fn(storage_preview_fn, service)
        self._ui_preview_fn: UiPreviewFn = ui_preview_fn if ui_preview_fn is not None else _empty_ui_preview
        self._confirm_fn: ConfirmFn = confirm_fn if confirm_fn is not None else self._ask_confirm
        self._previews: dict[CleanupItemKey, CleanupItemPreview] = {}
        self._loading: bool = False
        self._checkboxes: dict[CleanupItemKey, QCheckBox] = {}
        self._badges: dict[CleanupItemKey, QLabel] = {}
        self._select_all: dict[CleanupCategory, QCheckBox] = {}
        self._sections: dict[CleanupCategory, CollapsibleSection] = {}
        self._preset_buttons: dict[CleanupPresetKey, QPushButton] = {}
        self._scope: CleanupScope = CLEANUP_SCOPE_CURRENT if self._has_current_root else CLEANUP_SCOPE_ALL
        self._loader = PreviewLoader(self._storage_preview_fn, self)
        self._loader.ready.connect(self._on_storage_previews)
        self._loader.failed.connect(self._on_storage_preview_failed)

        scope_bar, self._radio_current, self._radio_all, self.lbl_scope_hint = self._build_scope_bar()
        self._preset_host = QWidget(self)
        self._preset_flow = FlowLayout(self._preset_host)
        preset_bar = self._build_preset_bar()
        self.warning_box, self.lbl_warning_icon = self._build_warning_box()
        safety_note, self.lbl_safety_icon, self.lbl_safety_text = self._build_safety_note()
        footer, self.btn_close, self.btn_execute = self._build_footer()
        self._assemble_ui(scope_bar, preset_bar, safety_note, footer)
        self._apply_keys(())
        self.set_scope(self._scope)
        self.init_theme_listener()

    def _resolve_storage_preview_fn(
        self, storage_preview_fn: StoragePreviewFn | None, service: DataCleanupService | None
    ) -> StoragePreviewFn:
        """주입한 저장 데이터 미리보기 함수를 우선 쓰고, 없으면 서비스나 빈 미리보기로 대체"""
        if storage_preview_fn is not None:
            return storage_preview_fn
        if service is not None:
            return lambda scope: service.preview(scope, self._current_root)
        return _empty_storage_preview


    def _assemble_ui(self, scope_bar: QWidget, preset_bar: QWidget, safety_note: QWidget, footer: QWidget) -> None:
        """범위, 프리셋, 항목 목록, 경고, 안내, 실행 버튼을 위에서 아래로 배치"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(scope_bar)
        layout.addWidget(preset_bar)

        heading = QLabel(ITEMS_HEADING, self)
        heading.setObjectName("GroupHeaderTitle")
        layout.addWidget(heading)
        layout.addWidget(self._build_items_area(), stretch=1)

        layout.addWidget(self.warning_box)
        layout.addWidget(safety_note)
        layout.addWidget(footer)

    def _build_scope_bar(self) -> tuple[QFrame, QRadioButton, QRadioButton, QLabel]:
        """정리 범위 라디오와 현재 작업 폴더 안내를 담은 카드 상자"""
        box = QFrame(self)
        box.setObjectName("SettingsCardBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        heading = QLabel(SCOPE_HEADING, box)
        heading.setObjectName("GroupHeaderTitle")
        layout.addWidget(heading)

        radio_row = QHBoxLayout()
        radio_row.setSpacing(14)
        radio_current = QRadioButton(CLEANUP_SCOPE_LABELS[CLEANUP_SCOPE_CURRENT], box)
        radio_all = QRadioButton(CLEANUP_SCOPE_LABELS[CLEANUP_SCOPE_ALL], box)
        radio_current.setToolTip(CURRENT_SCOPE_TOOLTIP)
        radio_all.setToolTip(ALL_SCOPE_TOOLTIP)
        radio_current.toggled.connect(partial(self._on_scope_toggled, CLEANUP_SCOPE_CURRENT))
        radio_all.toggled.connect(partial(self._on_scope_toggled, CLEANUP_SCOPE_ALL))
        if not self._has_current_root:
            radio_current.setEnabled(False)
            radio_current.setToolTip(CURRENT_SCOPE_DISABLED_TOOLTIP)
        radio_row.addWidget(radio_current)
        radio_row.addWidget(radio_all)
        radio_row.addStretch(1)
        layout.addLayout(radio_row)

        hint = QLabel("", box)
        hint.setObjectName("GroupHeaderSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box, radio_current, radio_all, hint

    def _build_preset_bar(self) -> QWidget:
        """프리셋 여섯 개를 아이콘과 이름이 든 작은 알약으로 흐르게 두는 빠른 선택 줄

        - 폭이 모자라면 다음 줄로 넘어가므로 좁은 창에서도 알약 글자가 잘리지 않음
        """
        bar = QWidget(self)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        heading = QLabel(PRESET_HEADING, bar)
        heading.setObjectName("GroupHeaderTitle")
        layout.addWidget(heading)
        for preset in PRESET_ORDER:
            label = CLEANUP_PRESET_LABELS[preset]
            pill = QPushButton(label, self._preset_host)
            pill.setObjectName(PRESET_PILL_OBJECT_NAME)
            pill.setAutoDefault(False)
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            pill.setIconSize(QSize(PRESET_PILL_ICON_SIZE, PRESET_PILL_ICON_SIZE))
            pill.setToolTip(PRESET_TOOLTIPS[preset])
            pill.setAccessibleName(f"{label} 프리셋")
            pill.setProperty(PRESET_WARNING_PROPERTY, "true" if preset == PRESET_WARNING_KEY else "false")
            pill.clicked.connect(partial(self.apply_preset, preset))
            self._preset_buttons[preset] = pill
            self._preset_flow.addWidget(pill)
        layout.addWidget(self._preset_host)
        return bar

    def _build_items_area(self) -> QScrollArea:
        """4범주 접이식 구역을 담은 스크롤 영역"""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(scroll)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(10)
        for category in CATEGORY_ORDER:
            section = self._build_category_section(category, body)
            body_layout.addWidget(section)
            self._sections[category] = section
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _build_category_section(self, category: CleanupCategory, parent: QWidget) -> CollapsibleSection:
        """범주 하나를 접이식 헤더, 카드 상자, 범주 전체 선택 행, 개별 항목 행으로 만듦"""
        section = CollapsibleSection(
            title=CLEANUP_CATEGORY_LABELS[category],
            subtitle=CATEGORY_SUBTITLES[category],
            is_expanded=True,
            parent=parent,
        )
        card = QFrame(section)
        card.setObjectName("SettingsCardBox")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        select_row = QFrame(card)
        select_row.setObjectName("SettingsCard")
        select_layout = QHBoxLayout(select_row)
        select_layout.setContentsMargins(10, 5, 8, 5)
        select_layout.setSpacing(8)
        select_all = QCheckBox(SELECT_ALL_TEXT, select_row)
        select_all.setToolTip(SELECT_ALL_TOOLTIP)
        select_all.clicked.connect(partial(self._on_select_all_clicked, category))
        select_layout.addWidget(select_all)
        select_layout.addStretch(1)
        card_layout.addWidget(select_row)
        self._select_all[category] = select_all

        rows: list[QWidget] = [select_row]
        for key in CATEGORY_CLEANUP_ITEM_KEYS[category]:
            row, checkbox, badge = self._build_item_row(key, card)
            card_layout.addWidget(row)
            rows.append(row)
            self._checkboxes[key] = checkbox
            self._badges[key] = badge
        for position, row in enumerate(rows):
            row.setProperty("is_first_row", "true" if position == 0 else "false")
            row.setProperty("is_last_row", "true" if position == len(rows) - 1 else "false")
        section.add_widget(card)
        return section

    def _build_item_row(self, key: CleanupItemKey, card: QWidget) -> tuple[QFrame, QCheckBox, QLabel]:
        """항목 하나를 체크박스, 항목 이름, 건수와 용량 배지 행으로 만듦"""
        row = QFrame(card)
        row.setObjectName("SettingsCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(8)

        checkbox = QCheckBox(CLEANUP_ITEM_LABELS[key], row)
        checkbox.setToolTip(item_tooltip(key))
        checkbox.toggled.connect(self._on_selection_changed)

        badge = QLabel(EMPTY_BADGE_TEXT, row)
        badge.setObjectName("GroupHeaderSubtitle")
        badge.setToolTip(BADGE_TOOLTIP)
        layout.addWidget(checkbox, stretch=1)
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return row, checkbox, badge

    def _build_warning_box(self) -> tuple[QFrame, QLabel]:
        """설정 범주를 골랐을 때만 보이는 되돌릴 수 없음 경고 상자"""
        box = QFrame(self)
        box.setObjectName("EncodeWarningBox")
        box.setAccessibleName(IRREVERSIBLE_WARNING_ACCESSIBLE_NAME)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        icon = QLabel(box)
        icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        text = QLabel(IRREVERSIBLE_WARNING_TEXT, box)
        text.setObjectName("WarningHintLabel")
        text.setWordWrap(True)
        layout.addWidget(icon)
        layout.addWidget(text, stretch=1)
        box.setVisible(False)
        return box, icon

    def _build_safety_note(self) -> tuple[QWidget, QLabel, QLabel]:
        """원본 영상 파일을 지우지 않는다는 안내 줄"""
        note = QWidget(self)
        layout = QHBoxLayout(note)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(6)
        icon = QLabel(note)
        icon.setFixedSize(WARNING_ICON_SIZE, WARNING_ICON_SIZE)
        text = QLabel(SAFETY_NOTE_TEXT, note)
        text.setObjectName("GroupHeaderSubtitle")
        text.setWordWrap(True)
        note.setToolTip(SAFETY_NOTE_TOOLTIP)
        text.setToolTip(SAFETY_NOTE_TOOLTIP)
        layout.addWidget(icon)
        layout.addWidget(text, stretch=1)
        return note, icon, text

    def _build_footer(self) -> tuple[QWidget, QPushButton, QPushButton]:
        """닫기와 선택 항목 정리 버튼 줄"""
        footer = QWidget(self)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)

        close_button = QPushButton(CLOSE_TEXT, footer)
        close_button.setAutoDefault(False)
        close_button.setToolTip(CLOSE_TOOLTIP)
        close_button.setAccessibleName("닫기")
        close_button.clicked.connect(self.reject)

        execute_button = QPushButton(EXECUTE_TEXT, footer)
        execute_button.setObjectName(EXECUTE_PRIMARY_OBJECT_NAME)
        execute_button.setAutoDefault(False)
        execute_button.setToolTip(EXECUTE_TOOLTIP)
        execute_button.setAccessibleName(EXECUTE_TEXT)
        execute_button.clicked.connect(self._on_execute)

        layout.addWidget(close_button)
        layout.addWidget(execute_button)
        return footer, close_button, execute_button

    def current_scope(self) -> CleanupScope:
        """지금 고른 정리 범위"""
        return self._scope

    def set_scope(self, scope: CleanupScope) -> None:
        """정리 범위를 바꾸고 미리보기를 다시 셈. 작업 폴더가 없으면 전체 범위로 맞춤"""
        target = (
            CLEANUP_SCOPE_CURRENT if scope == CLEANUP_SCOPE_CURRENT and self._has_current_root else CLEANUP_SCOPE_ALL
        )
        radio = self._radio_current if target == CLEANUP_SCOPE_CURRENT else self._radio_all
        if radio.isChecked():
            # - 이미 선택된 라디오는 신호를 내지 않으므로 같은 갱신을 직접 한 번 실행
            self._on_scope_toggled(target, True)
            return
        radio.setChecked(True)

    def _on_scope_toggled(self, scope: CleanupScope, checked: bool) -> None:
        """범위 선택이 바뀌면 안내 문구와 항목별 미리보기를 갱신"""
        if not checked:
            return
        self._scope = scope
        self._update_scope_hint()
        self.refresh_preview()

    def _update_scope_hint(self) -> None:
        """범위 안내 문구를 현재 범위와 작업 폴더 유무에 맞춤"""
        if not self._has_current_root:
            self.lbl_scope_hint.setText(NO_ROOT_HINT)
            self.lbl_scope_hint.setToolTip(NO_ROOT_HINT)
            return
        if self._scope == CLEANUP_SCOPE_CURRENT:
            text = CURRENT_SCOPE_HINT_TEMPLATE.format(root=self._current_root)
        else:
            text = ALL_SCOPE_HINT
        self.lbl_scope_hint.setText(text)
        self.lbl_scope_hint.setToolTip(text)

    def refresh_preview(self) -> None:
        """현재 범위의 건수를 다시 읽음. 화면/설정 항목은 바로, 저장 데이터는 백그라운드 결과가 도착하면 채움"""
        self._show_badges(self._ui_preview_fn(), UI_RESET_CLEANUP_ITEM_KEYS)
        self._loading = True
        for key in STORAGE_CLEANUP_ITEM_KEYS:
            self._badges[key].setText(LOADING_BADGE_TEXT)
        self._update_state()
        self._loader.start(self._scope)

    def _show_badges(self, previews: Mapping[CleanupItemKey, CleanupItemPreview], keys: Sequence[CleanupItemKey]) -> None:
        """고른 항목의 배지에 미리보기 건수와 용량을 표시"""
        for key in keys:
            self._badges[key].setText(item_badge_text(previews.get(key)))

    def _on_storage_previews(self, previews: Mapping[CleanupItemKey, CleanupItemPreview]) -> None:
        """저장 데이터 건수와 용량이 도착하면 배지를 채우고 실행 버튼을 다시 연다"""
        self._previews = {key: previews[key] for key in STORAGE_CLEANUP_ITEM_KEYS if key in previews}
        self._show_badges(self._previews, STORAGE_CLEANUP_ITEM_KEYS)
        self._finish_loading()

    def _on_storage_preview_failed(self) -> None:
        """작업 폴더가 없거나 저장소를 읽지 못하면 저장 데이터는 지울 것이 없는 것으로 표시"""
        self._previews = {key: CleanupItemPreview(key, 0, 0, False) for key in STORAGE_CLEANUP_ITEM_KEYS}
        self._show_badges(self._previews, STORAGE_CLEANUP_ITEM_KEYS)
        self._finish_loading()

    def _finish_loading(self) -> None:
        """읽기가 끝나 실행 버튼을 다시 연다"""
        self._loading = False
        self._update_state()

    def is_loading(self) -> bool:
        """저장 데이터 건수를 아직 읽는 중인지"""
        return self._loading

    def done(self, result_code: int) -> None:
        """창을 닫으면 진행 중 미리보기 결과를 버려 종료를 기다리지 않음"""
        self._loader.cancel()
        super().done(result_code)

    def apply_preset(self, preset: CleanupPresetKey, _checked: bool = False) -> None:
        """프리셋 하나를 골라 그 항목만 선택하고 해당 범주를 펼침"""
        keys = CLEANUP_PRESETS[preset]
        self._apply_keys(keys)
        self._expand_categories(keys)

    def _apply_keys(self, keys: Sequence[CleanupItemKey]) -> None:
        """고른 항목만 체크하고 나머지는 해제한 뒤 버튼과 경고 상태를 갱신"""
        wanted = set(keys)
        for key, checkbox in self._checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(key in wanted)
            checkbox.blockSignals(False)
        self._update_state()

    def _expand_categories(self, keys: Sequence[CleanupItemKey]) -> None:
        """선택한 항목이 들어 있는 범주를 펼침"""
        targets = {cleanup_item_category(key) for key in keys}
        for category, section in self._sections.items():
            if category in targets:
                section.set_expanded(True)

    def set_category_checked(self, category: CleanupCategory, checked: bool) -> None:
        """범주 전체 선택을 누르면 그 범주 항목을 모두 같은 상태로 맞춤

        - 항목마다 신호를 내면 상태 갱신이 반복되므로 한 번만 갱신
        """
        for key in CATEGORY_CLEANUP_ITEM_KEYS[category]:
            checkbox = self._checkboxes[key]
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._update_state()

    def _on_select_all_clicked(self, category: CleanupCategory, checked: bool) -> None:
        """범주 전체 선택 체크박스 클릭을 범주 단위 선택으로 넘김"""
        self.set_category_checked(category, checked)

    def checked_keys(self) -> tuple[CleanupItemKey, ...]:
        """지금 선택된 정리 항목을 화면 순서대로 반환"""
        return tuple(key for key in ALL_CLEANUP_ITEM_KEYS if self._checkboxes[key].isChecked())

    def selected_request(self) -> DataCleanupRequest | None:
        """선택한 항목이 없으면 None, 있으면 범위와 항목을 담은 요청"""
        keys = self.checked_keys()
        if not keys:
            return None
        return DataCleanupRequest(self._scope, keys)

    def _on_selection_changed(self, _checked: bool = False) -> None:
        """항목 체크가 바뀌면 실행 버튼과 경고 표시를 갱신"""
        self._update_state()

    def _update_state(self) -> None:
        """선택 상태에 맞춰 실행 버튼, 경고 상자, 범주 전체 선택 표시를 갱신

        - 저장 데이터 건수를 읽는 동안에는 실행 버튼을 잠가 화면에 보이는 건수와 실행이 어긋나지 않게 함
        """
        request = self.selected_request()
        irreversible = request is not None and bool(request.irreversible_keys)
        self.btn_execute.setEnabled(request is not None and not self._loading)
        self.warning_box.setVisible(irreversible)
        self._apply_execute_style(irreversible)
        self._sync_select_all()
        self._sync_preset_highlight()

    def _apply_execute_style(self, irreversible: bool) -> None:
        """설정 범주를 고르면 실행 버튼을 위험 색 스타일로 바꿈"""
        object_name = EXECUTE_DANGER_OBJECT_NAME if irreversible else EXECUTE_PRIMARY_OBJECT_NAME
        if self.btn_execute.objectName() == object_name:
            return
        self.btn_execute.setObjectName(object_name)
        style = self.btn_execute.style()
        if style is not None:
            style.unpolish(self.btn_execute)
            style.polish(self.btn_execute)

    def _sync_select_all(self) -> None:
        """범주 항목이 모두 선택됐는지에 맞춰 범주 전체 선택 표시를 맞춤"""
        for category, select_all in self._select_all.items():
            keys = CATEGORY_CLEANUP_ITEM_KEYS[category]
            select_all.setChecked(all(self._checkboxes[key].isChecked() for key in keys))

    def _sync_preset_highlight(self) -> None:
        """지금 선택한 항목 집합과 같은 프리셋 알약만 눌린 모양으로 표시"""
        checked = set(self.checked_keys())
        for preset, pill in self._preset_buttons.items():
            active = bool(checked) and checked == set(CLEANUP_PRESETS[preset])
            set_state_property(pill, PRESET_ACTIVE_PROPERTY, active)

    def _on_execute(self) -> None:
        """선택 항목 정리를 확정. 되돌릴 수 없는 항목이 있으면 확인을 한 번 거침"""
        request = self.selected_request()
        if request is None:
            return
        if request.confirm_keys and not self._confirm_fn():
            return
        self.accept()

    def _ask_confirm(self) -> bool:
        """되돌릴 수 없는 항목을 종류별 문장으로 모아 한 확인 상자에 보여 주고 답을 반환"""
        request = self.selected_request()
        keys = request.confirm_keys if request is not None else ()
        answer = QMessageBox.warning(
            self,
            CONFIRM_TITLE,
            confirm_text(keys),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트로 스타일시트와 경고, 안내 아이콘 색상을 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        for preset, pill in self._preset_buttons.items():
            color = palette.badge_pending[1] if preset == PRESET_WARNING_KEY else palette.text_secondary
            pill.setIcon(
                get_icon(PRESET_ICONS[preset], color=color, size=PRESET_PILL_ICON_SIZE)
            )
        self.lbl_warning_icon.setPixmap(
            get_icon(WARNING_ICON_NAME, color=palette.fg_maxrate_warn, size=WARNING_ICON_SIZE).pixmap(
                WARNING_ICON_SIZE, WARNING_ICON_SIZE
            )
        )
        self.lbl_safety_icon.setPixmap(
            get_icon(SAFETY_ICON_NAME, color=palette.text_secondary, size=WARNING_ICON_SIZE).pixmap(
                WARNING_ICON_SIZE, WARNING_ICON_SIZE
            )
        )
        # - 접이식 구역은 부모 스타일시트를 상속하지 않으므로 화살표 색을 직접 넘김
        for section in self._sections.values():
            section.apply_theme(palette)
