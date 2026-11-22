"""화질 비교 창의 비교 계획 구성, 단일 창 재사용과 세 진입점 연결 컨트롤러

- 원본 프로빙, 최근 완료 결과, 캐시된 화질 분석 점수를 모아 창에 넘김
- 표 행 우클릭 메뉴와 미리 보기 창의 요청을 같은 창으로 모음
- 창이 닫혀도 인스턴스를 남겨 다음 요청에서 새 대상으로 다시 씀
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QRect

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
from src.gui.dialogs.frame_compare_window import CompareSession, FrameCompareWindow
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
    """화질 비교 창을 하나만 유지하며 요청마다 새 대상으로 전환하는 컨트롤러

    - 추출기로 창에 자신을 넘겨, 설정이 바뀌어 서비스를 다시 만들어도 열린 창이 새 서비스를 씀
    """

    def __init__(
        self,
        service: FrameCompareService,
        store: FrameAnalysisStore,
        seek_seconds: Callable[[], int],
        *,
        history: EncodeHistory | None = None,
        service_factory: Callable[[], FrameCompareService] | None = None,
        preview: PreviewController | None = None,
        selected_path: Callable[[], str | None] | None = None,
    ) -> None:
        self._service: FrameCompareService = service
        self._service_factory: Callable[[], FrameCompareService] | None = service_factory
        self._history: EncodeHistory | None = history
        self._store: FrameAnalysisStore = store
        self._seek_seconds = seek_seconds
        self._window: FrameCompareWindow | None = None
        self._preview: PreviewController | None = preview
        self._selected_path = selected_path

    @property
    def window(self) -> FrameCompareWindow | None:
        """지금까지 만든 비교 창 인스턴스, 없으면 None"""
        return self._window

    @property
    def service(self) -> FrameCompareService:
        """지금 비교에 쓰는 서비스. 설정을 다시 읽은 뒤에는 새 인스턴스"""
        return self._service

    def open_path(
        self, source_path: str, position_sec: float = 0.0, *, geometry: QRect | None = None
    ) -> None:
        """영상 한 편의 화질 비교 창을 열거나, 이미 열려 있으면 새 대상으로 바꿔 앞으로 가져옴

        - geometry가 있으면 그 영역으로 옮겨 미리 보기 창과 같은 자리에 이어서 띄움
        """
        if not source_path:
            return
        self._refresh_service()
        window = self._ensure_window()
        window.load_session(self._build_session(source_path, position_sec))
        if geometry is not None:
            window.setGeometry(geometry)
        window.show()
        window.raise_()
        window.activateWindow()

    def open_from_preview(self, source_path: str, position_sec: float) -> None:
        """내장 미리 보기 창의 현재 시점과 창 영역을 넘겨받아 비교 창으로 바꾸고 미리 보기 창을 숨김"""
        if not source_path:
            return
        window = None if self._preview is None else self._preview.builtin_window
        geometry = None if window is None else window.geometry()
        self.open_path(source_path, position_sec, geometry=geometry)
        if self._preview is not None:
            self._preview.hide_builtin()

    def toggle(self) -> None:
        """화질 비교 전환: 보이는 창에 따라 미리 보기와 비교 창을 오가고, 둘 다 없으면 선택 영상을 엶"""
        preview_window = None if self._preview is None else self._preview.builtin_window
        if preview_window is not None and preview_window.isVisible():
            self.open_from_preview(preview_window.current_path, preview_window.position_sec)
            return
        if self._window is not None and self._window.isVisible():
            self._switch_to_preview()
            return
        path = None if self._selected_path is None else self._selected_path()
        if path:
            self.open_path(path)

    def _switch_to_preview(self) -> None:
        """비교 창의 현재 시점과 창 영역을 그대로 미리 보기로 넘기고 비교 창을 숨김"""
        window = self._window
        if window is None or self._preview is None:
            return
        path = window.source_path
        position_sec = window.position_sec
        geometry = window.geometry()
        window.hide()
        if path:
            self._preview.preview_path_at(path, position_sec, geometry)

    def _refresh_service(self) -> None:
        """설정에서 FFmpeg, FFprobe 경로를 다시 읽어 서비스를 새로 만듦"""
        if self._service_factory is not None:
            self._service = self._service_factory()

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
        """창이 요청한 프레임 쌍을 지금 서비스로 추출"""
        return self._service.extract_pair(
            original,
            compared,
            time_sec=time_sec,
            frame_width=frame_width,
            frame_height=frame_height,
            cancel_event=cancel_event,
        )

    def _ensure_window(self) -> FrameCompareWindow:
        if self._window is None:
            self._window = FrameCompareWindow(self, self._seek_seconds, None)
            self._window.preview_requested.connect(self._on_preview_requested)
        return self._window

    def _on_preview_requested(self, source_path: str, position_sec: float) -> None:
        """비교 창의 Shift+Space 요청으로 비교 창을 숨기고 미리 보기로 전환"""
        del source_path, position_sec
        self._switch_to_preview()

    def _build_session(self, source_path: str, position_sec: float) -> CompareSession:
        """보관 원본을 먼저 프로빙하고 최근 완료 결과, 캐시된 점수로 비교 계획을 만듦

        - 원본 교체 정책이면 지금 source_path는 결과 파일이므로 보관 원본의 해상도와 크기를 기준으로 씀
        - 표시 파일명과 캐시 조회 이름은 사용자가 고른 source_path 기준
        """
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

    def _result_source(self, result_path: str) -> FrameSource | None:
        """결과 파일을 프로빙해 비교 대상으로 만듦. 읽지 못하면 결과 항목을 두지 않음"""
        try:
            info = self._service.probe(result_path)
        except (FrameCompareFileMissing, FrameCompareExecutionError):
            return None
        return result_target(
            result_path, width=info.width, height=info.height, size_bytes=info.size_bytes
        )

    def _load_record(self, filename: str, size_bytes: int, mtime_ns: int) -> FrameAnalysisRecord | None:
        return self._store.load_many([(filename, size_bytes, mtime_ns)]).get(
            (filename, size_bytes, mtime_ns)
        )

    def shutdown(self) -> None:
        """창, 저장소, 재인코딩 이력 연결과 표 우클릭 함수를 정리"""
        if self._window is not None:
            self._window.shutdown()
            self._window.close()
            self._window = None
        set_row_compare_opener(None)
        self._store.close()
        if self._history is not None:
            self._history.close()


class FrameCompareWindowHost(Protocol):
    """비교 컨트롤러를 붙이는 메인 창의 공개 인터페이스"""

    table_view: VideoTableView

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


def attach_frame_compare(window: FrameCompareWindowHost, preview: PreviewController) -> FrameCompareController:
    """메인 창에 비교 컨트롤러를 만들어 표 우클릭과 미리 보기 버튼 요청을 연결"""
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
        lambda: preview.seek_seconds,
        history=history,
        service_factory=lambda: build_compare_service(config_provider(), history),
        preview=preview,
        selected_path=lambda: selected_source_path(window),
    )

    def open_row(ev: VideoEvaluation) -> None:
        report = window.current_report
        if report is None:
            return
        controller.open_path(resolve_source_filepath(report.root_dir, ev.video.filename))

    set_row_compare_opener(open_row)
    preview.set_compare_handler(controller.open_from_preview)
    window.table_view.destroyed.connect(controller.shutdown)
    return controller
