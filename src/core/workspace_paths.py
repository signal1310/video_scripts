"""다중 작업 폴더 경로 문자열의 파싱, 표시 라벨, 논리 파일명 변환 모듈"""

from __future__ import annotations

import os
from collections.abc import Sequence

PATH_SPEC_SEP: str = ";"
PATH_SPEC_JOIN: str = f"{PATH_SPEC_SEP} "
WON_SIGN: str = "₩"
LOGICAL_SEP: str = "/"
LABEL_JOIN: str = "_"
ROOT_LABEL_FALLBACK: str = "루트"


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


def parse_root_dirs(spec: str | Sequence[str]) -> tuple[str, ...]:
    """단일 경로, 세미콜론 구분 문자열, 경로 시퀀스를 고유 작업 폴더 튜플로 변환

    - 입력 문자열 형태는 유지하고 중복 판정만 normcase(abspath) 키로 수행
    - 입력 순서를 유지하며 먼저 등장한 경로를 남김
    """
    if isinstance(spec, str):
        candidates = _split_spec_text(spec)
    else:
        candidates = [path for item in spec for path in _split_spec_text(item)]

    seen_keys: set[str] = set()
    roots: list[str] = []
    for path in candidates:
        key = _root_key(path)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        roots.append(path)
    return tuple(roots)


def format_root_dirs(roots: Sequence[str]) -> str:
    """작업 폴더 목록을 세미콜론 구분 단일 문자열로 직렬화"""
    return PATH_SPEC_JOIN.join(roots)


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


def build_root_label_map(roots: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """작업 폴더별 표시 라벨과 라벨별 작업 폴더 매핑 반환

    - 반환값은 (정규화 키 -> 표시 라벨, 표시 라벨 -> 작업 폴더 경로)
    """
    unique_roots = parse_root_dirs(roots)
    labels = _assign_labels(unique_roots)
    label_to_root = {labels[_root_key(root)]: root for root in unique_roots}
    return labels, label_to_root


def compose_logical_filename(
    root_dir: str, rel_posix_path: str, root_spec: str | Sequence[str]
) -> str:
    """작업 폴더 수에 맞춘 VideoProps.filename 식별자 생성

    - 단일 작업 폴더면 상대 경로 그대로 사용
    - 다중 작업 폴더면 '표시 라벨/상대 경로' 형태로 소속을 함께 기록
    """
    roots = parse_root_dirs(root_spec)
    if len(roots) <= 1:
        return rel_posix_path
    labels, _ = build_root_label_map(roots)
    return f"{labels[_root_key(root_dir)]}{LOGICAL_SEP}{rel_posix_path}"


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


def _drive_key(path: str) -> str:
    """드라이브 문자 비교용 정규화 값"""
    return os.path.splitdrive(os.path.abspath(path))[0].lower()


def count_cross_drive_moves(primary_root: str, file_paths: Sequence[str]) -> int:
    """주 작업 폴더와 드라이브가 다른 파일 개수 집계"""
    primary_drive = _drive_key(primary_root)
    return sum(1 for path in file_paths if _drive_key(path) != primary_drive)
