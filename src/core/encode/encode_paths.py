"""인코딩 결과, 보관, 같은 이름 처리 경로 계산 모듈

- 순수 계산만 포함하며 파일 존재 여부는 호출자가 `exists`로 주입
- 결과 파일은 `<이름>.<확장자>.part` 임시 파일을 거쳐 최종 경로로 배치
"""

import os
from collections.abc import Callable, Collection

from src.core.config import (
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_OVERWRITE,
    NAME_CONFLICT_SETTINGS_SUBDIR,
    NAME_CONFLICT_SKIP,
    PREVIOUS_OUTPUT_DIRNAME,
    RESULT_POLICY_SEPARATE,
    ExecutionConfig,
)
from src.core.encoder_specs import get_encode_codec_family, get_encoder_spec

PART_SUFFIX: str = ".part"
MAX_NUMBERED_CANDIDATES: int = 999
CONTAINER_EXTENSIONS: dict[str, str] = {"mp4": ".mp4", "mkv": ".mkv", "mov": ".mov"}
# - 인코더 정의에 품질 옵션이 없을 때 설정 하위 폴더 이름에 쓰는 기본 표식
DEFAULT_QUALITY_TOKEN: str = "cq"


def excluded_folder_names(execution: ExecutionConfig) -> tuple[str, ...]:
    """스캔에서 빼야 할 인코딩 폴더 이름(보관 폴더, 별도 출력 폴더, 예전 기본 출력 폴더)"""
    names = (
        execution.archive_dirname.strip(),
        execution.output_dirname.strip(),
        PREVIOUS_OUTPUT_DIRNAME,
    )
    return tuple(dict.fromkeys(name for name in names if name))


def in_place_output_path(source_path: str, container: str) -> str:
    """원본과 같은 폴더에 확장자만 컨테이너에 맞춘 결과 경로"""
    stem = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(os.path.dirname(source_path), stem + CONTAINER_EXTENSIONS[container])


def archive_path_of(source_path: str, archive_dirname: str) -> str:
    """원본을 옮겨 둘 보관 폴더 경로(같은 폴더 하위)"""
    return os.path.join(os.path.dirname(source_path), archive_dirname, os.path.basename(source_path))


def separate_output_path(
    source_path: str,
    root_dir: str,
    output_dirname: str,
    container: str,
    settings_subdir: str = "",
) -> str:
    """작업 폴더 기준 상대 경로를 그대로 살린 별도 출력 폴더 경로

    - settings_subdir가 있으면 출력 폴더 바로 아래에 그 이름의 하위 폴더를 한 단계 넣음
    """
    relative_dir, name = os.path.split(os.path.relpath(source_path, root_dir))
    stem = os.path.splitext(name)[0]
    return os.path.join(
        root_dir,
        output_dirname,
        settings_subdir,
        relative_dir,
        stem + CONTAINER_EXTENSIONS[container],
    )


def _quality_token(quality: float) -> str:
    """폴더 이름에 쓰도록 품질값을 정규화. 소수점은 p, 음수 부호는 m으로 바꿈(22.5 -> 22p5)"""
    text = f"{quality:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", "p").replace("-", "m")


def settings_subdir_for(encoder: str, quality: float) -> str:
    """현재 인코딩 설정을 나타내는 하위 폴더 이름(예: hevc_cq22, h264_crf23)

    - 코덱 계열과 품질 옵션 이름, 품질값만 써서 같은 설정이면 항상 같은 이름이 되게 함
    - 파일 이름에 쓸 수 없는 문자는 넣지 않음
    - 폴더 이름은 계획 단계의 품질값으로 정해지고 임시 파일 경로도 그때 고정되므로, 이후 정밀
      결정과 사전 예측이 품질값을 바꿔도 폴더 이름은 계획값을 그대로 가리킴
    """
    family = get_encode_codec_family(encoder)
    spec = get_encoder_spec(encoder)
    prefix = DEFAULT_QUALITY_TOKEN
    if spec is not None and spec.quality_option:
        prefix = spec.quality_option.strip("-").split(":")[0] or DEFAULT_QUALITY_TOKEN
    return f"{family}_{prefix}{_quality_token(quality)}"


def path_key(path: str) -> str:
    """경로 비교용 키(대소문자와 구분자 차이 무시). 배치 안에서 이미 쓴 결과 경로 표시에 씀"""
    return os.path.normcase(os.path.normpath(path))


def numbered_candidate(path: str, number: int) -> str:
    """번호를 붙인 후보 경로, 예: `영상.mp4` -> `영상 (1).mp4`"""
    directory, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    return os.path.join(directory, f"{stem} ({number}){ext}")


def choose_free_path(
    candidate: str,
    name_conflict: str,
    exists: Callable[[str], bool],
) -> str | None:
    """같은 이름 처리 정책에 따라 쓸 수 있는 경로를 고름. 쓸 수 없으면 None

    - 건너뜀 정책: 이름이 있으면 None
    - 번호 정책: 번호를 붙여 비어 있는 첫 경로, 후보가 모두 찼으면 None
    - 그 밖의 값과 덮어쓰기 정책: 기존 파일을 지키기 위해 None(덮어쓰기는 `resolve_output_path`가 처리)
    """
    if not exists(candidate):
        return candidate
    if name_conflict == NAME_CONFLICT_SKIP:
        return None
    if name_conflict != NAME_CONFLICT_NUMBER:
        return None
    for number in range(1, MAX_NUMBERED_CANDIDATES + 1):
        numbered = numbered_candidate(candidate, number)
        if not exists(numbered):
            return numbered
    return None


def resolve_output_path(
    *,
    source_path: str,
    root_dir: str,
    output_dirname: str,
    container: str,
    name_conflict: str,
    result_policy: str,
    encoder: str,
    quality: float,
    exists: Callable[[str], bool],
    reserved: Collection[str] = (),
) -> str | None:
    """같은 이름 처리 정책까지 반영한 결과 파일 최종 경로. 쓸 수 있는 경로가 없으면 None

    - 덮어쓰기와 설정별 하위 폴더는 별도 폴더에 저장 결과 파일에만 적용하고, 다른 결과 처리
      방식에서는 무시해 기존 번호 규칙으로 동작(원본 보관 폴더와 원본은 어떤 경우에도 덮어쓰지 않음)
    - 설정별 하위 폴더 안에 같은 이름이 있으면 덮어쓰지 않고 번호를 붙임
    - 덮어쓰기는 이번 배치 전부터 있던 파일만 교체한다. reserved(이번 배치가 이미 쓴 경로 키)에
      든 경로는 번호 규칙으로 물러나 같은 배치의 다른 항목 결과를 지우지 않음
    - 덮어쓰기는 경로만 돌려주며, 실제 교체는 사후 게이트를 통과한 뒤 배치 단계가 함
    """
    effective = name_conflict
    if result_policy != RESULT_POLICY_SEPARATE and effective in (
        NAME_CONFLICT_OVERWRITE,
        NAME_CONFLICT_SETTINGS_SUBDIR,
    ):
        effective = NAME_CONFLICT_NUMBER
    subdir = ""
    if effective == NAME_CONFLICT_SETTINGS_SUBDIR:
        subdir = settings_subdir_for(encoder, quality)
        effective = NAME_CONFLICT_NUMBER
    if result_policy == RESULT_POLICY_SEPARATE:
        candidate = separate_output_path(
            source_path, root_dir, output_dirname, container, settings_subdir=subdir
        )
    else:
        candidate = in_place_output_path(source_path, container)
    if candidate == source_path:
        return candidate
    if effective == NAME_CONFLICT_OVERWRITE and path_key(candidate) not in reserved:
        return candidate
    if effective == NAME_CONFLICT_OVERWRITE:
        effective = NAME_CONFLICT_NUMBER
    return choose_free_path(candidate, effective, exists)


def part_path_of(output_path: str) -> str:
    """결과 임시 파일 경로(같은 폴더, 최종 이름 뒤에 .part)"""
    return output_path + PART_SUFFIX
