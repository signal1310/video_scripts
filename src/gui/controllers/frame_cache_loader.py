"""화질 분석 캐시 적재를 워커 스레드에서 실행하는 모듈

- 표를 다시 채울 때 파일을 읽는 일(내용 지문, 영상별 지정 내용 확인)과 SQLite 조회를 화면 스레드에서 떼어 냄
- 워커는 Qt 객체에 접근하지 않고 결과를 시그널로만 넘기며, 화면 스레드는 마지막으로 시작한 적재의 결과만 적용
- 적재를 다시 시작하면 앞선 워커에 취소를 알리고 그 결과는 버림
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from src.core.analysis.frame_analysis import frame_thresholds_from_config
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame, VideoEvaluation
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.frame_analysis_targets import build_requests
from src.service.frame_analysis_service import FrameAnalysisRequest, FrameAnalysisService
from src.service.frame_analysis_store import FrameAnalysisStore

logger = logging.getLogger(__name__)

FRAME_ANALYSIS_WORK_DIR: str = os.path.join(tempfile.gettempdir(), "video_scripts_frame_analysis")


@dataclass(frozen=True, slots=True)
class FrameCacheSnapshot:
    """표에 반영할 화질 분석 캐시 한 벌: 요청, 결과, 영상별 지정, 여백 확인 필요 표시"""

    root_dir: str
    requests: list[FrameAnalysisRequest]
    results: dict[str, FrameAnalysisResult]
    manual_vr: frozenset[str]
    crop_excluded: frozenset[str]
    manual_frames: dict[str, ActiveFrame]
    review_flagged: frozenset[str]


def frame_cache_service(
    store: FrameAnalysisStore, config: ClassifierConfig
) -> FrameAnalysisService:
    """표에 캐시 결과와 여백 확인 표시를 반영할 때 쓰는 서비스(측정 실행 없이 조회만)"""
    return FrameAnalysisService(
        store,
        work_dir=FRAME_ANALYSIS_WORK_DIR,
        has_vmaf=True,
        mode=config.encode.frame_analysis_mode,
        scale_algorithm=config.encode.scale_algorithm,
        thresholds=frame_thresholds_from_config(config),
    )


def source_paths(evaluations: Sequence[VideoEvaluation], root_dir: str) -> list[str]:
    """평가 목록의 원본 파일 절대 경로 목록"""
    return [resolve_source_filepath(root_dir, ev.video.filename) for ev in evaluations]


def names_for_paths(
    evaluations: Sequence[VideoEvaluation], root_dir: str, paths: frozenset[str]
) -> frozenset[str]:
    """저장된 경로 집합을 표 파일명 집합으로 바꿈"""
    return frozenset(
        ev.video.filename
        for ev in evaluations
        if resolve_source_filepath(root_dir, ev.video.filename) in paths
    )


def load_manual_vr_names(
    store: FrameAnalysisStore, evaluations: Sequence[VideoEvaluation], root_dir: str
) -> frozenset[str]:
    """수동 VR 보호로 저장된 영상의 파일명 집합"""
    overrides = store.load_vr_overrides(source_paths(evaluations, root_dir))
    return names_for_paths(evaluations, root_dir, overrides)


def load_crop_exclusion_names(
    store: FrameAnalysisStore, evaluations: Sequence[VideoEvaluation], root_dir: str
) -> frozenset[str]:
    """검은 여백 잘라내기 제외로 저장된 영상의 파일명 집합"""
    exclusions = store.load_crop_exclusions(source_paths(evaluations, root_dir))
    return names_for_paths(evaluations, root_dir, exclusions)


def load_manual_frame_names(
    store: FrameAnalysisStore, evaluations: Sequence[VideoEvaluation], root_dir: str
) -> dict[str, ActiveFrame]:
    """저장된 사용자 지정 활성 화면을 지금 프레임 크기와 맞춰 파일명별로 조회"""
    pairs = [
        (resolve_source_filepath(root_dir, ev.video.filename), ev.video) for ev in evaluations
    ]
    frames = store.load_manual_active_frames(
        {path: (video.width, video.height) for path, video in pairs}
    )
    return {video.filename: frames[path] for path, video in pairs if path in frames}


def run_frame_cache_load(
    store: FrameAnalysisStore,
    evaluations: Sequence[VideoEvaluation],
    root_dir: str,
    config: ClassifierConfig,
    cancel_event: threading.Event,
) -> FrameCacheSnapshot | None:
    """캐시 결과와 영상별 지정을 읽어 표에 반영할 값 묶음을 만듦, 취소되었으면 None

    - 내용 지문과 지정 내용 확인까지 이 함수 안에서 하므로 화면 스레드는 파일을 읽지 않음
    - 취소는 파일을 읽는 동안과 조회 경계마다 확인해 워커 스레드가 곧 끝나게 함
    """
    if cancel_event.is_set():
        return None
    paths = source_paths(evaluations, root_dir)
    overrides = store.load_vr_overrides(paths)
    requests = build_requests(
        evaluations, root_dir, overrides, is_cancelled=cancel_event.is_set
    )
    if cancel_event.is_set():
        return None
    service = frame_cache_service(store, config)
    results = service.load_results(requests)
    exclusions = store.load_crop_exclusions(paths)
    frames = load_manual_frame_names(store, evaluations, root_dir)
    flagged = service.crop_review_statuses(
        requests, crop_enabled=config.encode.crop_black_bars
    )
    if cancel_event.is_set():
        return None
    return FrameCacheSnapshot(
        root_dir=root_dir,
        requests=requests,
        results=results,
        manual_vr=names_for_paths(evaluations, root_dir, overrides),
        crop_excluded=names_for_paths(evaluations, root_dir, exclusions),
        manual_frames=frames,
        review_flagged=frozenset(flagged),
    )


class FrameCacheLoadWorker(QThread):
    """캐시 적재 한 건을 실행하는 전용 스레드"""

    completed = pyqtSignal(int, object)

    def __init__(
        self,
        store: FrameAnalysisStore,
        evaluations: Sequence[VideoEvaluation],
        root_dir: str,
        config: ClassifierConfig,
        token: int,
    ) -> None:
        super().__init__()
        self._store = store
        self._evaluations = list(evaluations)
        self._root_dir = root_dir
        self._config = config
        self._token = token
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """진행 중인 적재 취소 요청"""
        self._cancel_event.set()

    def run(self) -> None:
        """워커 스레드 본문. 읽기 실패는 기록만 하고 결과 없이 끝냄"""
        try:
            snapshot = run_frame_cache_load(
                self._store,
                self._evaluations,
                self._root_dir,
                self._config,
                self._cancel_event,
            )
        except Exception:
            logger.exception("화질 분석 캐시를 읽지 못했습니다")
            snapshot = None
        self.completed.emit(self._token, snapshot)


class FrameCacheLoader(QObject):
    """캐시 적재를 워커 스레드에서 실행하고 마지막으로 시작한 적재의 결과만 넘김

    - 워커 시그널은 큐 연결로 화면 스레드에서 받으므로 화면 스레드는 결과 적용만 함
    - 앞선 적재는 취소를 알려 곧 끝내고, 그 결과는 토큰이 달라 버려짐
    """

    def __init__(self, store: FrameAnalysisStore) -> None:
        super().__init__()
        self._store = store
        self._latest = 0
        self._root_dir = ""
        self._on_done: Callable[[FrameCacheSnapshot | None], None] | None = None
        self._workers: list[FrameCacheLoadWorker] = []
        self._closed = False

    @property
    def pending(self) -> bool:
        """결과를 기다리는 적재가 있으면 True"""
        return self._on_done is not None

    @property
    def root_dir(self) -> str:
        """진행 중인 적재의 작업 폴더"""
        return self._root_dir

    def start(
        self,
        evaluations: Sequence[VideoEvaluation],
        root_dir: str,
        config: ClassifierConfig,
        on_done: Callable[[FrameCacheSnapshot | None], None],
    ) -> None:
        """캐시 적재를 워커 스레드에서 시작하고 앞선 적재는 취소"""
        if self._closed:
            return
        self.cancel()
        self._latest += 1
        self._root_dir = root_dir
        self._on_done = on_done
        self._workers = [worker for worker in self._workers if not worker.isFinished()]
        worker = FrameCacheLoadWorker(self._store, evaluations, root_dir, config, self._latest)
        worker.completed.connect(self._deliver)
        self._workers.append(worker)
        worker.start()

    def cancel(self) -> None:
        """진행 중인 적재를 취소하고 그 결과를 버림"""
        self._on_done = None
        for worker in self._workers:
            worker.cancel()

    def shutdown(self) -> None:
        """창을 닫을 때 진행 중인 적재를 취소하고 스레드 종료를 기다림

        - 저장소 연결을 닫기 전에 기다려야 워커가 닫힌 연결을 다시 잡지 않음
        """
        self._closed = True
        self.cancel()
        for worker in self._workers:
            worker.wait()
        self._workers = []

    def _deliver(self, token: int, snapshot: object) -> None:
        """마지막 적재의 결과만 화면 스레드 콜백으로 넘김. 적재 실패도 알려 기다리던 시작을 잇게 함"""
        if self._closed or token != self._latest or self._on_done is None:
            return
        done, self._on_done = self._on_done, None
        done(snapshot if isinstance(snapshot, FrameCacheSnapshot) else None)

