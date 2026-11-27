"""일반 범주 고급 설정의 진단 카드(개발자 모드, 진단 폴더) 생성 모듈

- 토글은 `diagnostics.developer_mode` 행으로 등록해 다른 설정과 같은 자동 저장 경로를 씀
- 폴더 열기는 아이콘 버튼으로 두고 접근성 이름과 툴팁으로 기능을 알림
"""

from collections.abc import Callable

from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from src.core.config import DiagnosticsConfig
from src.gui.settings.setting_row import SettingRow, make_combo_row
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.icons import create_icon_button, get_active_palette, get_icon

DEVELOPER_MODE_KEY: str = "diagnostics.developer_mode"
DEVELOPER_MODE_TITLE: str = "개발자 모드"
DEVELOPER_MODE_DESC: str = (
    "상세 로그를 data/logs/dev.log에 남기고 화면이 0.5초 넘게 멈추면 그때의 호출 스택을 기록합니다. "
    "바꾼 값은 다음 실행부터 적용되므로, 설정을 저장한 뒤 앱을 다시 시작해 주십시오. "
    "평소보다 로그 파일이 빨리 커지고 화면 반응이 아주 조금 느려질 수 있으므로, "
    "문제를 확인할 때만 켜 두십시오."
)
DEVELOPER_MODE_CHOICES: tuple[tuple[str, bool], ...] = (
    ("사용 안 함 (기본)", False),
    ("사용", True),
)
ACTION_BUTTON_SIZE: int = 26
ACTION_BUTTON_SPACING: int = 6
OPEN_FOLDER_TOOLTIP: str = "진단 로그와 비정상 종료 보고서, 진단 보고서 zip이 저장된 data/logs 폴더를 엽니다."
OPEN_FOLDER_ACCESSIBLE_NAME: str = "진단 폴더 열기"
CREATE_REPORT_TOOLTIP: str = (
    "지금까지의 로그와 실행 환경을 모아 data/logs에 진단 보고서 zip을 만듭니다. "
    "만든 뒤 폴더를 열어 확인할 수 있습니다."
)
CREATE_REPORT_ACCESSIBLE_NAME: str = "진단 보고서 만들기"


def build_open_folder_button(on_open_folder: Callable[[], None]) -> QPushButton:
    """진단 폴더를 여는 아이콘 버튼 생성"""
    button = create_icon_button(
        None,
        OPEN_FOLDER_TOOLTIP,
        on_open_folder,
        accessible_name=OPEN_FOLDER_ACCESSIBLE_NAME,
        fixed_size=ACTION_BUTTON_SIZE,
    )
    button.setIcon(get_icon("folder-open", palette=get_active_palette(), size=14))
    return button


def build_create_report_button(on_create_report: Callable[[], None]) -> QPushButton:
    """진단 보고서를 만드는 아이콘 버튼 생성"""
    button = create_icon_button(
        None,
        CREATE_REPORT_TOOLTIP,
        on_create_report,
        accessible_name=CREATE_REPORT_ACCESSIBLE_NAME,
        fixed_size=ACTION_BUTTON_SIZE,
    )
    button.setIcon(get_icon("download", palette=get_active_palette(), size=14))
    return button


def build_diagnostics_card(
    rows: dict[str, SettingRow],
    on_open_folder: Callable[[], None],
    on_create_report: Callable[[], None],
) -> SettingsGroupCard:
    """개발자 모드 토글과 진단 폴더 열기, 진단 보고서 만들기 버튼을 담은 카드 생성"""
    card = SettingsGroupCard(
        icon_name="file-text",
        title="진단",
        subtitle="상세 로그, 진단 폴더, 진단 보고서",
    )
    make_combo_row(
        rows,
        card,
        DiagnosticsConfig,
        "developer_mode",
        DEVELOPER_MODE_TITLE,
        DEVELOPER_MODE_DESC,
        DEVELOPER_MODE_CHOICES,
        row_key=DEVELOPER_MODE_KEY,
    )
    # - 폴더 열기와 보고서 만들기 행은 저장 값을 다루지 않으므로 설정 행 사전에 넣지 않고 카드에만 붙임
    action_row = SettingRow(
        key="diagnostics.actions",
        title="진단 파일",
        desc="로그 폴더를 열거나 지금까지의 로그를 진단 보고서 zip으로 묶습니다.",
        editor=build_action_buttons(on_open_folder, on_create_report),
        default_val=None,
    )
    action_row.btn_reset.hide()
    card.add_row(action_row)
    return card


def build_action_buttons(
    on_open_folder: Callable[[], None],
    on_create_report: Callable[[], None],
) -> QWidget:
    """두 아이콘 버튼을 한 줄에 담은 위젯 생성"""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(ACTION_BUTTON_SPACING)
    layout.addWidget(build_open_folder_button(on_open_folder))
    layout.addWidget(build_create_report_button(on_create_report))
    return holder
