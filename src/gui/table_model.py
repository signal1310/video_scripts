"""VideoEvaluation 목록을 QTableView에 바인딩하는 Qt 테이블 뷰모델 모듈"""

import os
from collections import Counter
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from src.core.config import ClassifierConfig
from src.core.messages import (
    LABEL_MOVED,
    LABEL_NO_MOVE_TARGET,
    LABEL_ORIGINAL_LOCATION,
    LABEL_PSEUDO_CLASSIFY,
    LABEL_UNMEASURED,
    LABEL_UNMEASURED_DETAIL,
    format_keyframe_tooltip,
    format_quality_zone_tooltip,
)
from src.core.models import (
    SECONDS_PER_MINUTE,
    QualityZone,
    SmartAction,
    VideoEvaluation,
)
from src.core.sorting import WindowsNameOrder, natural_sort_key
from src.gui.column_filters import (
    ColumnDataType,
    ColumnSpec,
    NumericBucketKind,
    build_numeric_buckets,
    resolve_numeric_bucket,
)
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_DEFAULT_HEADER_CONFIG = ClassifierConfig()


def format_duration(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    total_sec = max(round(seconds), 0)
    mins, secs = divmod(total_sec, SECONDS_PER_MINUTE)
    hours, mins = divmod(mins, SECONDS_PER_MINUTE)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class VideoTableModel(QAbstractTableModel):
    """VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하는 테이블 뷰모델

    표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    config_changed = pyqtSignal()

    COL_STATUS = 0
    COL_NAME = 1
    COL_RES = 2
    COL_CODEC = 3
    COL_FPS = 4
    COL_DURATION = 5
    COL_SIZE = 6
    COL_BITRATE = 7
    COL_OPT_BITRATE = 8
    COL_RATIO = 9
    COL_ZONE = 10
    COL_KEYFRAME = 11
    COL_AFTER_SIZE = 12
    COL_SAVED_SIZE = 13
    COL_ACTION = 14

    _ZONE_SORT_RANK: ClassVar[dict[QualityZone, int]] = {
        QualityZone.LOW: 0,
        QualityZone.OPTIMAL: 1,
        QualityZone.OVER: 2,
    }

    HEADERS: ClassVar[list[str]] = [
        "분류 상태",
        "이름",
        "해상도",
        "코덱",
        "FPS",
        "길이",
        "용량",
        "b-rate",
        "최적 b-rate",
        "b-rate 비율",
        "화질 등급",
        "KF 간격",
        "예상후",
        "예상 절감",
        "인코딩 처방",
    ]

    HEADER_TOOLTIPS: ClassVar[dict[int, str]] = {
        0: "분류 상태 (현재 작업 폴더 내 위치 및 모의 분류 예정 상태)",
        1: "비디오 파일명 (호버 시 전체 절대 경로 표시)",
        2: "가로×세로 정규화 해상도 (↻: 회전 메타데이터 적용됨, 단위: px)",
        3: "비디오 코덱 포맷 (H.264, HEVC, AV1 등)",
        4: "초당 프레임 수 (단위: fps)",
        5: "영상 재생 시간 (시:분:초 포맷, HH:MM:SS)",
        6: "현재 비디오 파일 크기 (단위: MB)",
        7: "현재 비디오 비트레이트 (단위: kbps)",
        8: "CQ22 기준 해상도·프레임레이트·코덱 최적 비트레이트 (단위: kbps)",
        9: "최적 비트레이트 대비 현재 비트레이트 비율 (단위: 배, x)",
        10: format_quality_zone_tooltip(_DEFAULT_HEADER_CONFIG.thresholds),
        11: format_keyframe_tooltip(_DEFAULT_HEADER_CONFIG.thresholds),
        12: "인코딩 및 키프레임 교정 후 예상 파일 크기 (단위: MB)",
        13: "인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 교정 오버헤드, 단위: MB)",
        14: "최종 분류 및 인코딩 처방 (과화질 감축, 키프레임 교정, 원본 보존 등)",
    }

    COLUMN_SPECS: ClassVar[dict[int, ColumnSpec]] = {
        COL_STATUS: ColumnSpec(COL_STATUS, ColumnDataType.CATEGORY),
        COL_NAME: ColumnSpec(COL_NAME, ColumnDataType.TEXT),
        COL_RES: ColumnSpec(COL_RES, ColumnDataType.CATEGORY),
        COL_CODEC: ColumnSpec(COL_CODEC, ColumnDataType.CATEGORY),
        COL_FPS: ColumnSpec(
            COL_FPS,
            ColumnDataType.NUMBER,
            unit="fps",
            decimals=2,
            filter_step=1.0,
            bucket_kind=NumericBucketKind.FPS,
        ),
        COL_DURATION: ColumnSpec(
            COL_DURATION,
            ColumnDataType.NUMBER,
            unit="초",
            decimals=1,
            filter_step=10.0,
            bucket_kind=NumericBucketKind.DURATION,
        ),
        COL_SIZE: ColumnSpec(
            COL_SIZE,
            ColumnDataType.NUMBER,
            unit="MB",
            decimals=2,
            filter_step=5.0,
            bucket_kind=NumericBucketKind.SIZE,
        ),
        COL_BITRATE: ColumnSpec(
            COL_BITRATE,
            ColumnDataType.NUMBER,
            unit="kbps",
            decimals=0,
            filter_step=100.0,
            bucket_kind=NumericBucketKind.BITRATE,
        ),
        COL_OPT_BITRATE: ColumnSpec(
            COL_OPT_BITRATE,
            ColumnDataType.NUMBER,
            unit="kbps",
            decimals=0,
            filter_step=100.0,
            bucket_kind=NumericBucketKind.BITRATE,
        ),
        COL_RATIO: ColumnSpec(
            COL_RATIO,
            ColumnDataType.NUMBER,
            unit="배",
            decimals=2,
            filter_step=0.1,
            bucket_kind=NumericBucketKind.RATIO,
        ),
        COL_ZONE: ColumnSpec(
            COL_ZONE,
            ColumnDataType.ENUM,
            preset_values=tuple(z.value for z in reversed(QualityZone)),
        ),
        COL_KEYFRAME: ColumnSpec(
            COL_KEYFRAME,
            ColumnDataType.NUMBER,
            unit="초",
            decimals=2,
            filter_step=0.5,
            nullable_label=LABEL_UNMEASURED,
            bucket_kind=NumericBucketKind.KEYFRAME,
        ),
        COL_AFTER_SIZE: ColumnSpec(
            COL_AFTER_SIZE,
            ColumnDataType.NUMBER,
            unit="MB",
            decimals=2,
            filter_step=5.0,
            bucket_kind=NumericBucketKind.SIZE,
        ),
        COL_SAVED_SIZE: ColumnSpec(
            COL_SAVED_SIZE,
            ColumnDataType.NUMBER,
            unit="MB",
            decimals=2,
            filter_step=5.0,
            bucket_kind=NumericBucketKind.SAVED_SIZE,
        ),
        COL_ACTION: ColumnSpec(
            COL_ACTION,
            ColumnDataType.ENUM,
            preset_values=tuple(a.value for a in SmartAction),
        ),
    }

    def __init__(
        self,
        parent: Any | None = None,
        config: ClassifierConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._evaluations: list[VideoEvaluation] = []
        self._root_dir: str = ""
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._filter_summaries: dict[int, str] = {}
        self._color_cache: dict[str, QColor] = {}
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._bold_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)

    @classmethod
    def get_column_spec(cls, col: int) -> ColumnSpec:
        """지정 열의 데이터 타입·단위·선택지 스키마를 반환"""
        return cls.COLUMN_SPECS.get(col, ColumnSpec(col, ColumnDataType.TEXT))

    def set_filter_summaries(self, summaries: dict[int, str]) -> None:
        """헤더 툴팁에 표시할 활성 열 필터 요약 문구 맵을 갱신"""
        self._filter_summaries = dict(summaries)
        if self.HEADERS:
            self.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 0, len(self.HEADERS) - 1
            )

    def _get_cached_color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

    @property
    def config(self) -> ClassifierConfig:
        """현재 테이블 모델에 연결된 분류 임계치 설정 반환"""
        return self._config

    def set_config(self, config: ClassifierConfig) -> None:
        """활성 임계치 설정을 갱신해 화질·키프레임 열 헤더 툴팁과 필터 버킷을 최신화"""
        self._config = config
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal, self.COL_ZONE, self.COL_KEYFRAME
        )
        self.config_changed.emit()

    def set_data_source(
        self,
        evaluations: list[VideoEvaluation],
        root_dir: str = "",
        config: ClassifierConfig | None = None,
    ) -> None:
        """평가 결과 리스트와 현재 작업 폴더를 일괄 갱신"""
        self.beginResetModel()
        self._evaluations = list(evaluations)
        self._root_dir = root_dir
        cfg_updated = config is not None
        if config is not None:
            self._config = config
        self.endResetModel()
        if cfg_updated:
            self.config_changed.emit()

    @property
    def evaluations(self) -> list[VideoEvaluation]:
        """현재 모델에 로드된 VideoEvaluation 목록 반환"""
        return self._evaluations

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        """모델의 총 비디오 행 수 반환"""
        return 0 if parent.isValid() else len(self._evaluations)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        """테이블 컬럼 개수 반환"""
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """수평 헤더 텍스트·툴팁 및 정렬 방식 반환"""
        if (
            orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            if role == int(Qt.ItemDataRole.DisplayRole):
                return self.HEADERS[section]
            if role == int(Qt.ItemDataRole.ToolTipRole):
                if section == self.COL_ZONE:
                    base_tip = format_quality_zone_tooltip(self._config.thresholds)
                elif section == self.COL_KEYFRAME:
                    base_tip = format_keyframe_tooltip(self._config.thresholds)
                else:
                    base_tip = self.HEADER_TOOLTIPS.get(section, self.HEADERS[section])
                summary = self._filter_summaries.get(section)
                if summary:
                    return f"{base_tip}\n[활성 필터] {summary}"
                return base_tip
        return None

    @staticmethod
    def _format_status_label(ev: VideoEvaluation) -> str:
        vid = ev.video
        if vid.moved_dirname:
            return f"{LABEL_MOVED} · {vid.moved_dirname}"
        if ev.target_dirname:
            return f"{LABEL_PSEUDO_CLASSIFY} · {ev.target_dirname}"
        return LABEL_ORIGINAL_LOCATION

    def _get_status_info(
        self, ev: VideoEvaluation
    ) -> tuple[str, tuple[str, str, str]]:
        """현재 파일의 이동 상태(원본 위치, 모의 분류 예정, 실제 이동 완료) 텍스트와 뱃지 색상을 반환"""
        vid = ev.video
        p = ThemeManager().current_palette
        label = self._format_status_label(ev)
        if vid.moved_dirname:
            return label, p.badge_moved
        if ev.target_dirname:
            return label, p.badge_pseudo
        return label, p.badge_pass

    @classmethod
    def get_filter_value(cls, ev: VideoEvaluation, col: int) -> str | float | None:
        """지정 열의 동적 필터 평가용 정규화 값(문자열, 수치 또는 None) 반환"""
        vid = ev.video
        if col == cls.COL_STATUS:
            return cls._format_status_label(ev)
        if col == cls.COL_NAME:
            return vid.filename
        if col == cls.COL_RES:
            return f"{vid.width}×{vid.height}"
        if col == cls.COL_CODEC:
            return vid.codec
        if col == cls.COL_FPS:
            return float(vid.fps)
        if col == cls.COL_DURATION:
            return float(vid.duration)
        if col == cls.COL_SIZE:
            return float(vid.vid_size_MB)
        if col == cls.COL_BITRATE:
            return float(vid.vid_kbps)
        if col == cls.COL_OPT_BITRATE:
            return float(ev.optimal_kbps)
        if col == cls.COL_RATIO:
            return float(ev.bitrate_ratio)
        if col == cls.COL_ZONE:
            return ev.quality_zone.value
        if col == cls.COL_KEYFRAME:
            if vid.keyframe_interval is None or vid.keyframe_interval < 0:
                return None
            return float(vid.keyframe_interval)
        if col == cls.COL_AFTER_SIZE:
            return float(ev.expected_after_mb)
        if col == cls.COL_SAVED_SIZE:
            return float(ev.expected_delta_mb)
        if col == cls.COL_ACTION:
            return ev.smart_action.value
        return None

    def collect_column_value_counts(self, col: int) -> list[tuple[str, int]]:
        """열거형·범주형 열에 대해 현재 데이터셋의 고유 값별 행 수 목록 반환"""
        spec = self.get_column_spec(col)
        counts: Counter[str] = Counter()
        for ev in self._evaluations:
            val = self.get_filter_value(ev, col)
            counts[str(val or "")] += 1

        if spec.data_type is ColumnDataType.ENUM and spec.preset_values:
            ordered = [(val, counts.get(val, 0)) for val in spec.preset_values]
            extras = sorted(k for k in counts if k not in spec.preset_values)
            ordered.extend((k, counts[k]) for k in extras)
            return ordered

        if col == self.COL_RES:
            def _res_key(label: str) -> tuple[int, int, str]:
                parts = label.split("×", 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    w, h = int(parts[0]), int(parts[1])
                    return (-(w * h), -w, label)
                return (0, 0, label)

            return sorted(counts.items(), key=lambda item: _res_key(item[0]))

        return sorted(counts.items(), key=lambda item: natural_sort_key(item[0]))

    def collect_numeric_bounds(
        self, col: int
    ) -> tuple[float | None, float | None, int]:
        """수치형 열의 현재 데이터셋 최솟값, 최댓값 및 미측정(None) 행 수 반환"""
        values: list[float] = []
        null_count = 0
        for ev in self._evaluations:
            val = self.get_filter_value(ev, col)
            if val is None:
                null_count += 1
            elif isinstance(val, int | float):
                values.append(float(val))
        if not values:
            return None, None, null_count
        return min(values), max(values), null_count

    def collect_numeric_bucket_counts(
        self, col: int
    ) -> list[tuple[str, str, int]]:
        """수치형 열의 측정값을 도메인 구간 버킷별로 집계해 비어 있지 않은 목록 반환"""
        spec = self.get_column_spec(col)
        if spec.data_type is not ColumnDataType.NUMBER:
            return []

        buckets = build_numeric_buckets(spec.bucket_kind, self._config)
        if not buckets:
            return []

        counts: Counter[str] = Counter()
        for ev in self._evaluations:
            val = self.get_filter_value(ev, col)
            if isinstance(val, int | float):
                bucket = resolve_numeric_bucket(float(val), buckets)
                if bucket is not None:
                    counts[bucket.key] += 1

        return [
            (bucket.key, bucket.label, counts[bucket.key])
            for bucket in buckets
            if counts[bucket.key] > 0
        ]

    def _display_data(self, ev: VideoEvaluation, col: int) -> Any:
        vid = ev.video
        if col == self.COL_STATUS:
            text, _ = self._get_status_info(ev)
            return text
        if col == self.COL_NAME:
            return vid.filename
        if col == self.COL_RES:
            rot_mark = " ↻" if vid.rotate_type in (90, 270) else ""
            return f"{vid.width}×{vid.height}{rot_mark}"
        if col == self.COL_CODEC:
            return vid.codec
        if col == self.COL_FPS:
            return f"{vid.fps:.2f}"
        if col == self.COL_DURATION:
            return format_duration(vid.duration)
        if col == self.COL_SIZE:
            return f"{vid.vid_size_MB:,.2f}"
        if col == self.COL_BITRATE:
            return f"{vid.vid_kbps}"
        if col == self.COL_OPT_BITRATE:
            return f"{ev.optimal_kbps}"
        if col == self.COL_RATIO:
            return f"{ev.bitrate_ratio:.2f}"
        if col == self.COL_ZONE:
            return ev.quality_zone.value
        if col == self.COL_KEYFRAME:
            if vid.keyframe_interval is None or vid.keyframe_interval < 0:
                return LABEL_UNMEASURED
            warn = " ⚠" if ev.is_keyframe_defective else ""
            return f"{vid.keyframe_interval:.2f}{warn}"
        if col == self.COL_AFTER_SIZE:
            return f"{ev.expected_after_mb:,.2f}"
        if col == self.COL_SAVED_SIZE:
            if abs(ev.expected_delta_mb) < 0.005:
                return "0.00"
            if ev.expected_delta_mb > 0:
                return f"+{ev.expected_delta_mb:,.2f}"
            return f"{ev.expected_delta_mb:,.2f}"
        if col == self.COL_ACTION:
            return ev.smart_action.value
        return None

    def _sort_data(self, ev: VideoEvaluation, col: int) -> Any:
        vid = ev.video
        if col == self.COL_STATUS:
            text, _ = self._get_status_info(ev)
            return text
        if col == self.COL_NAME:
            return WindowsNameOrder(vid.filename)
        if col == self.COL_RES:
            return vid.width * vid.height
        if col == self.COL_CODEC:
            return vid.codec.lower()
        if col == self.COL_FPS:
            return vid.fps
        if col == self.COL_DURATION:
            return vid.duration
        if col == self.COL_SIZE:
            return vid.vid_size_MB
        if col == self.COL_BITRATE:
            return vid.vid_kbps
        if col == self.COL_OPT_BITRATE:
            return ev.optimal_kbps
        if col == self.COL_RATIO:
            return ev.bitrate_ratio
        if col == self.COL_ZONE:
            return self._ZONE_SORT_RANK.get(ev.quality_zone, 0)
        if col == self.COL_KEYFRAME:
            return (
                vid.keyframe_interval
                if vid.keyframe_interval is not None
                else -1.0
            )
        if col == self.COL_AFTER_SIZE:
            return ev.expected_after_mb
        if col == self.COL_SAVED_SIZE:
            return ev.expected_delta_mb
        if col == self.COL_ACTION:
            return ev.smart_action.value
        return None

    def _badge_data(self, ev: VideoEvaluation, col: int) -> Any:
        p = ThemeManager().current_palette
        if col == self.COL_STATUS:
            _, badge_colors = self._get_status_info(ev)
            return badge_colors
        if col == self.COL_ZONE:
            if ev.quality_zone == QualityZone.OVER:
                return p.badge_over
            if ev.quality_zone == QualityZone.OPTIMAL:
                return p.badge_optimal
            return p.badge_low
        if col == self.COL_ACTION:
            if ev.smart_action in (
                SmartAction.ENCODE_CQ22_OVER,
                SmartAction.ENCODE_CQ22_OPT_KF,
            ):
                return p.badge_std_encode
            if ev.smart_action == SmartAction.ENCODE_LOW_KF:
                return p.badge_low_kf_encode
            return p.badge_pass
        return None

    def _foreground_data(self, ev: VideoEvaluation, col: int) -> QColor | None:
        p = ThemeManager().current_palette
        if col == self.COL_RATIO:
            if ev.quality_zone == QualityZone.OVER:
                return self._get_cached_color(p.fg_ratio_over)
            if ev.quality_zone == QualityZone.OPTIMAL:
                return self._get_cached_color(p.fg_ratio_opt)
            return self._get_cached_color(p.fg_ratio_low)
        if col == self.COL_KEYFRAME:
            if ev.is_keyframe_defective:
                return self._get_cached_color(p.fg_kf_defect)
            if ev.video.keyframe_interval is None:
                return self._get_cached_color(p.text_muted)
            return self._get_cached_color(p.fg_kf_opt)
        if col == self.COL_SAVED_SIZE:
            if ev.expected_delta_mb > 0.05:
                return self._get_cached_color(p.fg_saved_pos)
            if ev.expected_delta_mb < -0.05:
                return self._get_cached_color(p.fg_saved_neg)
            return self._get_cached_color(p.text_muted)
        return None

    def _tooltip_data(self, ev: VideoEvaluation) -> str:
        vid = ev.video
        rel_path = (
            os.path.join(vid.moved_dirname, vid.filename)
            if vid.moved_dirname
            else vid.filename
        )
        full_path = os.path.join(self._root_dir, rel_path) if self._root_dir else rel_path
        kf_str = (
            f"{vid.keyframe_interval:.3f}초"
            if vid.keyframe_interval is not None
            else LABEL_UNMEASURED_DETAIL
        )
        target_str = ev.target_dirname or LABEL_NO_MOVE_TARGET
        return (
            f"파일명: {vid.filename}\n"
            f"경로: {full_path}\n"
            f"해상도: {vid.width}x{vid.height} (비율 {vid.aspect_ratio:.3f}, 회전 {vid.rotate_type}°)\n"
            f"코덱 / FPS: {vid.codec} / {vid.fps:.3f} fps\n"
            f"비트레이트: 현재 {vid.vid_kbps:,} kbps / 최적 {ev.optimal_kbps:,} kbps ({ev.bitrate_ratio:.3f}배)\n"
            f"키프레임 간격: {kf_str}\n"
            f"용량 변화: {vid.vid_size_MB:.2f} MB → {ev.expected_after_mb:.2f} MB (절감 {ev.expected_delta_mb:+.2f} MB)\n"
            f"처방: {ev.smart_action.value}\n"
            f"분류 대상 폴더: {target_str}"
        )

    def data(
        self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> Any:
        """지정 셀의 표시 문자열, 정렬 값, 배지, 툴팁 및 폰트 등 역할별 데이터 반환"""
        if not index.isValid() or not (0 <= index.row() < len(self._evaluations)):
            return None

        ev: VideoEvaluation = self._evaluations[index.row()]
        col = index.column()

        if role == self.EVAL_ROLE:
            return ev
        if role == int(Qt.ItemDataRole.DisplayRole):
            return self._display_data(ev, col)
        if role == self.SORT_ROLE:
            return self._sort_data(ev, col)
        if role == self.BADGE_ROLE:
            return self._badge_data(ev, col)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            if col == self.COL_NAME:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if col in (
                self.COL_STATUS,
                self.COL_RES,
                self.COL_CODEC,
                self.COL_ZONE,
                self.COL_KEYFRAME,
                self.COL_ACTION,
            ):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == int(Qt.ItemDataRole.ForegroundRole):
            return self._foreground_data(ev, col)
        if role == int(Qt.ItemDataRole.FontRole):
            if col in (self.COL_RATIO, self.COL_SAVED_SIZE):
                return self._bold_font
            if col in (
                self.COL_FPS,
                self.COL_SIZE,
                self.COL_BITRATE,
                self.COL_OPT_BITRATE,
                self.COL_AFTER_SIZE,
            ):
                return self._mono_font
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return self._tooltip_data(ev)
        return None
