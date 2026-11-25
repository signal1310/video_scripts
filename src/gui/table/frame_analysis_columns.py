"""화질 분석 표 열 세 개(활성 화면, 축소 후보, 측정 지표)의 셀 문구, 정렬, 필터, 툴팁 모듈

- 표 모델이 파일명별 `FrameAnalysisResult` 캐시를 보관하고, 이 모듈의 함수로 값을 계산
- 문구는 `TERMINOLOGY.md` 6.15절을 따르며, 축소 후보 툴팁에는 규격별 점수와 SSIM 대체 안내를 항상 포함
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from src.core.analysis.frame_analysis import is_crop_only_result
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.models import VideoEvaluation
from src.core.quality_probe import METRIC_SSIM, METRIC_VMAF
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY

if TYPE_CHECKING:
    from src.gui.styles.palettes import ThemePalette

ACTIVE_FRAME_COLUMN: int = 19
DOWNSCALE_CANDIDATE_COLUMN: int = 20
QUALITY_METRIC_COLUMN: int = 21
FRAME_ANALYSIS_COLUMNS: frozenset[int] = frozenset(
    {ACTIVE_FRAME_COLUMN, DOWNSCALE_CANDIDATE_COLUMN, QUALITY_METRIC_COLUMN}
)

ACTIVE_FRAME_FULL_TEXT: str = "-"
ACTIVE_FRAME_VR_TEXT: str = "VR 보호"
DOWNSCALE_KEEP_TEXT: str = "유지"
DOWNSCALE_UNMEASURABLE_TEXT: str = "측정 불가"
METRIC_VMAF_TEXT: str = "VMAF"
METRIC_SSIM_FALLBACK_TEXT: str = "SSIM (대체)"

_NO_RESULT_TIP: str = (
    "화질 분석 전이라 아직 측정하지 않았습니다. "
    "분석 메뉴에서 선택 영상 화질 분석이나 전체 영상 화질 분석을 실행하세요."
)
_ACTIVE_FRAME_TIP: str = (
    "검은 띠를 뺀 활성 화면 크기입니다. 이 크기로 화질 기준값과 축소 후보를 계산합니다."
)
_ACTIVE_FRAME_FULL_TIP: str = "검은 띠가 없어 전체 화면을 활성 화면으로 씁니다."
_ACTIVE_FRAME_EXCLUDED_TIP: str = "검은 여백 잘라내기 제외됨 (원본 유지)"
_ACTIVE_FRAME_VR_TIP: str = (
    "VR 영상으로 보호하고 있습니다. 검은 띠 감지와 잘라내기에서만 보호하며 축소 후보는 정상 측정합니다."
)
_SSIM_SUBSTITUTE_TIP: str = (
    "VMAF를 쓸 수 없어 SSIM으로 대신 쟀습니다. "
    "SSIM은 화질 차이를 0~1로 나타내며 1에 가까울수록 원본과 비슷합니다."
)
_DOWNSCALE_KEEP_TIP: str = (
    "화질 분석에서 축소 기준을 넘는 규격이 없어 원래 해상도를 유지하는 편이 좋습니다."
)
_DOWNSCALE_CANDIDATE_TIP: str = "화질 분석에서 {tier}로 줄여도 화질 차이가 기준 안에 있다고 판정했습니다."
_DOWNSCALE_UNMEASURABLE_TIP: str = "유효 구간이 없어 축소 후보를 판정하지 못했습니다."
_CROP_ONLY_CANDIDATE_TIP: str = (
    "검은 여백만 감지했습니다. 축소 후보를 확인하려면 화질 분석을 실행하세요."
)
_CROP_ONLY_METRIC_TIP: str = "검은 여백만 감지했습니다. 측정 지표를 보려면 화질 분석을 실행하세요."
_SCORE_HEADER: str = "규격별 왕복 점수:"
_NO_SCORE_TEXT: str = "- 점수 없음"
_METRIC_VMAF_TIP: str = (
    "축소 후보 판정에 쓴 지표입니다. VMAF는 원본과의 화질 차이를 0~100으로 나타내며 높을수록 원본과 비슷합니다."
)
_METRIC_SSIM_TIP: str = "축소 후보 판정에 SSIM을 대신 썼습니다. 화질 차이를 0~1로 나타내며 1에 가까울수록 원본과 비슷합니다."


def tier_display(tier_key: str) -> str:
    """규격 키의 표시 이름(예: 1080p), 모르는 키는 그대로 반환"""
    tier = RESOLUTION_TIER_BY_KEY.get(tier_key)
    return tier.display if tier is not None else tier_key


def contiguous_row_ranges(rows: Iterable[int]) -> list[tuple[int, int]]:
    """행 번호 목록을 연속 구간 (첫 행, 끝 행) 목록으로 묶음"""
    ranges: list[tuple[int, int]] = []
    ordered = sorted(rows)
    if not ordered:
        return ranges
    first = previous = ordered[0]
    for row in ordered[1:]:
        if row != previous + 1:
            ranges.append((first, previous))
            first = row
        previous = row
    ranges.append((first, previous))
    return ranges


def result_summary_text(result: FrameAnalysisResult | None) -> str:
    """표 열 세 개가 보여 주는 문구를 합친 한 줄 요약(빈 값은 뺌)"""
    if result is None:
        return ""
    parts = [
        active_frame_text(result),
        downscale_candidate_text(result),
        quality_metric_text(result),
    ]
    return ", ".join(part for part in parts if part)


def active_frame_text(result: FrameAnalysisResult | None) -> str:
    """활성 화면 셀 문구: 미측정 빈칸, VR 보호, 검은 띠 크기, 전체"""
    if result is None:
        return ""
    if result.is_vr_protected:
        return ACTIVE_FRAME_VR_TEXT
    if result.active_region.has_black_bars:
        return f"{result.active_region.width}x{result.active_region.height}"
    return ACTIVE_FRAME_FULL_TEXT


def active_frame_tooltip(result: FrameAnalysisResult | None, *, excluded: bool = False) -> str:
    """활성 화면 셀 툴팁. 잘라내기를 쓰지 않는 영상은 그 사유를 보여 줌"""
    if result is None:
        return _NO_RESULT_TIP
    if result.is_vr_protected:
        return _ACTIVE_FRAME_VR_TIP
    if result.active_region.has_black_bars:
        return _ACTIVE_FRAME_EXCLUDED_TIP if excluded else _ACTIVE_FRAME_TIP
    return _ACTIVE_FRAME_FULL_TIP


def active_frame_sort_value(result: FrameAnalysisResult | None) -> tuple[int, int]:
    """활성 화면 정렬 값: 미측정을 가장 앞에 두고 화면 면적 비교"""
    if result is None:
        return (0, 0)
    return (1, result.active_region.width * result.active_region.height)


def downscale_candidate_text(result: FrameAnalysisResult | None) -> str:
    """축소 후보 셀 문구: 미측정, 감지 전용 빈칸, 측정 불가, 유지, 규격명"""
    if result is None:
        return ""
    if is_crop_only_result(result):
        return ""
    if result.unmeasurable:
        return DOWNSCALE_UNMEASURABLE_TEXT
    if result.candidate_tier is None:
        return DOWNSCALE_KEEP_TEXT
    return tier_display(result.candidate_tier)


def _score_lines(result: FrameAnalysisResult) -> list[str]:
    """규격별 점수 줄 목록. 값이 있는 지표만 표시"""
    lines: list[str] = []
    for score in result.tier_scores:
        parts: list[str] = []
        if score.vmaf is not None:
            parts.append(f"VMAF {score.vmaf:.1f}")
        if score.ssim is not None:
            parts.append(f"SSIM {score.ssim:.4f}")
        if parts:
            lines.append(f"- {tier_display(score.tier)}: {', '.join(parts)}")
    return lines


def _score_block(result: FrameAnalysisResult) -> list[str]:
    """규격별 점수 머리글과 점수 줄 목록"""
    return [_SCORE_HEADER, *(_score_lines(result) or [_NO_SCORE_TEXT])]


def _valid_segment_text(result: FrameAnalysisResult) -> str:
    """유효 구간 수 문구"""
    return f"유효 구간: {result.valid_segment_count}/{result.total_segment_count}"


def downscale_candidate_tooltip(result: FrameAnalysisResult | None) -> str:
    """축소 후보 셀 툴팁: 감지 전용 안내, 판정 요지, 규격별 점수, 유효 구간, SSIM 대체 안내"""
    if result is None:
        return _NO_RESULT_TIP
    if is_crop_only_result(result):
        return _CROP_ONLY_CANDIDATE_TIP
    if result.unmeasurable:
        lines = [_DOWNSCALE_UNMEASURABLE_TIP, _valid_segment_text(result)]
    elif result.candidate_tier is None:
        lines = [_DOWNSCALE_KEEP_TIP, *_score_block(result), _valid_segment_text(result)]
    else:
        lines = [
            _DOWNSCALE_CANDIDATE_TIP.format(tier=tier_display(result.candidate_tier)),
            *_score_block(result),
            _valid_segment_text(result),
        ]
    if result.metric_used == METRIC_SSIM:
        lines.append(_SSIM_SUBSTITUTE_TIP)
    return "\n".join(lines)


def downscale_candidate_sort_value(result: FrameAnalysisResult | None) -> tuple[int, int]:
    """축소 후보 정렬 값: 미측정, 감지 전용, 측정 불가, 유지, 후보 규격 순서"""
    if result is None or is_crop_only_result(result):
        return (0, 0)
    if result.unmeasurable:
        return (1, 0)
    if result.candidate_tier is None:
        return (2, 0)
    tier = RESOLUTION_TIER_BY_KEY.get(result.candidate_tier)
    return (3, 0 if tier is None else tier.short_side)


def quality_metric_text(result: FrameAnalysisResult | None) -> str:
    """측정 지표 셀 문구: 미측정, 감지 전용 빈칸, VMAF, SSIM 대체"""
    if result is None or is_crop_only_result(result):
        return ""
    if result.metric_used == METRIC_SSIM:
        return METRIC_SSIM_FALLBACK_TEXT
    if result.metric_used == METRIC_VMAF:
        return METRIC_VMAF_TEXT
    return result.metric_used


def quality_metric_tooltip(result: FrameAnalysisResult | None) -> str:
    """측정 지표 셀 툴팁"""
    if result is None:
        return _NO_RESULT_TIP
    if is_crop_only_result(result):
        return _CROP_ONLY_METRIC_TIP
    return _METRIC_SSIM_TIP if result.metric_used == METRIC_SSIM else _METRIC_VMAF_TIP


def quality_metric_sort_value(result: FrameAnalysisResult | None) -> tuple[int, str]:
    """측정 지표 정렬 값: 미측정과 감지 전용을 앞에 두고, SSIM 대체, VMAF 순서"""
    if result is None or is_crop_only_result(result):
        return (0, "")
    return (1 if result.metric_used == METRIC_SSIM else 2, result.metric_used)


class FrameAnalysisResults:
    """파일명별 화질 분석 결과와 수동 VR 보호, 잘라내기 제외 목록을 보관하는 표 모델 보조"""

    def __init__(self) -> None:
        self._results: dict[str, FrameAnalysisResult] = {}
        self._manual_vr: frozenset[str] = frozenset()
        self._crop_excluded: frozenset[str] = frozenset()

    def set_results(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """결과 캐시 전체 교체"""
        self._results = dict(results)

    def update_results(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """결과 캐시에 도착한 결과를 파일명별로 병합"""
        self._results.update(results)

    def set_manual_vr(self, filenames: Iterable[str]) -> None:
        """수동 VR 보호 파일명 집합 교체"""
        self._manual_vr = frozenset(filenames)

    def set_crop_excluded(self, filenames: Iterable[str]) -> None:
        """검은 여백 잘라내기 제외 파일명 집합 교체"""
        self._crop_excluded = frozenset(filenames)

    def result(self, filename: str) -> FrameAnalysisResult | None:
        """파일명의 화질 분석 결과, 없으면 None"""
        return self._results.get(filename)

    def is_manual_vr(self, filename: str) -> bool:
        """파일명이 수동 VR 보호 대상인지 여부"""
        return filename in self._manual_vr

    def is_crop_excluded(self, filename: str) -> bool:
        """파일명이 검은 여백 잘라내기 제외 대상인지 여부"""
        return filename in self._crop_excluded

    @property
    def has_results(self) -> bool:
        """표시할 분석 결과가 있는지 여부"""
        return bool(self._results)

    def candidate_tier(self, filename: str) -> str | None:
        """파일명의 축소 후보 규격 키. 감지 전용, 후보 없음, 측정 불가면 None"""
        result = self._results.get(filename)
        if result is None or result.unmeasurable or is_crop_only_result(result):
            return None
        return result.candidate_tier

    def candidates(self, evals: Sequence[VideoEvaluation]) -> dict[str, str]:
        """선택 영상 중 축소 후보가 있는 파일명과 규격 키"""
        found: dict[str, str] = {}
        for ev in evals:
            tier = self.candidate_tier(ev.video.filename)
            if tier is not None:
                found[ev.video.filename] = tier
        return found

    def display(self, filename: str, col: int) -> str:
        """화질 분석 열의 표시 문구"""
        result = self._results.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            return active_frame_text(result)
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_text(result)
        return quality_metric_text(result)

    def filter_value(self, filename: str, col: int) -> str:
        """화질 분석 열의 필터 값(표시 문구와 같음)"""
        return self.display(filename, col)

    def sort_value(self, filename: str, col: int) -> object:
        """화질 분석 열의 정렬 값"""
        result = self._results.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            return active_frame_sort_value(result)
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_sort_value(result)
        return quality_metric_sort_value(result)

    def tooltip(self, filename: str, col: int) -> str:
        """화질 분석 열의 셀 툴팁"""
        result = self._results.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            return active_frame_tooltip(result, excluded=self.is_crop_excluded(filename))
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_tooltip(result)
        return quality_metric_tooltip(result)

    def foreground(self, filename: str, col: int, palette: "ThemePalette") -> str | None:
        """활성 화면 글자색. 검은 여백 잘라내기 제외 영상은 흐리게 표시"""
        if col != ACTIVE_FRAME_COLUMN or not self.is_crop_excluded(filename):
            return None
        result = self._results.get(filename)
        if result is None or not result.active_region.has_black_bars:
            return None
        return palette.text_muted


def is_frame_analysis_column(col: int) -> bool:
    """화질 분석 열인지 여부"""
    return col in FRAME_ANALYSIS_COLUMNS
