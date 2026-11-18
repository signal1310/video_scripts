"""작업 폴더의 비디오 메타데이터 목록을 인메모리로 관리하는 캐시 모듈"""

import logging
import os
from collections.abc import Collection

from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.file_manager import compute_dir_signature
from src.core.messages import format_invalid_root_dir_msg, format_keyframe_start
from src.core.models import VideoProps
from src.core.prober import (
    FFPROBE_EXECUTABLE_DEFAULT,
    CancelCheck,
    ProgressCallback,
    populate_keyframes,
    probe_directory,
)
from src.core.workspace_paths import are_all_valid_dirs, parse_root_dirs
from src.service.analysis_probe import persist_keyframes, probe_directory_with_store
from src.service.analysis_store import AnalysisStore

logger = logging.getLogger(__name__)

DirSignature = tuple[tuple[str, int, int], ...]


def _normalize_root_key(root_dir: str, *, recursive: bool = False) -> str:
    """작업 폴더 목록과 하위 폴더 포함 여부로 구분되는 캐시 키 생성"""
    norm = ";".join(
        os.path.normcase(os.path.abspath(path)) for path in parse_root_dirs(root_dir)
    )
    return f"{norm}|recursive={int(recursive)}"


class TableCache:
    """지정한 작업 폴더의 비디오 메타데이터(VideoProps) 목록을 인메모리로 관리

    최초 로드 이후 키프레임 간격 조회가 추가 요구되면 미측정 파일만 선택적 증분 업데이트 수행
    생성자는 디스크에 접근하지 않으며, 폴더 프로빙은 probe 팩토리에서만 수행
    """

    def __init__(
        self,
        root_dir: str,
        data: list[VideoProps],
        *,
        include_keyframe: bool,
        renamed_pairs: dict[str, str] | None = None,
    ) -> None:
        self._root_dir: str = root_dir
        self._data: list[VideoProps] = data
        self._include_keyframe: bool = include_keyframe
        self._renamed_pairs: dict[str, str] = dict(renamed_pairs) if renamed_pairs else {}

    @classmethod
    def probe(
        cls,
        root_dir: str,
        *,
        include_keyframe: bool,
        on_progress: ProgressCallback = None,
        is_cancelled: CancelCheck = None,
        store: AnalysisStore | None = None,
        reuse_store: bool = True,
        recursive: bool = False,
        excluded_dirnames: Collection[str] = (),
        ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
    ) -> "TableCache":
        """작업 폴더를 프로빙해 새 캐시를 생성

        콘솔 출력 없이 동작하므로 진행률 표시가 필요하면 on_progress를 전달
        """
        if not are_all_valid_dirs(root_dir):
            raise DomainFileNotFoundError(
                format_invalid_root_dir_msg(root_dir),
                code=ErrorCode.ROOT_DIR_INVALID,
            )

        renamed: dict[str, str] = {}
        if store is None:
            data = probe_directory(
                root_dir,
                include_keyframe_interval=include_keyframe,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                recursive=recursive,
                excluded_dirnames=excluded_dirnames,
                ffprobe=ffprobe,
            )
        else:
            data = probe_directory_with_store(
                root_dir,
                include_keyframe=include_keyframe,
                store=store,
                reuse_store=reuse_store,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                recursive=recursive,
                excluded_dirnames=excluded_dirnames,
                renamed_out=renamed,
                ffprobe=ffprobe,
            )
        return cls(root_dir, data, include_keyframe=include_keyframe, renamed_pairs=renamed)

    @property
    def root_dir(self) -> str:
        """캐시가 바인딩된 작업 폴더 루트 경로 반환"""
        return self._root_dir

    @property
    def data(self) -> list[VideoProps]:
        """캐시된 VideoProps 목록 반환"""
        return self._data

    @property
    def include_keyframe(self) -> bool:
        """이 캐시에 키프레임 측정이 포함되었는지 여부 반환"""
        return self._include_keyframe

    @property
    def renamed_pairs(self) -> dict[str, str]:
        """지문 매칭으로 복구된 (이전 파일명 -> 새 파일명) 쌍 사본 반환"""
        return dict(self._renamed_pairs)

    def update_keyframe(
        self,
        keyframe_flag: bool,
        on_progress: ProgressCallback = None,
        is_cancelled: CancelCheck = None,
        store: AnalysisStore | None = None,
        ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
    ) -> None:
        """기존 캐시에서 키프레임 정보가 비어 있는 항목만 추가 프로빙

        이미 분석이 완료된 영상은 중복 작업 생략
        """
        needs_update = keyframe_flag and (
            not self._include_keyframe
            or any(v.keyframe_interval is None for v in self._data)
        )
        if not needs_update:
            return

        self._include_keyframe = True

        missing = [v for v in self._data if v.keyframe_interval is None]
        if missing:
            logger.info("%s", format_keyframe_start(len(missing)))
        populate_keyframes(
            self._data,
            self._root_dir,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
            ffprobe=ffprobe,
        )
        if store is not None:
            persist_keyframes(store, self._root_dir, missing)

    def drop_files(self, filenames: Collection[str]) -> int:
        """지정한 논리 파일명의 행을 인메모리 목록에서 빼고 제거 개수를 반환"""
        targets = set(filenames)
        before = len(self._data)
        self._data[:] = [vid for vid in self._data if vid.filename not in targets]
        return before - len(self._data)


class CacheRegistry:
    """정규화한 작업 폴더 경로별 TableCache와 디렉터리 시그니처 저장소

    서비스 인스턴스마다 주입할 수 있어 테스트와 노트북 세션이 전역 상태를 공유하지 않도록 분리 가능
    """

    def __init__(self) -> None:
        self._caches: dict[str, TableCache] = {}
        self._signatures: dict[str, DirSignature] = {}

    def get(
        self,
        root_dir: str,
        *,
        recursive: bool = False,
    ) -> TableCache | None:
        """지정한 작업 폴더 범위에 등록된 캐시를 반환하고 없으면 None 반환"""
        return self._caches.get(_normalize_root_key(root_dir, recursive=recursive))

    def put(
        self,
        root_dir: str,
        cache: TableCache,
        signature: DirSignature | None = None,
        *,
        recursive: bool = False,
        excluded_dirnames: Collection[str] = (),
    ) -> None:
        """지정한 작업 폴더 범위의 캐시와 디렉터리 시그니처를 등록

        시그니처 생략 시 등록 시점의 디렉터리 상태를 기준으로 삼음
        """
        key = _normalize_root_key(root_dir, recursive=recursive)
        self._caches[key] = cache
        self._signatures[key] = (
            signature
            if signature is not None
            else compute_dir_signature(
                root_dir,
                recursive=recursive,
                excluded_dirnames=excluded_dirnames,
            )
        )

    def saved_signature(self, root_dir: str, *, recursive: bool = False) -> DirSignature | None:
        """지정한 작업 폴더 범위에 저장된 디렉터리 시그니처를 반환하고 없으면 None"""
        return self._signatures.get(_normalize_root_key(root_dir, recursive=recursive))

    def store_signature(self, root_dir: str, signature: DirSignature, *, recursive: bool = False) -> None:
        """등록된 캐시 범위의 시그니처를 호출자가 만든 현재 값으로 바꿈, 캐시가 없으면 무시"""
        key = _normalize_root_key(root_dir, recursive=recursive)
        if key in self._caches:
            self._signatures[key] = signature

    def invalidate(self, root_dir: str | None = None, *, recursive: bool = False) -> None:
        """지정된 경로 범위(생략 시 전체)의 캐시와 디렉터리 시그니처를 초기화"""
        if root_dir is None:
            self._caches.clear()
            self._signatures.clear()
            return
        key = _normalize_root_key(root_dir, recursive=recursive)
        self._caches.pop(key, None)
        self._signatures.pop(key, None)

    def is_stale(
        self,
        root_dir: str,
        *,
        recursive: bool = False,
        excluded_dirnames: Collection[str] = (),
    ) -> bool:
        """등록된 캐시의 디렉터리 파일 시그니처가 현재 디스크 상태와 달라졌는지 반환"""
        key = _normalize_root_key(root_dir, recursive=recursive)
        if key not in self._caches:
            return False
        saved_sig = self._signatures.get(key)
        if saved_sig is None:
            return False
        curr_sig = compute_dir_signature(
            root_dir,
            recursive=recursive,
            excluded_dirnames=excluded_dirnames,
        )
        return curr_sig != saved_sig

    def drop_if_stale(
        self,
        root_dir: str,
        *,
        recursive: bool = False,
        excluded_dirnames: Collection[str] = (),
    ) -> None:
        """디렉터리 파일 시그니처가 저장 시점과 달라졌으면 해당 범위의 캐시를 무효화"""
        if not self.is_stale(
            root_dir,
            recursive=recursive,
            excluded_dirnames=excluded_dirnames,
        ):
            return
        key = _normalize_root_key(root_dir, recursive=recursive)
        self._caches.pop(key, None)
        self._signatures.pop(key, None)

    def refresh_signature(
        self,
        root_dir: str,
        *,
        recursive: bool = False,
        excluded_dirnames: Collection[str] = (),
    ) -> None:
        """캐시가 등록된 범위에 한해 현재 디렉터리 시그니처를 다시 저장"""
        key = _normalize_root_key(root_dir, recursive=recursive)
        if key in self._caches:
            self._signatures[key] = compute_dir_signature(
                root_dir,
                recursive=recursive,
                excluded_dirnames=excluded_dirnames,
            )


DEFAULT_CACHE_REGISTRY = CacheRegistry()
