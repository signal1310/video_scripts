"""작업 폴더의 비디오 메타데이터 목록을 인메모리로 관리하는 캐시 모듈"""

import logging
import os

from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.messages import format_invalid_root_dir_msg
from src.core.models import VideoProps
from src.core.prober import (
    CancelCheck,
    ProgressCallback,
    populate_keyframes,
    probe_directory,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._root_dir: str = root_dir
        self._data: list[VideoProps] = data
        self._include_keyframe: bool = include_keyframe

    @classmethod
    def probe(
        cls,
        root_dir: str,
        *,
        include_keyframe: bool,
        on_progress: ProgressCallback = None,
        is_cancelled: CancelCheck = None,
    ) -> "TableCache":
        """작업 폴더를 프로빙해 새 캐시를 생성

        콘솔 출력 없이 동작하므로 진행률 표시가 필요하면 on_progress를 전달
        """
        if not os.path.isdir(root_dir):
            raise DomainFileNotFoundError(
                format_invalid_root_dir_msg(root_dir),
                code=ErrorCode.ROOT_DIR_INVALID,
            )

        logger.info("작업 폴더의 비디오 메타데이터를 분석합니다.")
        logger.info("작업 폴더: %s", root_dir)

        data = probe_directory(
            root_dir,
            include_keyframe_interval=include_keyframe,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )
        logger.info("비디오 메타데이터 분석을 완료했습니다.")
        return cls(root_dir, data, include_keyframe=include_keyframe)

    @property
    def root_dir(self) -> str:
        """캐시가 바인딩된 작업 폴더 루트 경로 반환"""
        return self._root_dir

    @property
    def data(self) -> list[VideoProps]:
        """캐시된 VideoProps 목록 반환"""
        return self._data

    def update_keyframe(
        self,
        keyframe_flag: bool,
        on_progress: ProgressCallback = None,
        is_cancelled: CancelCheck = None,
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

        logger.info("기존 테이블에 키프레임 간격 정보를 추가 분석합니다.")
        logger.info("작업 폴더: %s", self._root_dir)

        populate_keyframes(
            self._data,
            self._root_dir,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )
        logger.info("키프레임 분석을 완료했습니다.")
