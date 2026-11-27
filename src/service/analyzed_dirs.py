"""분석을 끝낸 적 있는 작업 폴더 목록을 data 폴더에 기록하고 조회하는 모듈

- 기록은 경로 문자열 대신 볼륨 식별자와 볼륨 기준 상대 경로로 남겨 드라이브 문자나 상위 폴더가 바뀌어도 같은 폴더로 찾음
- 하위 폴더 포함 분석 기록은 그 아래 폴더를 모두 덮으므로, 하위 폴더 단위 화면도 분석된 폴더로 봄
- 하위 폴더를 포함하는 화면은 하위 폴더 포함 기록이 있을 때만 분석된 폴더로 봄(분석하지 않은 하위 폴더를 몰래 분석하지 않기 위함)
- 하위 폴더만 분석한 기록은 상위 폴더를 분석한 것으로 보지 않음(그 상위 폴더의 다른 영상은 아직 분석하지 않았으므로)
"""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from src.core.analysis.volume_identity import VolumeLocation, is_resolved_volume_id, volume_location
from src.core.paths import get_data_dir, write_utf8_file
from src.core.workspace_paths import parse_root_dirs

ANALYZED_DIRS_FILENAME: str = "analyzed_dirs.json"
# - 옛 기록은 정규화한 작업 폴더 목록 문자열이며, 하위 폴더 포함 기록에만 이 접미사가 붙음
LEGACY_RECURSIVE_SUFFIX: str = "|recursive=1"
VOLUME_PATH_DEPTH_SEPARATOR: str = "/"

RECORD_VOLUME_ID_KEY: str = "volume_id"
RECORD_VOLUME_PATH_KEY: str = "volume_path"
RECORD_RECURSIVE_KEY: str = "recursive"


@dataclass(frozen=True, slots=True)
class AnalyzedDir:
    """분석을 끝낸 폴더 하나: 볼륨 식별자, 볼륨 기준 상대 경로, 하위 폴더 포함 여부"""

    volume_id: str
    volume_path: str
    recursive: bool

    def covers(self, location: VolumeLocation) -> bool:
        """이 기록이 지정 위치를 분석한 범위에 두는지 판정

        - 같은 위치는 하위 폴더 포함 여부와 무관하게 참
        - 하위 폴더 포함 기록은 같은 볼륨의 하위 경로를 모두 포함하고, 하위 폴더만 분석한 기록은 상위를 포함하지 않음
        """
        if location.volume_id != self.volume_id:
            return False
        if location.volume_path == self.volume_path:
            return True
        if not self.recursive:
            return False
        return not self.volume_path or location.volume_path.startswith(
            self.volume_path + VOLUME_PATH_DEPTH_SEPARATOR
        )


def _marker_path() -> str:
    return os.path.join(get_data_dir(), ANALYZED_DIRS_FILENAME)


def _record_for_path(path: str, *, recursive: bool) -> AnalyzedDir:
    """폴더 경로를 현재 볼륨 위치로 바꾼 기록"""
    location = volume_location(path)
    return AnalyzedDir(location.volume_id, location.volume_path, recursive)


def _record_from_mapping(item: Mapping[str, object]) -> AnalyzedDir | None:
    """저장된 기록 한 항목을 기록으로 해석, 형식이 맞지 않으면 None

    - 하위 폴더 포함 여부는 참 bool일 때만 인정함. 문자열 "false" 같은 값을 참으로 읽으면 모든 하위 폴더를 덮게 됨
    """
    volume_id = item.get(RECORD_VOLUME_ID_KEY)
    volume_path = item.get(RECORD_VOLUME_PATH_KEY)
    if not isinstance(volume_id, str) or not isinstance(volume_path, str):
        return None
    return AnalyzedDir(volume_id, volume_path, item.get(RECORD_RECURSIVE_KEY) is True)


def _legacy_key_text(key: str) -> tuple[str, bool]:
    """옛 키의 폴더 목록 문자열과 하위 폴더 포함 여부"""
    if key.endswith(LEGACY_RECURSIVE_SUFFIX):
        return key[: -len(LEGACY_RECURSIVE_SUFFIX)], True
    return key, False


def _records_from_legacy_key(key: str) -> list[AnalyzedDir]:
    """옛 문자열 키를 현재 볼륨 위치 기록으로 변환

    - 옛 키는 정규화한 작업 폴더 경로를 `;`로 이은 문자열이며, 하위 폴더 포함 기록은 접미사로 구분
    - 경로가 더는 해석되지 않아도 상대 경로 계산이 예외 없이 경로 기반 식별자로 대체되므로 기록은 남음
    """
    text, recursive = _legacy_key_text(key)
    return [_record_for_path(root, recursive=recursive) for root in parse_root_dirs(text)]


def _is_convertible_legacy_key(key: str) -> bool:
    """옛 키를 볼륨 위치 기록으로 바꿔도 같은 폴더를 계속 찾을 수 있는지

    - 드라이브가 연결되지 않아 경로 기반 식별자로 대체되면 그 기록은 다시 연결된 뒤 같은 폴더를 찾지 못하므로
      옛 키를 그대로 남겨 두고 다음 저장 때 바꿈
    """
    text, _recursive = _legacy_key_text(key)
    return all(is_resolved_volume_id(volume_location(root).volume_id) for root in parse_root_dirs(text))


def _load_items() -> tuple[list[AnalyzedDir], list[str]]:
    """저장된 기록 목록과 아직 볼륨 위치 기록으로 바꾸지 못한 옛 문자열 키 목록"""
    path = _marker_path()
    if not os.path.isfile(path):
        return [], []
    try:
        with open(path, encoding="utf-8") as f:
            loaded: object = json.load(f)
    except (OSError, ValueError):
        logging.getLogger(__name__).warning(
            "분석 폴더 기록 읽기 실패로 빈 목록을 사용합니다: %s", path, exc_info=True
        )
        return [], []
    if not isinstance(loaded, list):
        return [], []
    entries: list[AnalyzedDir] = []
    legacy_keys: list[str] = []
    for item in loaded:
        if isinstance(item, dict):
            record = _record_from_mapping(item)
            if record is not None:
                entries.append(record)
        elif isinstance(item, str):
            legacy_keys.append(item)
    return entries, legacy_keys


def _load_entries() -> list[AnalyzedDir]:
    """저장된 분석 폴더 기록 목록, 옛 문자열 키는 볼륨 위치 기록으로 변환해 함께 씀"""
    entries, legacy_keys = _load_items()
    return [*entries, *(record for key in legacy_keys for record in _records_from_legacy_key(key))]


def _entry_to_mapping(entry: AnalyzedDir) -> dict[str, object]:
    """기록 한 항목을 저장 형식으로 변환"""
    return {
        RECORD_VOLUME_ID_KEY: entry.volume_id,
        RECORD_VOLUME_PATH_KEY: entry.volume_path,
        RECORD_RECURSIVE_KEY: entry.recursive,
    }


def is_analyzed_dir(root_dir: str, *, recursive: bool = False) -> bool:
    """지정 작업 폴더가 모두 분석된 범위에 들어 있는지 반환

    - 작업 폴더마다 같은 위치 기록이나 하위 폴더 포함 기록이 하나라도 있으면 분석된 것으로 봄
    - 화면이 하위 폴더를 포함하면 하위 폴더 포함 기록만 인정함. 하위 폴더를 분석하지 않은 폴더로 복원을 시작해
      분석하지 않은 하위 폴더 영상을 몰래 분석하지 않게 함
    """
    roots = parse_root_dirs(root_dir)
    if not roots:
        return False
    entries = [entry for entry in _load_entries() if entry.recursive or not recursive]
    return all(any(entry.covers(volume_location(root)) for entry in entries) for root in roots)


def mark_analyzed_dir(root_dir: str, *, recursive: bool = False) -> None:
    """분석이 결과를 내고 끝난 작업 폴더를 기록, 이미 기록된 항목은 다시 쓰지 않음

    - 옛 문자열 키는 저장할 때 볼륨 위치 기록으로 바꿔 새 형식으로 다시 씀
    - 폴더가 지금 연결되어 있지 않아 볼륨을 확인할 수 없는 옛 키는 문자열 그대로 남겨 다음 저장 때 바꿈
    """
    entries, legacy_keys = _load_items()
    convertible = [key for key in legacy_keys if _is_convertible_legacy_key(key)]
    converted = [record for key in convertible for record in _records_from_legacy_key(key)]
    kept_keys = [key for key in legacy_keys if key not in convertible]
    new_entries = [_record_for_path(root, recursive=recursive) for root in parse_root_dirs(root_dir)]
    known = [*entries, *converted]
    added = [entry for entry in new_entries if entry not in known]
    if not added and not convertible:
        return
    payload = [*(_entry_to_mapping(entry) for entry in (*known, *added)), *kept_keys]
    try:
        write_utf8_file(_marker_path(), json.dumps(payload, ensure_ascii=False, indent=1))
    except OSError:
        logging.getLogger(__name__).warning("분석 폴더 기록 저장 실패", exc_info=True)
