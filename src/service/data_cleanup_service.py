"""통합 데이터 정리 및 초기화 허브의 백엔드 서비스 모듈

- 4범주 21개 정리 항목의 범위(현재 작업 폴더/전체 저장 데이터)를 정하고, 저장 데이터(범주 1, 2)를 실제로 지움
- 미리보기는 아무것도 지우지 않고 항목별 건수와 임시 파일 용량만 셈
- 화면 배치와 설정(범주 3, 4)은 항목 구분만 하고 실제 초기화는 허브 화면(5c-6c-2)이 메모리 상태와 저장 파일로 수행
- 원본 영상 파일은 어떤 항목에서도 지우지 않으며, 남은 임시 파일은 주입한 휴지통 함수로만 옮김
"""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from src.core.encode.encode_paths import PART_SUFFIX
from src.core.workspace_paths import is_within_roots, parse_root_dirs
from src.service.analysis_store import AnalysisStore
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_leftovers import TrashFn, is_protected_part
from src.service.encode.encode_output_history import EncodeOutputHistory
from src.service.encode.encode_resume import find_unrecorded_parts
from src.service.frame_analysis_store import FrameAnalysisStore

logger = logging.getLogger(__name__)

CleanupScope = Literal["current", "all"]
CleanupCategory = Literal["encode", "analysis", "layout", "settings"]
CleanupItemKey = Literal[
    "part_files",
    "discarded_history",
    "encode_output_history",
    "probe_cache",
    "frame_cache",
    "manual_targets",
    "crop_exclusions",
    "crop_review_acks",
    "manual_active_frames",
    "vr_overrides",
    "column_order",
    "column_widths",
    "column_visibility",
    "table_sort_group",
    "window_geometry",
    "recent_dirs_selection",
    "criteria_settings",
    "encode_settings",
    "general_display_settings",
    "shortcuts_settings",
    "exception_rules",
]

CLEANUP_SCOPE_CURRENT: CleanupScope = "current"
CLEANUP_SCOPE_ALL: CleanupScope = "all"
CLEANUP_SCOPES: tuple[CleanupScope, ...] = (CLEANUP_SCOPE_CURRENT, CLEANUP_SCOPE_ALL)
CLEANUP_SCOPE_LABELS: dict[CleanupScope, str] = {
    CLEANUP_SCOPE_CURRENT: "현재 작업 폴더만",
    CLEANUP_SCOPE_ALL: "전체 저장 데이터",
}
CLEANUP_CATEGORY_LABELS: dict[CleanupCategory, str] = {
    "encode": "인코딩 기록 및 임시 파일",
    "analysis": "분석 캐시 및 영상별 지정",
    "layout": "화면 배치 및 기록",
    "settings": "설정 및 규칙",
}
# - 항목 순서가 화면 체크박스 순서와 프리셋 구성을 그대로 결정하므로 선언 순서를 유지
CATEGORY_CLEANUP_ITEM_KEYS: dict[CleanupCategory, tuple[CleanupItemKey, ...]] = {
    "encode": ("part_files", "discarded_history", "encode_output_history"),
    "analysis": (
        "probe_cache",
        "frame_cache",
        "manual_targets",
        "crop_exclusions",
        "crop_review_acks",
        "manual_active_frames",
        "vr_overrides",
    ),
    "layout": (
        "column_order",
        "column_widths",
        "column_visibility",
        "table_sort_group",
        "window_geometry",
        "recent_dirs_selection",
    ),
    "settings": (
        "criteria_settings",
        "encode_settings",
        "general_display_settings",
        "shortcuts_settings",
        "exception_rules",
    ),
}
CLEANUP_ITEM_LABELS: dict[CleanupItemKey, str] = {
    "part_files": "임시 .part 파일",
    "discarded_history": "이전 폐기 기록",
    "encode_output_history": "인코딩 완료 이력",
    "probe_cache": "영상 분석 캐시",
    "frame_cache": "화질 분석/화면 여백 캐시",
    "manual_targets": "영상별 출력 해상도 직접 지정",
    "crop_exclusions": "화면 여백 제외 지정",
    "crop_review_acks": "여백 확인 필요 표시",
    "manual_active_frames": "사용자 지정 활성 화면",
    "vr_overrides": "수동 VR 보호 지정",
    "column_order": "표 열 순서",
    "column_widths": "표 열 너비",
    "column_visibility": "표 표시 열/프리셋",
    "table_sort_group": "정렬/그룹/필터",
    "window_geometry": "창 크기/위치 및 상단 접힘",
    "recent_dirs_selection": "최근 폴더 및 표 선택 기억",
    "criteria_settings": "판별 기준 설정",
    "encode_settings": "인코딩 설정",
    "general_display_settings": "일반/표시/해상도 분석 설정",
    "shortcuts_settings": "사용자 지정 단축키",
    "exception_rules": "예외 규칙 목록",
}
CLEANUP_ITEM_CATEGORY: dict[CleanupItemKey, CleanupCategory] = {
    key: category for category, keys in CATEGORY_CLEANUP_ITEM_KEYS.items() for key in keys
}
# - 범주 1, 2는 저장 데이터라 서비스가 직접 지우고, 범주 3, 4는 화면과 설정 상태를 허브가 다시 만듦
STORAGE_CLEANUP_ITEM_KEYS: tuple[CleanupItemKey, ...] = (
    *CATEGORY_CLEANUP_ITEM_KEYS["encode"],
    *CATEGORY_CLEANUP_ITEM_KEYS["analysis"],
)
UI_RESET_CLEANUP_ITEM_KEYS: tuple[CleanupItemKey, ...] = (
    *CATEGORY_CLEANUP_ITEM_KEYS["layout"],
    *CATEGORY_CLEANUP_ITEM_KEYS["settings"],
)
ALL_CLEANUP_ITEM_KEYS: tuple[CleanupItemKey, ...] = (*STORAGE_CLEANUP_ITEM_KEYS, *UI_RESET_CLEANUP_ITEM_KEYS)

CleanupPresetKey = Literal[
    "part_only", "retry_encode", "frame_only", "reanalyze_fresh", "reset_layout", "reset_all"
]
# - 선언 순서가 화면 알약 순서를 그대로 결정하므로 세분화한 순서(단일 항목 -> 전체)를 유지
CLEANUP_PRESETS: dict[CleanupPresetKey, tuple[CleanupItemKey, ...]] = {
    "part_only": ("part_files",),
    "retry_encode": (*(CATEGORY_CLEANUP_ITEM_KEYS["encode"]),),
    "frame_only": ("frame_cache",),
    "reanalyze_fresh": (*(CATEGORY_CLEANUP_ITEM_KEYS["analysis"]),),
    "reset_layout": CATEGORY_CLEANUP_ITEM_KEYS["layout"],
    "reset_all": ALL_CLEANUP_ITEM_KEYS,
}
CLEANUP_PRESET_LABELS: dict[CleanupPresetKey, str] = {
    "part_only": "임시 파일만 정리",
    "retry_encode": "인코딩 다시 시도",
    "frame_only": "화질 분석만 다시",
    "reanalyze_fresh": "분석 처음부터 다시",
    "reset_layout": "화면 배치만 초기화",
    "reset_all": "전체 초기화",
}
IRREVERSIBLE_CATEGORY: CleanupCategory = "settings"
PART_TRASH_FAILED_TEMPLATE: str = "임시 파일 {count}개를 휴지통으로 옮기지 못했습니다: {names}"
PART_UNMOVABLE_TEMPLATE: str = "휴지통이 없는 드라이브에 있는 임시 파일 {count}개는 지우지 않고 남겼습니다"
SCOPE_ROOTS_REQUIRED_TEXT: str = "현재 작업 폴더 범위를 정하려면 작업 폴더 경로가 필요합니다"
MovableFn = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class CleanupItemPreview:
    """정리 항목 한 개의 미리보기. count는 지울 기록 수, size_bytes는 지울 파일 용량 합"""

    key: CleanupItemKey
    count: int
    size_bytes: int
    available: bool


@dataclass(frozen=True, slots=True)
class CleanupPlanResult:
    """정리 실행 결과: 항목별 지운 건수와 항목별 실패 사유"""

    cleared_counts: dict[CleanupItemKey, int]
    errors: dict[CleanupItemKey, str]


# - 화면이 범위별 미리보기를 요청할 때 쓰는 서명. 저장 데이터 건수를 화면 밖에서 읽을 때 재사용
StoragePreviewFn = Callable[[CleanupScope], Mapping[CleanupItemKey, CleanupItemPreview]]


def _default_trash_fn(path: str) -> bool:
    """기본 휴지통 옮기기: send2trash가 있으면 그 함수를, 없으면 옮기지 못한 것으로 봄

    - 서비스 계층은 Qt를 모르므로 화면은 QFile.moveToTrash를 주입해 씀
    - send2trash는 선택 설치라 정적 import 대신 런타임 조회로 확인
    """
    try:
        send2trash = importlib.import_module("send2trash").send2trash
    except (ImportError, AttributeError):
        logger.warning("send2trash가 없어 임시 파일을 휴지통으로 옮기지 않습니다: %s", path)
        return False
    try:
        send2trash(path)
    except OSError as error:
        logger.warning("임시 파일을 휴지통으로 옮기지 못했습니다: %s (%s)", path, error)
        return False
    return True


def _always_movable(_path: str) -> bool:
    """기본 판정: 모든 임시 파일을 옮길 수 있는 것으로 봄"""
    return True


def _path_key(path: str) -> str:
    """경로 비교용 키. 구분자와 대소문자 차이를 없앰"""
    return os.path.normcase(os.path.normpath(path))


def _file_size(path: str) -> int:
    """파일 크기. 읽을 수 없으면 0"""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def cleanup_item_category(key: CleanupItemKey) -> CleanupCategory:
    """항목이 속한 범주 반환"""
    category = CLEANUP_ITEM_CATEGORY.get(key)
    if category is None:
        raise ValueError(f"알 수 없는 정리 항목입니다: {key}")
    return category


def partition_cleanup_keys(
    keys: Sequence[CleanupItemKey],
) -> tuple[tuple[CleanupItemKey, ...], tuple[CleanupItemKey, ...]]:
    """정리 항목을 (저장 데이터 정리, 화면/설정 초기화) 두 묶음으로 나눔

    - 입력 순서를 유지하고 중복은 한 번만 남김
    - 반환값은 (범주 1, 2 항목, 범주 3, 4 항목)
    """
    storage: list[CleanupItemKey] = []
    ui_reset: list[CleanupItemKey] = []
    for key in keys:
        target = storage if cleanup_item_category(key) in ("encode", "analysis") else ui_reset
        if key not in target:
            target.append(key)
    return tuple(storage), tuple(ui_reset)


def _scope_roots(scope: CleanupScope, root_spec: str | Sequence[str]) -> tuple[str, ...] | None:
    """범위가 현재 작업 폴더면 그 폴더 목록, 전체면 None. 현재 범위인데 폴더가 없으면 ValueError"""
    if scope not in CLEANUP_SCOPES:
        raise ValueError(f"알 수 없는 정리 범위입니다: {scope}")
    if scope == CLEANUP_SCOPE_ALL:
        return None
    roots = parse_root_dirs(root_spec)
    if not roots:
        raise ValueError(SCOPE_ROOTS_REQUIRED_TEXT)
    return roots


class DataCleanupService:
    """저장 데이터 정리 항목의 미리보기와 실행을 맡는 서비스

    - 저장소를 주입받아 실제 `data/`에 접근하지 않고 테스트할 수 있음
    - 범주 3, 4 항목은 저장 파일 유무만 미리보기에 담고 실행에서는 건너뛰어 허브가 직접 초기화
    - 범위가 전체면 기록에 남은 경로만 대상으로 하고 드라이브 전체를 훑지 않음
    """

    def __init__(
        self,
        *,
        analysis_store: AnalysisStore,
        frame_store: FrameAnalysisStore,
        encode_history: EncodeHistory,
        output_history: EncodeOutputHistory,
        state_files: Mapping[CleanupItemKey, str] | None = None,
        trash_fn: TrashFn | None = None,
        movable_fn: MovableFn | None = None,
    ) -> None:
        self._analysis_store: AnalysisStore = analysis_store
        self._frame_store: FrameAnalysisStore = frame_store
        self._encode_history: EncodeHistory = encode_history
        self._output_history: EncodeOutputHistory = output_history
        self._state_files: dict[CleanupItemKey, str] = dict(state_files or {})
        self._trash_fn: Callable[[str], bool] = trash_fn if trash_fn is not None else _default_trash_fn
        # - 휴지통이 없는 드라이브의 임시 파일은 옮기면 영구 삭제가 되므로 건드리지 않고 남김
        self._movable_fn: MovableFn = movable_fn if movable_fn is not None else _always_movable

    def preview(
        self,
        scope: CleanupScope,
        root_spec: str | Sequence[str] = "",
    ) -> dict[CleanupItemKey, CleanupItemPreview]:
        """정리 범위의 항목별 건수와 용량을 지우지 않고 계산

        - 저장 데이터 항목은 저장소를 읽어 세고, 저장 파일이 없으면 지울 것이 없는 것으로 봄
        - 임시 파일은 기록된 경로와 작업 폴더에서 찾은 기록 밖 `.part` 파일을 함께 셈
        """
        roots = _scope_roots(scope, root_spec)
        previews: dict[CleanupItemKey, CleanupItemPreview] = {}
        for key in ALL_CLEANUP_ITEM_KEYS:
            if cleanup_item_category(key) in ("encode", "analysis"):
                count, size_bytes = self._count_storage_item(key, roots)
                previews[key] = CleanupItemPreview(key, count, size_bytes, self._storage_available(key, roots))
            else:
                count, size_bytes = self._state_file_size(key)
                previews[key] = CleanupItemPreview(key, count, size_bytes, True)
        return previews

    def execute_storage_cleanup(
        self,
        keys: Sequence[CleanupItemKey],
        scope: CleanupScope,
        root_spec: str | Sequence[str] = "",
    ) -> CleanupPlanResult:
        """범주 1, 2 항목을 실제로 지우고 항목별 결과를 반환

        - 범주 3, 4 항목은 허브가 초기화하므로 여기서는 건너뜀
        - 임시 파일은 휴지통 함수로만 옮기고 원본 영상은 건드리지 않음
        - 한 항목이 실패해도 나머지 항목은 계속 진행하고 사유를 모음
        """
        roots = _scope_roots(scope, root_spec)
        storage_keys, _ui_keys = partition_cleanup_keys(keys)
        cleared: dict[CleanupItemKey, int] = {}
        errors: dict[CleanupItemKey, str] = {}
        for key in storage_keys:
            try:
                count, error = self._clear_storage_item(key, roots)
            except (sqlite3.Error, OSError, ValueError) as failure:
                logger.warning("데이터 정리 항목을 지우지 못했습니다: %s (%s)", key, failure)
                errors[key] = str(failure)
                continue
            cleared[key] = count
            if error:
                errors[key] = error
        return CleanupPlanResult(cleared, errors)

    def _count_storage_item(self, key: CleanupItemKey, roots: tuple[str, ...] | None) -> tuple[int, int]:
        """저장 데이터 항목 한 개의 건수와 파일 용량 합"""
        if key == "part_files":
            paths = self._part_paths(roots)
            return len(paths), sum(_file_size(path) for path in paths)
        if not self._store_db_exists(key):
            return 0, 0
        return self._count_store_rows(key, roots), 0

    def _count_store_rows(self, key: CleanupItemKey, roots: tuple[str, ...] | None) -> int:
        """SQLite 저장소에 남은 항목 건수

        - 완료 이력은 두 DB 파일에 나뉘어 있어 파일이 있는 쪽만 셈(없는 쪽을 읽어 빈 파일을 만들지 않음)
        """
        if key == "discarded_history":
            return self._encode_history.count_discarded(roots)
        if key == "encode_output_history":
            return self._count_output_history(roots)
        if key == "probe_cache":
            return self._analysis_store.count_for_roots(roots)
        if key == "manual_targets":
            return self._analysis_store.count_manual_targets(roots)
        if key == "frame_cache":
            return self._frame_store.count_for_roots(roots)
        if key == "crop_exclusions":
            return self._frame_store.count_crop_exclusions(roots)
        if key == "crop_review_acks":
            return self._frame_store.count_crop_review_acks(roots)
        if key == "manual_active_frames":
            return self._frame_store.count_manual_active_frames(roots)
        return self._frame_store.count_vr_overrides(roots)

    def _count_output_history(self, roots: tuple[str, ...] | None) -> int:
        """인코딩 완료 이력 건수: 결과 지문 기록과 완료 상태 작업 항목을 파일이 있는 쪽만 합산"""
        count = 0
        if self._db_exists(self._output_history.db_path):
            count += self._output_history.count_outputs(roots)
        if self._db_exists(self._encode_history.db_path):
            count += self._encode_history.count_completed_history(roots)
        return count

    def _storage_available(self, key: CleanupItemKey, roots: tuple[str, ...] | None) -> bool:
        """저장 데이터 항목을 확인할 저장소나 작업 폴더가 있는지"""
        if key == "part_files":
            return roots is not None or bool(self._part_paths(roots))
        return self._store_db_exists(key)

    def _state_file_size(self, key: CleanupItemKey) -> tuple[int, int]:
        """화면/설정 항목의 저장 파일 건수와 용량. 저장 파일 경로를 모르면 지울 것이 없는 것으로 봄"""
        path = self._state_files.get(key)
        if not path or not os.path.isfile(path):
            return 0, 0
        return 1, _file_size(path)

    def _clear_storage_item(self, key: CleanupItemKey, roots: tuple[str, ...] | None) -> tuple[int, str]:
        """저장 데이터 항목 한 개를 지우고 (지운 건수, 실패 사유)를 반환"""
        if key == "part_files":
            return self._clear_part_files(roots)
        if not self._store_db_exists(key):
            return 0, ""
        return self._clear_store_rows(key, roots), ""

    def _clear_store_rows(self, key: CleanupItemKey, roots: tuple[str, ...] | None) -> int:
        """SQLite 저장소의 항목을 지우고 지운 건수 반환"""
        if key == "discarded_history":
            return self._encode_history.clear_discarded(roots)
        if key == "encode_output_history":
            return self._clear_output_history(roots)
        if key == "probe_cache":
            return self._analysis_store.clear_for_roots(roots)
        if key == "manual_targets":
            return self._analysis_store.clear_manual_targets(roots)
        if key == "frame_cache":
            return self._frame_store.clear_for_roots(roots)
        if key == "crop_exclusions":
            return self._frame_store.clear_crop_exclusions(roots)
        if key == "crop_review_acks":
            return self._frame_store.clear_crop_review_acks(roots)
        if key == "manual_active_frames":
            return self._frame_store.clear_manual_active_frames(roots)
        return self._frame_store.clear_vr_overrides(roots)

    def _clear_output_history(self, roots: tuple[str, ...] | None) -> int:
        """결과 지문과 완료 상태 작업 항목을 함께 지워 다음 인코딩 대상에 다시 포함되게 함

        - 두 DB 파일을 따로 확인해 한쪽 파일만 있어도 다른 쪽을 건너뛰지 않음
        """
        cleared = 0
        if self._db_exists(self._output_history.db_path):
            cleared += self._output_history.clear_outputs(roots)
        if self._db_exists(self._encode_history.db_path):
            cleared += self._encode_history.clear_completed_history(roots)
        return cleared

    def _store_db_exists(self, key: CleanupItemKey) -> bool:
        """항목이 쓰는 SQLite 파일이 이미 있는지. 없으면 지우기를 시도해 빈 파일을 만들지 않음

        - 완료 이력은 결과 지문 DB와 작업 기록 DB 두 파일을 쓰므로 둘 중 하나만 있어도 확인 가능
        """
        if key == "encode_output_history":
            return self._db_exists(self._output_history.db_path) or self._db_exists(
                self._encode_history.db_path
            )
        if key in ("part_files", "discarded_history"):
            return self._db_exists(self._encode_history.db_path)
        if key in ("probe_cache", "manual_targets"):
            return self._db_exists(self._analysis_store.db_path)
        return self._db_exists(self._frame_store.db_path)

    def _db_exists(self, db_path: str) -> bool:
        """SQLite 파일이 이미 있는지"""
        return os.path.isfile(db_path)

    def _part_paths(self, roots: tuple[str, ...] | None) -> list[str]:
        """정리 범위의 임시 파일 경로 목록

        - 기록된 임시 파일 경로와 작업 폴더에서 찾은 기록 밖 `.part` 파일을 경로 기준으로 합침
        - 범위가 전체면 기록된 경로만 셈(모든 드라이브를 훑지 않음)
        - 기록 DB가 없으면 기록 조회를 건너뛰어 미리보기가 빈 DB 파일을 만들지 않게 함
        - 교체 대기, 교체 포기로 실패한 파일, 열린 배치에서 진행 중인 파일은 실제 결과물이거나 인코더가 쓰는 중이라 제외
        - `.part` 접미사가 아닌 경로도 함께 걸러 미리보기 건수와 실행 건수를 일치시킴
        """
        recorded: dict[str, str] = {}
        protected: set[str] = set()
        if self._store_db_exists("part_files"):
            recorded = {_path_key(path): path for path in self._encode_history.recorded_part_paths()}
            protected = {
                _path_key(path)
                for path, record in self._encode_history.part_records().items()
                if is_protected_part(record)
            }
        found: dict[str, str] = {}
        for key, path in recorded.items():
            if not os.path.isfile(path):
                continue
            if roots is None or is_within_roots(path, roots):
                found[key] = path
        if roots is not None:
            for path in find_unrecorded_parts(roots, list(recorded.values())):
                found.setdefault(_path_key(path), path)
        return sorted(
            path
            for key, path in found.items()
            if key not in protected and path.lower().endswith(PART_SUFFIX)
        )

    def _clear_part_files(self, roots: tuple[str, ...] | None) -> tuple[int, str]:
        """임시 파일만 휴지통으로 옮기고 (옮긴 수, 실패 사유)를 반환

        - 휴지통이 없는 드라이브의 파일은 영구 삭제가 되므로 건드리지 않고 남긴 뒤 남긴 수를 안내
        """
        moved = 0
        failed: list[str] = []
        unmovable = 0
        for path in self._part_paths(roots):
            if not _is_movable(path, self._movable_fn):
                unmovable += 1
                continue
            if _trash_part(path, self._trash_fn):
                moved += 1
            else:
                failed.append(os.path.basename(path))
        reasons: list[str] = []
        if failed:
            reasons.append(PART_TRASH_FAILED_TEMPLATE.format(count=len(failed), names=", ".join(failed)))
        if unmovable:
            reasons.append(PART_UNMOVABLE_TEMPLATE.format(count=unmovable))
        return moved, " / ".join(reasons)


def _is_movable(path: str, movable_fn: MovableFn) -> bool:
    """휴지통으로 옮겨도 영구 삭제가 되지 않는 파일인지. 판정 실패는 보수적으로 옮기지 않음"""
    try:
        return bool(movable_fn(path))
    except OSError as error:
        logger.warning("임시 파일을 옮길 수 있는지 확인하지 못했습니다: %s (%s)", path, error)
        return False


def _trash_part(path: str, trash_fn: Callable[[str], bool]) -> bool:
    """임시 파일 하나를 휴지통으로 옮김. 실패는 예외 없이 거짓"""
    try:
        return bool(trash_fn(path))
    except OSError as error:
        logger.warning("임시 파일을 휴지통으로 옮기지 못했습니다: %s (%s)", path, error)
        return False
