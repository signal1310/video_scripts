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

from PyQt6.QtWidgets import QWidget

from src.core.analysis.frame_analysis import (
    ActiveRegion,
    FrameAnalysisThresholds,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    frame_thresholds_from_config,
    is_usable_analysis_version,
    rejudge_candidate_tier,
    resolve_crop_frame,
)
from src.core.analysis.frame_compare import (
    ORIGINAL_LABEL,
    ComparePlan,
    FrameSource,
    is_same_file_path,
    resolve_compare_plan,
    result_target,
)
from src.core.config import ClassifierConfig
from src.core.models import ActiveFrame, SummaryReport, VideoEvaluation
from src.core.quality_probe import METRIC_VMAF
from src.core.workspace_paths import resolve_source_filepath
from src.gui.controllers.dialog_coordinator import DialogCoordinator
from src.gui.controllers.manual_target_controller import ManualTargetController
from src.gui.dialogs.frame_compare_meta import CompareSession
from src.gui.preview.preview_controller import PreviewController
from src.gui.table.table_context_menu import set_row_compare_opener
from src.gui.table.video_table_view import VideoTableView
from src.service.analysis_store import open_default_store
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import job_from_json
from src.service.frame_analysis_record import FrameAnalysisRecord
from src.service.frame_analysis_service import crop_review_status
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.frame_compare_service import (
    WORK_DIR_NAME,
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
    FrameCompareService,
)
from src.service.frame_override_rows import review_region_box
from src.service.session import ClassifierSession


def build_compare_service(config: ClassifierConfig, history: EncodeHistory) -> FrameCompareService:
    """설정의 FFmpeg, FFprobe 경로와 축소 보간 방식, 임시 작업 폴더로 서비스 생성"""
    return FrameCompareService(
        ffmpeg_executable=config.execution.ffmpeg_path.strip() or "ffmpeg",
        ffprobe_executable=config.execution.ffprobe_path.strip() or "ffprobe",
        work_dir=os.path.join(tempfile.gettempdir(), WORK_DIR_NAME),
        history=history,
        scale_algorithm=config.encode.scale_algorithm,
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


def crop_review_hold(
    store: FrameAnalysisStore,
    source_path: str,
    region: ActiveRegion,
    *,
    identity_override: tuple[int, int] | None,
    crop_enabled: bool,
    is_vr_protected: bool,
    excluded: bool,
) -> bool:
    """서비스와 같은 확인 표시를 읽어 화질 비교의 자동 크롭 보류 여부를 판정

    - 표와 인코딩이 쓰는 `crop_review_status`를 그대로 불러 같은 기준을 씀
    - 사용자 지정 활성 화면 판정은 호출자가 `resolve_crop_frame`에 넘기므로 여기서는 확인 표시만 읽음
    """
    overrides = None if identity_override is None else {source_path: identity_override}
    acknowledged = store.load_crop_review_acknowledged(
        {source_path: review_region_box(region)}, identity_overrides=overrides
    )
    return crop_review_status(
        region,
        crop_enabled=crop_enabled,
        is_vr_protected=is_vr_protected,
        acknowledged=source_path in acknowledged,
        excluded=excluded,
    ).hold_applies


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
        config_provider: Callable[[], ClassifierConfig] | None = None,
        report_provider: Callable[[], SummaryReport | None] | None = None,
    ) -> None:
        self._service: FrameCompareService = service
        self._service_factory: Callable[[], FrameCompareService] | None = service_factory
        self._history: EncodeHistory | None = history
        self._store: FrameAnalysisStore = store
        self._preview: PreviewController | None = preview
        self._config_provider: Callable[[], ClassifierConfig] | None = config_provider
        self._report_provider: Callable[[], SummaryReport | None] | None = report_provider

    @property
    def service(self) -> FrameCompareService:
        """지금 비교에 쓰는 서비스. 설정을 다시 읽은 뒤에는 새 인스턴스"""
        return self._service

    def open_path(
        self,
        source: str,
        position_sec: float | None = None,
        *,
        auto_play: bool = False,
        owner: QWidget | None = None,
    ) -> None:
        """영상 한 편의 비교 세션을 만들어 미리 보기 창의 비교 모드로 엶

        - position_sec이 없으면 미리 보기 창이 이번 실행에서 기억한 시점부터 엶
        - auto_play는 미리 보기가 재생 중이던 상태로 넘어올 때 참
        - owner는 비교를 요청한 창. 넘기지 않으면 미리 보기 창이 메인 창을 소유자로 되돌림
        """
        if not source or self._preview is None:
            return
        self._refresh_service()
        self._preview.open_compare(source, position_sec, auto_play=auto_play, owner=owner)

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
        config = self._config()
        thresholds = frame_thresholds_from_config(config)
        candidate = None if record is None else self._rejudged_candidate(record, thresholds)
        scores = () if record is None else record.tier_scores
        # - 원본 교체 모드에서 source_path는 결과 파일이라, 영상별 지정은 결과 파일이 아니라 지금
        #   화면에 띄우는 보관 원본의 식별 값과 맞춰 봄
        rendered_identity = (
            None
            if pair is None or is_same_file_path(pair[0], source_path)
            else (info.size_bytes, info.mtime_ns)
        )
        active = self._crop_frame(
            source_path,
            info.width,
            info.height,
            record,
            identity_override=rendered_identity,
            config=config,
            thresholds=thresholds,
        )
        result_crop = None if result is None else self._result_crop(source_path)
        plan = resolve_compare_plan(
            source_path=original_path,
            width=info.width,
            height=info.height,
            size_bytes=info.size_bytes,
            result=result,
            candidate_tier=candidate,
            active_frame=active,
            result_crop=result_crop,
        )
        return CompareSession(
            plan=plan,
            duration_sec=info.duration_sec,
            fps=info.fps,
            filename=filename,
            position_sec=min(max(position_sec, 0.0), max(info.duration_sec, 0.0)),
            scores=scores,
            width=info.width if active is None else active.width,
            height=info.height if active is None else active.height,
            source_path=source_path,
            active_frame=active,
            scale_algorithm=config.encode.scale_algorithm,
            evaluation=self._find_evaluation(filename),
        )

    def _find_evaluation(self, filename: str) -> VideoEvaluation | None:
        """현재 표 리포트에서 파일명이 같은 판정 결과를 찾음. 없으면 None"""
        report = None if self._report_provider is None else self._report_provider()
        if report is None:
            return None
        return next(
            (ev for ev in report.evaluations if ev.video.filename == filename), None
        )

    def _config(self) -> ClassifierConfig:
        """주입된 설정 제공자나 기본 설정"""
        return self._config_provider() if self._config_provider is not None else ClassifierConfig()

    def _rejudged_candidate(
        self, record: FrameAnalysisRecord, thresholds: FrameAnalysisThresholds
    ) -> str | None:
        """저장된 규격 점수로 현재 SSIM/VMAF 기준의 축소 후보를 다시 판정

        - 표와 인코딩 경로가 `rejudge_candidate_tier`로 다시 판정하므로 비교 창도 같은 기준을 씀
        - 저장 점수가 없으면 저장 후보를 그대로 씀
        """
        if not record.tier_scores:
            return record.candidate_tier
        return rejudge_candidate_tier(
            record.tier_scores,
            record.candidate_tier,
            use_vmaf=record.metric_used == METRIC_VMAF,
            thresholds=thresholds,
        )

    def _crop_frame(
        self,
        source_path: str,
        width: int,
        height: int,
        record: FrameAnalysisRecord | None,
        *,
        identity_override: tuple[int, int] | None,
        config: ClassifierConfig,
        thresholds: FrameAnalysisThresholds,
    ) -> ActiveFrame | None:
        """설정과 영상별 제외, 사용자 지정 활성 화면을 확인해 잘라내기 확정 활성 화면 반환. 보류면 None

        - 저장된 활성 화면은 현재 검은 여백 무시 면적 상한으로 다시 판정해 표와 기준을 맞춤
        - 원본 교체 모드에서 source_path는 결과 파일이고 화면에 띄우는 원본은 보관 파일이라,
          영상별 지정은 저장 경로의 결과 파일이 아니라 보관 원본의 식별 값과 맞춰 봄
        - 식별 값이 보관 원본과 맞지 않는 행은 결과 파일용 지정일 수 있어 지우지 않고 무시하며,
          결과 파일 좌표로 다시 그린 사각형도 이 비교에서 걸림
        """
        if record is None:
            return None
        overrides = (
            None if identity_override is None else {source_path: identity_override}
        )
        excluded_paths = self._store.load_crop_exclusions(
            [source_path], identity_overrides=overrides
        )
        manual = self._store.load_manual_active_frames(
            {source_path: (width, height)}, identity_overrides=overrides
        )
        region = apply_no_bars_ratio(record.active_region, width, height, thresholds=thresholds)
        excluded = source_path in excluded_paths
        review_hold = crop_review_hold(
            self._store,
            source_path,
            region,
            identity_override=identity_override,
            crop_enabled=config.encode.crop_black_bars,
            is_vr_protected=record.is_vr_protected,
            excluded=excluded,
        )
        frame = resolve_crop_frame(
            region,
            crop_enabled=config.encode.crop_black_bars,
            excluded=excluded or review_hold,
            is_vr_protected=record.is_vr_protected,
            manual_frame=manual.get(source_path),
        )
        if frame is None or (frame.width >= width and frame.height >= height):
            return None
        return frame

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

    def _result_crop(self, source_path: str) -> ActiveFrame | None:
        """최근 완료 결과를 만든 계획의 crop 필터에서 원본에 적용할 활성 화면 복원

        - crop_filter가 없는 과거 결과는 None(원본도 자르지 않음)
        """
        if self._history is None:
            return None
        row = self._history.latest_item_for_source(source_path)
        if row is None:
            return None
        crop_filter = job_from_json(row.plan_json).crop_filter
        return None if crop_filter is None else ActiveFrame.from_crop_filter(crop_filter)

    def _load_record(
        self, filename: str, size_bytes: int, mtime_ns: int
    ) -> FrameAnalysisRecord | None:
        """캐시 행을 읽되 지금 분석 방식의 버전과 다르면 무시

        - 분석 방식이 바뀐 뒤 남은 캐시는 후보와 활성 화면이 지금 판정과 어긋남
        """
        record = self._store.load_many([(filename, size_bytes, mtime_ns)]).get(
            (filename, size_bytes, mtime_ns)
        )
        if record is None:
            return None
        provider = self._config_provider
        config = ClassifierConfig() if provider is None else provider()
        if not is_usable_analysis_version(
            record.analysis_version,
            analysis_version_for_mode(
                config.encode.frame_analysis_mode, config.encode.scale_algorithm
            ),
        ):
            return None
        return record

    def shutdown(self) -> None:
        """표 우클릭 함수, 열린 디코더, 저장소, 인코딩 이력 연결을 정리"""
        set_row_compare_opener(None)
        self._service.close()
        self._store.close()
        if self._history is not None:
            self._history.close()


class FrameCompareWindowHost(Protocol):
    """비교 컨트롤러를 붙이는 메인 창의 공개 인터페이스"""

    table_view: VideoTableView
    dialogs: DialogCoordinator
    manual_targets: ManualTargetController

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
        config_provider=config_provider,
        report_provider=lambda: window.current_report,
    )

    def open_row(ev: VideoEvaluation) -> None:
        report = window.current_report
        if report is None:
            return
        controller.open_path(resolve_source_filepath(report.root_dir, ev.video.filename))

    set_row_compare_opener(open_row)
    preview.attach_compare(
        controller.build_session,
        controller,
        apply_manual_target=lambda ev, value: window.manual_targets.request([ev], value),
    )
    window.dialogs.register_shortcut_listener(preview.sync_shortcuts)
    window.table_view.destroyed.connect(controller.shutdown)
    return controller

