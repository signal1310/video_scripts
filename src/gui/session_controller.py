"""GUI 시작 경로 결정, VideoClassifier 바인딩 및 설정 세션 동기화 컨트롤러 모듈"""

import logging
import os

from src.classifier import VideoClassifier
from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_dir,
    resolve_effective_root_dir,
)
from src.core.config import ClassifierConfig, set_root_dir


def resolve_initial_directory(
    classifier: VideoClassifier | None,
    initial_dir: str | None,
    fallback_root: str = "",
) -> str:
    """초기 분류기 인스턴스, 명시 인자, 또는 .rootdir·통합 상태 순으로 유효한 시작 경로를 결정"""
    if classifier is not None:
        return classifier.root_dir
    if initial_dir and os.path.isdir(initial_dir):
        return initial_dir
    try:
        root = resolve_effective_root_dir(fallback_root)
        if root and os.path.isdir(root):
            return root
    except Exception:
        logging.getLogger(__name__).debug(".rootdir 초기 경로 조회 생략", exc_info=True)
    return ""


def load_initial_state(classifier: VideoClassifier | None) -> AppState:
    """통합 설정 파일(data/config.json)을 로드하고, 기존 분류기 보유 시 해당 설정·경로·예외 규칙을 동기화"""
    state = load_app_state()
    if classifier is not None:
        state.apply_classifier_config(classifier.config)
        state.paths.root_dir = classifier.root_dir
        state.paths.recent_dirs = record_recent_dir(
            state.paths.recent_dirs, classifier.root_dir
        )
        if classifier.exception_tokens:
            state.ui.exception_tokens = list(classifier.exception_tokens)
    return state


def bind_classifier_directory(
    current: VideoClassifier | None,
    target_dir: str,
    *,
    persist_rootdir: bool = False,
    config: ClassifierConfig | None = None,
    exception_tokens: list[str] | None = None,
) -> tuple[VideoClassifier, bool]:
    """지정 경로에 맞는 VideoClassifier를 생성하거나 루트 경로를 전환하고 변경 여부를 반환"""
    norm_dir = os.path.normpath(target_dir)
    if persist_rootdir:
        try:
            set_root_dir(norm_dir)
        except Exception:
            logging.getLogger(__name__).warning(".rootdir 경로 저장 실패", exc_info=True)

    if current is None:
        clf = VideoClassifier(root_dir=norm_dir, config=config)
        if exception_tokens is not None:
            clf.set_exception_tokens(exception_tokens)
        clf.pseudo_classify_mode(True)
        return clf, True

    changed = os.path.abspath(current.root_dir) != os.path.abspath(norm_dir)
    if changed:
        current.set_root_dir(norm_dir)
    return current, changed


def apply_settings_to_classifier(
    current: VideoClassifier | None,
    fallback_path: str,
    new_cfg: ClassifierConfig,
    new_root: str,
    *,
    exception_tokens: list[str] | None = None,
) -> tuple[VideoClassifier | None, bool, str | None]:
    """환경설정 변경 사항(ClassifierConfig 및 작업 경로)을 분류기 세션에 반영

    (갱신된 분류기, 경로 변경 여부, 새 경로 문자열 또는 None) 반환
    """
    prev_root = current.root_dir if current is not None else fallback_path.strip()
    valid_new_root = new_root if (new_root and os.path.isdir(new_root)) else None
    root_changed = bool(
        valid_new_root
        and (not prev_root or os.path.abspath(prev_root) != os.path.abspath(valid_new_root))
    )

    updated = current
    if valid_new_root is not None and updated is not None:
        updated.set_root_dir(valid_new_root)

    if updated is None and valid_new_root is not None:
        updated = VideoClassifier(root_dir=valid_new_root, config=new_cfg)
        if exception_tokens is not None:
            updated.set_exception_tokens(exception_tokens)
        updated.pseudo_classify_mode(True)
    elif updated is not None:
        updated.set_config(new_cfg)

    return updated, root_changed, valid_new_root
