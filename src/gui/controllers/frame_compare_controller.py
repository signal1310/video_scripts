"""화질 비교 창의 비교 계획 구성, 단일 창 재사용과 세 진입점 연결 컨트롤러

- 원본 프로빙, 최근 완료 결과, 캐시된 화질 분석 점수를 모아 창에 넘김
- 표 행 우클릭 메뉴와 미리 보기 창의 요청을 같은 창으로 모음
- 창이 닫혀도 인스턴스를 남겨 다음 요청에서 새 대상으로 다시 씀
"""

from __future__ import annotations

import os
import tempfile
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
from src.gui.dialogs.frame_compare_window import CompareSession, FrameCompareWindow
from src.gui.preview.preview_controller import PreviewController
from src.gui.table.table_context_menu import set_row_compare_opener
from src.gui.table.video_table_view import VideoTableView
from src.service.analysis_store import open_default_store
from src.service.encode.encode_history import EncodeHistory
from src.service.frame_analysis_service import FrameAnalysisRecord, FrameAnalysisStore
from src.service.frame_compare_service import (
    WORK_DIR_NAME,
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
    return CompareSession(plan=plan, duration_sec=0.0, fps=0.0, filename=os.path.basename(source_path))


def _with_original(plan: ComparePlan, original_path: str) -> ComparePlan:
    """원본 교체 정책에서 비교 기준을 보관 파일로 바꾼 계획"""
    if original_path == plan.original.path:
        return plan
    original = FrameSource(
        key=plan.original.key,
        path=original_path,
        label=plan.original.label,
        detail=plan.original.detail,
    )
    return ComparePlan(
        original=original, targets=plan.targets, default_key=plan.default_key, reason=plan.reason
    )


class FrameCompareController:
    """화질 비교 창을 하나만 유지하며 요청마다 새 대상으로 전환하는 컨트롤러"""

    def __init__(
        self,
        service: FrameCompareService,
        store: FrameAnalysisStore,
        seek_seconds: Callable[[], int],
    ) -> None:
        self._service: FrameCompareService = service
        self._store: FrameAnalysisStore = store
        self._seek_seconds = seek_seconds
        self._window: FrameCompareWindow | None = None

    @property
    def window(self) -> FrameCompareWindow | None:
        """지금까지 만든 비교 창 인스턴스, 없으면 None"""
        return self._window

    def open_path(self, source_path: str, position_sec: float = 0.0) -> None:
        """영상 한 편의 화질 비교 창을 열거나, 이미 열려 있으면 새 대상으로 바꿔 앞으로 가져옴"""
        if not source_path:
            return
        window = self._ensure_window()
        window.load_session(self._build_session(source_path, position_sec))
        window.show()
        window.raise_()
        window.activateWindow()

    def _ensure_window(self) -> FrameCompareWindow:
        if self._window is None:
            self._window = FrameCompareWindow(self._service, self._seek_seconds, None)
        return self._window

    def _build_session(self, source_path: str, position_sec: float) -> CompareSession:
        """프로빙, 최근 완료 결과, 캐시된 점수로 비교 계획을 만듦"""
        try:
            info = self._service.probe(source_path)
        except (FrameCompareFileMissing, FrameCompareExecutionError) as error:
            return _error_session(source_path, str(error))
        pair = self._service.comparable_result(source_path)
        result = None if pair is None else self._result_source(pair[1])
        record = self._load_record(info.filename, info.size_bytes, info.mtime_ns)
        candidate = None if record is None else record.candidate_tier
        scores = () if record is None else record.tier_scores
        plan = resolve_compare_plan(
            source_path=source_path,
            width=info.width,
            height=info.height,
            size_bytes=info.size_bytes,
            result=result,
            candidate_tier=candidate,
        )
        if pair is not None:
            plan = _with_original(plan, pair[0])
        return CompareSession(
            plan=plan,
            duration_sec=info.duration_sec,
            fps=info.fps,
            filename=info.filename,
            position_sec=min(max(position_sec, 0.0), max(info.duration_sec, 0.0)),
            scores=scores,
            width=info.width,
            height=info.height,
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
        """창과 저장소 연결을 닫음"""
        if self._window is not None:
            self._window.shutdown()
            self._window.close()
            self._window = None
        self._store.close()


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
    )

    def open_row(ev: VideoEvaluation) -> None:
        report = window.current_report
        if report is None:
            return
        controller.open_path(resolve_source_filepath(report.root_dir, ev.video.filename))

    set_row_compare_opener(open_row)
    preview.set_compare_handler(controller.open_path)
    window.table_view.destroyed.connect(controller.shutdown)
    return controller
