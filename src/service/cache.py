import os
from collections.abc import Callable

from src.core.models import VideoProps
from src.core.prober import ProgressCallback, populate_keyframes, probe_directory


def _default_console_progress(current: int, total: int, filename: str) -> None:
    progress: float = (current / max(total, 1)) * 100
    print(
        f"\r[progress{progress:5.1f}%] 파일 '{filename}' 완료".ljust(120),
        end="",
        flush=True,
    )


class TableCache:
    """
    - 지정한 작업 경로의 비디오 메타데이터(VideoProps) 목록을 인메모리에 보관 및 관리.
    - 최초 로드 이후 키프레임 간격 조회가 추가로 필요해진 경우, 미측정 파일들에 대해서만 선택적으로 증분 업데이트를 수행.
    """

    def __init__(
        self,
        root_dir: str,
        init_keyframe_flag: bool = False,
        on_progress: ProgressCallback = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        if not os.path.isdir(root_dir):
            raise FileNotFoundError(
                f"지정된 루트 디렉터리를 찾을 수 없거나 유효하지 않음: '{root_dir}'"
            )

        self._root_dir: str = root_dir
        self._include_keyframe: bool = init_keyframe_flag

        log_fn = on_log or print
        prog_fn = on_progress or _default_console_progress

        log_fn("[INFO] 현재 작업 경로의 비디오 메타데이터를 분석.")
        log_fn(f"[INFO] 작업 경로: {self._root_dir}")

        self._data: list[VideoProps] = probe_directory(
            self._root_dir,
            include_keyframe_interval=self._include_keyframe,
            on_progress=prog_fn,
        )
        if on_progress is None:
            print("\r[INFO] 비디오 메타데이터 분석 완료.".ljust(120))

    @property
    def root_dir(self) -> str:
        return self._root_dir

    @property
    def data(self) -> list[VideoProps]:
        return self._data

    def update_keyframe(
        self,
        keyframe_flag: bool,
        on_progress: ProgressCallback = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        """
        - 기존 캐시 데이터 내에서 키프레임 정보가 비어있는 항목을 감지하여 추가 프로빙을 진행.
        - 이미 분석이 완료된 영상에 대해서는 중복 작업을 생략하여 실행 시간을 단축.
        """
        needs_update = keyframe_flag and (
            not self._include_keyframe
            or any(v.keyframe_interval is None for v in self._data)
        )
        if not needs_update:
            return

        self._include_keyframe = True
        log_fn = on_log or print
        prog_fn = on_progress or _default_console_progress

        log_fn("[INFO] 기존 테이블에 키프레임 간격 정보 추가 분석 진행.")
        log_fn(f"[INFO] 작업 경로: {self._root_dir}")

        populate_keyframes(
            self._data,
            self._root_dir,
            on_progress=prog_fn,
        )
        if on_progress is None:
            print("\r[INFO] 키프레임 분석 작업 완료.".ljust(120))
