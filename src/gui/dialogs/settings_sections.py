"""환경설정 대화상자 섹션별 카드 및 입력 위젯 생성·툴팁 정의 모듈"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.constants import THRESHOLD_BOUNDS
from src.gui.dialogs.setting_row import (
    SettingRow,
    make_float_spin_row,
    make_int_spin_row,
    make_text_row,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.theme import ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard


@dataclass
class SettingsFormFields:
    """환경설정 다이얼로그의 입력 위젯과 그룹 카드, 설정 행 맵을 보관하는 컨테이너"""

    rows: dict[str, SettingRow]
    cards: list[SettingsGroupCard]
    combo_theme: QComboBox
    edit_root_dir: QLineEdit
    btn_browse_root: QPushButton
    edit_dir_std: QLineEdit
    edit_dir_low: QLineEdit
    spin_over_ratio: QDoubleSpinBox
    spin_low_ratio: QDoubleSpinBox
    spin_kf_threshold: QDoubleSpinBox
    spin_small_size: QDoubleSpinBox
    spin_w_av1: QDoubleSpinBox
    spin_w_hevc: QDoubleSpinBox
    spin_w_h264: QDoubleSpinBox
    spin_w_legacy: QDoubleSpinBox
    spin_base_w: CustomIntegerSpinBox
    spin_base_h: CustomIntegerSpinBox
    spin_base_bitrate: CustomIntegerSpinBox
    spin_base_fps: QDoubleSpinBox
    spin_area_exp: QDoubleSpinBox
    spin_fps_exp: QDoubleSpinBox
    spin_small_min_saved: QDoubleSpinBox
    spin_small_cum_ratio: QDoubleSpinBox
    spin_small_cum_mb: QDoubleSpinBox
    spin_kf_overhead: QDoubleSpinBox
    spin_cui_maxlen: CustomIntegerSpinBox


def _build_env_card(
    rows: dict[str, SettingRow],
    cards: list[SettingsGroupCard],
    on_browse_root: Callable[[], None],
) -> tuple[
    SettingsGroupCard,
    QComboBox,
    QLineEdit,
    QPushButton,
    QLineEdit,
    QLineEdit,
]:
    card_env = SettingsGroupCard(
        icon_name="folder",
        title="기본 환경 및 분류 폴더",
        subtitle="",
    )
    cards.append(card_env)

    combo_theme = QComboBox()
    p = get_active_palette()
    combo_theme.addItem(get_icon("monitor", palette=p, size=14, pad_right=6), "시스템 연동", ThemeManager.MODE_SYSTEM)
    combo_theme.addItem(get_icon("moon", palette=p, size=14, pad_right=6), "다크", ThemeManager.MODE_DARK)
    combo_theme.addItem(get_icon("sun", palette=p, size=14, pad_right=6), "라이트", ThemeManager.MODE_LIGHT)
    combo_theme.setFixedWidth(max(168, combo_theme.sizeHint().width() + 14))

    row_theme = SettingRow(
        key="UI_THEME",
        title="화면 테마",
        desc="운영체제 테마 연동, 다크 또는 라이트 테마 중 하나를 선택합니다.",
        editor=combo_theme,
        default_val=ThemeManager.MODE_SYSTEM,
        on_reset=lambda: combo_theme.setCurrentIndex(
            combo_theme.findData(ThemeManager.MODE_SYSTEM)
        ),
    )
    rows["UI_THEME"] = row_theme
    card_env.add_row(row_theme)

    path_box = QWidget()
    path_layout = QHBoxLayout(path_box)
    path_layout.setContentsMargins(0, 0, 0, 0)
    path_layout.setSpacing(6)

    edit_root_dir = QLineEdit()
    edit_root_dir.setPlaceholderText("기본 작업 폴더 경로...")
    edit_root_dir.setFixedWidth(252)

    btn_browse_root = QPushButton()
    btn_browse_root.setObjectName("IconActionButton")
    btn_browse_root.setIcon(get_icon("folder", palette=p, size=14))
    btn_browse_root.setToolTip("기본 작업 폴더를 선택합니다.")
    btn_browse_root.setFixedSize(28, 28)
    btn_browse_root.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_browse_root.clicked.connect(on_browse_root)
    path_layout.addWidget(edit_root_dir)
    path_layout.addWidget(btn_browse_root)

    row_root = SettingRow(
        key=".rootdir",
        title="기본 작업 폴더",
        desc="분석 대상 비디오 파일들이 위치한 기본 작업 폴더 경로를 지정합니다.",
        editor=path_box,
        default_val="",
        on_reset=lambda: edit_root_dir.setText(""),
    )
    rows[".rootdir"] = row_root
    card_env.add_row(row_root)

    edit_dir_std = make_text_row(
        rows, card_env, "DIR_STANDARD_CQ22", "표준 인코딩 폴더명",
        "과화질 감축 및 적정화질 키프레임 교정 대상 영상이 이동될 하위 폴더 이름입니다.",
        width=220,
    )
    edit_dir_low = make_text_row(
        rows, card_env, "DIR_LOW_QUALITY_KEYFRAME", "저화질 키프레임 폴더명",
        "저화질이면서 키프레임이 불량인 영상이 노이즈 증폭 방지 프리셋으로 분류될 하위 폴더 이름입니다.",
        width=220,
    )
    return card_env, combo_theme, edit_root_dir, btn_browse_root, edit_dir_std, edit_dir_low


def build_settings_form(
    general_layout: QVBoxLayout,
    advanced_section: CollapsibleSection,
    on_browse_root: Callable[[], None],
) -> SettingsFormFields:
    """환경설정 기본·고급 그룹 카드와 각 입력 위젯 조립·배치"""
    rows: dict[str, SettingRow] = {}
    cards: list[SettingsGroupCard] = []

    card_env, combo_theme, edit_root_dir, btn_browse_root, edit_dir_std, edit_dir_low = (
        _build_env_card(rows, cards, on_browse_root)
    )
    general_layout.addWidget(card_env)

    card_quality = SettingsGroupCard(
        icon_name="zap",
        title="화질 및 판정 기준",
        subtitle="",
    )
    cards.append(card_quality)

    spin_over_ratio = make_float_spin_row(
        rows, card_quality, "THRESHOLD_OPTIMAL_BITRATE_RATE", "과화질 기준 배수",
        "최적 비트레이트 대비 현재 비트레이트가 이 배수를 초과하면 과화질(용량 감축 대상)로 판정합니다.",
        *THRESHOLD_BOUNDS["over_bitrate_ratio"], 0.05, 2, "배",
    )
    spin_low_ratio = make_float_spin_row(
        rows, card_quality, "THRESHOLD_LOW_BITRATE_RATE", "저화질 기준 배수",
        "최적 비트레이트 대비 현재 비트레이트가 이 배수 미만이면 저화질(노이즈 증폭 방지)로 판정합니다.",
        *THRESHOLD_BOUNDS["low_bitrate_ratio"], 0.05, 2, "배",
    )
    spin_kf_threshold = make_float_spin_row(
        rows, card_quality, "THRESHOLD_KEYFRAME_INTERVAL", "최대 키프레임 간격",
        "탐색(Seek) 지연이나 버벅임을 방지하기 위한 허용 상한입니다. 초과 시 1초 키프레임 교정 대상으로 분류합니다.",
        *THRESHOLD_BOUNDS["keyframe_threshold"], 0.1, 1, "초",
    )
    spin_small_size = make_float_spin_row(
        rows, card_quality, "SMALL_FOOTAGE_SIZE_MB", "소형 영상 기준 용량",
        "이 용량 미만의 영상을 소형 영상으로 분류하여 인코딩 실익과 누적 절감 기여도를 별도로 판정합니다.",
        *THRESHOLD_BOUNDS["small_footage_mb"], 5.0, 1, "MB",
    )
    general_layout.addWidget(card_quality)

    card_codecs = SettingsGroupCard(
        icon_name="film",
        title="코덱별 압축 가중치",
        subtitle="H.264 대비 상대 비율",
    )
    cards.append(card_codecs)

    spin_w_av1 = make_float_spin_row(
        rows, card_codecs, "CODEC_WEIGHT_AV1", "AV1 코덱 가중치",
        "H.264와 동등한 화질을 내기 위해 필요한 상대 비트레이트 비율입니다.",
        *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
    )
    spin_w_hevc = make_float_spin_row(
        rows, card_codecs, "CODEC_WEIGHT_HEVC_VP9", "HEVC / VP9 코덱 가중치",
        "H.264와 동등한 화질을 내기 위해 필요한 상대 비트레이트 비율입니다.",
        *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
    )
    spin_w_h264 = make_float_spin_row(
        rows, card_codecs, "CODEC_WEIGHT_H264", "H.264 코덱 가중치",
        "전체 화질과 비트레이트 계산의 기준점이 되는 표준 가중치입니다 (1.00 고정을 권장합니다).",
        *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
    )
    spin_w_legacy = make_float_spin_row(
        rows, card_codecs, "CODEC_WEIGHT_LEGACY", "구형 코덱 가중치",
        "구형 코덱 영상이 H.264 수준의 화질을 유지하기 위해 요구하는 추가 비트레이트 가중치입니다.",
        *THRESHOLD_BOUNDS["codec_weight_legacy"], 0.05, 2, "배",
    )
    advanced_section.add_widget(card_codecs)

    card_scaling = SettingsGroupCard(
        icon_name="sliders",
        title="비트레이트 기준점 및 스케일링",
        subtitle="",
    )
    cards.append(card_scaling)

    bw_min, bw_max = THRESHOLD_BOUNDS["base_width"]
    spin_base_w = make_int_spin_row(
        rows, card_scaling, "BASE_RESOLUTION_WIDTH", "기준 가로 해상도",
        "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 가로 폭입니다.",
        int(bw_min), int(bw_max), 10, "px",
    )
    bh_min, bh_max = THRESHOLD_BOUNDS["base_height"]
    spin_base_h = make_int_spin_row(
        rows, card_scaling, "BASE_RESOLUTION_HEIGHT", "기준 세로 해상도",
        "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 세로 높이입니다.",
        int(bh_min), int(bh_max), 10, "px",
    )
    br_min, br_max = THRESHOLD_BOUNDS["base_target_bitrate"]
    spin_base_bitrate = make_int_spin_row(
        rows, card_scaling, "BASE_TARGET_BITRATE", "기준 목표 비트레이트",
        "설정한 기준 해상도와 프레임레이트(H.264)에서의 CQ22 수준 목표 비트레이트입니다.",
        int(br_min), int(br_max), 50, "kbps",
    )
    spin_base_fps = make_float_spin_row(
        rows, card_scaling, "BASE_FPS", "기준 프레임레이트",
        "비트레이트 스케일링의 기준점이 되는 표준 프레임레이트입니다.",
        *THRESHOLD_BOUNDS["base_fps"], 1.0, 1, "fps",
    )
    spin_area_exp = make_float_spin_row(
        rows, card_scaling, "AREA_SCALING_EXPONENT", "해상도 면적 스케일링 지수",
        "해상도 변화에 따른 인간 시각 인지 한계와 비트레이트 스케일링 곡선을 결정하는 지수입니다.",
        *THRESHOLD_BOUNDS["area_scaling_exponent"], 0.01, 2,
    )
    spin_fps_exp = make_float_spin_row(
        rows, card_scaling, "FPS_SCALING_EXPONENT", "프레임레이트 보정 지수",
        "기준 프레임레이트(BASE_FPS)를 초과하는 고주사율 영상에 적용하는 비트레이트 보정 지수입니다.",
        *THRESHOLD_BOUNDS["fps_scaling_exponent"], 0.01, 2,
    )
    advanced_section.add_widget(card_scaling)

    card_footage = SettingsGroupCard(
        icon_name="bar-chart",
        title="소형 영상 및 콘솔 출력",
        subtitle="",
    )
    cards.append(card_footage)

    spin_small_min_saved = make_float_spin_row(
        rows, card_footage, "SMALL_FOOTAGE_MIN_SAVED_MB", "개별 최소 절감량",
        "소형 영상 1개당 예상 절감량이 이 기준 미만이면 불필요한 인코딩을 건너뛰고 원본을 보존합니다.",
        *THRESHOLD_BOUNDS["small_footage_min_saved_mb"], 0.5, 1, "MB",
    )
    spin_small_cum_ratio = make_float_spin_row(
        rows, card_footage, "SMALL_FOOTAGE_CUMULATIVE_RATIO", "누적 절감 비중",
        "전체 절감 가능량 중 소형 영상이 차지하는 비중이 이 값을 넘으면 누적 최적화 모드로 전환합니다.",
        *THRESHOLD_BOUNDS["small_footage_cumulative_ratio"], 0.01, 2, "배",
    )
    spin_small_cum_mb = make_float_spin_row(
        rows, card_footage, "SMALL_FOOTAGE_CUMULATIVE_MB", "누적 절감 합계",
        "소형 영상들의 예상 절감 합계가 이 용량을 넘으면 대용량 영상과 함께 누적 인코딩 대상에 포함합니다.",
        *THRESHOLD_BOUNDS["small_footage_cumulative_mb"], 50.0, 1, "MB",
    )
    spin_kf_overhead = make_float_spin_row(
        rows, card_footage, "KEYFRAME_1S_OVERHEAD_RATE", "키프레임 교정 오버헤드",
        "키프레임을 1초로 교정할 때 I-프레임 증가로 인해 발생하는 예상 용량 증가율입니다.",
        *THRESHOLD_BOUNDS["keyframe_1s_overhead_rate"], 0.01, 2, "배",
    )
    ml_min, ml_max = THRESHOLD_BOUNDS["tabulate_filename_maxlen"]
    spin_cui_maxlen = make_int_spin_row(
        rows, card_footage, "TABULATE_FILENAME_MAXLEN", "콘솔 파일명 최대 길이",
        "주피터 노트북과 터미널 출력에서 파일명 열의 최대 글자 폭을 지정합니다 (초과 시 말줄임표로 표시합니다).",
        int(ml_min), int(ml_max), 5, "자",
    )
    advanced_section.add_widget(card_footage)

    return SettingsFormFields(
        rows=rows,
        cards=cards,
        combo_theme=combo_theme,
        edit_root_dir=edit_root_dir,
        btn_browse_root=btn_browse_root,
        edit_dir_std=edit_dir_std,
        edit_dir_low=edit_dir_low,
        spin_over_ratio=spin_over_ratio,
        spin_low_ratio=spin_low_ratio,
        spin_kf_threshold=spin_kf_threshold,
        spin_small_size=spin_small_size,
        spin_w_av1=spin_w_av1,
        spin_w_hevc=spin_w_hevc,
        spin_w_h264=spin_w_h264,
        spin_w_legacy=spin_w_legacy,
        spin_base_w=spin_base_w,
        spin_base_h=spin_base_h,
        spin_base_bitrate=spin_base_bitrate,
        spin_base_fps=spin_base_fps,
        spin_area_exp=spin_area_exp,
        spin_fps_exp=spin_fps_exp,
        spin_small_min_saved=spin_small_min_saved,
        spin_small_cum_ratio=spin_small_cum_ratio,
        spin_small_cum_mb=spin_small_cum_mb,
        spin_kf_overhead=spin_kf_overhead,
        spin_cui_maxlen=spin_cui_maxlen,
    )
