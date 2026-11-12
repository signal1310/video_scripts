from typing import Any, Callable, Dict, List, Optional, Tuple
from src.core.config import ClassifierConfig, get_root_dir
from src.core.models import SummaryReport, VideoProps
from src.service.classifier_service import VideoClassifierService
from src.ui.cui_printer import CUIPrinter


class VideoClassifier:
    """
    - Jupyter Notebook(classify.ipynb) 및 스크립트 실행을 위한 고수준 파사드(Facade) 클래스.
    - 내부적으로 3계층 클린 아키텍처(VideoClassifierService, VideoEvaluator, CUIPrinter)를 구동.
    """

    def __init__(
        self,
        root_dir: Optional[str] = None,
        config: Optional[ClassifierConfig] = None,
        over_bitrate_ratio: Optional[float] = None,
        include_keyframe_interval: bool = False,
    ) -> None:
        target_root = root_dir if root_dir is not None else get_root_dir()
        cfg = config or ClassifierConfig.from_env(reload_dotenv=True)
        if over_bitrate_ratio is not None:
            cfg.over_bitrate_ratio = over_bitrate_ratio

        self._service = VideoClassifierService(root_dir=target_root, config=cfg)
        self._service.include_keyframe = include_keyframe_interval
        self._filename_maxlen: int = cfg.tabulate_filename_maxlen

    @property
    def config(self) -> ClassifierConfig:
        return self._service.config

    @property
    def root_dir(self) -> str:
        return self._service.root_dir

    @property
    def video_props(self) -> List[VideoProps]:
        """
        - 현재 작업 대상 폴더 내 비디오 파일들의 메타데이터 목록을 반환.
        """
        return self._service.prepare_cache().data

    @property
    def exception_rules(self) -> List[Callable[[VideoProps], bool]]:
        return self._service.exception_rules

    def set_filename_max_length(self, max_length: int) -> None:
        """
        - 콘솔 테이블 출력 시 파일명 열의 최대 표시 너비를 설정.
        """
        self._filename_maxlen = max_length
        self._service.config.tabulate_filename_maxlen = max_length

    def set_over_bitrate_ratio(self, ratio: float) -> None:
        """
        - 과화질 판별 경계 배수(기본값 1.2 -> 720p H.264 기준 1800kbps)를 런타임에 동적으로 변경.
        """
        self._service.config.over_bitrate_ratio = ratio

    def set_low_bitrate_ratio(self, ratio: float) -> None:
        """
        - 저화질 판별 경계 배수(기본값 0.8 -> 720p H.264 기준 1200kbps)를 런타임에 동적으로 변경.
        """
        self._service.config.low_bitrate_ratio = ratio

    def set_keyframe_threshold(self, seconds: float) -> None:
        """
        - 허용 가능한 키프레임 간격의 상한선(기본값 2.2초)을 런타임에 동적으로 변경.
        """
        self._service.config.keyframe_threshold = seconds

    def set_small_footage_mb(self, size_mb: float) -> None:
        """
        - 소형 영상(Footage)으로 분류할 기준 용량(기본값 30.0MB)을 런타임에 동적으로 변경.
        """
        self._service.config.small_footage_mb = size_mb

    def include_keyframe_interval(self, flag: bool = True) -> None:
        """
        - 단순 조회(print) 시 키프레임 실측 포함 여부를 지정.
        - classify() 실행 시에는 키프레임 분석이 자동으로 활성화되므로 별도 호출 불필요.
        """
        self._service.include_keyframe = flag
        if flag and self._service.cache is not None:
            self._service.prepare_cache(force_keyframe=True)

    def pseudo_classify_mode(self, flag: bool = True) -> None:
        """
        - 실제 디스크 파일 이동 없이 메모리 상에서 가분류 결과만 확인하는 모드를 활성화.
        """
        self._service.pseudo_classify = flag

    def add_exception_rule(self, pred: Callable[[VideoProps], bool]) -> None:
        """
        - 분류에서 제외하고 원본 보존할 사용자 정의 조건식(람다 함수)을 등록.
        """
        self._service.exception_rules.append(pred)

    def clear_exception_rules(self) -> None:
        """
        - 등록되어 있는 모든 예외 조건식을 초기화하여 기본 분류 상태로 복귀.
        """
        self._service.exception_rules.clear()

    def reset_exceptions(self) -> None:
        """
        - clear_exception_rules()의 직관적인 별칭 메서드.
        """
        self.clear_exception_rules()

    def get_summary_report(self, ensure_keyframe: bool = False) -> SummaryReport:
        """
        - 현재 작업 폴더에 대한 구조화된 종합 분석 리포트 객체(SummaryReport)를 반환.
        """
        return self._service.evaluate_all(ensure_keyframe=ensure_keyframe)

    def classify(self) -> SummaryReport:
        """
        - 퀄리티(CQ 22), 1초 키프레임 보장, 소형 영상 분포 분석을 반영하여 영상을 목적 폴더로 분류.
        """
        return self._service.classify()

    def print(
        self,
        *,
        sanitize_emoji: bool = True,
        sort_key: Callable[[Dict[str, Any]], Tuple | list] | None = None,
    ) -> None:
        """
        - 분류 그룹별 테이블과 3단 용량 시뮬레이션 요약 리포트를 서식에 맞추어 콘솔에 출력.
        """
        cache = self._service.prepare_cache(force_keyframe=self._service.include_keyframe)
        report = self._service.evaluate_all(ensure_keyframe=self._service.include_keyframe)

        CUIPrinter.print_report(
            cache=cache,
            report=report,
            config=self._service.config,
            sort_key=sort_key,
            filename_maxlen=self._filename_maxlen,
            sanitize_emoji=sanitize_emoji,
        )

    def unclassify_files(self, *, unmark_pseudo_classified_only: bool = False) -> None:
        """
        - 하위 분류 폴더의 파일들을 다시 루트 경로로 안전하게 복원하고 빈 폴더를 정리.
        """
        moved_count, warnings = self._service.unclassify_files(
            unmark_pseudo_classified_only=unmark_pseudo_classified_only
        )
        for w in warnings:
            print(w)
        if unmark_pseudo_classified_only:
            print("[INFO] 모의 분류(가분류) 표시 초기화 완료.")
        else:
            print(f"[INFO] 분류 해제 완료: 총 {moved_count}개 파일이 루트 작업 경로로 복구됨.")
