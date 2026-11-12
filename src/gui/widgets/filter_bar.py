from typing import Callable, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig
from src.core.models import VideoProps
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


def parse_exception_token(token: str) -> Optional[Callable[[VideoProps], bool]]:
    """
    - 사용자가 입력한 예외 규칙 문자열을 Callable 판정 함수로 변환.
    - 파이썬 람다 식 (예: lambda v: v.duration < 10), 속성 조건식 (예: v.fps >= 60, v.width < 720),
      또는 단순 파일명 키워드 (예: sample, 무손실)를 유연하게 지원.
    """
    expr = token.strip()
    if not expr:
        return None

    safe_builtins = {
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "len": len,
    }

    if expr.startswith("lambda ") or expr.startswith("lambda:"):
        try:
            fn = eval(expr, {"__builtins__": safe_builtins})
            return lambda v: bool(fn(v))
        except Exception:
            pass
    elif "v." in expr or "v[" in expr:
        try:
            code = compile(expr, "<rule>", "eval")
            return lambda v: bool(
                eval(code, {"__builtins__": safe_builtins}, {"v": v})
            )
        except Exception:
            pass

    kw = expr.lower()
    return lambda v, k=kw: k in v.filename.lower()


class FilterTuningBar(QFrame, ThemeAwareMixin):
    """
    - 검색, 필터링, 임계값 조정 및 예외 규칙 입력을 제공하는 컨트롤 바 위젯.
    """

    filter_changed = pyqtSignal()
    thresholds_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # 1행: 검색 및 필터링
        row_top = QHBoxLayout()
        row_top.setSpacing(10)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.textChanged.connect(self.filter_changed.emit)

        lbl_zone = QLabel("화질 등급:")
        lbl_zone.setObjectName("SectionLabel")
        self.combo_zone = QComboBox()
        self.combo_zone.addItem("전체", "ALL")
        self.combo_zone.addItem("과화질", "OVER")
        self.combo_zone.addItem("적정화질", "OPTIMAL")
        self.combo_zone.addItem("저화질", "LOW")
        self.combo_zone.setFixedWidth(100)
        self.combo_zone.currentIndexChanged.connect(self.filter_changed.emit)

        lbl_target = QLabel("표시 대상:")
        lbl_target.setObjectName("SectionLabel")
        self.combo_target = QComboBox()
        self.combo_target.addItem("전체", "ALL")
        self.combo_target.addItem("인코딩 대상", "ENCODE_ONLY")
        self.combo_target.addItem("원본 보존", "PASS_ONLY")
        self.combo_target.addItem("키프레임 불량", "DEFECT_KF")
        self.combo_target.setFixedWidth(120)
        self.combo_target.currentIndexChanged.connect(self.filter_changed.emit)

        self.lbl_filter_count = QLabel("표시: 0 / 0개")
        self.lbl_filter_count.setObjectName("SectionLabel")
        self.lbl_filter_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_filter_count.setMinimumWidth(110)

        row_top.addWidget(self.search_edit, stretch=1)
        row_top.addWidget(lbl_zone)
        row_top.addWidget(self.combo_zone)
        row_top.addSpacing(4)
        row_top.addWidget(lbl_target)
        row_top.addWidget(self.combo_target)
        row_top.addSpacing(6)
        row_top.addWidget(self.lbl_filter_count)
        main_layout.addLayout(row_top)

        # 2행: 임계값 조정 및 예외 규칙
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(10)

        lbl_over = QLabel("과화질 배수:")
        lbl_over.setObjectName("SectionLabel")
        self.spin_over_ratio = QDoubleSpinBox()
        self.spin_over_ratio.setRange(1.00, 3.00)
        self.spin_over_ratio.setSingleStep(0.05)
        self.spin_over_ratio.setDecimals(2)
        self.spin_over_ratio.setSuffix("x")
        self.spin_over_ratio.setValue(1.20)
        self.spin_over_ratio.setFixedWidth(85)
        self.spin_over_ratio.setToolTip(
            "최적 비트레이트 대비 과화질 판정 배수 (기본 1.20x = 720p H.264 기준 1800kbps)"
        )
        self.spin_over_ratio.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_kf = QLabel("KF 상한:")
        lbl_kf.setObjectName("SectionLabel")
        self.spin_kf_limit = QDoubleSpinBox()
        self.spin_kf_limit.setRange(0.5, 10.0)
        self.spin_kf_limit.setSingleStep(0.1)
        self.spin_kf_limit.setDecimals(1)
        self.spin_kf_limit.setSuffix("s")
        self.spin_kf_limit.setValue(2.2)
        self.spin_kf_limit.setFixedWidth(80)
        self.spin_kf_limit.setToolTip(
            "허용 가능한 최대 키프레임 간격 (초과 시 1초 키프레임 교정 대상으로 분류)"
        )
        self.spin_kf_limit.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_small = QLabel("소형 기준:")
        lbl_small.setObjectName("SectionLabel")
        self.spin_small_mb = QDoubleSpinBox()
        self.spin_small_mb.setRange(0.0, 500.0)
        self.spin_small_mb.setSingleStep(5.0)
        self.spin_small_mb.setDecimals(0)
        self.spin_small_mb.setSuffix("MB")
        self.spin_small_mb.setValue(30.0)
        self.spin_small_mb.setFixedWidth(85)
        self.spin_small_mb.setToolTip(
            "소형 클립(Footage)으로 판별할 용량 기준 (기본 30MB)"
        )
        self.spin_small_mb.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_exc = QLabel("예외 규칙:")
        lbl_exc.setObjectName("SectionLabel")
        self.edit_exceptions = QLineEdit()
        self._action_exc = self.edit_exceptions.addAction(
            get_icon("ban", palette=get_active_palette(), size=14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.edit_exceptions.setPlaceholderText(
            "파일명 키워드(예: sample, 무손실) 또는 파이썬 조건식(예: v.duration < 10, v.fps >= 60)"
        )
        self.edit_exceptions.setClearButtonEnabled(True)
        self.edit_exceptions.setToolTip(
            "파일명 키워드 또는 파이썬 람다/조건식(예: v.duration < 10, v.fps >= 60)을 입력하면 "
            "해당 영상을 인코딩에서 제외하고 원본 보존으로 분류합니다. (쉼표로 다중 등록 가능)"
        )
        self.edit_exceptions.textChanged.connect(self._on_spin_or_text_changed)

        row_bottom.addWidget(lbl_over)
        row_bottom.addWidget(self.spin_over_ratio)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_kf)
        row_bottom.addWidget(self.spin_kf_limit)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_small)
        row_bottom.addWidget(self.spin_small_mb)
        row_bottom.addSpacing(8)
        row_bottom.addWidget(lbl_exc)
        row_bottom.addWidget(self.edit_exceptions, stretch=1)
        main_layout.addLayout(row_bottom)

        self.init_theme_listener()

    def _on_spin_or_text_changed(self) -> None:
        if not self._updating:
            self.thresholds_changed.emit()

    def sync_from_config(self, cfg: ClassifierConfig) -> None:
        """
        - ClassifierConfig 설정 객체의 임계값을 스핀박스 컨트롤에 동기화.
        """
        self._updating = True
        try:
            self.spin_over_ratio.setValue(cfg.over_bitrate_ratio)
            self.spin_kf_limit.setValue(cfg.keyframe_threshold)
            self.spin_small_mb.setValue(cfg.small_footage_mb)
        finally:
            self._updating = False

    def apply_to_classifier(self, classifier: VideoClassifier) -> None:
        """
        - 현재 UI의 스핀박스 임계값과 예외 규칙들을 VideoClassifier 인스턴스에 반영.
        """
        classifier.set_over_bitrate_ratio(self.spin_over_ratio.value())
        classifier.set_keyframe_threshold(self.spin_kf_limit.value())
        classifier.set_small_footage_mb(self.spin_small_mb.value())

        classifier.clear_exception_rules()
        raw_text = self.edit_exceptions.text().strip()
        if raw_text:
            tokens = [
                t.strip()
                for t in raw_text.replace(";", ",").split(",")
                if t.strip()
            ]
            for token in tokens:
                rule_fn = parse_exception_token(token)
                if rule_fn is not None:
                    classifier.add_exception_rule(rule_fn)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 입력창 액션 아이콘 색상을 갱신.
        """
        if hasattr(self, "_action_search"):
            self._action_search.setIcon(
                get_icon("search", palette=palette, size=14, pad_right=7)
            )
        if hasattr(self, "_action_exc"):
            self._action_exc.setIcon(
                get_icon("ban", palette=palette, size=14, pad_right=7)
            )
