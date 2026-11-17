"""GUI 시작 경로 결정, 분류 세션 바인딩 및 설정 세션 동기화 컨트롤러 모듈"""

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QWidget

from src.core import app_state as app_state_mod
from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_roots,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import (
    ClassifierConfig,
    SavingsConfig,
    ThresholdsConfig,
    merge_tuner_criteria,
)
from src.core.exception_rules import exclude_exception_tokens, merge_exception_tokens
from src.core.models import SummaryReport, VideoEvaluation
from src.core.paths import set_root_dir
from src.core.resolution_tiers import copy_manual_targets
from src.core.sorting import natural_sort_key
from src.core.workspace_paths import (
    are_all_valid_dirs,
    format_root_dirs,
    normalize_root_spec,
    parse_root_dirs,
)
from src.gui.table_state_sync import (
    apply_saved_filters,
    apply_saved_sort,
    snapshot_column_filters,
    snapshot_sort_keys,
)
from src.gui.window_layout import (
    export_top_area_collapsed,
    export_window_ui_state,
)
from src.service.analysis_store import (
    load_manual_target_store,
    open_default_store,
    save_manual_target_store,
)
from src.service.session import ClassifierSession

FILTER_PERSIST_DEBOUNCE_MS: int = 500

if TYPE_CHECKING:
    from src.gui.dialog_coordinator import DialogCoordinator
    from src.gui.menu_bar import MainMenuController
    from src.gui.proxy_model import VideoSortFilterProxyModel
    from src.gui.report_evaluator import ReportEvaluator
    from src.gui.table_model import VideoTableModel
    from src.gui.theme import ThemeManager
    from src.gui.widgets import FilterTuningBar, HeaderBar, VideoTableView


def _root_keys(spec: str) -> tuple[str, ...]:
    """작업 폴더 목록의 비교용 키(순서 포함)"""
    return tuple(os.path.normcase(os.path.abspath(path)) for path in parse_root_dirs(spec))


def resolve_initial_directory(
    classifier: ClassifierSession | None,
    initial_dir: str | None,
) -> str:
    """초기 분류기 인스턴스, 명시 인자, 또는 data/rootdir.txt 순으로 유효한 시작 경로를 결정"""
    if classifier is not None:
        return classifier.root_dir
    if initial_dir and are_all_valid_dirs(initial_dir):
        return normalize_root_spec(initial_dir)
    try:
        root = resolve_effective_root_dir()
        if root and are_all_valid_dirs(root):
            return normalize_root_spec(root)
    except Exception:
        logging.getLogger(__name__).debug("data/rootdir.txt 초기 경로 조회 생략", exc_info=True)
    return ""


def load_initial_state(classifier: ClassifierSession | None) -> AppState:
    """통합 설정 파일(data/config.json)을 로드하고, 기존 분류기 보유 시 해당 설정, 경로, 예외 규칙을 동기화"""
    state = load_app_state()
    # - 출력 해상도 직접 지정은 전용 DB 테이블이 원본이며, 설정 파일의 옛 맵은 테이블 최초 생성 때만 옮김
    state.ui.manual_target_tiers = load_manual_target_store(state.ui.manual_target_tiers)
    if classifier is not None:
        state.apply_classifier_config(classifier.config)
        state.paths.recent_dirs = record_recent_roots(
            state.paths.recent_dirs, classifier.root_dir
        )
        if classifier.exception_tokens:
            state.ui.exception_tokens = list(classifier.exception_tokens)
        if classifier.manual_target_store:
            state.ui.manual_target_tiers = classifier.manual_target_store
    return state


def _build_pseudo_classifier(
    root_dir: str,
    config: ClassifierConfig | None,
    exception_tokens: list[str] | None,
    *,
    recursive: bool = False,
) -> ClassifierSession:
    if config is None:
        state = load_app_state()
        cfg = state.to_classifier_config()
        tokens = exception_tokens if exception_tokens is not None else state.ui.exception_tokens
    else:
        cfg = config
        tokens = exception_tokens
    clf = ClassifierSession(root_dir=root_dir, config=cfg, store=open_default_store())
    if tokens is not None:
        clf.set_exception_tokens(tokens)
    clf.set_recursive(recursive)
    clf.pseudo_classify_mode(True)
    return clf


def bind_classifier_directory(
    current: ClassifierSession | None,
    target_dir: str,
    *,
    persist_rootdir: bool = False,
    config: ClassifierConfig | None = None,
    exception_tokens: list[str] | None = None,
    recursive: bool = False,
) -> tuple[ClassifierSession, bool]:
    """지정 경로(다중 경로 포함)에 맞는 분류 세션을 생성하거나 루트 경로를 전환하고 변경 여부를 반환"""
    norm_dir = normalize_root_spec(target_dir)
    if persist_rootdir:
        try:
            set_root_dir(norm_dir)
        except Exception:
            logging.getLogger(__name__).warning("data/rootdir.txt 경로 저장 실패", exc_info=True)

    if current is None:
        return _build_pseudo_classifier(
            norm_dir, config, exception_tokens, recursive=recursive
        ), True

    changed = _root_keys(current.root_dir) != _root_keys(norm_dir)
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
    recursive: bool = False,
) -> tuple[ClassifierSession | None, bool, str | None]:
    """환경설정 변경 사항(ClassifierConfig 및 작업 경로)을 분류기 세션에 반영

    (갱신된 분류기, 경로 변경 여부, 새 경로 문자열 또는 None) 반환
    """
    prev_root = current.root_dir if current is not None else fallback_path.strip()
    stripped_new = new_root.strip()
    if not stripped_new:
        root_cleared = bool(prev_root)
        return None, root_cleared, "" if root_cleared else None

    valid_new_root = normalize_root_spec(stripped_new) if are_all_valid_dirs(stripped_new) else None
    root_changed = bool(
        valid_new_root
        and (not prev_root or _root_keys(prev_root) != _root_keys(valid_new_root))
    )

    updated = current
    # - 작업 폴더가 같으면 set_root_dir의 디렉터리 시그니처 검사를 건너뜀(GUI 스레드 비용 제거)
    #   오래된 캐시 검사는 다음 분석, 분류 시작 때 prepare_cache에서 다시 수행됨
    if root_changed and updated is not None and valid_new_root is not None:
        updated.set_root_dir(valid_new_root)

    if updated is None and valid_new_root is not None:
        updated = _build_pseudo_classifier(
            valid_new_root, new_cfg, exception_tokens, recursive=recursive
        )
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
    if not resolved_dir or not are_all_valid_dirs(resolved_dir):
        return None
    return _build_pseudo_classifier(
        resolved_dir,
        app_state.to_classifier_config(),
        app_state.ui.exception_tokens or None,
        recursive=app_state.ui.recursive_scan,
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
    report: SummaryReport | None, root_dir: str, config: ClassifierConfig
) -> tuple[bool, bool]:
    """리포트, 작업 경로, 분류 폴더명을 점검해 (실제 분류 가능 여부, 복원 가능 여부) 쌍 반환"""
    can_classify = report is not None and any(
        ev.target_dirname is not None and ev.video.moved_dirname is None
        for ev in report.evaluations
    )
    can_unclassify = report is not None and any(
        ev.video.moved_dirname is not None for ev in report.evaluations
    )
    if not can_unclassify and root_dir:
        # - 사용자가 만든 일반 하위 폴더가 아니라 설정된 분류 폴더가 있을 때만 복원 허용
        # - 다중 작업 폴더면 각 작업 폴더를 모두 검사
        can_unclassify = any(
            os.path.isdir(os.path.join(root, name))
            for root in parse_root_dirs(root_dir)
            for name in config.class_dirnames()
        )
    return can_classify, can_unclassify


class SessionCoordinator:
    """GUI 세션 상태(AppState), 분류 세션, 위젯, 메뉴 동기화를 조율"""

    def __init__(
        self,
        classifier: ClassifierSession | None,
        initial_dir: str | None,
    ) -> None:
        self.state: AppState = load_initial_state(classifier)
        # - 마지막으로 DB에 기록한 직접 지정 맵, 달라졌을 때만 다시 기록
        self._saved_manual_targets: dict[str, dict[str, str]] | None = None
        self.initial_dir: str = resolve_initial_directory(classifier, initial_dir)
        self.classifier: ClassifierSession | None = create_initial_classifier(
            classifier, self.initial_dir, self.state
        )
        self.is_syncing: bool = False
        self._filter_persist_timer: QTimer | None = None
        self.attach_manual_targets()

    def attach_manual_targets(self) -> None:
        """저장된 출력 해상도 직접 지정 맵을 분류 세션에 적용"""
        if self.classifier is not None:
            self.classifier.set_manual_target_store(self.state.ui.manual_target_tiers)

    def apply_manual_targets(self, filenames: list[str], value: str | None) -> None:
        """선택 영상의 출력 해상도 직접 지정을 세션에 설정하고 상태에 반영해 저장"""
        if self.classifier is None:
            return
        self.classifier.set_manual_target_tier(filenames, value)
        self._commit_manual_targets()

    def manual_target_count(self) -> int:
        """현재 작업 폴더에 지정된 출력 해상도 직접 지정 영상 수"""
        if self.classifier is None:
            return 0
        return len(self.classifier.manual_target_tiers)

    def clear_manual_targets(self) -> None:
        """현재 작업 폴더의 출력 해상도 직접 지정을 모두 해제하고 상태에 반영해 저장"""
        if self.classifier is None:
            return
        self.classifier.clear_manual_target_tiers()
        self._commit_manual_targets()

    def _commit_manual_targets(self) -> None:
        if self.classifier is None:
            return
        self.state.ui.manual_target_tiers = self.classifier.manual_target_store
        self.persist()

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
        if self._filter_persist_timer is not None:
            self._filter_persist_timer.stop()
        try:
            save_app_state(self.state, root_dir=root_dir)
        except Exception:
            logging.getLogger(__name__).warning("통합 설정 파일 저장 실패", exc_info=True)
        self._save_manual_targets_if_changed()

    def _save_manual_targets_if_changed(self) -> None:
        """출력 해상도 직접 지정 맵이 마지막 저장과 다를 때만 전용 DB 테이블에 기록"""
        current = copy_manual_targets(self.state.ui.manual_target_tiers)
        if current == self._saved_manual_targets:
            return
        save_manual_target_store(current)
        self._saved_manual_targets = current

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
            self.attach_manual_targets()
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
            header_bar.set_analyze_mode(ui.analyze_mode)
            filter_bar.sync_filter_state(
                search=flt.search,
                exception_tokens=ui.exception_tokens,
            )
            filter_bar.set_disabled_exception_count(len(ui.disabled_exception_tokens))
            table_model.set_config(cfg)
            if callable(apply_proxy_cb):
                apply_proxy_cb()
            menu.sync_theme_mode(theme_mode)
            menu.sync_analyze_mode(ui.analyze_mode)
            header_bar.set_recursive(ui.recursive_scan)
            menu.sync_recursive(ui.recursive_scan)
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
        if not target_dir or not are_all_valid_dirs(target_dir):
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
            recursive=self.state.ui.recursive_scan,
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
        """작업 폴더 목록을 전환하고 상태, 최근 목록을 저장한 뒤 경로 변경 여부 반환"""
        norm_dir = normalize_root_spec(directory)
        prev_dir = header_bar.path_edit.text().strip()
        dir_changed = not prev_dir or _root_keys(prev_dir) != _root_keys(norm_dir)
        header_bar.set_path(norm_dir)
        self.classifier, _ = bind_classifier_directory(
            self.classifier,
            norm_dir,
            persist_rootdir=False,
            config=self.state.to_classifier_config(),
            exception_tokens=self.state.ui.exception_tokens,
            recursive=self.state.ui.recursive_scan,
        )
        self.sync_classifier_and_table(filter_bar, table_model)
        self.state.paths.recent_dirs = record_recent_roots(
            self.state.paths.recent_dirs, self.classifier.root_dir
        )
        self.persist(root_dir=self.classifier.root_dir)
        self.refresh_recent_menu(menu, self.classifier.root_dir)
        return dir_changed

    def add_directory(
        self,
        directory: str,
        header_bar: "HeaderBar",
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        menu: "MainMenuController",
    ) -> bool:
        """현재 작업 폴더 목록 뒤에 새 폴더(또는 여러 폴더)를 추가하고 전환

        - 모두 이미 등록된 폴더면 아무 것도 바꾸지 않고 False 반환
        """
        current = parse_root_dirs(self.current_root_dir(header_bar.path_edit.text()))
        merged = parse_root_dirs([*current, directory])
        if len(merged) == len(current):
            return False
        self.switch_directory(
            format_root_dirs(merged), header_bar, filter_bar, table_model, menu
        )
        return True

    def set_recursive_scan(
        self,
        enabled: bool,
        header_bar: "HeaderBar",
        menu: "MainMenuController",
    ) -> bool:
        """하위 폴더 포함 설정을 상태, 분류 세션, 헤더와 메뉴에 반영하고 변경 여부 반환"""
        changed = self.state.ui.recursive_scan != enabled
        self.state.ui.recursive_scan = enabled
        if self.classifier is not None:
            self.classifier.set_recursive(enabled)
        header_bar.set_recursive(enabled)
        menu.sync_recursive(enabled)
        self.persist()
        return changed

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
        savings: SavingsConfig,
        filter_bar: "FilterTuningBar",
        table_model: "VideoTableModel",
        dialogs: "DialogCoordinator",
    ) -> ClassifierConfig:
        """판별 기준 조정 창에서 변경된 임계값, 효율 기준을 세션, 분류기, UI에 반영"""
        new_cfg = merge_tuner_criteria(
            self.state.to_classifier_config(), thresholds, savings
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
            recursive=self.state.ui.recursive_scan,
        )
        if valid_new_root is not None:
            header_bar.set_path(valid_new_root)
            if valid_new_root:
                self.state.paths.recent_dirs = record_recent_roots(
                    self.state.paths.recent_dirs, valid_new_root
                )

        self.state.apply_classifier_config(new_cfg)
        self.state.ui.theme_mode = theme_mode
        menu.sync_theme_mode(theme_mode)
        self.refresh_recent_menu(menu, header_bar.path_edit.text())
        self.sync_classifier_and_table(filter_bar, table_model, new_cfg)
        return root_changed

    def sync_filters_to_state(self, filter_bar: "FilterTuningBar") -> None:
        """현재 검색 필터 입력을 AppState에 반영하고 타이핑마다 디스크 저장하지 않도록 저장을 지연"""
        self.state.ui.filters.search = filter_bar.search_edit.text()
        self._schedule_persist()

    def sync_column_filters(self, proxy_model: "VideoSortFilterProxyModel") -> None:
        """프록시 모델의 열별 필터 조건을 열 ID 키로 AppState에 반영하고 저장을 지연"""
        self.state.ui.filters.columns = snapshot_column_filters(proxy_model)
        self._schedule_persist()

    def sync_sort_keys(self, proxy_model: "VideoSortFilterProxyModel") -> None:
        """프록시 모델의 정렬 우선순위를 (열 ID, 오름차순 여부) 목록으로 AppState에 반영"""
        self.state.ui.filters.sort_keys = snapshot_sort_keys(proxy_model)
        self._schedule_persist()

    def restore_table_state(
        self,
        proxy_model: "VideoSortFilterProxyModel",
        table_view: "VideoTableView",
    ) -> None:
        """저장된 열 필터와 정렬 우선순위를 프록시에 다시 적용, 복원 중 바뀌는 상태를 피하려고 저장값을 먼저 복사"""
        filters_state = self.state.ui.filters
        columns = dict(filters_state.columns)
        sort_keys = list(filters_state.sort_keys)
        apply_saved_filters(proxy_model, columns)
        apply_saved_sort(proxy_model, table_view.filter_header, sort_keys)

    def submit_cached_reevaluation(
        self,
        evaluator: "ReportEvaluator",
        on_report: Callable[[SummaryReport], None],
        on_done: Callable[[], None],
    ) -> None:
        """메모리 캐시 재평가를 백그라운드에서 실행, 결과 적용 뒤 on_done 호출, 캐시가 없으면 바로 on_done"""
        job = self.classifier.cached_report_job() if self.classifier is not None else None
        if job is None:
            on_done()
            return

        def apply(report: SummaryReport) -> None:
            on_report(report)
            on_done()

        evaluator.submit(job, apply)

    def set_preview_audio(self, volume_percent: int, muted: bool) -> None:
        """미리 보기 창의 음량과 음소거 상태를 AppState에 반영하고 슬라이더 드래그마다 저장하지 않도록 지연"""
        self.state.ui.preview_volume = volume_percent
        self.state.ui.preview_muted = muted
        self._schedule_persist()

    def _schedule_persist(self) -> None:
        if self._filter_persist_timer is None:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.setInterval(FILTER_PERSIST_DEBOUNCE_MS)
            timer.timeout.connect(self.persist)
            self._filter_persist_timer = timer
        self._filter_persist_timer.start()

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
        self.state.ui.layout.top_area_collapsed = export_top_area_collapsed(window)
        # - 빈 표에서는 맨 위 행이 없으므로 이전에 저장한 위치를 유지
        if (top := table_view.top_visible_row_key()) is not None:
            self.state.ui.layout.table_top_filename, self.state.ui.layout.table_top_row = top
        self.persist()


def update_filter_exceptions(
    filter_bar: "FilterTuningBar", keywords: list[str], *, add: bool
) -> None:
    """필터 바의 예외 규칙 목록에 항목을 병합하거나 제거"""
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
