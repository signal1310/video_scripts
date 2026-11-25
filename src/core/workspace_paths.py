"""다중 작업 폴더 경로 문자열의 파싱, 표시 라벨, 논리 파일명 변환 모듈"""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import lru_cache

PATH_SPEC_SEP: str = ";"
PATH_SPEC_JOIN: str = f"{PATH_SPEC_SEP} "
WON_SIGN: str = "₩"
LOGICAL_SEP: str = "/"
LABEL_JOIN: str = "_"
ROOT_LABEL_FALLBACK: str = "루트"
# - 파일마다 반복 호출되는 작업 폴더 문자열 해석 결과를 담는 상한, 실사용 작업 폴더 조합 수 기준
_ROOT_SPEC_CACHE_SIZE: int = 32


def normalize_path_separators(raw: str) -> str:
    """한국어 키보드 원화 기호를 백슬래시로 치환하고 앞뒤 공백 제거"""
    return raw.strip().replace(WON_SIGN, "\\")


def _split_spec_text(text: str) -> list[str]:
    """단일 문자열을 작업 폴더 후보 목록으로 분할

    - 문자열 전체가 실존 폴더면 폴더명에 구분자가 있어도 분할하지 않음
    """
    stripped = text.strip()
    if not stripped:
        return []
    if os.path.isdir(stripped):
        return [stripped]
    return [part.strip() for part in stripped.split(PATH_SPEC_SEP) if part.strip()]


def _root_key(path: str) -> str:
    """작업 폴더 중복과 조회에 쓰는 정규화 키"""
    return os.path.normcase(os.path.abspath(path))


def _dedupe_roots(candidates: Sequence[str]) -> tuple[str, ...]:
    """정규화 키 기준으로 중복을 제거하고 먼저 등장한 경로를 남김"""
    seen_keys: set[str] = set()
    roots: list[str] = []
    for path in candidates:
        key = _root_key(path)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        roots.append(path)
    return tuple(roots)


@lru_cache(maxsize=_ROOT_SPEC_CACHE_SIZE)
def _parse_root_text(spec: str) -> tuple[str, ...]:
    """단일 문자열 작업 폴더 지정을 해석하며 디스크 조회(isdir) 결과를 함께 캐시

    - 파일마다 호출되므로 같은 문자열의 반복 해석 비용을 제거
    """
    return _dedupe_roots(_split_spec_text(spec))


def parse_root_dirs(spec: str | Sequence[str]) -> tuple[str, ...]:
    """단일 경로, 세미콜론 구분 문자열, 경로 시퀀스를 고유 작업 폴더 튜플로 변환

    - 입력 문자열 형태는 유지하고 중복 판정만 normcase(abspath) 키로 수행
    - 입력 순서를 유지하며 먼저 등장한 경로를 남김
    - 문자열 입력 결과는 프로세스 안에서 캐시되므로 이후 폴더 생성, 삭제는 분할 판정에 반영되지 않음
    """
    if isinstance(spec, str):
        return _parse_root_text(spec)
    return _dedupe_roots([path for item in spec for path in _split_spec_text(item)])


def format_root_dirs(roots: Sequence[str]) -> str:
    """작업 폴더 목록을 세미콜론 구분 단일 문자열로 직렬화"""
    return PATH_SPEC_JOIN.join(roots)


def is_within_roots(path: str, roots: Sequence[str]) -> bool:
    """경로가 작업 폴더 중 하나와 같거나 그 아래인지

    - 구분자와 대소문자 차이는 normcase, abspath로 맞춰 비교
    - 이름만 같은 접두 폴더(작업 폴더 이름 뒤에 글자가 더 붙은 형제 폴더)는 안으로 보지 않음
    - 빈 경로나 빈 폴더는 비교하지 않음(빈 폴더는 현재 폴더로 해석되어 범위가 넓어짐)
    """
    if not path:
        return False
    target = os.path.normcase(os.path.abspath(path))
    for root in roots:
        if not root:
            continue
        base = os.path.normcase(os.path.abspath(root))
        if target == base or target.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def normalize_root_spec(raw: str) -> str:
    """사용자 입력 작업 폴더 문자열을 원화 기호 치환, 폴더별 normpath 표준 표기로 변환"""
    return format_root_dirs(
        [os.path.normpath(path) for path in parse_root_dirs(normalize_path_separators(raw))]
    )


def primary_root_dir(spec: str | Sequence[str]) -> str:
    """첫 번째(주) 작업 폴더 경로 반환, 없으면 빈 문자열"""
    roots = parse_root_dirs(spec)
    return roots[0] if roots else ""


def are_all_valid_dirs(spec: str | Sequence[str]) -> bool:
    """작업 폴더가 1개 이상이고 모두 실존하는 디렉터리인지 판별"""
    roots = parse_root_dirs(spec)
    return bool(roots) and all(os.path.isdir(root) for root in roots)


def _path_name_parts(path: str) -> list[str]:
    """라벨 생성에 쓰는 경로 구성 요소 목록(드라이브 루트는 드라이브 문자 한 개)"""
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    parts = [part for part in tail.replace("\\", "/").split("/") if part]
    if parts:
        return parts
    drive_name = drive.rstrip(":")
    return [drive_name] if drive_name else [ROOT_LABEL_FALLBACK]


def _join_label(parts: list[str], depth: int) -> str:
    """경로 끝에서 depth개 구성 요소를 라벨로 결합"""
    return LABEL_JOIN.join(parts[-depth:])


def _assign_labels(roots: Sequence[str]) -> dict[str, str]:
    """작업 폴더 정규화 키별 표시 라벨 생성

    - 기본 라벨은 폴더 basename, 겹치면 상위 폴더명을 차례로 붙임
    - 상위 구성 요소로도 구분되지 않으면 순번을 붙여 항상 고유하게 함
    - 라벨에는 구분자가 들어가지 않아 첫 '/'가 항상 라벨 경계가 됨
    """
    keys = [_root_key(root) for root in roots]
    parts_by_key = {key: _path_name_parts(root) for key, root in zip(keys, roots, strict=True)}
    depth_by_key = dict.fromkeys(keys, 1)

    while True:
        labels = {key: _join_label(parts_by_key[key], depth_by_key[key]) for key in keys}
        counts: dict[str, int] = {}
        for label in labels.values():
            counts[label] = counts.get(label, 0) + 1
        expandable = [
            key
            for key in keys
            if counts[labels[key]] > 1 and depth_by_key[key] < len(parts_by_key[key])
        ]
        if not expandable:
            break
        for key in expandable:
            depth_by_key[key] += 1

    labels = {key: _join_label(parts_by_key[key], depth_by_key[key]) for key in keys}
    used: dict[str, int] = {}
    final_labels: dict[str, str] = {}
    for key in keys:
        label = labels[key]
        if label in used:
            used[label] += 1
            label = f"{label}{LABEL_JOIN}{used[label]}"
        else:
            used[label] = 1
        final_labels[key] = label
    return final_labels


@lru_cache(maxsize=_ROOT_SPEC_CACHE_SIZE)
def _label_maps(unique_roots: tuple[str, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """고유 작업 폴더 튜플 기준 라벨 맵 계산, 파일마다 재계산하지 않도록 캐시"""
    labels = _assign_labels(unique_roots)
    label_to_root = {labels[_root_key(root)]: root for root in unique_roots}
    return labels, label_to_root


def build_root_label_map(roots: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """작업 폴더별 표시 라벨과 라벨별 작업 폴더 매핑 반환

    - 반환값은 (정규화 키 -> 표시 라벨, 표시 라벨 -> 작업 폴더 경로)
    - 반환 사전은 캐시와 공유되므로 호출자가 변경하지 않음
    """
    return _label_maps(parse_root_dirs(roots))


def to_display_filename(logical_filename: str) -> str:
    """표 표시용으로 논리 구분자(/)를 운영체제 경로 구분자로 바꿈, 저장과 경로 해석에는 쓰지 않음"""
    return logical_filename.replace(LOGICAL_SEP, os.sep)


def root_logical_prefix(root_dir: str, root_spec: str | Sequence[str]) -> str:
    """작업 폴더의 논리 파일명 접두사('표시 라벨/') 반환, 단일 작업 폴더면 빈 문자열

    - 여러 파일을 한 작업 폴더에서 만들 때 라벨 계산을 한 번만 하도록 분리
    """
    roots = parse_root_dirs(root_spec)
    if len(roots) <= 1:
        return ""
    labels, _ = build_root_label_map(roots)
    return f"{labels[_root_key(root_dir)]}{LOGICAL_SEP}"


def compose_logical_filename(
    root_dir: str, rel_posix_path: str, root_spec: str | Sequence[str]
) -> str:
    """작업 폴더 수에 맞춘 VideoProps.filename 식별자 생성

    - 단일 작업 폴더면 상대 경로 그대로 사용
    - 다중 작업 폴더면 '표시 라벨/상대 경로' 형태로 소속을 함께 기록
    """
    return f"{root_logical_prefix(root_dir, root_spec)}{rel_posix_path}"


def split_logical_filename(
    root_spec: str | Sequence[str], logical_filename: str
) -> tuple[str, str]:
    """논리 파일명을 (소속 작업 폴더, 작업 폴더 내 상대 경로 posix) 쌍으로 분해

    - 라벨이 일치하는 작업 폴더가 없으면 주 작업 폴더로 폴백
    """
    roots = parse_root_dirs(root_spec)
    posix = logical_filename.replace("\\", "/")
    if len(roots) <= 1:
        return (roots[0] if roots else "", posix)
    head, sep, rest = posix.partition(LOGICAL_SEP)
    _, label_to_root = build_root_label_map(roots)
    root = label_to_root.get(head)
    if root is None or not sep:
        return (roots[0], posix)
    return (root, rest)


def resolve_source_filepath(root_spec: str | Sequence[str], logical_filename: str) -> str:
    """논리 파일명으로부터 원본 위치의 OS 파일 경로 반환"""
    root, rel_posix = split_logical_filename(root_spec, logical_filename)
    return os.path.normpath(os.path.join(root, *rel_posix.split(LOGICAL_SEP)))


def logical_owner_root(logical_filename: str, root_spec: str | Sequence[str]) -> str | None:
    """논리 파일명이 가리키는 작업 폴더를 범위 안에서 찾음. 범위 밖이거나 알 수 없으면 None

    - 여러 작업 폴더로 저장한 논리 파일명은 '표시 라벨/상대 경로'이므로 첫 조각이 범위 안 라벨이면 그 폴더 소유
    - 작업 폴더가 하나면 첫 조각은 하위 폴더일 수 있으므로 실제 하위 폴더일 때만 그 폴더 소유로 봄
    - 경로 구분자가 없는 파일명은 어느 작업 폴더에도 있을 수 있으므로 그 폴더에 실제 파일이 있을 때만 소유로 봄
    - 그 밖의 첫 조각은 다른 작업 폴더의 라벨로 보고 건드리지 않음
    """
    roots = parse_root_dirs(root_spec)
    if not roots:
        return None
    posix = logical_filename.replace("\\", "/")
    head, sep, _rest = posix.partition(LOGICAL_SEP)
    if not sep:
        if len(roots) != 1 or not os.path.isfile(os.path.join(roots[0], posix)):
            return None
        return roots[0]
    _labels, label_to_root = build_root_label_map(roots)
    owner = label_to_root.get(head)
    if owner is not None:
        return owner
    if len(roots) == 1 and os.path.isdir(os.path.join(roots[0], head)):
        return roots[0]
    return None


def _scan_root_names(root_dir: str) -> tuple[frozenset[str], frozenset[str]]:
    """작업 폴더 직속의 파일 이름과 하위 폴더 이름 집합. 읽지 못하면 빈 집합

    - 파일마다 os.path.isfile, os.path.isdir을 부르지 않도록 한 번만 훑음
    - 이름은 normcase로 맞춰 대소문자 차이를 없앰(logical_owner_root의 존재 확인과 같은 판정)
    """
    files: set[str] = set()
    dirs: set[str] = set()
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                name = os.path.normcase(entry.name)
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    dirs.add(name)
                else:
                    files.add(name)
    except OSError:
        return frozenset(), frozenset()
    return frozenset(files), frozenset(dirs)


class LogicalOwnerResolver:
    """여러 행의 논리 파일명 소유 판정을 한 번 준비한 자료로 되풀이하는 판정기

    - 작업 폴더 파싱, 라벨 맵, 단일 폴더의 직속 파일 이름과 하위 폴더 이름 집합을 만들 때 한 번만 준비
    - 판정 결과는 logical_owner_root와 같음. 폴더를 훑은 뒤 파일이 지워져도 그 사실은 반영하지 않음
    """

    def __init__(self, root_spec: str | Sequence[str]) -> None:
        self._roots: tuple[str, ...] = parse_root_dirs(root_spec)
        self._label_to_root: dict[str, str] = {}
        self._files: frozenset[str] = frozenset()
        self._dirs: frozenset[str] = frozenset()
        if not self._roots:
            return
        _labels, self._label_to_root = build_root_label_map(self._roots)
        if len(self._roots) == 1:
            self._files, self._dirs = _scan_root_names(self._roots[0])

    def owner(self, logical_filename: str) -> str | None:
        """논리 파일명이 가리키는 작업 폴더. 범위 밖이거나 알 수 없으면 None"""
        if not self._roots:
            return None
        posix = logical_filename.replace("\\", "/")
        head, sep, _rest = posix.partition(LOGICAL_SEP)
        if not sep:
            if len(self._roots) != 1:
                return None
            return self._roots[0] if os.path.normcase(posix) in self._files else None
        owner = self._label_to_root.get(head)
        if owner is not None:
            return owner
        if len(self._roots) == 1 and os.path.normcase(head) in self._dirs:
            return self._roots[0]
        return None


def _drive_key(path: str) -> str:
    """드라이브 문자 비교용 정규화 값"""
    return os.path.splitdrive(os.path.abspath(path))[0].lower()


def count_cross_drive_moves(primary_root: str, file_paths: Sequence[str]) -> int:
    """주 작업 폴더와 드라이브가 다른 파일 개수 집계"""
    primary_drive = _drive_key(primary_root)
    return sum(1 for path in file_paths if _drive_key(path) != primary_drive)
