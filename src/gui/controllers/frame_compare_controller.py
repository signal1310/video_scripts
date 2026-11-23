"""화질 비교 세션 구성, 프레임 추출과 진입점 라우팅 컨트롤러

- 원본 프로빙, 최근 완료 결과, 캐시된 화질 분석 점수를 모아 미리 보기 창의 비교 모드에 넘길 세션을 만듦
- 표 행 우클릭과 진행 창 결과 목록, 미리 보기 전환 단축키를 같은 단일 창으로 보냄
- 창과 하단 재생 바는 미리 보기 창이 하나만 가지므로 이 컨트롤러는 창 수명을 관리하지 않음
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from typing import Protocol

from src.core.analysis.frame_compare import (
    ORIGINAL_LABEL,
    ComparePlan,
    FrameSource,
    resolve_compare_plan,
    result_target,
)
from src.core.config import ClassifierConfig
from src.core.models import SummaryReport, VideoEvaluation
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.dialog_coordinator import DialogCoordinator
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.preview.preview_controller import PreviewController
from src.gui.table.table_context_menu import set_row_compare_opener
from src.gui.table.video_table_view import VideoTableView
from src.service.analysis_store import open_default_store
from src.service.encode.encode_history import EncodeHistory
from src.service.frame_analysis_service import FrameAnalysisRecord, FrameAnalysisStore
from src.service.frame_compare_service import (
    WORK_DIR_NAME,
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
    FrameCompareService,
)
from src.service.session import ClassifierSession


def build_compare_service(config: ClassifierConfig, history: EncodeHistory) -> FrameCompareService:
    """설정의 FFmpeg, FFprobe 경로와 임시 작업 폴더로 서비스 생성"""
    return FrameCompareService(
        ffmpeg_executable=config.execution.ffmpeg_path.strip() or "ffmpeg",
        ffprobe_executable=config.execution.ffprobe_path.strip() or "ffprobe",
        work_dir=os.path.join(tempfile.gettempdir(), WORK_DIR_NAME),
        history=history,
    )


def _error_session(source_path: str, reason: str) -> CompareSession:
    """프로빙에 실패한 영상의 비교 불가 세션"""
    plan = ComparePlan(
        original=FrameSource(key=ORIGINAL_LABEL, path=source_path, label=ORIGINAL_LABEL),
        targets=(),
        default_key=None,
        reason=reason,
    )
    return CompareSession(
        plan=plan,
        duration_sec=0.0,
        fps=0.0,
        filename=os.path.basename(source_path),
        source_path=source_path,
    )


class FrameCompareController:
    """비교 세션을 만들고 추출을 실행하며 진입점을 미리 보기 창의 비교 모드로 모으는 컨트롤러"""

    def __init__(
        self,
        service: FrameCompareService,
        store: FrameAnalysisStore,
        *,
        history: EncodeHistory | None = None,
        service_factory: Callable[[], FrameCompareService] | None = None,
        preview: PreviewController | None = None,
    ) -> None:
        self._service: FrameCompareService = service
        self._service_factory: Callable[[], FrameCompareService] | None = service_factory
        self._history: EncodeHistory | None = history
        self._store: FrameAnalysisStore = store
        self._preview: PreviewController | None = preview

    @property
    def service(self) -> FrameCompareService:
        """지금 비교에 쓰는 서비스. 설정을 다시 읽은 뒤에는 새 인스턴스"""
        return self._service

    def open_path(
        self, source_path: str, position_sec: float = 0.0, *, auto_play: bool = False
    ) -> None:
        """영상 한 편의 비교 세션을 만들어 미리 보기 창의 비교 모드로 엶

        - auto_play는 미리 보기가 재생 중이던 상태로 넘어올 때 참
        """
        if not source_path or self._preview is None:
            return
        self._refresh_service()
        self._preview.open_compare(source_path, position_sec, auto_play=auto_play)

    def toggle(self) -> None:
        """화질 비교 전환 단축키를 미리 보기 컨트롤러에 넘김"""
        if self._preview is not None:
            self._preview.toggle_compare()

    def build_session(self, source_path: str, position_sec: float) -> CompareSession:
        """보관 원본을 먼저 프로빙하고 최근 완료 결과, 캐시된 점수로 비교 계획을 만듦

        - 원본 교체 정책이면 지금 source_path는 결과 파일이므로 보관 원본의 해상도와 크기를 기준으로 씀
        - 표시 파일명과 캐시 조회 이름은 사용자가 고른 source_path 기준
        """
        self._refresh_service()
        pair = self._service.comparable_result(source_path)
        original_path = source_path if pair is None else pair[0]
        try:
            info = self._service.probe(original_path)
        except (FrameCompareFileMissing, FrameCompareExecutionError) as error:
            return _error_session(source_path, str(error))
        result = None if pair is None else self._result_source(pair[1])
        filename = os.path.basename(source_path)
        record = self._load_record(filename, info.size_bytes, info.mtime_ns)
        candidate = None if record is None else record.candidate_tier
        scores = () if record is None else record.tier_scores
        plan = resolve_compare_plan(
            source_path=original_path,
            width=info.width,
            height=info.height,
            size_bytes=info.size_bytes,
            result=result,
            candidate_tier=candidate,
        )
        return CompareSession(
            plan=plan,
            duration_sec=info.duration_sec,
            fps=info.fps,
            filename=filename,
            position_sec=min(max(position_sec, 0.0), max(info.duration_sec, 0.0)),
            scores=scores,
            width=info.width,
            height=info.height,
            source_path=source_path,
        )

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """비교 뷰가 요청한 프레임 쌍을 지금 서비스로 추출"""
        return self._service.extract_pair(
            original,
            compared,
            time_sec=time_sec,
            frame_width=frame_width,
            frame_height=frame_height,
            cancel_event=cancel_event,
        )

    def release_decoders(self) -> None:
        """비교 뷰가 숨을 때 메모리 프레임 디코더의 파일 핸들을 놓음"""
        self._service.close()

    def _refresh_service(self) -> None:
        """설정에서 FFmpeg, FFprobe 경로를 다시 읽어 서비스를 새로 만듦"""
        if self._service_factory is not None:
            self._service.close()
            self._service = self._service_factory()

    def _result_source(self, result_path: str) -> FrameSource | None:
        """결과 파일을 프로빙해 비교 대상으로 만듦. 읽지 못하면 결과 항목을 두지 않음"""
        try:
            info = self._service.probe(result_path)
        except (FrameCompareFileMissing, FrameCompareExecutionError):
            return None
        return result_target(
            result_path, width=info.width, height=info.height, size_bytes=info.size_bytes
        )

    def _load_record(
        self, filename: str, size_bytes: int, mtime_ns: int
    ) -> FrameAnalysisRecord | None:
        return self._store.load_many([(filename, size_bytes, mtime_ns)]).get(
            (filename, size_bytes, mtime_ns)
        )

    def shutdown(self) -> None:
        """표 우클릭 함수, 열린 디코더, 저장소, 재인코딩 이력 연결을 정리"""
        set_row_compare_opener(None)
        self._service.close()
        self._store.close()
        if self._history is not None:
            self._history.close()


class FrameCompareWindowHost(Protocol):
    """비교 컨트롤러를 붙이는 메인 창의 공개 인터페이스"""

    table_view: VideoTableView
    dialogs: DialogCoordinator

    @property
    def current_report(self) -> SummaryReport | None:
        """가장 최근에 적용한 요약 리포트"""
        ...

    @property
    def classifier(self) -> ClassifierSession | None:
        """현재 분류 세션"""
        ...


def selected_source_path(window: FrameCompareWindowHost) -> str | None:
    """표에서 가장 위에 선택된 영상의 경로, 없으면 None"""
    row = window.table_view.topmost_selected_video_row()
    return None if row is None else window.table_view.video_path_at_row(row)


def attach_frame_compare(
    window: FrameCompareWindowHost, preview: PreviewController
) -> FrameCompareController:
    """메인 창에 비교 컨트롤러를 만들어 표 우클릭과 미리 보기 전환을 연결"""
    default_store = open_default_store()
    try:
        db_path = default_store.db_path
    finally:
        default_store.close()
    store = FrameAnalysisStore(db_path)
    history = EncodeHistory(db_path)

    def config_provider() -> ClassifierConfig:
        classifier = window.classifier
        return classifier.config if classifier is not None else ClassifierConfig()

    controller = FrameCompareController(
        build_compare_service(config_provider(), history),
        store,
        history=history,
        service_factory=lambda: build_compare_service(config_provider(), history),
        preview=preview,
    )

    def open_row(ev: VideoEvaluation) -> None:
        report = window.current_report
        if report is None:
            return
        controller.open_path(resolve_source_filepath(report.root_dir, ev.video.filename))

    set_row_compare_opener(open_row)
    preview.attach_compare(controller.build_session, controller)
    window.dialogs.register_shortcut_listener(preview.sync_shortcuts)
    window.table_view.destroyed.connect(controller.shutdown)
    return controller

