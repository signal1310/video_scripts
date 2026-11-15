"""GUI 시작 경로 결정, 분류 세션 바인딩 및 설정 세션 동기화 컨트롤러 모듈"""

import logging
import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QMainWindow, QMessageBox, QWidget

from src.core import app_state as app_state_mod
from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_dir,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import (
    ClassifierConfig,
    EfficiencyConfig,
    ThresholdsConfig,
    merge_tuner_criteria,
)
from src.core.exception_rules import exclude_exception_tokens, merge_exception_tokens
from src.core.models import SummaryReport, VideoEvaluation
from src.core.paths import set_root_dir
from src.core.sorting import natural_sort_key
from src.gui.window_layout import export_window_ui_state
from src.service.session import ClassifierSession

if TYPE_CHECKING:
    from src.gui.dialog_coordinator import DialogCoordinator
    from src.gui.menu_bar import MainMenuController
    from src.gui.table_model import VideoTableModel
    from src.gui.theme import ThemeManager
    from src.gui.widgets import FilterTuningBar, HeaderBar, VideoTableView


def resolve_initial_directory(
    classifier: ClassifierSession | None,
    initial_dir: str | None,
) -> str:
    """초기 분류기 인스턴스, 명시 인자, 또는 data/rootdir.txt 순으로 유효한 시작 경로를 결정"""
    if classifier is not None:
        return classifier.root_dir
    if initial_dir and os.path.isdir(initial_dir):
        return initial_dir
    try:
        root = resolve_effective_root_dir()
        if root and os.path.isdir(root):
            return root
    except Exception:
        logging.getLogger(__name__).debug("data/rootdir.txt 초기 경로 조회 생략", exc_info=True)
    return ""


def load_initial_state(classifier: ClassifierSession | None) -> AppState:
    """통합 설정 파일(data/config.json)을 로드하고, 기존 분류기 보유 시 해당 설정, 경로, 예외 규칙을 동기화"""
    state = load_app_state()
    if classifier is not None:
        state.apply_classifier_config(classifier.config)
        state.paths.recent_dirs = record_recent_dir(
            state.paths.recent_dirs, classifier.root_dir
        )
        if classifier.exception_tokens:
            state.ui.exception_tokens = list(classifier.exception_tokens)
    return state


def _build_pseudo_classifier(
    root_dir: str,
    config: ClassifierConfig | None,
    exception_tokens: list[str] | None,
) -> ClassifierSession:
    if config is None:
        state = load_app_state()
        cfg = state.to_classifier_config()
        tokens = exception_tokens if exception_tokens is not None else state.ui.exception_tokens
    else:
        cfg = config
        tokens = exception_tokens
    clf = ClassifierSession(root_dir=root_dir, config=cfg)
    if tokens is not None:
        clf.set_exception_tokens(tokens)
    clf.pseudo_classify_mode(True)
    return clf


def bind_classifier_directory(
    current: ClassifierSession | None,
    target_dir: str,
    *,
    persist_rootdir: bool = False,
    config: ClassifierConfig | None = None,
    exception_tokens: list[str] | None = None,
) -> tuple[ClassifierSession, bool]:
    """지정 경로에 맞는 분류 세션을 생성하거나 루트 경로를 전환하고 변경 여부를 반환"""
    norm_dir = os.path.normpath(target_dir)
    if persist_rootdir:
        try:
            set_root_dir(norm_dir)
        except Exception:
            logging.getLogger(__name__).warning("data/rootdir.txt 경로 저장 실패", exc_info=True)

    if current is None:
        return _build_pseudo_classifier(norm_dir, config, exception_tokens), True

    changed = os.path.abspath(current.root_dir) != os.path.abspath(norm_dir)
    if changed:
        current.set_root_dir(norm_dir)
    return current, changed


def apply_settings_to_classifier(
    current: ClassifierSession | None,
    fallback_path: str,
    new_cfg: ClassifierConfig,
    new_root: str,
    *,
    exception_tokens: list[str] | None = None,
) -> tuple[ClassifierSession | None, bool, str | None]:
    """환경설정 변경 사항(ClassifierConfig 및 작업 경로)을 분류기 세션에 반영

    (갱신된 분류기, 경로 변경 여부, 새 경로 문자열 또는 None) 반환
    """
    prev_root = current.root_dir if current is not None else fallback_path.strip()
    stripped_new = new_root.strip()
    if not stripped_new:
        root_cleared = bool(prev_root)
        return None, root_cleared, "" if root_cleared else None

    norm_new = os.path.normpath(stripped_new)
    valid_new_root = norm_new if os.path.isdir(norm_new) else None
    root_changed = bool(
        valid_new_root
        and (not prev_root or os.path.abspath(prev_root) != os.path.abspath(valid_new_root))
    )

    updated = current
    if valid_new_root is not None and updated is not None:
        updated.set_root_dir(valid_new_root)

    if updated is None and valid_new_root is not None:
        updated = _build_pseudo_classifier(valid_new_root, new_cfg, exception_tokens)
    elif updated is not None:
        updated.set_config(new_cfg)

    return updated, root_changed, valid_new_root


def create_initial_classifier(
    classifier: ClassifierSession | None,
    resolved_dir: str,
    app_state: AppState,
) -> ClassifierSession | None:
    """초기 분류기가 없고 유효한 시작 경로가 존재하면 상태 기반 분류 세션 인스턴스 생성"""
    if classifier is not None:
        return classifier
    if not resolved_dir or not os.path.isdir(resolved_dir):
        return None
    return _build_pseudo_classifier(
        resolved_dir,
        app_state.to_classifier_config(),
        app_state.ui.exception_tokens or None,
    )


def remove_recent_dir(recent_dirs: list[str], target_path: str) -> list[str]:
    """대소문자 및 구분자를 정규화하여 지정 경로를 최근 작업 폴더 목록에서 제거"""
    norm_target = os.path.normcase(os.path.abspath(target_path))
    return [
        d for d in recent_dirs if os.path.normcase(os.path.abspath(d)) != norm_target
    ]


def prune_active_exception_tokens(
    active_tokens: list[str], disabled_tokens: list[str]
) -> list[str]:
    """활성 예외 규칙에 포함된 항목을 비활성 보관 목록에서 대소문자 무관하게 제거"""
    active_lower = {t.lower() for t in active_tokens}
    return [t for t in disabled_tokens if t.lower() not in active_lower]


def evaluate_action_availability(
    report: SummaryReport | None, root_dir: str
) -> tuple[bool, bool]:
    """리포트와 작업 경로를 점검해 (실제 분류 가능 여부, 복원 가능 여부) 쌍 반환"""
    can_classify = report is not None and any(
        ev.target_dirname is not None and ev.video.moved_dirname is None
        for ev in report.evaluations
    )
    can_unclassify = report is not None and any(
        ev.video.moved_dirname is not None for ev in report.evaluations
    )
    if not can_unclassify and root_dir and os.path.isdir(root_dir):
        try:
            with os.scandir(root_dir) as it:
                can_unclassify = any(entry.is_dir() for entry in it)
        except OSError:
            can_unclassify = False
    return can_classify, can_unclassify


class SessionCoordinator:
    """GUI 세션 상태(AppState), 분류 세션, 위젯, 메뉴 동기화를 조율"""

    def __init__(
        self,
        classifier: ClassifierSession | None,
        initial_dir: str | None,
    ) -> None:
        self.state: AppState = load_initial_state(classifier)
        self.initial_dir: str = resolve_initial_directory(classifier, initial_dir)
        self.classifier: ClassifierSession | None = create_initial_classifier(
            classifier, self.initial_dir, self.state
        )
        self.is_syncing: bool = False

    def current_root_dir(self, fallback_path: str) -> str:
        """활성 분류기의 루트 경로 또는 fallback 경로 문자열 반환"""
        if self.classifier is not None:
            return self.classifier.root_dir
        return fallback_path.strip()

    def apply_initial_theme(
        self, theme_mgr: "ThemeManager", *, has_external_classifier: bool
    ) -> None:
        """저장된 상태 파일이 있을 때 초기 테마 모드를 ThemeManager에 반영"""
        if (
            not has_external_classifier
            and os.path.isfile(app_state_mod.get_state_path())
            and self.state.ui.theme_mode != theme_mgr.mode
        ):
            theme_mgr.set_mode(self.state.ui.theme_mode)

    def persist(self, *, root_dir: str | None = None) -> None:
        """동기화 중이 아닐 때 통합 설정 상태를 안전하게 디스크에 저장"""
        if self.is_syncing:
            return
        try:
            save_app_state(self.state, root_dir=root_dir)
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)

    def refresh_recent_menu(
        self, menu: "MainMenuController", current_path: str
    ) -> None:
        """최근 작업 폴더 메뉴를 현재 AppState 기준으로 갱신"""
        menu.update_recent_dirs(self.state.paths.recent_dirs, current_path.strip())

    def sync_classifier_and_table(
        self,
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        fallback_cfg: ClassifierConfig | None = None,
    ) -> ClassifierConfig:
        """필터 바의 임계값, 예외 규칙을 분류기에 적용하고 테이블 모델 설정을 동기화"""
        if self.classifier is not None:
            self.classifier.set_exception_tokens(filter_bar.get_exception_tokens())
            cfg = self.classifier.config
        else:
            cfg = fallback_cfg or self.state.to_classifier_config()
        table_model.set_config(cfg)
        return cfg

    def sync_to_widgets(
        self,
        header_bar: "HeaderBar",
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        menu: "MainMenuController",
        theme_mode: str,
        apply_proxy_cb: object = None,
    ) -> None:
        """현재 AppState 전체를 상단, 필터, 테이블, 메뉴 위젯에 일괄 반영"""
        self.is_syncing = True
        try:
            ui = self.state.ui
            flt = ui.filters
            cfg = self.state.to_classifier_config()
            header_bar.set_scan_mode(ui.scan_mode)
            filter_bar.sync_filter_state(
                search=flt.search,
                exception_tokens=ui.exception_tokens,
            )
            filter_bar.set_disabled_exception_count(len(ui.disabled_exception_tokens))
            table_model.set_config(cfg)
            if callable(apply_proxy_cb):
                apply_proxy_cb()
            menu.sync_theme_mode(theme_mode)
            menu.sync_scan_mode(ui.scan_mode)
            self.refresh_recent_menu(menu, header_bar.path_edit.text())
        finally:
            self.is_syncing = False

    def ensure_classifier(
        self,
        host: QWidget,
        target_dir: str,
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
    ) -> ClassifierSession | None:
        """유효한 작업 폴더 기준으로 분류 세션을 준비하거나 경로 전환"""
        if not target_dir or not os.path.isdir(target_dir):
            QMessageBox.warning(
                host, "경로 확인 필요", "유효한 작업 폴더를 먼저 선택해 주세요."
            )
            return None
        was_none = self.classifier is None
        self.classifier, changed = bind_classifier_directory(
            self.classifier,
            target_dir,
            config=self.state.to_classifier_config(),
            exception_tokens=self.state.ui.exception_tokens,
        )
        if was_none or changed:
            self.sync_classifier_and_table(filter_bar, table_model)
        return self.classifier

    def switch_directory(
        self,
        directory: str,
        header_bar: "HeaderBar",
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        menu: "MainMenuController",
    ) -> bool:
        """작업 폴더를 전환하고 상태, 최근 목록을 저장한 뒤 경로 변경 여부 반환"""
        norm_dir = os.path.normpath(directory)
        prev_dir = header_bar.path_edit.text().strip()
        dir_changed = not prev_dir or os.path.abspath(prev_dir) != os.path.abspath(norm_dir)
        header_bar.path_edit.setText(norm_dir)
        self.classifier, _ = bind_classifier_directory(
            self.classifier,
            norm_dir,
            persist_rootdir=False,
            config=self.state.to_classifier_config(),
            exception_tokens=self.state.ui.exception_tokens,
        )
        self.sync_classifier_and_table(filter_bar, table_model)
        self.state.paths.recent_dirs = record_recent_dir(
            self.state.paths.recent_dirs, self.classifier.root_dir
        )
        self.persist(root_dir=self.classifier.root_dir)
        self.refresh_recent_menu(menu, self.classifier.root_dir)
        return dir_changed

    def apply_exceptions(
        self,
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
    ) -> ClassifierConfig:
        """예외 규칙 변경을 상태, 분류기, 테이블에 반영"""
        tokens = filter_bar.get_exception_tokens()
        self.state.ui.exception_tokens = list(tokens)
        self.state.ui.disabled_exception_tokens = prune_active_exception_tokens(
            tokens, self.state.ui.disabled_exception_tokens
        )
        filter_bar.set_disabled_exception_count(
            len(self.state.ui.disabled_exception_tokens)
        )
        effective_cfg = self.sync_classifier_and_table(filter_bar, table_model)
        self.persist()
        return effective_cfg

    def apply_criteria(
        self,
        thresholds: ThresholdsConfig,
        efficiency: EfficiencyConfig,
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        dialogs: "DialogCoordinator",
    ) -> ClassifierConfig:
        """판별 기준 조정 창에서 변경된 임계값, 효율 기준을 세션, 분류기, UI에 반영"""
        new_cfg = merge_tuner_criteria(
            self.state.to_classifier_config(), thresholds, efficiency
        )
        self.state.apply_classifier_config(new_cfg)
        if self.classifier is not None:
            self.classifier.set_config(new_cfg)
        table_model.set_config(new_cfg)
        dialogs.sync_criteria(new_cfg, sync_tuner=False)
        self.persist()
        return new_cfg

    def apply_settings(
        self,
        new_cfg: ClassifierConfig,
        new_root: str,
        header_bar: "HeaderBar",
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        menu: "MainMenuController",
        theme_mode: str,
    ) -> bool:
        """설정 다이얼로그 확정 값을 분류기, 상태, 위젯에 반영하고 경로 변경 여부 반환"""
        self.classifier, root_changed, valid_new_root = apply_settings_to_classifier(
            self.classifier,
            header_bar.path_edit.text(),
            new_cfg,
            new_root,
            exception_tokens=self.state.ui.exception_tokens,
        )
        if valid_new_root is not None:
            header_bar.path_edit.setText(valid_new_root)
            if valid_new_root:
                self.state.paths.recent_dirs = record_recent_dir(
                    self.state.paths.recent_dirs, valid_new_root
                )

        self.state.apply_classifier_config(new_cfg)
        self.state.ui.theme_mode = theme_mode
        menu.sync_theme_mode(theme_mode)
        self.refresh_recent_menu(menu, header_bar.path_edit.text())
        self.sync_classifier_and_table(filter_bar, table_model, new_cfg)
        return root_changed

    def sync_filters_to_state(self, filter_bar: "FilterTuningBar") -> None:
        """현재 검색 필터 입력을 AppState에 저장"""
        self.state.ui.filters.search = filter_bar.search_edit.text()
        self.persist()

    def apply_exception_editor_result(
        self,
        prev_tokens: list[str],
        result: tuple[list[str], list[str]],
        filter_bar: "FilterTuningBar",
    ) -> None:
        """예외 규칙 편집기 확정 결과를 상태 및 필터 바에 반영"""
        active_tokens, disabled_tokens = result
        self.state.ui.disabled_exception_tokens = list(disabled_tokens)
        filter_bar.set_disabled_exception_count(len(disabled_tokens))
        filter_bar.set_exception_tokens(active_tokens)
        if active_tokens == prev_tokens:
            self.persist()

    def remove_missing_recent_dir(
        self, path: str, menu: "MainMenuController", current_path: str
    ) -> None:
        """존재하지 않는 최근 폴더를 목록에서 제거하고 저장, 메뉴 동기화"""
        self.state.paths.recent_dirs = remove_recent_dir(
            self.state.paths.recent_dirs, path
        )
        self.persist()
        self.refresh_recent_menu(menu, current_path)

    def clear_recent_dirs(
        self, menu: "MainMenuController", current_path: str
    ) -> None:
        """최근 작업 폴더 목록을 비우고 저장, 메뉴 동기화"""
        self.state.paths.recent_dirs.clear()
        self.persist()
        self.refresh_recent_menu(menu, current_path)

    def save_window_layout(
        self, window: QMainWindow, table_view: "VideoTableView"
    ) -> None:
        """현재 창 지오메트리와 테이블 헤더 상태를 직렬화하여 저장"""
        geom_b64, hdr_b64 = export_window_ui_state(window, table_view)
        self.state.ui.layout.window_geometry = geom_b64
        self.state.ui.layout.table_header_state = hdr_b64
        self.persist()


def update_filter_exceptions(
    filter_bar: "FilterTuningBar", keywords: list[str], *, add: bool
) -> None:
    """필터 바의 예외 키워드 목록에 항목을 병합하거나 제거"""
    curr = filter_bar.get_exception_tokens()
    if add:
        merged, changed = merge_exception_tokens(curr, keywords)
        if changed:
            filter_bar.set_exception_tokens(merged)
    else:
        filtered = exclude_exception_tokens(curr, keywords)
        if len(filtered) != len(curr):
            filter_bar.set_exception_tokens(filtered)


def order_report_evaluations(report: SummaryReport) -> list[VideoEvaluation]:
    """리포트 평가 목록을 파일명 자연 정렬 순서로 반환"""
    return sorted(
        report.evaluations,
        key=lambda ev: natural_sort_key(ev.video.filename),
    )
