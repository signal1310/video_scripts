import os
from typing import Any, ClassVar

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont

from src.core.config import ClassifierConfig
from src.core.file_manager import file_exists_in
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
from src.core.sorting import WindowsNameOrder
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY, FONT_UI_PT
from src.gui.theme import ThemeManager

_EMPTY_INDEX = QModelIndex()
_DEFAULT_HEADER_CONFIG = ClassifierConfig()


def format_duration(seconds: float) -> str:
    """
    - 초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환.
    """
    total_sec = max(round(seconds), 0)
    mins, secs = divmod(total_sec, SECONDS_PER_MINUTE)
    hours, mins = divmod(mins, SECONDS_PER_MINUTE)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class VideoTableModel(QAbstractTableModel):
    """
    - VideoEvaluation 목록을 PyQt6 QTableView에 바인딩하기 위한 테이블 뷰모델.
    - 표시 텍스트, 정렬 수치(SORT_ROLE), 뱃지 스타일(BADGE_ROLE), 도메인 객체(EVAL_ROLE) 제공.
    """

    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    EVAL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

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
        10: format_quality_zone_tooltip(_DEFAULT_HEADER_CONFIG),
        11: format_keyframe_tooltip(_DEFAULT_HEADER_CONFIG),
        12: "인코딩 및 키프레임 교정 후 예상 파일 크기 (단위: MB)",
        13: "인코딩 후 예상 절감량 (+: 절감, -: 1초 키프레임 교정 오버헤드, 단위: MB)",
        14: "최종 분류 및 인코딩 처방 (과화질 감축, 키프레임 교정, 원본 보존 등)",
    }

    def __init__(
        self,
        parent: Any | None = None,
        config: ClassifierConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._evaluations: list[VideoEvaluation] = []
        self._root_dir: str = ""
        self._pseudo_mode: bool = True
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._in_root_map: dict[str, bool] = {}
        self._in_moved_map: dict[str, bool] = {}
        self._color_cache: dict[str, QColor] = {}
        self._mono_font = QFont(FONT_MONO_FAMILY, FONT_UI_PT)
        self._bold_font = QFont(FONT_UI_FAMILY, FONT_UI_PT, QFont.Weight.Bold)

    def _get_cached_color(self, hex_str: str) -> QColor:
        color = self._color_cache.get(hex_str)
        if color is None:
            color = QColor(hex_str)
            self._color_cache[hex_str] = color
        return color

    def set_config(self, config: ClassifierConfig) -> None:
        """활성 임계치 설정을 갱신해 화질·키프레임 열 헤더 툴팁을 최신화."""
        self._config = config
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal, self.COL_ZONE, self.COL_KEYFRAME
        )

    def set_data_source(
        self,
        evaluations: list[VideoEvaluation],
        root_dir: str,
        pseudo_mode: bool,
        config: ClassifierConfig | None = None,
    ) -> None:
        """
        - 평가 결과 리스트와 현재 작업 폴더 및 모의 분류 상태를 일괄 갱신.
        """
        self.beginResetModel()
        self._evaluations = list(evaluations)
        self._root_dir = root_dir
        self._pseudo_mode = pseudo_mode
        if config is not None:
            self._config = config
        root_valid = bool(root_dir and os.path.isdir(root_dir))
        moved_evals = [ev for ev in self._evaluations if ev.video.moved_dirname]
        self._in_root_map = {
            ev.video.filename: (
                file_exists_in(root_dir, ev.video.filename) if root_valid else True
            )
            for ev in moved_evals
        }
        self._in_moved_map = {
            ev.video.filename: (
                file_exists_in(
                    os.path.join(root_dir, ev.video.moved_dirname or ""),
                    ev.video.filename,
                )
                if root_valid
                else False
            )
            for ev in moved_evals
        }
        self.endResetModel()

    @property
    def evaluations(self) -> list[VideoEvaluation]:
        return self._evaluations

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self._evaluations)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            if role == int(Qt.ItemDataRole.DisplayRole):
                return self.HEADERS[section]
            if role == int(Qt.ItemDataRole.ToolTipRole):
                if section == self.COL_ZONE:
                    return format_quality_zone_tooltip(self._config)
                if section == self.COL_KEYFRAME:
                    return format_keyframe_tooltip(self._config)
                return self.HEADER_TOOLTIPS.get(section, self.HEADERS[section])
        return None

    def _get_status_info(
        self, ev: VideoEvaluation
    ) -> tuple[str, tuple[str, str, str]]:
        """
        - 현재 파일의 이동 상태(원본 위치, 모의 분류 예정, 실제 이동 완료) 텍스트와 뱃지 색상을 반환.
        """
        vid = ev.video
        p = ThemeManager().current_palette
        if vid.moved_dirname:
            is_still_in_root = self._in_root_map.get(vid.filename, True)
            is_in_moved = self._in_moved_map.get(vid.filename, False)
            if not is_still_in_root and (is_in_moved or not self._pseudo_mode):
                return f"{LABEL_MOVED} · {vid.moved_dirname}", p.badge_moved
            return f"{LABEL_PSEUDO_CLASSIFY} · {vid.moved_dirname}", p.badge_pseudo
        return LABEL_ORIGINAL_LOCATION, p.badge_pass

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
        if col in (self.COL_RATIO, self.COL_ZONE):
            return ev.bitrate_ratio
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
        full_path = (
            os.path.join(self._root_dir, vid.filename)
            if self._root_dir
            else vid.filename
        )
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
