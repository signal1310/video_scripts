"""화질 분석 표 열 세 개(활성 화면, 축소 후보, 측정 지표)의 셀 문구, 정렬, 필터, 툴팁 모듈

- 표 모델이 파일명별 `FrameAnalysisResult` 캐시를 보관하고, 이 모듈의 함수로 값을 계산
- 문구는 `TERMINOLOGY.md` 6.15절을 따르며, 축소 후보 툴팁에는 규격별 점수와 SSIM 대체 안내를 항상 포함
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from src.core.analysis.frame_analysis import (
    ActiveRegion,
    is_crop_only_result,
    is_failed_crop_scan,
)
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.quality_probe import METRIC_SSIM, METRIC_VMAF
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, RESOLUTION_TIERS
from src.gui.frame_analysis_skip import SkipKind, skip_reason_tooltip

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
# - 자동 배치가 건너뛴 HDR 행의 활성 화면 셀 표시 문구. 우클릭 분석으로 잰 결과가 있으면 그 결과를 보여 줌
ACTIVE_FRAME_HDR_TEXT: str = "HDR"
ACTIVE_FRAME_FAILED_TEXT: str = "감지 실패"
# - 활성 화면 열 필터에만 있는 특수 항목. 셀 값 집합에는 넣지 않고 필터 전용으로 붙임
ACTIVE_FRAME_MARGINS_VARY_TEXT: str = "구간별 여백 차이 존재"
# - 영상별 잘라내기 제외로 전체 화면을 쓰는 행의 표시 문구. 미리 보기 창의 같은 버튼 문구와 같지만
#   gui.table이 gui.preview를 임포트하지 않도록 표 층에 따로 둠(TERMINOLOGY 6.17절)
ACTIVE_FRAME_KEEP_FULL_TEXT: str = "전체 화면 유지"
ACTIVE_FRAME_KEEP_FULL_ICON: str = "crop-full"
# - 사용자 지정 활성 화면의 연필 아이콘과 접근성 이름
ACTIVE_FRAME_MANUAL_ICON: str = "pencil"
ACTIVE_FRAME_MANUAL_NAME: str = "사용자 지정 활성 화면"
# - 활성 화면을 직접 지정할 때까지 자동 잘라내기를 보류한 영상의 표시 이름. 셀 아이콘, 툴팁 제목,
#   필터 바 칩, 활성 화면 열 필터 값이 같은 이름을 씀(TERMINOLOGY 4.5절)
CROP_REVIEW_TEXT: str = "여백 확인 필요"
DOWNSCALE_KEEP_TEXT: str = "유지"
DOWNSCALE_UNMEASURABLE_TEXT: str = "측정 불가"
# - 화질 분석 대상에서 자동으로 건너뛴 행의 표시 문구. 사유는 셀 툴팁으로 보여 줌(TERMINOLOGY 6.15절)
DOWNSCALE_SKIPPED_TEXT: str = "건너뜀"


def is_downscale_plain_text(text: str) -> bool:
    """축소 후보 열에서 칩 대신 일반 글자로 보여 줄 값인지 여부(유지, 건너뜀)"""
    return text in (DOWNSCALE_KEEP_TEXT, DOWNSCALE_SKIPPED_TEXT)
METRIC_VMAF_TEXT: str = "VMAF"
METRIC_SSIM_FALLBACK_TEXT: str = "SSIM (대체)"
# - 축소 후보 열의 표시 값: 미측정 빈칸, 감지 전용 빈칸, 측정 불가, 유지, 건너뜀, 규격 표시 이름
DOWNSCALE_CANDIDATE_VALUE_SET: frozenset[str] = frozenset(
    {
        "",
        DOWNSCALE_KEEP_TEXT,
        DOWNSCALE_UNMEASURABLE_TEXT,
        DOWNSCALE_SKIPPED_TEXT,
        *(tier.display for tier in RESOLUTION_TIERS),
    }
)
# - 측정 지표 열의 표시 값: 미측정 빈칸, VMAF, SSIM 대체
QUALITY_METRIC_VALUE_SET: frozenset[str] = frozenset(
    {"", METRIC_VMAF_TEXT, METRIC_SSIM_FALLBACK_TEXT}
)

_NO_RESULT_TIP: str = (
    "화질 분석 전이라 아직 측정하지 않았습니다. "
    "분석 메뉴에서 선택 영상 화질 분석이나 전체 영상 화질 분석을 실행하세요."
)
_ACTIVE_FRAME_TIP: str = (
    "화면 여백을 뺀 활성 화면 크기입니다. 이 크기로 화질 기준값과 축소 후보를 계산합니다."
)
_ACTIVE_FRAME_FULL_TIP: str = "화면 여백이 없어 전체 화면을 활성 화면으로 씁니다."
# - 여백 감지 자체가 실패했을 때. 다시 감지하면 재시도한다는 사실까지 알림
_ACTIVE_FRAME_FAILED_TIP: str = (
    "화면 여백을 감지하지 못했습니다. 영상을 읽지 못한 지점이 있어 전체 화면을 그대로 씁니다. "
    "다시 감지하면 재시도합니다."
)
_ACTIVE_FRAME_EXCLUDED_TIP: str = "여백 잘라내기 제외됨 (원본 유지)"
# - 영상별 잘라내기 제외로 전체 화면을 쓰는 행의 툴팁. 감지한 크기를 쓰지 않는다는 사실까지 알림
_ACTIVE_FRAME_KEEP_FULL_TIP: str = (
    "이 영상은 잘라내지 않고 전체 화면을 유지합니다. "
    "자동으로 감지한 활성 화면({size})은 쓰지 않습니다."
)
# - 설정이 꺼져 있으면 감지한 크기를 보고하되 잘라내지는 않음을 같은 사유 툴팁으로 알림
_ACTIVE_FRAME_CROP_OFF_TIP: str = (
    "화면 여백 잘라내기 설정이 꺼져 있어 인코딩할 때 화면 여백을 자르지 않습니다."
)
# - 사용자 지정 활성 화면은 자동 감지 결과를 덮어쓴 상태임을 알리는 한 문장
_ACTIVE_FRAME_MANUAL_TIP: str = "사용자 지정 활성 화면입니다."
# - 자동 잘라내기를 보류한 영상의 툴팁. 제목 뒤에 감지 설명, 보류 안내, 확인 방법을 차례로 붙임
_CROP_REVIEW_TITLE: str = CROP_REVIEW_TEXT
_CROP_REVIEW_REASON_TEMPLATE: str = (
    "화면 여백을 찾을 때 일부 지점의 여백이 어두워 판단에서 제외했습니다. "
    "그 지점이 실제 화면의 어두운 부분인 경우, 지금 잡은 활성 화면({size}) 밖의 여백을 잘라낼 때 "
    "실제 화면의 어두운 부분까지 화면 여백으로 보고 함께 잘릴 수 있습니다."
)
_CROP_REVIEW_HOLD_TIP: str = (
    "활성 화면을 직접 지정할 때까지 자동으로 잡은 화면 여백을 잘라내지 않고 전체 화면으로 인코딩합니다."
)
_CROP_REVIEW_SOLVE_TIP: str = (
    "미리 보기 창에서 활성 화면을 확인해 주세요. 직접 지정하거나 자동 감지로 되돌리면 이 경고가 사라집니다."
)
# - 구간마다 여백이 달라 합집합만 잡힌 영상에 덧붙이는 안내
_ACTIVE_FRAME_MARGINS_VARY_TIP: str = (
    "구간마다 화면 여백이 달라 가장 넓게 겹치는 영역만 활성 화면으로 잡았습니다. "
    "미리 보기에서 직접 지정할 수 있습니다."
)
_ACTIVE_FRAME_VR_TIP: str = (
    "VR 영상으로 보호하고 있습니다. 여백 감지와 잘라내기에서만 보호하며 축소 후보는 정상 측정합니다."
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
    "화면 여백만 감지했습니다. 축소 후보를 확인하려면 화질 분석을 실행하세요."
)
# - 감지 자체가 실패해 축소 후보를 볼 근거가 없는 행의 안내
_FAILED_SCAN_CANDIDATE_TIP: str = (
    "여백 감지에 실패해 축소 후보를 확인하지 못했습니다. 다시 감지하면 재시도합니다."
)
_CROP_ONLY_METRIC_TIP: str = "화면 여백만 감지했습니다. 측정 지표를 보려면 화질 분석을 실행하세요."
# - 감지 자체가 실패해 측정 지표를 볼 근거가 없는 행의 안내
_FAILED_SCAN_METRIC_TIP: str = (
    "여백 감지에 실패해 측정 지표를 확인하지 못했습니다. 다시 감지하면 재시도합니다."
)
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


def active_frame_text(result: FrameAnalysisResult | None, *, skip_reason: SkipKind | None = None) -> str:
    """활성 화면 셀 문구: 미측정 빈칸, HDR, VR 보호, 감지 실패, 검은 띠 크기, 전체

    - VR 보호는 감지 결과를 쓰지 않으므로 스캔이 실패해도 보호 문구를 그대로 씀
    - 자동 배치가 건너뛴 HDR 행은 결과가 없을 때 HDR 문구를 쓰고, 결과가 있으면 그 결과를 보여 줌
    """
    if result is None:
        if skip_reason is SkipKind.HDR:
            return ACTIVE_FRAME_HDR_TEXT
        return ""
    if result.is_vr_protected:
        return ACTIVE_FRAME_VR_TEXT
    if is_failed_crop_scan(result):
        return ACTIVE_FRAME_FAILED_TEXT
    if result.active_region.has_black_bars:
        return f"{result.active_region.width}x{result.active_region.height}"
    return ACTIVE_FRAME_FULL_TEXT


def crop_review_tooltip(region: ActiveRegion) -> str:
    """여백 확인 필요 툴팁: 제목, 감지 설명, 구간별 여백 안내, 보류 안내, 확인 방법

    - 구간별 여백 안내는 그 플래그가 함께 있으면 첫 문단 뒤에 같은 문장을 붙임
    - 셀과 파일 이름 아이콘이 같은 문구를 씀
    """
    lines = [
        _CROP_REVIEW_TITLE,
        _CROP_REVIEW_REASON_TEMPLATE.format(size=f"{region.width}x{region.height}"),
    ]
    if region.margins_vary:
        lines.append(_ACTIVE_FRAME_MARGINS_VARY_TIP)
    lines.append(_CROP_REVIEW_HOLD_TIP)
    lines.append(_CROP_REVIEW_SOLVE_TIP)
    return "\n".join(lines)


def active_frame_tooltip(
    result: FrameAnalysisResult | None,
    *,
    excluded: bool = False,
    manual: bool = False,
    crop_enabled: bool = True,
) -> str:
    """활성 화면 셀 툴팁. 잘라내기를 쓰지 않는 영상과 직접 지정 영상은 그 사유를 보여 줌

    - 직접 지정은 값 문구에 크기를 그대로 두고 문장 한 줄만 덧붙임
    - 감지 자체가 실패한 행은 검은 띠 없음이 아니라 실패 사유를 보여 주고 재시도를 안내
    - 구간별 여백이 달라 합집합으로만 잡힌 영상은 직접 지정 안내를 한 문장 더 붙임(직접 지정 시 생략)
    - 검은 여백 잘라내기 설정이 꺼져 있으면 크기만 보여 주고 자르지 않는다는 줄을 덧붙임
    """
    if result is None:
        return _NO_RESULT_TIP
    if result.is_vr_protected:
        return _ACTIVE_FRAME_VR_TIP
    if manual:
        tip = f"{_ACTIVE_FRAME_TIP}\n{_ACTIVE_FRAME_MANUAL_TIP}"
        if not crop_enabled:
            return f"{tip}\n{_ACTIVE_FRAME_CROP_OFF_TIP}"
        return tip
    if is_failed_crop_scan(result):
        return _ACTIVE_FRAME_FAILED_TIP
    if result.active_region.has_black_bars:
        if not crop_enabled:
            # - 영상별 잘라내기 제외는 설정을 다시 켜면 다시 자르므로, 꺼진 상태에서도 제외 사유를 잃지 않게 덧붙임
            tip = (
                f"{_ACTIVE_FRAME_EXCLUDED_TIP}\n{_ACTIVE_FRAME_CROP_OFF_TIP}"
                if excluded
                else f"{_ACTIVE_FRAME_TIP}\n{_ACTIVE_FRAME_CROP_OFF_TIP}"
            )
        else:
            tip = _ACTIVE_FRAME_EXCLUDED_TIP if excluded else _ACTIVE_FRAME_TIP
    else:
        tip = _ACTIVE_FRAME_FULL_TIP
    if result.active_region.margins_vary:
        return f"{tip}\n{_ACTIVE_FRAME_MARGINS_VARY_TIP}"
    return tip


def active_frame_sort_value(result: FrameAnalysisResult | None) -> tuple[int, int]:
    """활성 화면 정렬 값: 미측정을 가장 앞에 두고 화면 면적 비교

    - HDR 문구만 있는 행은 측정값이 없으므로 미측정과 같은 자리에 둠
    """
    if result is None:
        return (0, 0)
    return (1, result.active_region.width * result.active_region.height)


def downscale_candidate_text(
    result: FrameAnalysisResult | None, *, skip_reason: SkipKind | None = None
) -> str:
    """축소 후보 셀 문구: 미측정, 감지 전용 빈칸, 측정 불가, 유지, 건너뜀, 규격명

    - 자동 배치에서 건너뛴 행은 옛 측정 결과가 있어도 건너뜀 문구를 보여 주고,
      측정 결과는 활성 화면, 측정 지표 열에 그대로 둠
    - HDR은 우클릭 분석으로 톤 매핑 측정이 가능하므로 건너뜀 대신 측정한 결과를 보여 줌
    """
    if skip_reason is not None and skip_reason is not SkipKind.HDR:
        return DOWNSCALE_SKIPPED_TEXT
    if result is None or is_crop_only_result(result):
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


def _hard_skip(skip_reason: SkipKind | None) -> SkipKind | None:
    """건너뜀 문구와 정렬을 강제하는 사유. HDR은 우클릭 분석으로 측정할 수 있어 제외

    - HDR 행은 자동 배치 사유 툴팁을 결과 안내 뒤에 덧붙이는 방식으로 알림
    """
    return None if skip_reason is SkipKind.HDR else skip_reason


def downscale_candidate_tooltip(
    result: FrameAnalysisResult | None, *, skip_reason: SkipKind | None = None
) -> str:
    """축소 후보 셀 툴팁: 감지 전용 안내, 판정 요지, 규격별 점수, 유효 구간, SSIM 대체 안내

    - 감지에 실패한 행은 감지 성공을 전제한 안내 대신 실패 사유와 재시도 안내를 씀
    - 자동 배치에서 건너뛴 행은 셀 문구(건너뜀)와 맞추기 위해 옛 측정 결과의 안내보다 사유를 먼저 알림
    - HDR 행은 자동 배치가 건너뛰지만 우클릭 분석으로 톤 매핑 측정이 가능함을 결과 안내에 덧붙임
    """
    if (hard := _hard_skip(skip_reason)) is not None:
        return skip_reason_tooltip(hard)
    if result is None:
        return skip_reason_tooltip(skip_reason) if skip_reason is not None else _NO_RESULT_TIP
    lines = _result_tooltip_lines(result)
    if skip_reason is SkipKind.HDR:
        lines.append(skip_reason_tooltip(SkipKind.HDR))
    return "\n".join(lines)


def _result_tooltip_lines(result: FrameAnalysisResult) -> list[str]:
    """측정 결과의 축소 후보 안내 문구 줄 목록"""
    if is_failed_crop_scan(result):
        return [_FAILED_SCAN_CANDIDATE_TIP]
    if is_crop_only_result(result):
        return [_CROP_ONLY_CANDIDATE_TIP]
    if result.unmeasurable:
        lines = [
            result.unmeasurable_reason or _DOWNSCALE_UNMEASURABLE_TIP,
            _valid_segment_text(result),
        ]
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
    return lines


def downscale_candidate_sort_value(
    result: FrameAnalysisResult | None, *, skip_reason: SkipKind | None = None
) -> tuple[int, int]:
    """축소 후보 정렬 값: 미측정, 감지 전용, 건너뜀, 측정 불가, 유지, 후보 규격 순서

    - 건너뜀은 후보가 없으므로 옛 측정 결과와 무관하게 미측정과 같은 자리에 둠
    - HDR 행은 우클릭 분석 결과가 있으면 그 후보 규격으로 정렬
    """
    if _hard_skip(skip_reason) is not None or result is None or is_crop_only_result(result):
        return (0, 0)
    if result.unmeasurable:
        return (1, 0)
    if result.candidate_tier is None:
        return (2, 0)
    tier = RESOLUTION_TIER_BY_KEY.get(result.candidate_tier)
    return (3, 0 if tier is None else tier.short_side)


def quality_metric_text(result: FrameAnalysisResult | None) -> str:
    """측정 지표 셀 문구: 미측정, 감지 전용 빈칸, 감지 실패 빈칸, VMAF, SSIM 대체"""
    if result is None or is_crop_only_result(result) or is_failed_crop_scan(result):
        return ""
    if result.metric_used == METRIC_SSIM:
        return METRIC_SSIM_FALLBACK_TEXT
    if result.metric_used == METRIC_VMAF:
        return METRIC_VMAF_TEXT
    return result.metric_used


def quality_metric_tooltip(result: FrameAnalysisResult | None) -> str:
    """측정 지표 셀 툴팁. 감지에 실패한 행은 감지 성공을 전제한 안내 대신 실패 사유와 재시도 안내를 씀"""
    if result is None:
        return _NO_RESULT_TIP
    if is_failed_crop_scan(result):
        return _FAILED_SCAN_METRIC_TIP
    if is_crop_only_result(result):
        return _CROP_ONLY_METRIC_TIP
    return _METRIC_SSIM_TIP if result.metric_used == METRIC_SSIM else _METRIC_VMAF_TIP


def quality_metric_sort_value(result: FrameAnalysisResult | None) -> tuple[int, str]:
    """측정 지표 정렬 값: 미측정과 감지 전용을 앞에 두고, SSIM 대체, VMAF 순서"""
    if result is None or is_crop_only_result(result):
        return (0, "")
    return (1 if result.metric_used == METRIC_SSIM else 2, result.metric_used)


class FrameAnalysisResults:
    """파일명별 화질 분석 결과, 수동 VR 보호, 잘라내기 제외, 활성 화면, 여백 확인 필요 표시 보관

    - 사용자 지정 활성 화면은 자동 감지 결과를 덮어쓰므로 셀 값도 지정 크기로 보여 줌
    - 여백 확인 필요 표시는 서비스가 정한 자동 크롭 보류 영상 파일명만 담음
    """

    def __init__(self) -> None:
        self._results: dict[str, FrameAnalysisResult] = {}
        self._manual_vr: frozenset[str] = frozenset()
        self._crop_excluded: frozenset[str] = frozenset()
        self._manual_frames: dict[str, ActiveFrame] = {}
        self._review_flagged: frozenset[str] = frozenset()
        self._skip_reasons: dict[str, SkipKind] = {}

    def set_results(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """결과 캐시 전체 교체"""
        self._results = dict(results)

    def set_skip_reasons(self, reasons: Mapping[str, SkipKind]) -> None:
        """화질 분석 건너뜀 사유를 파일명별로 교체"""
        self._skip_reasons = dict(reasons)

    def update_results(self, results: Mapping[str, FrameAnalysisResult]) -> None:
        """결과 캐시에 도착한 결과를 파일명별로 병합"""
        self._results.update(results)

    def set_manual_vr(self, filenames: Iterable[str]) -> None:
        """수동 VR 보호 파일명 집합 교체"""
        self._manual_vr = frozenset(filenames)

    def set_crop_excluded(self, filenames: Iterable[str]) -> None:
        """검은 여백 잘라내기 제외 파일명 집합 교체"""
        self._crop_excluded = frozenset(filenames)

    def set_manual_frames(self, frames: Mapping[str, ActiveFrame]) -> None:
        """사용자 지정 활성 화면을 파일명별로 교체"""
        self._manual_frames = dict(frames)

    def set_review_flagged(self, filenames: Iterable[str]) -> None:
        """여백 확인 필요 표시를 보일 파일명 집합 교체"""
        self._review_flagged = frozenset(filenames)

    def is_review_flagged(self, filename: str) -> bool:
        """파일명이 여백 확인 필요 표시 대상인지 여부"""
        return filename in self._review_flagged

    @property
    def review_flagged_count(self) -> int:
        """여백 확인 필요 영상 수"""
        return len(self._review_flagged)

    def result(self, filename: str) -> FrameAnalysisResult | None:
        """파일명의 화질 분석 결과, 없으면 None"""
        return self._results.get(filename)

    def is_manual_vr(self, filename: str) -> bool:
        """파일명이 수동 VR 보호 대상인지 여부"""
        return filename in self._manual_vr

    def is_crop_excluded(self, filename: str) -> bool:
        """파일명이 검은 여백 잘라내기 제외 대상인지 여부"""
        return filename in self._crop_excluded

    def manual_frame(self, filename: str) -> ActiveFrame | None:
        """파일명의 사용자 지정 활성 화면, 없으면 None(자동 감지 결과 사용)"""
        return self._manual_frames.get(filename)

    @property
    def has_results(self) -> bool:
        """표시할 분석 결과가 있는지 여부"""
        return bool(self._results)

    def candidate_tier(self, filename: str) -> str | None:
        """파일명의 축소 후보 규격 키. 감지 전용, 후보 없음, 측정 불가, 건너뜀이면 None

        - 자동 배치에서 건너뛴 행은 옛 측정 결과의 규격을 후보로 쓰지 않음
        - HDR은 우클릭 분석으로 잰 결과가 있으면 그 후보를 씀
        """
        if (reason := self._skip_reasons.get(filename)) is not None and reason is not SkipKind.HDR:
            return None
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

    def is_keep_full(self, filename: str) -> bool:
        """영상별 잘라내기 제외로 전체 화면을 쓰는 행인지 여부

        - 검은 띠가 있고 사용자 지정 활성 화면이 없으며 VR 보호가 아닌 영상만 해당
        - 설정 끔은 표시 문구에서 따로 판정하므로 여기서는 보지 않음(정렬 키가 설정과 무관하게 유지됨)
        """
        result = self._results.get(filename)
        if result is None or filename not in self._crop_excluded:
            return False
        if result.is_vr_protected or is_failed_crop_scan(result):
            return False
        if filename in self._manual_frames:
            return False
        return result.active_region.has_black_bars

    def display(self, filename: str, col: int, *, crop_enabled: bool = True) -> str:
        """화질 분석 열의 표시 문구

        - 사용자 지정 활성 화면은 자동 감지 결과 대신 지정 크기를 그대로 보여 줌
        - 잘라내기 제외로 전체 화면을 쓰는 영상은 전체 화면 유지 문구를 보여 줌(설정 끔이면 크기 그대로)
        - HDR로 건너뛴 행은 결과가 없으면 HDR 문구를 보여 줌(우클릭 측정 결과가 있으면 그 결과)
        """
        result = self._results.get(filename)
        manual = self._manual_frames.get(filename)
        skip_reason = self._skip_reasons.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            if manual is not None and (result is None or not result.is_vr_protected):
                return f"{manual.width}x{manual.height}"
            if crop_enabled and self.is_keep_full(filename):
                return ACTIVE_FRAME_KEEP_FULL_TEXT
            return active_frame_text(result, skip_reason=skip_reason)
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_text(result, skip_reason=skip_reason)
        return quality_metric_text(result)

    def filter_value(self, filename: str, col: int, *, crop_enabled: bool = True) -> str:
        """화질 분석 열의 필터 값(표시 문구와 같음)

        - 활성 화면 열은 크기 문구에 더해 여백 확인 필요 영상을 따로 고를 수 있게 같은 이름 값을 둠
        - 목록 중 하나만 골라도 그 행을 남기도록 표 모델이 값마다 겹쳐 셈
        """
        return self.display(filename, col, crop_enabled=crop_enabled)

    def special_filter_values(self, filename: str, col: int) -> tuple[str, ...]:
        """팝오버에 필터 전용 특수 항목으로 따로 보여 줄 값

        - 셀 값 집합에는 섞지 않고, 필터 일치 판정에만 함께 씀
        """
        if col != ACTIVE_FRAME_COLUMN:
            return ()
        result = self._results.get(filename)
        if result is not None and result.active_region.margins_vary:
            return (ACTIVE_FRAME_MARGINS_VARY_TEXT,)
        return ()

    def filter_values(
        self, filename: str, col: int, *, crop_enabled: bool = True
    ) -> tuple[str, ...]:
        """화질 분석 열의 필터 값 목록. 활성 화면 열은 여백 확인 필요 영상에 이름 값을 하나 더 둠

        - 구간마다 여백이 달라 합집합으로만 잡힌 영상은 특수 항목 값도 함께 가져
          팝오버에서 그 항목 하나만 골라 모아 볼 수 있게 함(셀 표시 문구는 그대로)
        """
        value = self.display(filename, col, crop_enabled=crop_enabled)
        if col == ACTIVE_FRAME_COLUMN and self.is_review_flagged(filename):
            return (value, CROP_REVIEW_TEXT, *self.special_filter_values(filename, col))
        return (value, *self.special_filter_values(filename, col))

    def counted_filter_values(
        self, filename: str, col: int, *, crop_enabled: bool = True
    ) -> tuple[str, ...]:
        """팝오버 값별 행 수를 셀 때 쓰는 값 목록. 특수 항목 값을 빼고 셈"""
        value = self.display(filename, col, crop_enabled=crop_enabled)
        if col == ACTIVE_FRAME_COLUMN and self.is_review_flagged(filename):
            return (value, CROP_REVIEW_TEXT)
        return (value,)

    def sort_value(
        self, filename: str, col: int, *, frame_area: int = 0, crop_enabled: bool = True
    ) -> object:
        """화질 분석 열의 정렬 값

        - 전체 화면 유지 문구가 표시될 때만 전체 화면 면적으로 정렬해 전체 화면 행과 나란히 둠
        - 잘라내기 설정이 꺼져 셀이 감지 크기를 보여 주면 표시 값과 같이 감지 크기로 정렬
        """
        result = self._results.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            manual = self._manual_frames.get(filename)
            if manual is not None:
                return (1, manual.width * manual.height)
            if crop_enabled and self.is_keep_full(filename):
                return (1, frame_area)
            return active_frame_sort_value(result)
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_sort_value(
                result, skip_reason=self._skip_reasons.get(filename)
            )
        return quality_metric_sort_value(result)

    def tooltip(self, filename: str, col: int, *, crop_enabled: bool = True) -> str:
        """화질 분석 열의 셀 툴팁. 활성 화면은 잘라내기 설정이 꺼져 있으면 그 사유를 함께 보여 줌

        - 결과가 없고 자동 배치에서 건너뛴 행은 활성 화면, 측정 지표 셀도 축소 후보와 같은 사유 툴팁을 씀
        """
        result = self._results.get(filename)
        manual = self._manual_frames.get(filename)
        skip_reason = self._skip_reasons.get(filename)
        if col == ACTIVE_FRAME_COLUMN:
            if result is None and manual is not None:
                tip = f"{_ACTIVE_FRAME_TIP}\n{_ACTIVE_FRAME_MANUAL_TIP}"
                if not crop_enabled:
                    return f"{tip}\n{_ACTIVE_FRAME_CROP_OFF_TIP}"
                return tip
            if skip_reason is not None and result is None:
                return skip_reason_tooltip(skip_reason)
            if crop_enabled and self.is_keep_full(filename) and result is not None:
                region = result.active_region
                return _ACTIVE_FRAME_KEEP_FULL_TIP.format(size=f"{region.width}x{region.height}")
            return active_frame_tooltip(
                result,
                excluded=self.is_crop_excluded(filename),
                manual=manual is not None,
                crop_enabled=crop_enabled,
            )
        if col == DOWNSCALE_CANDIDATE_COLUMN:
            return downscale_candidate_tooltip(result, skip_reason=skip_reason)
        if skip_reason is not None and result is None:
            return skip_reason_tooltip(skip_reason)
        return quality_metric_tooltip(result)

    def strikeout(self, filename: str, col: int, *, crop_enabled: bool = True) -> bool:
        """활성 화면 셀에 취소선을 그을지 여부

        - 잘라내기 설정이 꺼져 있고 검은 띠가 있는 자동 감지 셀만 해당
        - 사용자 지정 셀은 굵은 글씨로만 구분하고 취소선을 긋지 않음
        - 검은 띠가 없는 `전체` 행은 흐림과 취소선 없이 그대로 둠
        """
        if col != ACTIVE_FRAME_COLUMN or crop_enabled:
            return False
        if self.manual_frame(filename) is not None:
            return False
        result = self._results.get(filename)
        return result is not None and result.active_region.has_black_bars

    def foreground(
        self,
        filename: str,
        col: int,
        palette: "ThemePalette",
        *,
        crop_enabled: bool = True,
    ) -> str | None:
        """활성 화면 글자색. 검은 여백 잘라내기를 쓰지 않는 영상은 흐리게 표시

        - 사용자 지정 활성 화면은 제외 지정보다 우선하므로 흐리게 표시하지 않음
        - 잘라내기를 쓰는 동안 제외 지정 행은 전체 화면 유지 알약으로 바뀌므로 흐리게 두지 않음
        - 잘라내기 설정이 꺼져 있으면 검은 띠가 있는 행을 취소선과 함께 옅은 글자로 표시
        """
        if not self.strikeout(filename, col, crop_enabled=crop_enabled):
            return None
        return palette.fg_cell_inactive


def is_frame_analysis_column(col: int) -> bool:
    """화질 분석 열인지 여부"""
    return col in FRAME_ANALYSIS_COLUMNS
