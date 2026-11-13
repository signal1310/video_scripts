import logging
import os

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig, get_root_dir, set_root_dir


def resolve_initial_directory(
    classifier: VideoClassifier | None, initial_dir: str | None
) -> str:
    """초기 분류기 인스턴스, 명시 인자, 또는 .rootdir 파일 순으로 유효한 시작 경로를 결정."""
    if classifier is not None:
        return classifier.root_dir
    if initial_dir and os.path.isdir(initial_dir):
        return initial_dir
    try:
        root = get_root_dir()
        if root and os.path.isdir(root):
            return root
    except Exception:
        logging.getLogger(__name__).debug(".rootdir 초기 경로 조회 생략", exc_info=True)
    return ""


def load_initial_config(classifier: VideoClassifier | None) -> ClassifierConfig | None:
    """분류기 보유 시 해당 설정을, 미보유 시 .env 설정을 안전하게 로드."""
    if classifier is not None:
        return classifier.config
    try:
        return ClassifierConfig.from_env(reload_dotenv=False)
    except Exception:
        logging.getLogger(__name__).debug("초기 .env 설정 로드 생략", exc_info=True)
        return None


def bind_classifier_directory(
    current: VideoClassifier | None,
    target_dir: str,
    *,
    persist_rootdir: bool = False,
) -> tuple[VideoClassifier, bool]:
    """
    - 지정 경로에 맞는 VideoClassifier를 생성하거나 루트 경로를 전환하고 변경 여부를 반환.
    """
    norm_dir = os.path.normpath(target_dir)
    if persist_rootdir:
        try:
            set_root_dir(norm_dir)
        except Exception:
            logging.getLogger(__name__).warning(".rootdir 경로 저장 실패", exc_info=True)

    if current is None:
        clf = VideoClassifier(root_dir=norm_dir)
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
) -> tuple[VideoClassifier | None, bool, str | None]:
    """
    - 환경설정 변경 사항(ClassifierConfig 및 작업 경로)을 분류기 세션에 반영.
    - (갱신된 분류기, 경로 변경 여부, 새 경로 문자열 또는 None)을 반환.
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
        updated.pseudo_classify_mode(True)
    elif updated is not None:
        updated.set_config(new_cfg)

    return updated, root_changed, valid_new_root
