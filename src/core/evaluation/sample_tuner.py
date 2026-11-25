"""품질값 정밀 결정의 이진 탐색과 축소 후보 비교 순수 규칙(결정 18)

- 프로브 측정(샘플 인코딩, VMAF/SSIM)은 서비스 계층(`sample_tuner_service`)이 맡고 이 모듈은 판정만 함
- 품질값이 높을수록(저비트레이트) 화질이 떨어지는 단조 관계를 전제로, 목표를 통과하는 가장 높은 값 탐색
- 기준값은 화질 분석 왕복 측정과 같은 정의(`quality_target`)를 씀
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from src.core.quality_target import (
    PROTECT_CQ_DELTA_MAX,
    ROUNDTRIP_SSIM_THRESHOLD,
    ROUNDTRIP_VMAF_THRESHOLD,
    format_cq_value,
    snap_quality,
)

# - 결과 캐시 무효화 판. 판정 규칙이나 탐색 방식이 바뀌면 올림
SAMPLE_TUNER_VERSION: int = 2
# - 결정 18: 영상당 3~4회 샘플 인코딩을 넘기지 않음
TUNE_MAX_PROBES: int = 4
# - 인코더 품질 눈금. 정수 척도 인코더는 1.0, 소수 첫째 자리 인코더는 0.1(`snap_quality`와 같은 규칙)
QUALITY_GRANULARITY_INTEGER: float = 1.0
QUALITY_GRANULARITY_DECIMAL: float = 0.1
# - 6a-2 설정/화면이 그대로 쓰는 사유 코드. 문구는 서비스와 화면이 고름
REASON_CONVERGED: str = "converged"
REASON_FALLBACK_BASELINE: str = "fallback_baseline"
REASON_UNMEASURABLE: str = "unmeasurable"
# - 축소 후보 비교 판정 코드(`compare_downscale` 반환값). 작업 JSON과 사유 문구가 같은 값을 씀
COMPARE_CANDIDATE_FAILED: str = "candidate_failed"
COMPARE_CANDIDATE_ONLY_PASS: str = "candidate_only_pass"
COMPARE_CANDIDATE_SMALLER: str = "candidate_smaller"
COMPARE_ORIGINAL_NOT_LARGER: str = "original_not_larger"
# - 확정 해상도 선택. 작업 JSON에 그대로 남겨 이어서 인코딩할 때 비교를 다시 하지 않음
DECISION_KEEP_ORIGINAL: str = "original"
DECISION_KEEP_DOWNSCALE: str = "downscale"
# - 축소 후보가 이기는 비교 사유. 나머지 사유는 원본 해상도 유지
_DOWNSCALE_WIN_REASONS: frozenset[str] = frozenset({COMPARE_CANDIDATE_ONLY_PASS, COMPARE_CANDIDATE_SMALLER})


def quality_granularity(integer_scale: bool) -> float:
    """인코더 척도에 따른 품질값 최소 간격 반환"""
    return QUALITY_GRANULARITY_INTEGER if integer_scale else QUALITY_GRANULARITY_DECIMAL


@dataclass(frozen=True, slots=True)
class QualityProbe:
    """품질값 하나에서 잰 표본: VMAF, SSIM, 그 품질값의 비트레이트(kbps)"""

    quality: float
    vmaf: float | None = None
    ssim: float | None = None
    bitrate_kbps: float = 0.0

    @property
    def measurable(self) -> bool:
        """VMAF나 SSIM 중 하나라도 측정되었는지"""
        return self.vmaf is not None or self.ssim is not None


@dataclass(frozen=True, slots=True)
class TuneTarget:
    """목표 화질 기준. 기본값은 화질 분석 왕복 측정과 같은 상수"""

    vmaf_min: float = ROUNDTRIP_VMAF_THRESHOLD
    ssim_min: float = ROUNDTRIP_SSIM_THRESHOLD

    @property
    def cache_key(self) -> str:
        """캐시 키에 넣을 기준 문자열. 같은 의미의 값이 같은 키가 되게 자릿수를 고정"""
        return f"{self.vmaf_min:.3f}/{self.ssim_min:.6f}"


@dataclass(frozen=True, slots=True)
class TuneResult:
    """탐색 결과: 고른 품질값, 잰 프로브 목록, 수렴 여부와 사유 코드"""

    best_quality: float
    probes: tuple[QualityProbe, ...]
    converged: bool
    reason: str

    @property
    def best_probe(self) -> QualityProbe | None:
        """고른 품질값의 프로브. 그 값으로 잰 표본이 없으면 None"""
        for probe in reversed(self.probes):
            if probe.quality == self.best_quality:
                return probe
        return None


@dataclass(frozen=True, slots=True)
class DownscaleComparison:
    """축소 후보와 원본 규격의 비교 판정 결과"""

    use_downscale: bool
    candidate_probe: QualityProbe
    original_probe: QualityProbe
    reason: str = field(default="")


def probe_passes(probe: QualityProbe | None, target: TuneTarget) -> bool:
    """프로브가 목표를 넘었는지 판정. SSIM은 항상, VMAF는 있을 때만 기준을 넘어야 함

    - 두 점수가 모두 없으면 판정할 수 없으므로 거짓(실패)으로 봄
    """
    if probe is None or not probe.measurable:
        return False
    if probe.ssim is not None and probe.ssim < target.ssim_min:
        return False
    if probe.vmaf is None:
        return probe.ssim is not None
    return probe.vmaf >= target.vmaf_min


def tune_bounds(baseline_quality: float, *, delta_max: float = PROTECT_CQ_DELTA_MAX) -> tuple[float, float]:
    """탐색 구간 반환: 기준 품질값부터 품질값 자동 조절 한도까지

    - 저화질 보호가 허용하는 폭을 넘지 않게 하려고 상한을 자동 조절 한도로 둠
    """
    return (baseline_quality, baseline_quality + delta_max)


def _measure_safely(
    measure: Callable[[float], QualityProbe | None],
    quality: float,
) -> QualityProbe:
    """측정 함수를 호출하고 실패를 점수 없는 프로브로 바꿈

    - 샘플 인코딩 프로세스 실패처럼 측정 함수가 예외를 던지는 경우를 탐색 중단 없이 처리
    """
    try:
        probe = measure(quality)
    except Exception:
        probe = None
    if probe is None:
        return QualityProbe(quality=quality)
    return QualityProbe(
        quality=quality,
        vmaf=probe.vmaf,
        ssim=probe.ssim,
        bitrate_kbps=probe.bitrate_kbps,
    )


def search_quality(
    measure: Callable[[float], QualityProbe | None],
    low: float,
    high: float,
    target: TuneTarget | None = None,
    *,
    max_probes: int = TUNE_MAX_PROBES,
    integer_scale: bool = False,
) -> TuneResult:
    """목표를 통과하는 가장 높은 품질값을 이진 탐색으로 찾음

    - 시작점은 주어진 구간의 중간(기본 호출은 기준 품질값과 보호 한도의 중간)
    - 통과하면 아래 경계를, 실패하면 위 경계를 옮기며 통과한 최고값만 후보로 남겨 실패한 값을 돌려주지 않음
    - 프로브 수 상한이나 인코더 눈금 미만 구간에서 멈추고, 아무 값도 통과하지 못하면 기준 품질값으로 되돌림
    """
    resolved_target = target if target is not None else TuneTarget()
    low_bound, high_bound = (low, high) if high >= low else (high, low)
    granularity = quality_granularity(integer_scale)
    probes: list[QualityProbe] = []
    seen: set[float] = set()

    def _snap(value: float) -> float:
        return snap_quality(value, q_min=low_bound, q_max=high_bound, integer_scale=integer_scale)

    lo = low_bound
    hi = high_bound
    # - 상한이 아직 시험되지 않았으면 통과 직후 상한을 바로 재서 구간 전체가 통과할 때 2회로 끝냄
    ceiling_untested = True
    jump_to_ceiling = False
    best_pass: float | None = None
    candidate = _snap((lo + hi) / 2.0)
    while candidate not in seen and len(probes) < max_probes:
        seen.add(candidate)
        probe = _measure_safely(measure, candidate)
        probes.append(probe)
        if probe_passes(probe, resolved_target):
            best_pass = candidate
            lo = candidate
            jump_to_ceiling = ceiling_untested
        else:
            hi = candidate
            ceiling_untested = False
            jump_to_ceiling = False
        if hi - lo < granularity:
            break
        candidate = _snap(hi) if jump_to_ceiling else _snap((lo + hi) / 2.0)

    measured = [probe for probe in probes if probe.measurable]
    if best_pass is None:
        reason = REASON_FALLBACK_BASELINE if measured else REASON_UNMEASURABLE
        return TuneResult(best_quality=low_bound, probes=tuple(probes), converged=False, reason=reason)
    return TuneResult(best_quality=best_pass, probes=tuple(probes), converged=True, reason=REASON_CONVERGED)


def compare_downscale(
    candidate_probe: QualityProbe,
    original_probe: QualityProbe,
    target: TuneTarget | None = None,
) -> DownscaleComparison:
    """축소 후보와 원본 규격 중 목표를 넘으면서 비트레이트가 낮은 쪽을 고름

    - 축소 후보가 목표를 못 넘으면 원본 유지, 후보만 넘으면 축소
    - 둘 다 넘으면 비트레이트가 낮은 쪽을 고르고, 같으면 해상도를 지키려고 원본 유지
    """
    resolved_target = target if target is not None else TuneTarget()
    candidate_passes = probe_passes(candidate_probe, resolved_target)
    if not candidate_passes:
        return DownscaleComparison(False, candidate_probe, original_probe, COMPARE_CANDIDATE_FAILED)
    if not probe_passes(original_probe, resolved_target):
        return DownscaleComparison(True, candidate_probe, original_probe, COMPARE_CANDIDATE_ONLY_PASS)
    if candidate_probe.bitrate_kbps < original_probe.bitrate_kbps:
        return DownscaleComparison(True, candidate_probe, original_probe, COMPARE_CANDIDATE_SMALLER)
    return DownscaleComparison(False, candidate_probe, original_probe, COMPARE_ORIGINAL_NOT_LARGER)


def format_comparison_reason(comparison: DownscaleComparison) -> str:
    """축소 후보 비교 결과를 사유 한 줄로 바꿈

    - 축소를 유지하면 원본 규격 대비 비트레이트 차이를 함께 적어 판단 근거를 남김
    """
    if not comparison.use_downscale:
        return (
            "품질값 정밀 결정: 원본 해상도 유지 (축소 시 화질 기준 미달)"
            if comparison.reason == COMPARE_CANDIDATE_FAILED
            else "품질값 정밀 결정: 원본 해상도 유지 (축소 이득 없음)"
        )
    saving = relative_bitrate_saving_percent(comparison)
    if comparison.reason == COMPARE_CANDIDATE_ONLY_PASS:
        return "품질값 정밀 결정: 축소 유지 (원본 해상도는 화질 기준 미달)"
    return f"품질값 정밀 결정: 축소 유지 (원본 해상도 대비 {saving}% 작음)"


def relative_bitrate_saving_percent(comparison: DownscaleComparison) -> int:
    """축소 후보가 원본 규격보다 작은 비트레이트 비율(%). 원본 비트레이트를 모르면 0"""
    original = comparison.original_probe.bitrate_kbps
    if original <= 0:
        return 0
    ratio = 1.0 - comparison.candidate_probe.bitrate_kbps / original
    return max(0, round(ratio * 100))


def format_result_reason(result: TuneResult) -> str:
    """탐색 결과를 사유 한 줄로 바꿈. 6a-2 화면과 표시가 같은 표현을 쓰도록 한 곳에 둠"""
    value = format_cq_value(result.best_quality)
    if result.reason == REASON_UNMEASURABLE:
        return f"품질값 {value} 유지 (화질 측정 불가)"
    if result.reason == REASON_FALLBACK_BASELINE:
        return f"품질값 {value} 유지 (기준 미달)"
    return f"품질값 {value} 확인 (정밀 시험 {len(result.probes)}회)"

