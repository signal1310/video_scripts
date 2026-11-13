import logging
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    DEFAULT_CONFIGS,
    ClassifierConfig,
    get_root_dir,
    save_env_values,
    set_root_dir,
)
from src.gui.constants import THRESHOLD_BOUNDS, browse_directory
from src.gui.dialogs.setting_row import (
    SettingRow,
    make_float_spin_row,
    make_int_spin_row,
    make_text_row,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager
from src.gui.widgets.collapsible_section import CollapsibleSection
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.settings_group_card import SettingsGroupCard


class SettingsDialog(QDialog, ThemeAwareMixin):
    """
    - .env 및 .rootdir 설정을 조회하고 수정하는 환경설정 대화상자.
    - 그룹 카드(SettingsGroupCard)와 접이식(Accordion) 고급 설정을 단일 스크롤 뷰로 상하 배치.
    """

    settings_applied = pyqtSignal(ClassifierConfig, str)

    scroll_area: QScrollArea
    section_advanced: CollapsibleSection
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

    def __init__(
        self,
        current_config: ClassifierConfig | None = None,
        current_root_dir: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("환경설정")
        self.resize(630, 680)
        self.setMinimumSize(560, 440)

        self._is_loading: bool = True
        self._cfg = current_config or ClassifierConfig.from_env(reload_dotenv=True)
        self._initial_root_dir = current_root_dir or self._load_current_root_dir()
        self._last_saved_root_dir: str = self._initial_root_dir
        self._rows: dict[str, SettingRow] = {}
        self._cards: list[SettingsGroupCard] = []

        self.scroll_area = QScrollArea(self)
        content_widget, content_layout = self._init_ui_shell()
        self._build_general_cards(content_layout)
        self.section_advanced = CollapsibleSection(
            title="고급 설정",
            subtitle="(코덱 압축 가중치, 해상도 스케일링, 세부 파라미터)",
            is_expanded=False,
        )
        self._build_advanced_cards(self.section_advanced)
        content_layout.addWidget(self.section_advanced)
        content_layout.addStretch(1)
        self.scroll_area.setWidget(content_widget)
        self._load_values()
        self._connect_row_signals()
        self._is_loading = False
        self.init_theme_listener()

    @staticmethod
    def _load_current_root_dir() -> str:
        try:
            return get_root_dir()
        except Exception:
            return ""

    def _init_ui_shell(self) -> tuple[QWidget, QVBoxLayout]:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(22, 20, 22, 18)
        main_layout.setSpacing(14)

        # 1. 상단 타이틀 및 설명
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        lbl_title = QLabel("환경설정")
        lbl_title.setObjectName("AppTitle")
        lbl_desc = QLabel(
            "설정을 변경하면 즉시 자동 저장되어 분석 및 분류에 반영됩니다."
        )
        lbl_desc.setObjectName("SettingDesc")
        lbl_desc.setWordWrap(True)
        header_box.addWidget(lbl_title)
        header_box.addWidget(lbl_desc)
        main_layout.addLayout(header_box)

        # 2. 단일 스크롤 영역
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 4, 8, 12)
        content_layout.setSpacing(14)

        main_layout.addWidget(self.scroll_area, stretch=1)
        return content_widget, content_layout

    def _build_general_cards(self, layout: QVBoxLayout) -> None:
        # 카드 1: 기본 환경 및 분류 폴더
        card_env = SettingsGroupCard(
            icon_name="folder",
            title="기본 환경 및 분류 폴더",
            subtitle="작업 루트 경로 및 타겟 폴더명",
        )
        self._cards.append(card_env)

        # UI 테마
        self.combo_theme = QComboBox()
        p = get_active_palette()
        self.combo_theme.addItem(get_icon("monitor", palette=p, size=14, pad_right=6), "시스템 적응형", ThemeManager.MODE_SYSTEM)
        self.combo_theme.addItem(get_icon("moon", palette=p, size=14, pad_right=6), "다크", ThemeManager.MODE_DARK)
        self.combo_theme.addItem(get_icon("sun", palette=p, size=14, pad_right=6), "라이트", ThemeManager.MODE_LIGHT)
        self.combo_theme.setFixedWidth(140)

        row_theme = SettingRow(
            key="UI_THEME",
            title="UI 인터페이스 테마",
            desc="운영체제 테마 연동(시스템), 다크 또는 라이트 테마.",
            editor=self.combo_theme,
            default_val=ThemeManager.MODE_SYSTEM,
            on_reset=lambda: self.combo_theme.setCurrentIndex(
                self.combo_theme.findData(ThemeManager.MODE_SYSTEM)
            ),
        )
        self._rows["UI_THEME"] = row_theme
        card_env.add_row(row_theme)

        # 기본 작업 디렉터리
        path_box = QWidget()
        path_layout = QHBoxLayout(path_box)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(6)

        self.edit_root_dir = QLineEdit()
        self.edit_root_dir.setPlaceholderText("기본 작업 폴더 경로...")
        self.edit_root_dir.setFixedWidth(206)

        self.btn_browse_root = QPushButton()
        self.btn_browse_root.setObjectName("IconActionButton")
        self.btn_browse_root.setIcon(get_icon("folder", palette=p, size=14))
        self.btn_browse_root.setToolTip("기본 작업 폴더 선택")
        self.btn_browse_root.setFixedSize(28, 28)
        self.btn_browse_root.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse_root.clicked.connect(self._on_browse_root_dir)
        path_layout.addWidget(self.edit_root_dir)
        path_layout.addWidget(self.btn_browse_root)

        row_root = SettingRow(
            key=".rootdir",
            title="기본 작업 디렉터리",
            desc="분석 대상 비디오 파일들이 위치하는 기본 루트 폴더 경로.",
            editor=path_box,
            default_val="",
            on_reset=lambda: self.edit_root_dir.setText(""),
        )
        self._rows[".rootdir"] = row_root
        card_env.add_row(row_root)

        # 타겟 분류 폴더명
        self.edit_dir_std = make_text_row(
            self._rows, card_env, "DIR_STANDARD_CQ22", "표준 인코딩 대상 폴더명",
            "과화질 감축 및 적정화질 키프레임 교정 대상 영상들이 이동/분류될 타겟 폴더 명칭.",
            width=200,
        )
        self.edit_dir_low = make_text_row(
            self._rows, card_env, "DIR_LOW_QUALITY_KEYFRAME", "저화질 키프레임 교정 대상 폴더명",
            "저화질이면서 키프레임이 불량인 영상들이 노이즈 증폭 방지 프리셋으로 분류될 타겟 폴더 명칭.",
            width=200,
        )
        layout.addWidget(card_env)

        # 카드 2: 화질 및 비트레이트 판별 기준
        card_quality = SettingsGroupCard(
            icon_name="zap",
            title="화질 및 판정 기준",
            subtitle="과화질 및 키프레임 판정 임계치",
        )
        self._cards.append(card_quality)

        self.spin_over_ratio = make_float_spin_row(
            self._rows, card_quality, "THRESHOLD_OPTIMAL_BITRATE_RATE", "과화질 판별 경계 배수",
            "최적 비트레이트 대비 현재 비트레이트가 이 배수를 초과하면 과화질(용량 감축 대상)로 판정.",
            *THRESHOLD_BOUNDS["over_bitrate_ratio"], 0.05, 2, "배",
        )
        self.spin_low_ratio = make_float_spin_row(
            self._rows, card_quality, "THRESHOLD_LOW_BITRATE_RATE", "저화질 판별 경계 배수",
            "최적 비트레이트 대비 현재 비트레이트가 이 배수 미만이면 저화질(재인코딩 시 노이즈 증폭 방지)로 판정.",
            *THRESHOLD_BOUNDS["low_bitrate_ratio"], 0.05, 2, "배",
        )
        self.spin_kf_threshold = make_float_spin_row(
            self._rows, card_quality, "THRESHOLD_KEYFRAME_INTERVAL", "최대 허용 키프레임 간격",
            "탐색(Seek) 지연 및 버벅임을 유발하는 최대 키프레임 간격. 초과 시 1초 키프레임 교정 대상으로 분류.",
            *THRESHOLD_BOUNDS["keyframe_threshold"], 0.1, 1, "초",
        )
        self.spin_small_size = make_float_spin_row(
            self._rows, card_quality, "SMALL_FOOTAGE_SIZE_MB", "소형 영상 기준 용량",
            "이 용량 미만의 영상을 소형 클립으로 분류하여 인코딩 실익 및 누적 절감 기여도를 별도 판정.",
            *THRESHOLD_BOUNDS["small_footage_mb"], 5.0, 1, "MB",
        )
        layout.addWidget(card_quality)

    def _build_advanced_cards(self, section: CollapsibleSection) -> None:
        # 그룹 1: 코덱별 압축 가중치
        card_codecs = SettingsGroupCard(
            icon_name="film",
            title="코덱별 압축 가중치",
            subtitle="H.264 동등 화질 대비 요구 비율",
        )
        self._cards.append(card_codecs)

        self.spin_w_av1 = make_float_spin_row(
            self._rows, card_codecs, "CODEC_WEIGHT_AV1", "AV1 코덱 가중치",
            "H.264 동등 화질 달성에 필요한 상대 비트레이트 비율 (기본 0.65 -> 약 35% 용량 절감).",
            *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
        )
        self.spin_w_hevc = make_float_spin_row(
            self._rows, card_codecs, "CODEC_WEIGHT_HEVC_VP9", "HEVC / VP9 코덱 가중치",
            "H.264 동등 화질 달성에 필요한 상대 비트레이트 비율 (기본 0.80 -> 약 20% 용량 절감).",
            *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
        )
        self.spin_w_h264 = make_float_spin_row(
            self._rows, card_codecs, "CODEC_WEIGHT_H264", "H.264 기준 코덱 가중치",
            "전체 화질/비트레이트 계산의 기준점이 되는 표준 가중치 (기본 1.00 고정 권장).",
            *THRESHOLD_BOUNDS["codec_weight_efficient"], 0.05, 2, "배",
        )
        self.spin_w_legacy = make_float_spin_row(
            self._rows, card_codecs, "CODEC_WEIGHT_LEGACY", "구형 레거시 코덱 가중치",
            "구형 코덱 영상이 H.264 수준 화질을 유지하기 위해 요구하는 추가 비트레이트 가중치 (기본 1.30).",
            *THRESHOLD_BOUNDS["codec_weight_legacy"], 0.05, 2, "배",
        )
        section.add_widget(card_codecs)

        # 그룹 2: 비트레이트 스케일링 기준점
        card_scaling = SettingsGroupCard(
            icon_name="sliders",
            title="비트레이트 기준점 및 스케일링 지수",
            subtitle="720p 30fps 기준점 및 지수 곡선",
        )
        self._cards.append(card_scaling)

        bw_min, bw_max = THRESHOLD_BOUNDS["base_width"]
        self.spin_base_w = make_int_spin_row(
            self._rows, card_scaling, "BASE_RESOLUTION_WIDTH", "기준 가로 해상도",
            "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 가로 폭.",
            int(bw_min), int(bw_max), 10, "px",
        )
        bh_min, bh_max = THRESHOLD_BOUNDS["base_height"]
        self.spin_base_h = make_int_spin_row(
            self._rows, card_scaling, "BASE_RESOLUTION_HEIGHT", "기준 세로 해상도",
            "표준 CQ22 목표 비트레이트가 정의되는 기준 해상도의 세로 높이.",
            int(bh_min), int(bh_max), 10, "px",
        )
        br_min, br_max = THRESHOLD_BOUNDS["base_target_bitrate"]
        self.spin_base_bitrate = make_int_spin_row(
            self._rows, card_scaling, "BASE_TARGET_BITRATE", "기준 목표 비트레이트",
            "기준 해상도(720p 30fps H.264)에서 CQ22 수준의 최적 적정 비트레이트.",
            int(br_min), int(br_max), 50, "kbps",
        )
        self.spin_base_fps = make_float_spin_row(
            self._rows, card_scaling, "BASE_FPS", "기준 프레임레이트",
            "비트레이트 스케일링의 기준점이 되는 표준 프레임레이트.",
            *THRESHOLD_BOUNDS["base_fps"], 1.0, 1, "fps",
        )
        self.spin_area_exp = make_float_spin_row(
            self._rows, card_scaling, "AREA_SCALING_EXPONENT", "해상도 면적 스케일링 지수",
            "해상도 변화에 따른 인간 시각 인지 한계와 비트레이트 스케일링 곡선을 결정하는 지수 (기본 0.82).",
            *THRESHOLD_BOUNDS["area_scaling_exponent"], 0.01, 2,
        )
        self.spin_fps_exp = make_float_spin_row(
            self._rows, card_scaling, "FPS_SCALING_EXPONENT", "프레임레이트 보정 지수",
            "60fps 이상 고주사율 영상에 대한 비트레이트 스케일링 곡선 보정 지수 (기본 0.60).",
            *THRESHOLD_BOUNDS["fps_scaling_exponent"], 0.01, 2,
        )
        section.add_widget(card_scaling)

        # 그룹 3: 소형 영상 누적 통계 및 콘솔 출력
        card_footage = SettingsGroupCard(
            icon_name="bar-chart",
            title="소형 클립 누적 통계 및 콘솔 출력",
            subtitle="단편 클립 절감 기준 및 터미널 출력",
        )
        self._cards.append(card_footage)

        self.spin_small_min_saved = make_float_spin_row(
            self._rows, card_footage, "SMALL_FOOTAGE_MIN_SAVED_MB", "소형 영상 최소 절감량 하한선",
            "소형 클립 1개당 예상 절감량이 이 기준 미만인 경우 불필요한 인코딩 작업을 방지하고 원본 보존.",
            *THRESHOLD_BOUNDS["small_footage_min_saved_mb"], 0.5, 1, "MB",
        )
        self.spin_small_cum_ratio = make_float_spin_row(
            self._rows, card_footage, "SMALL_FOOTAGE_CUMULATIVE_RATIO", "소형 영상 누적 절감 비중 임계값",
            "폴더 전체 절감 가능량 중 소형 클립들이 차지하는 비중이 이 값을 초과하면 누적 최적화 모드로 전환 (기본 0.15 = 15%).",
            *THRESHOLD_BOUNDS["small_footage_cumulative_ratio"], 0.01, 2, "배",
        )
        self.spin_small_cum_mb = make_float_spin_row(
            self._rows, card_footage, "SMALL_FOOTAGE_CUMULATIVE_MB", "소형 영상 누적 절감 합계 임계값",
            "소형 클립들의 예상 절감 합계가 이 용량을 초과하면 대용량 영상과 함께 누적 인코딩 대상에 포함.",
            *THRESHOLD_BOUNDS["small_footage_cumulative_mb"], 50.0, 1, "MB",
        )
        self.spin_kf_overhead = make_float_spin_row(
            self._rows, card_footage, "KEYFRAME_1S_OVERHEAD_RATE", "키프레임 교정 오버헤드 계수",
            "키프레임을 1초로 교정할 때 I-프레임 증가로 인해 발생하는 용량 증가율 (기본 0.06 = 6%).",
            *THRESHOLD_BOUNDS["keyframe_1s_overhead_rate"], 0.01, 2, "배",
        )
        ml_min, ml_max = THRESHOLD_BOUNDS["tabulate_filename_maxlen"]
        self.spin_cui_maxlen = make_int_spin_row(
            self._rows, card_footage, "TABULATE_FILENAME_MAXLEN", "콘솔 출력 파일명 최대 길이",
            "주피터 노트북 및 터미널 출력 시 파일명 칼럼의 최대 글자 폭 (초과 시 말줄임표 처리).",
            int(ml_min), int(ml_max), 5, "자",
        )
        section.add_widget(card_footage)

    def _load_values(self) -> None:
        idx = self.combo_theme.findData(ThemeManager().mode)
        if idx >= 0:
            self.combo_theme.setCurrentIndex(idx)

        self.edit_root_dir.setText(self._initial_root_dir)
        self.edit_dir_std.setText(self._cfg.dir_standard_cq22)
        self.edit_dir_low.setText(self._cfg.dir_low_quality_keyframe)

        self.spin_over_ratio.setValue(self._cfg.over_bitrate_ratio)
        self.spin_low_ratio.setValue(self._cfg.low_bitrate_ratio)
        self.spin_kf_threshold.setValue(self._cfg.keyframe_threshold)
        self.spin_small_size.setValue(self._cfg.small_footage_mb)

        self.spin_w_av1.setValue(
            self._cfg.codec_weights.get("av1", float(DEFAULT_CONFIGS["CODEC_WEIGHT_AV1"]))
        )
        self.spin_w_hevc.setValue(
            self._cfg.codec_weights.get(
                "hevc", float(DEFAULT_CONFIGS["CODEC_WEIGHT_HEVC_VP9"])
            )
        )
        self.spin_w_h264.setValue(
            self._cfg.codec_weights.get("h264", float(DEFAULT_CONFIGS["CODEC_WEIGHT_H264"]))
        )
        self.spin_w_legacy.setValue(
            self._cfg.codec_weights.get(
                "mpeg4", float(DEFAULT_CONFIGS["CODEC_WEIGHT_LEGACY"])
            )
        )

        self.spin_base_w.setValue(self._cfg.base_width)
        self.spin_base_h.setValue(self._cfg.base_height)
        self.spin_base_bitrate.setValue(self._cfg.base_target_bitrate)
        self.spin_base_fps.setValue(self._cfg.base_fps)
        self.spin_area_exp.setValue(self._cfg.area_scaling_exponent)
        self.spin_fps_exp.setValue(self._cfg.fps_scaling_exponent)

        self.spin_small_min_saved.setValue(self._cfg.small_footage_min_saved_mb)
        self.spin_small_cum_ratio.setValue(self._cfg.small_footage_cumulative_ratio)
        self.spin_small_cum_mb.setValue(self._cfg.small_footage_cumulative_mb)
        self.spin_kf_overhead.setValue(self._cfg.keyframe_1s_overhead_rate)
        self.spin_cui_maxlen.setValue(self._cfg.tabulate_filename_maxlen)

    def _on_browse_root_dir(self) -> None:
        curr = self.edit_root_dir.text().strip()
        start = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = browse_directory(self, "기본 작업 폴더 선택", start)
        if chosen:
            self.edit_root_dir.setText(os.path.normpath(chosen))
            self._auto_save()

    def _connect_row_signals(self) -> None:
        for row in self._rows.values():
            row.value_changed.connect(self._on_setting_changed)

    def _on_setting_changed(self) -> None:
        if getattr(self, "_is_loading", False):
            return
        self._auto_save()

    def sync_thresholds(
        self, over_ratio: float, kf_threshold: float, small_mb: float
    ) -> None:
        """
        - 메인 윈도우의 FilterTuningBar에서 변경된 임계값을 열려 있는 설정 창에 동기화.
        """
        self._is_loading = True
        try:
            self.spin_over_ratio.setValue(over_ratio)
            self.spin_kf_threshold.setValue(kf_threshold)
            self.spin_small_size.setValue(small_mb)
        finally:
            self._is_loading = False

    def _auto_save(self) -> None:
        if getattr(self, "_is_loading", False):
            return

        updates: dict[str, str] = {
            key: row.get_env_value()
            for key, row in self._rows.items()
            if key in DEFAULT_CONFIGS
        }

        try:
            save_env_values(updates, reload_dotenv=True)
        except Exception:
            logging.getLogger(__name__).warning(".env 설정 저장 실패", exc_info=True)

        root_dir_val = self.edit_root_dir.text().strip()
        if root_dir_val != self._last_saved_root_dir:
            try:
                set_root_dir(root_dir_val)
                self._last_saved_root_dir = root_dir_val
            except Exception:
                logging.getLogger(__name__).warning(".rootdir 설정 저장 실패", exc_info=True)

        chosen_theme = self.combo_theme.currentData()
        if chosen_theme and chosen_theme != ThemeManager().mode:
            ThemeManager().set_mode(chosen_theme)

        new_config = ClassifierConfig.from_env(reload_dotenv=True)
        self.settings_applied.emit(new_config, root_dir_val)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 다이얼로그 스타일시트 및 아이콘 색상을 갱신.
        """
        self.setStyleSheet(build_stylesheet(palette))
        if hasattr(self, "combo_theme"):
            self.combo_theme.setItemIcon(
                0, get_icon("monitor", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                1, get_icon("moon", palette=palette, size=14, pad_right=6)
            )
            self.combo_theme.setItemIcon(
                2, get_icon("sun", palette=palette, size=14, pad_right=6)
            )
        if hasattr(self, "btn_browse_root"):
            self.btn_browse_root.setIcon(get_icon("folder", palette=palette, size=14))
        if hasattr(self, "section_advanced"):
            self.section_advanced.apply_theme(palette)
        for card in getattr(self, "_cards", []):
            card.apply_theme(palette)
        for row in getattr(self, "_rows", {}).values():
            row.apply_theme(palette)
