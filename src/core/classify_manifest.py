"""평탄 분류 파일의 해시 접미사 부여와 data/classify_manifest.json 원위치 매핑 모듈"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from src.core.paths import get_classify_manifest_path, write_utf8_file

HASH_SEP: str = "__"
HASH_HEX_LEN: int = 8
HASH_DIGEST_SIZE: int = HASH_HEX_LEN // 2
HASHED_STEM_RE: re.Pattern[str] = re.compile(r"^(.+)__([0-9a-f]{8})$")
_HASH_TOKEN_RE: re.Pattern[str] = re.compile(r"__([0-9a-f]{8})(?=$|[\s\[#])")
_BRACKET_TAG_RE: re.Pattern[str] = re.compile(r"\[[^\]]+\]")
_HASHTAG_RE: re.Pattern[str] = re.compile(r"(?:(?<=^)|(?<=\s))#[^\s#\[\]]+(?=(?:\s|$))")
_WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")
FINGERPRINT_CHUNK_BYTES: int = 64 * 1024
FINGERPRINT_DIGEST_BYTES: int = 16
MANIFEST_JSON_INDENT: int = 2

_MANIFEST_FIELDS: tuple[str, ...] = (
    "hash_key",
    "primary_root",
    "target_dirname",
    "flat_filename",
    "src_path",
    "logical_filename",
)

logger = logging.getLogger(__name__)

_manifest_cache: dict[str, tuple[tuple[int, int], dict[str, ManifestRecord]]] = {}


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    """분류 폴더로 평탄 이동된 파일 1건의 원본 위치 매핑 레코드"""

    hash_key: str
    primary_root: str
    target_dirname: str
    flat_filename: str
    src_path: str
    logical_filename: str
    fingerprint: str = ""


def _normalized_path(path: str) -> str:
    """경로 비교용 정규화 키"""
    return os.path.normcase(os.path.abspath(path))


def compute_path_hash(src_path: str) -> str:
    """정규화된 원본 절대 경로로부터 결정적 8자리 소문자 hex 해시 산출"""
    norm = _normalized_path(src_path).encode("utf-8")
    return hashlib.blake2b(norm, digest_size=HASH_DIGEST_SIZE).hexdigest()


def build_hashed_filename(orig_basename: str, hash_key: str) -> str:
    """원본 파일명에 해시 접미사를 붙인 평탄 파일명 생성"""
    stem, ext = os.path.splitext(orig_basename)
    return f"{stem}{HASH_SEP}{hash_key}{ext}"


def compute_fingerprint(filepath: str, filesize_bytes: int) -> str:
    """파일 앞, 뒤 청크와 크기로 내용 지문을 산출하며 수정 시각은 사용하지 않음

    이름이 바뀌어도 내용이 같으면 같은 지문이 나와 분석과 복원 매핑을 재사용할 수 있음
    """
    digest = hashlib.blake2b(digest_size=FINGERPRINT_DIGEST_BYTES)
    digest.update(filesize_bytes.to_bytes(8, "little"))
    with open(filepath, "rb") as f:
        if filesize_bytes <= 2 * FINGERPRINT_CHUNK_BYTES:
            digest.update(f.read())
        else:
            digest.update(f.read(FINGERPRINT_CHUNK_BYTES))
            f.seek(filesize_bytes - FINGERPRINT_CHUNK_BYTES)
            digest.update(f.read(FINGERPRINT_CHUNK_BYTES))
    return digest.hexdigest()


def extract_hash_from_filename(flat_filename: str) -> tuple[str, str, str] | None:
    """평탄 파일명에서 (해시를 뺀 stem, 해시 키, 확장자) 추출

    - 끝 위치뿐 아니라 뒤에 태그가 이어진 해시 접미사도 인식함
    - 해시 접미사가 없으면 None 반환
    """
    stem, ext = os.path.splitext(flat_filename)
    matches = [m for m in _HASH_TOKEN_RE.finditer(stem) if m.start() > 0]
    if not matches:
        return None
    match = matches[-1]
    cleaned_stem = f"{stem[:match.start()]}{stem[match.end():]}".strip()
    if not cleaned_stem:
        return None
    return cleaned_stem, match.group(1), ext


def _strip_tags_for_match(stem: str) -> str:
    """대괄호와 단독 해시태그를 제거해 기본 파일명 줄기를 비교용으로 정규화"""
    cleaned = _BRACKET_TAG_RE.sub("", stem)
    cleaned = _HASHTAG_RE.sub("", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip(" _-")


def record_key(primary_root: str, target_dirname: str, flat_filename: str) -> str:
    """분류 폴더 내 평탄 파일의 물리 경로 기준 레코드 키 생성"""
    return _normalized_path(os.path.join(primary_root, target_dirname, flat_filename))


def _same_path(left: str, right: str) -> bool:
    """두 경로가 정규화 기준으로 같은지 판별"""
    return _normalized_path(left) == _normalized_path(right)


def _record_from_mapping(raw: Mapping[str, object]) -> ManifestRecord | None:
    """JSON 항목을 레코드로 변환, 필드가 빠졌거나 문자열이 아니면 None 반환"""
    values: dict[str, str] = {}
    for name in _MANIFEST_FIELDS:
        value = raw.get(name)
        if not isinstance(value, str):
            return None
        values[name] = value
    fingerprint = raw.get("fingerprint", "")
    values["fingerprint"] = fingerprint if isinstance(fingerprint, str) else ""
    return ManifestRecord(**values)


def _parse_manifest_file(path: str) -> dict[str, ManifestRecord]:
    """매니페스트 JSON을 레코드 맵으로 변환

    - 손상되었거나 형식이 다르면 경고 후 빈 사전
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        logger.warning("분류 매니페스트를 읽지 못해 빈 매핑으로 진행합니다: %s", path)
        return {}
    if not isinstance(raw, list):
        logger.warning("분류 매니페스트 형식이 올바르지 않아 빈 매핑으로 진행합니다: %s", path)
        return {}

    records: dict[str, ManifestRecord] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        record = _record_from_mapping(item)
        if record is None:
            continue
        key = record_key(record.primary_root, record.target_dirname, record.flat_filename)
        records[key] = record
    return records


def _read_manifest_cached(path: str) -> Mapping[str, ManifestRecord]:
    """파일 시그니처(수정 시각, 크기)가 같으면 파싱 결과를 재사용하는 읽기 전용 조회

    - 툴팁과 행 상태처럼 자주 호출되는 경로에서 JSON 재파싱 비용을 피하기 위함
    - 반환 사전은 호출자가 변경하지 않아야 함
    """
    try:
        stat_result = os.stat(path)
    except FileNotFoundError:
        _manifest_cache.pop(path, None)
        return {}
    except OSError:
        return {}
    signature = (stat_result.st_mtime_ns, stat_result.st_size)
    cached = _manifest_cache.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    records = _parse_manifest_file(path)
    _manifest_cache[path] = (signature, records)
    return records


def load_manifest_records(manifest_path: str | None = None) -> dict[str, ManifestRecord]:
    """data/classify_manifest.json을 읽어 레코드 맵(키: 분류 폴더 내 물리 경로) 반환

    - 파일이 없으면 빈 사전
    - 파일이 손상되었으면 경고를 남기고 빈 사전
    - 반환 사전은 호출자가 자유롭게 변경할 수 있는 복사본
    """
    path = get_classify_manifest_path() if manifest_path is None else manifest_path
    return dict(_read_manifest_cached(path))


def save_manifest_records(
    records: Mapping[str, ManifestRecord], manifest_path: str | None = None
) -> None:
    """레코드 맵을 data/classify_manifest.json에 원자적으로 저장

    - 레코드가 없으면 매니페스트 파일을 삭제
    """
    path = get_classify_manifest_path() if manifest_path is None else manifest_path
    if not records:
        if os.path.isfile(path):
            os.remove(path)
        return
    ordered = sorted(
        records.values(),
        key=lambda rec: (rec.primary_root, rec.target_dirname, rec.flat_filename),
    )
    payload = json.dumps(
        [asdict(rec) for rec in ordered],
        ensure_ascii=False,
        indent=MANIFEST_JSON_INDENT,
    )
    write_utf8_file(path, f"{payload}\n")


def _find_flat_by_hash(directory: str, hash_key: str) -> str | None:
    """분류 폴더에서 같은 해시 키를 가진 파일 경로 탐색

    - 샤나인코더 재인코딩으로 확장자가 바뀐 파일도 찾기 위해 확장자를 무시
    """
    if not os.path.isdir(directory):
        return None
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            parsed = extract_hash_from_filename(entry.name)
            if parsed is not None and parsed[1] == hash_key:
                return entry.path
    return None


def _find_flat_by_fingerprint(directory: str, fingerprint: str) -> str | None:
    """분류 폴더에서 같은 내용 지문을 가진 파일 경로 탐색"""
    if not fingerprint or not os.path.isdir(directory):
        return None
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            try:
                size = entry.stat().st_size
                if compute_fingerprint(entry.path, size) == fingerprint:
                    return entry.path
            except OSError:
                continue
    return None


def find_moved_flat_path(
    primary_root: str,
    moved_dirname: str,
    src_path: str,
    records: Mapping[str, ManifestRecord] | None = None,
) -> str:
    """이동 완료된 영상의 분류 폴더 내 실제 물리 경로 반환

    - 매니페스트 기록의 평탄 파일명이 디스크에 있으면 그 경로 사용
    - 기록이 없거나 파일명이 바뀐 경우 해시 키, 지문 순으로 분류 폴더를 탐색
    - 그래도 없으면 원본 basename 경로 반환
    """
    target_dir = os.path.join(primary_root, moved_dirname)
    source_records: Mapping[str, ManifestRecord] = (
        _read_manifest_cached(get_classify_manifest_path()) if records is None else records
    )
    record = next(
        (
            rec
            for rec in source_records.values()
            if rec.target_dirname == moved_dirname
            and _same_path(rec.primary_root, primary_root)
            and _same_path(rec.src_path, src_path)
        ),
        None,
    )
    if record is not None:
        direct_path = os.path.join(target_dir, record.flat_filename)
        if os.path.isfile(direct_path):
            return direct_path
        hash_key = record.hash_key
    else:
        hash_key = compute_path_hash(src_path)

    by_hash = _find_flat_by_hash(target_dir, hash_key)
    if by_hash is not None:
        return by_hash
    if record is not None and record.fingerprint:
        by_fingerprint = _find_flat_by_fingerprint(target_dir, record.fingerprint)
        if by_fingerprint is not None:
            return by_fingerprint
    return os.path.join(target_dir, os.path.basename(src_path))


RestoreHashIndex = dict[tuple[str, str, str], tuple[str, ManifestRecord]]
RestoreFingerprintIndex = dict[tuple[str, str, str], tuple[str, ManifestRecord]]


def build_restore_hash_index(records: Mapping[str, ManifestRecord]) -> RestoreHashIndex:
    """(해시 키, 분류 폴더명, 정규화 주 작업 폴더) 기준 복원 색인 생성

    - 같은 키가 여럿이면 레코드 순회에서 먼저 나온 항목을 남겨 선형 탐색과 같은 결과를 유지
    """
    index: RestoreHashIndex = {}
    for key, rec in records.items():
        index.setdefault(
            (rec.hash_key, rec.target_dirname, _normalized_path(rec.primary_root)),
            (key, rec),
        )
    return index


def build_restore_fingerprint_index(
    records: Mapping[str, ManifestRecord],
) -> RestoreFingerprintIndex:
    """(내용 지문, 분류 폴더명, 정규화 주 작업 폴더) 기준 복원 색인 생성"""
    index: RestoreFingerprintIndex = {}
    for key, rec in records.items():
        if not rec.fingerprint:
            continue
        index.setdefault(
            (rec.fingerprint, rec.target_dirname, _normalized_path(rec.primary_root)),
            (key, rec),
        )
    return index


def _compose_restored_target(rec: ManifestRecord, basename: str) -> tuple[str, str]:
    """원본 폴더와 논리 경로 접두사를 유지한 채 지정 파일명으로 복원 대상 경로와 논리 이름 생성"""
    restored_path = os.path.join(os.path.dirname(rec.src_path), basename)
    logical_dir = posixpath.dirname(rec.logical_filename)
    restored_logical = f"{logical_dir}/{basename}" if logical_dir else basename
    return restored_path, restored_logical


def _strip_hash_if_present(flat_filename: str) -> str:
    """평탄 파일명에서 해시 접미사가 있으면 떼어낸 복원용 파일명 반환"""
    parsed = extract_hash_from_filename(flat_filename)
    if parsed is None:
        return flat_filename
    cleaned_stem, _, ext = parsed
    return f"{cleaned_stem}{ext}"


def resolve_restore_destination(
    primary_root: str,
    target_dirname: str,
    flat_filename: str,
    records: Mapping[str, ManifestRecord],
    hash_index: RestoreHashIndex,
    *,
    current_path: str | None = None,
    fingerprint_index: RestoreFingerprintIndex | None = None,
) -> tuple[str, str, str | None]:
    """분류 폴더 내 평탄 파일을 되돌릴 원본 위치 판정

    - 반환값은 (복원할 원본 절대 경로, 표시용 논리 파일명, 매칭된 레코드 키)
    - 직접 이름 일치 -> 해시 접미사 일치 -> 내용 지문 일치 순으로 원위치를 찾음
    - 태그 추가 등으로 이름이 바뀐 경우 원래 폴더로 되돌리면서 바뀐 이름의 태그는 유지
    - 매니페스트에 없는 레거시 파일은 주 작업 폴더 직하위 경로로 폴백
    """
    direct_key = record_key(primary_root, target_dirname, flat_filename)
    direct = records.get(direct_key)
    if direct is not None:
        return direct.src_path, direct.logical_filename, direct_key

    norm_root = _normalized_path(primary_root)
    parsed = extract_hash_from_filename(flat_filename)
    if parsed is not None:
        cleaned_stem, hash_key, new_ext = parsed
        hit = hash_index.get((hash_key, target_dirname, norm_root))
        if hit is not None:
            key, rec = hit
            orig_stem = os.path.splitext(os.path.basename(rec.src_path))[0]
            if cleaned_stem != orig_stem and _strip_tags_for_match(
                cleaned_stem
            ) == _strip_tags_for_match(orig_stem):
                restored_path, restored_logical = _compose_restored_target(
                    rec, f"{cleaned_stem}{new_ext}"
                )
                return restored_path, restored_logical, key
            logical_stem = os.path.splitext(rec.logical_filename)[0]
            restored = os.path.join(os.path.dirname(rec.src_path), f"{orig_stem}{new_ext}")
            return restored, f"{logical_stem}{new_ext}", key

    if current_path and fingerprint_index:
        try:
            fp = compute_fingerprint(current_path, os.path.getsize(current_path))
        except OSError:
            fp = ""
        if fp:
            fp_hit = fingerprint_index.get((fp, target_dirname, norm_root))
            if fp_hit is not None:
                key, rec = fp_hit
                restored_name = _strip_hash_if_present(flat_filename)
                restored_path, restored_logical = _compose_restored_target(rec, restored_name)
                return restored_path, restored_logical, key

    return os.path.join(primary_root, flat_filename), flat_filename, None
