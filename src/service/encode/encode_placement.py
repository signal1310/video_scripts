"""결과 파일 배치: 덮어쓰기 없이 옮기고, 실패하면 원본과 결과가 모두 남도록 되돌림

- 대상 이름이 이미 있으면 FileExistsError로 실패하며, 그 시점까지 바꾼 파일은 원래 자리로 되돌림(AGENTS.md 5절)
- 원본 교체 정책에서 결과 경로가 원본과 같을 때만 덮어씀(설계상 의도)
- 이미 끝난 단계는 건너뛰므로 같은 함수를 반복 호출해도 안전함(교체 대기 재시도)
"""

import os
import time

from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_REPLACE
from src.core.encode.encode_paths import archive_path_of


def move_without_overwrite(source: str, destination: str) -> None:
    """대상이 없을 때만 이름을 바꿈. 대상이 있으면 FileExistsError"""
    if os.path.exists(destination):
        raise FileExistsError(f"대상 파일이 이미 있습니다: {destination}")
    os.rename(source, destination)


def place_result(
    *,
    source: str,
    part: str,
    final: str,
    archive_dirname: str,
    policy: str,
    mtime_ns: int,
) -> None:
    """결과 임시 파일을 최종 경로로 배치하고 수정 시각을 원본 값으로 맞춤

    - 원본 보관: 원본을 보관 폴더로 옮긴 뒤 결과를 배치. 결과 배치가 실패하면 원본을 되돌림
    - 별도 출력과 원본 교체(경로가 다른 경우): 결과만 옮기고, 원본 교체는 결과 배치 뒤에 원본을 지움
    """
    if os.path.exists(part):
        if policy == RESULT_POLICY_ARCHIVE and os.path.exists(source):
            archive = archive_path_of(source, archive_dirname)
            os.makedirs(os.path.dirname(archive), exist_ok=True)
            move_without_overwrite(source, archive)
            try:
                move_without_overwrite(part, final)
            except OSError:
                move_without_overwrite(archive, source)
                raise
        else:
            os.makedirs(os.path.dirname(final), exist_ok=True)
            if final == source and policy == RESULT_POLICY_REPLACE:
                os.replace(part, final)
            else:
                move_without_overwrite(part, final)
    if policy == RESULT_POLICY_REPLACE and final != source and os.path.exists(source):
        os.remove(source)
    os.utime(final, ns=(time.time_ns(), mtime_ns))
