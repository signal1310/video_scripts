"""영상별 직접 지정의 JSON 백업 내보내기와 불러오기 모듈

- 분석 캐시(`video_cache.sqlite3`)가 비워지거나 파일을 잃어도 영상별 지정을 되살릴 수 있게
  네 종류의 지정(출력 해상도 직접 지정, 사용자 지정 활성 화면, 잘라내기 제외, 수동 VR 보호)을
  JSON 파일로 남기고 다시 적용함
- 불러오기는 지금 파일의 내용 키(크기, 내용 지문)를 먼저 찾고, 없으면 같은 경로와 크기로 짝지음
  (이름이 바뀐 영상도 내용이 같으면 찾음)
- 원본 미디어는 읽어 지문을 계산할 때만 접근하고 내용은 바꾸지 않음
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass

from src.core.analysis.file_manager import is_video_file
from src.core.exceptions import DomainValueError, ErrorCode
from src.core.models import ActiveFrame
from src.core.resolution_tiers import is_valid_manual_target
from src.core.workspace_paths import (
    is_within_roots,
    logical_name_for_path,
    normalize_root_key,
    resolve_source_filepath,
)
from src.service.analysis_store import AnalysisStore
from src.service.designation_content import file_content_identity
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.session import ClassifierSession

logger = logging.getLogger(__name__)

FORMAT_NAME: str = "video_scripts_designations"
FORMAT_VERSION: int = 1
# - 지정 종류 값. JSON의 `kind`와 각 종류의 저장 값 JSON 키 접두로 씀
KIND_MANUAL_TARGET: str = "manual_target"
KIND_MANUAL_FRAME: str = "manual_active_frame"
KIND_CROP_EXCLUSION: str = "crop_exclusion"
KIND_VR_OVERRIDE: str = "vr_override"
# - 종류별 저장 값 JSON 키
_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    KIND_MANUAL_TARGET: ("tier",),
    KIND_MANUAL_FRAME: ("manual_x", "manual_y", "manual_width", "manual_height"),
    KIND_CROP_EXCLUSION: (KIND_CROP_EXCLUSION,),
    KIND_VR_OVERRIDE: (KIND_VR_OVERRIDE,),
}


@dataclass(frozen=True, slots=True)
class ImportResult:
    """지정 백업 불러오기 결과(적용, 못 찾은, 잘못된 형식 건수)

    - `applied`는 백업 항목을 지금 영상에 반영한 건수이며, 거짓 값인 잘라내기 제외, 수동 VR 보호
      항목이 지정을 지운 경우도 포함함(지정 상태를 백업과 같게 만든 것이므로)
    """

    applied: int
    unmatched: int
    skipped_invalid: int


def export_designations(
    analysis_store: AnalysisStore,
    frame_store: FrameAnalysisStore,
    root_dirs: Sequence[str] | None,
    path: str,
) -> int:
    """영상별 직접 지정을 JSON 파일로 내보내고 쓴 항목 수 반환

    - 임시 파일에 쓰고 `os.replace`로 옮겨 쓰다 만 파일이 남지 않게 함
    - `root_dirs`를 주면 그 작업 폴더 범위의 지정만 내보냄
    """
    entries: list[dict[str, object]] = []
    for row in analysis_store.read_manual_target_rows():
        located = _resolve_target_path(row.root_key, row.filename)
        if located is None:
            logger.warning(
                "출력 해상도 직접 지정 행의 경로를 만들지 못해 건너뜁니다: %s %s",
                row.root_key,
                row.filename,
            )
            continue
        if root_dirs is not None and not is_within_roots(located, root_dirs):
            continue
        entries.append(
            _entry(KIND_MANUAL_TARGET, located, row.filesize_bytes, row.fingerprint, {"tier": row.tier})
        )
    for row in frame_store.read_manual_active_frame_rows(root_dirs):
        values = {key: getattr(row, key) for key in _VALUE_KEYS[KIND_MANUAL_FRAME]}
        entries.append(
            _entry(KIND_MANUAL_FRAME, row.source_path, row.filesize_bytes, row.fingerprint, values)
        )
    for row in frame_store.read_crop_exclusion_rows(root_dirs):
        entries.append(
            _entry(
                KIND_CROP_EXCLUSION,
                row.source_path,
                row.filesize_bytes,
                row.fingerprint,
                {KIND_CROP_EXCLUSION: bool(row.exclude_crop)},
            )
        )
    for row in frame_store.read_vr_override_rows(root_dirs):
        entries.append(
            _entry(
                KIND_VR_OVERRIDE,
                row.source_path,
                row.filesize_bytes,
                row.fingerprint,
                {KIND_VR_OVERRIDE: bool(row.manual_vr)},
            )
        )
    document = {"format": FORMAT_NAME, "version": FORMAT_VERSION, "entries": entries}
    _write_atomically(path, document)
    return len(entries)


def _resolve_target_path(root_key: str, filename: str) -> str | None:
    """(작업 폴더 키, 논리 파일명)을 절대 경로로 되돌림. 만들 수 없으면 None

    - 저장 키는 normcase 표기이므로 실제 파일이 있으면 그 절대 경로의 대소문자 표기를 따라감
    """
    if not root_key or not filename:
        return None
    try:
        resolved = resolve_source_filepath(root_key, filename)
    except (OSError, ValueError):
        return None
    if os.path.exists(resolved):
        return os.path.abspath(resolved)
    return resolved


def _entry(
    kind: str, source_path: str, size: int | None, fingerprint: str | None, values: dict[str, object]
) -> dict[str, object]:
    """JSON 항목 하나(공통 식별 값과 종류별 저장 값)"""
    entry: dict[str, object] = {
        "kind": kind,
        "path": source_path,
        "filesize_bytes": size,
        "fingerprint": fingerprint,
    }
    entry.update(values)
    return entry


def _write_atomically(path: str, document: object) -> None:
    """JSON을 같은 폴더의 임시 파일에 쓰고 `os.replace`로 목표 경로에 옮김"""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(dir=folder, suffix=".part")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=1)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            logger.debug("내보내기 임시 파일을 지우지 못했습니다: %s", temp_path)
        raise


def _normcase(value: str) -> str:
    """경로를 Windows 대소문자 무시 표기로 맞춤(스캔 경로와 저장 경로의 대조용)"""
    return os.path.normcase(os.path.normpath(value))


def _current_videos(root_dirs: Sequence[str]) -> dict[str, tuple[int, str | None]]:
    """작업 폴더 아래 지금 영상 파일의 (크기, 지문 또는 None) 목록

    - 경로는 저장 행이 남기는 절대 경로 표기로 맞춰 백업 항목과 같은 표기로 비교하게 함
    - 내용 키(지문)는 크기를 읽는 같은 훑기에서 한 번만 계산
    """
    result: dict[str, tuple[int, str | None]] = {}
    for root in root_dirs:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if not is_video_file(name):
                    continue
                path = os.path.abspath(os.path.join(dirpath, name))
                try:
                    size = os.stat(path).st_size
                except OSError:
                    continue
                identity = file_content_identity(path)
                result.setdefault(path, (size, None if identity is None else identity[1]))
    return result


def import_designations(
    session: ClassifierSession,
    analysis_store: AnalysisStore,
    frame_store: FrameAnalysisStore,
    path: str,
    root_dirs: Sequence[str],
) -> ImportResult:
    """JSON 백업을 읽어 작업 폴더의 지금 영상에 다시 적용

    - 지금 파일의 내용 키(크기, 지문)가 같은 영상을 먼저 찾고, 없으면 같은 경로와 크기로 찾음
    - 형식이나 버전이 다른 파일, 읽지 못한 파일, 항목 목록이 없는 파일은 도메인 오류로 거절함
    - 짝을 못 찾은 항목은 `unmatched`, 잘못된 항목은 `skipped_invalid`로 세고 나머지는 계속 적용함
    """
    entries = _read_document(path)
    current = _current_videos(root_dirs)
    by_content: dict[tuple[int, str], str] = {}
    by_path: dict[tuple[str, int], str] = {}
    for current_path, (size, fingerprint) in current.items():
        if fingerprint is not None:
            by_content.setdefault((size, fingerprint), current_path)
        by_path.setdefault((_normcase(current_path), size), current_path)

    applied = 0
    unmatched = 0
    skipped_invalid = 0
    for entry in entries:
        if not isinstance(entry, dict) or not _entry_is_valid(entry):
            skipped_invalid += 1
            continue
        target = _find_target(entry, by_content, by_path)
        if target is None:
            unmatched += 1
            continue
        if _apply(session, analysis_store, frame_store, entry, target):
            applied += 1
        else:
            skipped_invalid += 1
    return ImportResult(applied=applied, unmatched=unmatched, skipped_invalid=skipped_invalid)


def _read_document(path: str) -> list[object]:
    """백업 파일을 읽어 항목 목록을 반환

    - 파일이 없거나 읽지 못한 경우와 잘못된 JSON은 읽기 실패 도메인 오류로 바꿈
    - 형식 이름, 버전, 항목 목록이 다르면 형식 도메인 오류로 거절함
    """
    try:
        with open(path, encoding="utf-8") as handle:
            document: object = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DomainValueError(
            "지정 백업 파일을 읽을 수 없습니다. 파일이 있는지, 올바른 백업 파일인지 확인해 주세요.",
            code=ErrorCode.DESIGNATION_BACKUP_INVALID,
        ) from error
    if not isinstance(document, dict):
        raise _invalid_backup_error()
    if document.get("format") != FORMAT_NAME or document.get("version") != FORMAT_VERSION:
        raise _invalid_backup_error()
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise _invalid_backup_error()
    return entries


def _invalid_backup_error() -> DomainValueError:
    """형식이 다른 백업 파일에 대한 도메인 오류"""
    return DomainValueError(
        "지원하지 않는 지정 백업 파일입니다. 형식이나 버전이 다릅니다.",
        code=ErrorCode.DESIGNATION_BACKUP_INVALID,
    )


def _entry_is_valid(entry: dict[str, object]) -> bool:
    """항목이 적용에 필요한 최소 값과 값 형식을 지녔는지. 저장 값의 유효성은 적용 때 각 저장 규격이 걸러냄"""
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in _VALUE_KEYS:
        return False
    if not isinstance(entry.get("path"), str) or not entry["path"]:
        return False
    size = entry.get("filesize_bytes")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int)):
        return False
    fingerprint = entry.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        return False
    if kind in (KIND_CROP_EXCLUSION, KIND_VR_OVERRIDE) and type(entry.get(kind)) is not bool:
        return False
    return all(key in entry for key in _VALUE_KEYS[kind])


def _find_target(
    entry: dict[str, object],
    by_content: dict[tuple[int, str], str],
    by_path: dict[tuple[str, int], str],
) -> str | None:
    """항목과 같은 내용의 지금 영상을 찾음. 없으면 같은 경로와 크기의 영상을 찾음"""
    size = entry.get("filesize_bytes")
    fingerprint = entry.get("fingerprint")
    path = _normcase(str(entry["path"]))
    if isinstance(size, int) and not isinstance(size, bool) and isinstance(fingerprint, str):
        found = by_content.get((size, fingerprint))
        if found is not None:
            return found
    if isinstance(size, int) and not isinstance(size, bool):
        return by_path.get((path, size))
    return None


def _apply(
    session: ClassifierSession,
    analysis_store: AnalysisStore,
    frame_store: FrameAnalysisStore,
    entry: dict[str, object],
    target: str,
) -> bool:
    """항목 한 건을 지금 영상에 적용. 종류별 저장 규격이 거부하면 거짓"""
    kind = str(entry["kind"])
    try:
        if kind == KIND_MANUAL_TARGET:
            _apply_manual_target(session, analysis_store, entry, target)
        elif kind == KIND_MANUAL_FRAME:
            _apply_manual_frame(frame_store, entry, target)
        elif kind == KIND_CROP_EXCLUSION:
            frame_store.set_crop_exclusion(os.path.abspath(target), bool(entry[KIND_CROP_EXCLUSION]))
        else:
            frame_store.set_vr_override(
                source_path=os.path.abspath(target),
                filename=os.path.basename(target),
                manual_vr=bool(entry[KIND_VR_OVERRIDE]),
            )
    except (ValueError, DomainValueError) as error:
        logger.warning("지정 백업 항목을 적용하지 못했습니다(%s): %s", kind, error)
        return False
    return True


def _apply_manual_target(
    session: ClassifierSession,
    analysis_store: AnalysisStore,
    entry: dict[str, object],
    target: str,
) -> None:
    """출력 해상도 직접 지정 항목을 세션의 저장 규격으로 적용

    - 세션 맵에 반영하고, 세션이 쓰는 분석 캐시 저장소의 전용 테이블에도 그대로 기록해
      백업 불러오기만으로 지정이 남게 함(화면 저장 경로는 세션 맵을 나중에 덮어씀)
    - 표 전체를 교체 저장하므로 저장되어 있던 모든 행을 시작점으로 삼아 다른 작업 폴더의 지정을 지우지 않음
    """
    value = entry["tier"]
    if not isinstance(value, str) or not is_valid_manual_target(value):
        raise DomainValueError(
            "출력 해상도 지정 값이 올바르지 않습니다.",
            code=ErrorCode.MANUAL_TARGET_INVALID,
        )
    located = logical_name_for_path(target, session.root_dir)
    if located is None:
        raise DomainValueError(
            "영상이 현재 작업 폴더 안에 있지 않아 지정을 적용할 수 없습니다.",
            code=ErrorCode.ROOT_DIR_INVALID,
        )
    filename = located[1]
    key = normalize_root_key(session.root_dir)
    merged: dict[str, dict[str, str]] = {}
    for row in analysis_store.read_manual_target_rows():
        merged.setdefault(normalize_root_key(row.root_key), {})[row.filename] = row.tier
    for root, values in session.manual_target_store.items():
        merged.setdefault(normalize_root_key(root), {}).update(values)
    merged.setdefault(key, {})[filename] = value
    session.set_manual_target_store(merged)
    analysis_store.write_manual_targets(merged)


def _entry_int(entry: dict[str, object], key: str) -> int:
    """항목의 정수 저장 값을 꺼냄. 정수가 아니면 ValueError로 저장 규격이 거부하게 함"""
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"저장 값 {key}는 정수여야 합니다")
    return value


def _apply_manual_frame(
    frame_store: FrameAnalysisStore, entry: dict[str, object], target: str
) -> None:
    """사용자 지정 활성 화면 항목을 저장 규격으로 적용

    - 영상 프레임 크기를 모르므로 저장 값보다 넉넉한 크기로 맞춰 저장 규격이 값을 그대로 지키게 함
    """
    frame = ActiveFrame(
        width=_entry_int(entry, "manual_width"),
        height=_entry_int(entry, "manual_height"),
        x=_entry_int(entry, "manual_x"),
        y=_entry_int(entry, "manual_y"),
    )
    frame_store.set_manual_active_frame(
        os.path.abspath(target),
        frame,
        frame_width=frame.x + frame.width + 2,
        frame_height=frame.y + frame.height + 2,
    )
