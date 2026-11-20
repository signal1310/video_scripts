"""재인코딩 임시 파일 정리의 판정 규칙: 묶음 분류, 고를 수 없는 이유, 결과 파일 모양과 원본 후보 경로

- Qt와 파일 IO에 닿지 않는 순수 함수. 기록과 파일 상태는 서비스 계층이 읽어 사실 값으로 넘김
"""

import os
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from src.core.encode.encode_paths import PART_SUFFIX
from src.core.encode.encode_state import EncodeState

RECENT_WRITE_SEC: int = 600
NS_PER_SEC: int = 1_000_000_000
RECENT_WRITE_NS: int = RECENT_WRITE_SEC * NS_PER_SEC
RESULT_CONTAINER_EXTS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov"})


class LeftoverGroup(Enum):
    """정리 창의 묶음. 고를 수 없는 파일은 BLOCKED로 두고 이유를 따로 붙임"""

    RECORDED = "recorded"
    OWN_RESULT = "own_result"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class BlockReason(Enum):
    """고를 수 없는 이유"""

    REPLACE = "replace"
    RESUME_TARGET = "resume_target"
    RECENT = "recent"


@dataclass(frozen=True, slots=True)
class LeftoverFacts:
    """임시 파일 한 개에 대해 기록과 파일 상태에서 읽은 사실

    - replace_result: 교체 대기이거나 교체 포기로 실패한 결과 파일
    - batch_open: 기록 행이 아직 닫히지 않은 배치 소속
    - own_result_shape: 이름 모양과 원본 영상 존재로 이 프로그램 결과로 보이는지
    """

    recorded_state: EncodeState | None
    replace_result: bool
    batch_open: bool
    own_result_shape: bool
    age_ns: int


@dataclass(frozen=True, slots=True)
class LeftoverVerdict:
    """묶음과 고를 수 없는 이유. 고를 수 없으면 이유가 있음"""

    group: LeftoverGroup
    block: BlockReason | None

    @property
    def selectable(self) -> bool:
        """사용자가 옮기기로 고를 수 있는지"""
        return self.group is not LeftoverGroup.BLOCKED


def classify_leftover(facts: LeftoverFacts) -> LeftoverVerdict:
    """사실 값으로 묶음을 정함. 고를 수 없는 조건을 먼저 보고, 나머지는 기록, 결과 모양, 출처 불명 순서

    - 교체 대기와 교체 포기 결과는 실제 결과물이라 옮기지 않음
    - 열린 배치의 진행 중, 대기 항목은 이어서 재인코딩 때 정리되므로 옮기지 않음
    - 최근에 바뀐 파일은 다른 프로그램이 쓰는 중일 수 있어 옮기지 않음
    """
    if facts.replace_result:
        return LeftoverVerdict(LeftoverGroup.BLOCKED, BlockReason.REPLACE)
    if facts.batch_open and facts.recorded_state in (EncodeState.RUNNING, EncodeState.PENDING):
        return LeftoverVerdict(LeftoverGroup.BLOCKED, BlockReason.RESUME_TARGET)
    if facts.age_ns < RECENT_WRITE_NS:
        return LeftoverVerdict(LeftoverGroup.BLOCKED, BlockReason.RECENT)
    if facts.recorded_state is not None:
        return LeftoverVerdict(LeftoverGroup.RECORDED, None)
    if facts.own_result_shape:
        return LeftoverVerdict(LeftoverGroup.OWN_RESULT, None)
    return LeftoverVerdict(LeftoverGroup.UNKNOWN, None)


def is_result_part_name(name: str) -> bool:
    """결과 임시 파일 이름인지: 최종 이름 뒤에 .part가 붙은 영상 컨테이너 이름(예: 영상.mp4.part)"""
    if not name.lower().endswith(PART_SUFFIX):
        return False
    final_name = name[: -len(PART_SUFFIX)]
    return os.path.splitext(final_name)[1].lower() in RESULT_CONTAINER_EXTS


def source_candidates(part_path: str, output_dirname: str, video_exts: Collection[str]) -> list[str]:
    """결과 임시 파일에 대응하는 원본 영상 후보 경로. 영상 확장자 집합은 호출자가 넘김

    - 같은 폴더에서 바꾸는 경우: 같은 폴더의 같은 이름
    - 별도 출력 폴더인 경우: 출력 폴더 이름을 떼어 낸 원본 폴더의 같은 이름
    """
    directory, name = os.path.split(part_path)
    stem = os.path.splitext(name[: -len(PART_SUFFIX)])[0] if name.lower().endswith(PART_SUFFIX) else name
    directories = [directory]
    parts = os.path.normpath(directory).split(os.sep)
    if output_dirname and output_dirname in parts:
        # - 드라이브 문자가 든 경로도 같은 구분자로 이어야 상대 경로로 바뀌지 않음
        index = len(parts) - 1 - parts[::-1].index(output_dirname)
        directories.append(os.sep.join(parts[:index] + parts[index + 1 :]))
    return [os.path.join(folder, stem + ext) for folder in directories for ext in sorted(video_exts)]
