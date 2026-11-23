"""화질 비교 세션 정보와 메타 줄, 점수 칩, 재생 위치 표시 동기화

- 세션은 창이 여는 영상 한 건의 비교 계획과 재생 정보, 캐시된 화질 분석 점수를 담음
- 표시 갱신은 위젯을 직접 조작하되 계산과 문구는 `frame_compare.py` 순수 함수를 재사용
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    RESULT_TARGET_KEY,
    ComparePlan,
    FrameSource,
    analysis_score_text,
    format_timecode,
)
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.gui.dialogs.frame_compare_canvas import FrameCompareCanvas
from src.gui.dialogs.frame_compare_layout import (
    ORIGINAL_PATH_TIP,
    RESULT_PATH_TIP,
)
from src.gui.preview.video_preview_controls import PlaybackControls
from src.service.frame_compare_decoder import DecodedFrame

ROW_SPACING_PX: int = 8
SCORE_TIP_TEMPLATE: str = (
    "화질 분석에서 잰 {tier} 축소 후보의 점수입니다. "
    "VMAF는 100에 가까울수록, SSIM은 1에 가까울수록 원본과 비슷합니다."
)
_MS_PER_SECOND: int = 1000


@dataclass(frozen=True, slots=True)
class CompareMeta:
    """메타 줄 위젯 묶음"""

    original: QLabel
    target: QLabel
    time: QLabel


@dataclass(frozen=True, slots=True)
class CompareSession:
    """화질 비교 창이 여는 영상 한 건의 비교 계획, 재생 정보와 캐시된 화질 분석 점수"""

    plan: ComparePlan
    duration_sec: float
    fps: float
    filename: str
    position_sec: float = 0.0
    scores: tuple[TierScore, ...] = ()
    width: int = 0
    height: int = 0
    source_path: str = ""


def build_meta_row(parent: QWidget) -> CompareMeta:
    """원본, 비교 대상, 시점을 한 줄로 보여 주는 메타 줄"""
    row = QWidget(parent)
    row.setObjectName("CompareMeta")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(14, 7, 14, 7)
    layout.setSpacing(ROW_SPACING_PX)
    labels = (QLabel("", row), QLabel("", row), QLabel("", row))
    for index, label in enumerate(labels):
        label.setObjectName("CompareMetaText")
        if index:
            separator = QLabel("/", row)
            separator.setObjectName("CompareMetaSeparator")
            layout.addWidget(separator)
        layout.addWidget(label)
    layout.addStretch(1)
    return CompareMeta(*labels)


def build_score_label(parent: QWidget) -> QLabel:
    """메타 줄에 인라인으로 붙는 화질 분석 점수 칩"""
    label = QLabel("", parent)
    label.setObjectName("CompareScoreChip")
    label.hide()
    return label


def frame_image(frame: DecodedFrame | None, fallback: bytes) -> QImage:
    """메모리 디코더의 RGB24 프레임이나 FFmpeg가 만든 BMP 바이트를 QImage로 바꿈"""
    if frame is None:
        return QImage.fromData(fallback, "BMP")
    return QImage(
        frame.data, frame.width, frame.height, frame.bytes_per_line, QImage.Format.Format_RGB888
    )


def sync_position(
    timeline: PlaybackControls, meta: CompareMeta, position_sec: float, duration_sec: float
) -> None:
    """재생 위치를 막대, 시각 표기, 메타 줄에 반영"""
    timeline.set_duration(round(duration_sec * _MS_PER_SECOND))
    timeline.set_position(round(position_sec * _MS_PER_SECOND))
    meta.time.setText(f"시점 {format_timecode(position_sec)}")


def sync_target_labels(
    meta: CompareMeta,
    target_button: QToolButton,
    canvas: FrameCompareCanvas,
    plan: ComparePlan,
    target: FrameSource,
) -> None:
    """원본과 비교 대상 라벨, 경로 툴팁, 캔버스 라벨을 맞춤"""
    meta.original.setText(f"원본 {plan.original.detail}")
    meta.original.setToolTip(ORIGINAL_PATH_TIP.format(path=plan.original.path))
    meta.target.setText(f"{target.label} {target.detail}")
    if target.key == RESULT_TARGET_KEY:
        meta.target.setToolTip(RESULT_PATH_TIP.format(path=target.path))
    else:
        meta.target.setToolTip(target.tooltip)
    target_button.setText(target.label)
    canvas.set_labels(plan.original.label, target.label, target.tooltip)


def show_score(
    score_label: QLabel, target: FrameSource, scores: tuple[TierScore, ...]
) -> None:
    """축소 비교 대상의 캐시된 화질 분석 점수를 표시, 없으면 숨김"""
    if target.tier_key is None:
        score_label.hide()
        return
    score = next((item for item in scores if item.tier == target.tier_key), None)
    if score is None:
        score_label.hide()
        return
    tier = RESOLUTION_TIER_BY_KEY.get(target.tier_key)
    display = target.tier_key if tier is None else tier.display
    text = analysis_score_text(display, score.vmaf, score.ssim)
    if not text:
        score_label.hide()
        return
    score_label.setText(text)
    score_label.setToolTip(SCORE_TIP_TEMPLATE.format(tier=display))
    score_label.show()
