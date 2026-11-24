"""재인코딩 창 요약 탭의 배치 요약, 상태 분포 칩, 성적표 문구를 만드는 순수 모듈

- 시작 전 브리핑, 진행 중 대조, 완료 후 성적표가 같은 계산을 공유하도록 값 생성을 한곳에 둠
- 화면은 이 모듈이 만든 문자열을 그대로 표시하고, 색과 배치만 맡음
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from src.core.encode.encode_batch_book import (
    STATUS_BY_STATE,
    STATUS_DISCARDED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
)
from src.core.encode.encode_progress_values import saved_summary_text, size_text
from src.core.encode.encode_state import EncodeState
from src.core.models import VideoEvaluation

BRIEF_TITLE: str = "배치 요약"
BRIEF_TARGET_CAPTION: str = "대상"
BRIEF_SIZE_CAPTION: str = "예상 용량 변화"
BRIEF_NET_CAPTION: str = "예상 순 절약"
BRIEF_ENCODER_CAPTION: str = "인코더/화질 기준값"
BRIEF_POLICY_CAPTION: str = "결과 파일 처리"
BRIEF_CAPTIONS: tuple[str, ...] = (
    BRIEF_TARGET_CAPTION,
    BRIEF_SIZE_CAPTION,
    BRIEF_NET_CAPTION,
    BRIEF_ENCODER_CAPTION,
    BRIEF_POLICY_CAPTION,
)
LIVE_SAVING_CAPTION: str = "완료분 절약"
FINAL_SAVING_CAPTION: str = "최종 성적"
NO_CHANGE_TEXT: str = "변화 없음"
SAVED_SUFFIX: str = "절약"
GREW_SUFFIX: str = "증가"
STATUS_CHIP_ORDER: tuple[str, ...] = (
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_PENDING,
    STATUS_REPLACE_PENDING,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
ATTENTION_STATUS_ORDER: tuple[str, ...] = (STATUS_DISCARDED, STATUS_FAILED, STATUS_REPLACE_PENDING)
ATTENTION_TEMPLATE: str = "주의가 필요한 영상 {count}개: {items}"
ATTENTION_DETAIL_TEMPLATE: str = "{status} {count}"
DONE_BADGE_TEMPLATE: str = "{done}/{total}개 완료"
FINAL_SPEED_TEMPLATE: str = "총 소요 {elapsed}, 평균 {average}"


@dataclass(frozen=True, slots=True)
class BriefValues:
    """시작 전 브리핑 카드의 값 5종"""

    target: str
    size_change: str
    net_saved: str
    encoder: str
    result_policy: str


def _delta_text(delta_mb: float) -> str:
    """절약은 양수, 증가는 음수로 받은 값을 문장으로 바꿈"""
    if abs(delta_mb) < 0.05:
        return NO_CHANGE_TEXT
    if delta_mb > 0:
        return f"{size_text(delta_mb)} {SAVED_SUFFIX}"
    return f"{size_text(abs(delta_mb))} {GREW_SUFFIX}"


def brief_values(
    evaluations: Sequence[VideoEvaluation],
    *,
    baseline_quality: float,
    encoder_label: str,
    result_policy_label: str,
) -> BriefValues:
    """대상 목록과 설정으로 시작 전 브리핑 값을 만듦"""
    current_mb = sum(ev.video.vid_size_MB for ev in evaluations)
    expected_mb = sum(max(ev.expected_after_mb, 0.0) for ev in evaluations)
    net_mb = sum(ev.expected_delta_mb for ev in evaluations)
    return BriefValues(
        target=f"{len(evaluations)}개",
        size_change=f"{size_text(current_mb)} -> {size_text(expected_mb)}",
        net_saved=_delta_text(net_mb),
        encoder=f"{encoder_label} / 화질 기준값 {baseline_quality:.1f}",
        result_policy=result_policy_label,
    )


def status_counts(items: Iterable[tuple[str, EncodeState, str]]) -> dict[str, int]:
    """배치 항목의 상태 표기별 개수"""
    counts = dict.fromkeys(STATUS_CHIP_ORDER, 0)
    for _path, state, _reason in items:
        status = STATUS_BY_STATE[state]
        counts[status] = counts.get(status, 0) + 1
    return counts


def status_chips(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    """0개가 아닌 상태를 고정 순서로 돌려줌"""
    return tuple((status, counts[status]) for status in STATUS_CHIP_ORDER if counts.get(status, 0) > 0)


def attention_text(counts: Mapping[str, int]) -> str:
    """주의가 필요한 상태(결과 폐기, 실패, 교체 대기)가 있으면 한 줄 안내. 없으면 빈 문자열"""
    parts = [
        ATTENTION_DETAIL_TEMPLATE.format(status=status, count=counts.get(status, 0))
        for status in ATTENTION_STATUS_ORDER
        if counts.get(status, 0) > 0
    ]
    if not parts:
        return ""
    total = sum(counts.get(status, 0) for status in ATTENTION_STATUS_ORDER)
    return ATTENTION_TEMPLATE.format(count=total, items=", ".join(parts))


def done_badge(done: int, total: int) -> str:
    """진행 배지 문구"""
    return DONE_BADGE_TEMPLATE.format(done=done, total=total)


def done_count(items: Iterable[tuple[str, EncodeState, str]]) -> int:
    """완료 상태 항목 수"""
    return sum(1 for _path, state, _reason in items if state is EncodeState.DONE)


def target_count(items: Iterable[tuple[str, EncodeState, str]]) -> int:
    """배치 대상 항목 수"""
    return sum(1 for _item in items)


def saving_text(actual_mb: float | None, expected_mb: float | None, *, finished: bool, size_change: str = "") -> str:
    """완료분 또는 최종 절약 대조 문구. 완료 후에는 최종 용량 변화를 앞에 붙임"""
    prefix = FINAL_SAVING_CAPTION if finished else LIVE_SAVING_CAPTION
    body = saved_summary_text(actual_mb, expected_mb)
    if finished and size_change:
        return f"{prefix}: {size_change}, {body}"
    return f"{prefix}: {body}"


def final_speed_text(elapsed: str, average: str) -> str:
    """완료 후 총 소요와 평균 속도 문구"""
    return FINAL_SPEED_TEMPLATE.format(elapsed=elapsed, average=average)
