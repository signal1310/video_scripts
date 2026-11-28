"""이전 결과 폐기 기록을 이번 인코딩 대상에 적용: 원본 파일 상태를 읽어 대상에서 뺄 영상의 경로를 고름

- 폐기 기록 조회는 호출자가 하고, 이 모듈은 파일 상태 읽기와 core 판정 호출만 맡음
- 원본을 읽지 못한 영상은 기록과 비교할 수 없으므로 대상에 남김
"""

import os
from collections.abc import Iterable, Mapping

from src.core.encode.encode_previous_discard import DiscardFact, is_previously_discarded
from src.core.models import VideoEvaluation
from src.core.workspace_paths import resolve_source_filepath


def find_previous_discards(
    evaluations: Iterable[VideoEvaluation],
    root_dir: str,
    facts: Mapping[str, DiscardFact],
    encoder: str,
) -> frozenset[str]:
    """인코딩 대상 가운데 이전 폐기 기록과 원본, 인코더, 화질 기준값이 같은 영상의 원본 경로 집합

    - 기록과 인코더, 화질 기준값, 직접 지정, 키프레임이 이미 어긋난 영상은 원본 상태를 읽지 않고 건너뜀
    - 원본 크기와 수정 시각까지 같아야 폐기로 보므로, 그 판정은 파일 상태를 읽은 뒤에 맡김
    """
    if not facts:
        return frozenset()
    excluded: set[str] = set()
    for ev in evaluations:
        if not ev.is_encode_target:
            continue
        fact = facts.get(resolve_source_filepath(root_dir, ev.video.filename))
        if fact is None or not _fact_can_match(fact, ev, encoder):
            continue
        source = resolve_source_filepath(root_dir, ev.video.filename)
        try:
            stat = os.stat(source)
        except OSError:
            continue
        if is_previously_discarded(
            stat.st_size,
            stat.st_mtime_ns,
            fact,
            ev.manual_target,
            encoder=encoder,
            quality=ev.applied_cq,
            keyframe_exceeded=ev.is_keyframe_exceeded,
        ):
            excluded.add(source)
    return frozenset(excluded)


def _fact_can_match(fact: DiscardFact, ev: VideoEvaluation, encoder: str) -> bool:
    """원본 상태를 읽기 전에 확인할 수 있는 폐기 일치 조건(직접 지정, 키프레임, 인코더, 화질 기준값)"""
    return (
        ev.manual_target is None
        and not ev.is_keyframe_exceeded
        and fact.encoder == encoder
        and fact.quality == ev.applied_cq
    )
