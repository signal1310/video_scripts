"""GUI 테스트 공용 격리, 창 검증 헬퍼"""

import functools
import os
import sys
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenuBar, QWidget

from src.core.encode.ffmpeg_capability import probe_ffmpeg_capability
from src.gui.menus.menu_callbacks import MenuBarCallbacks

# - 합성 누름 이벤트로 올라간 수식키 상태를 되돌리려고 떼기 이벤트를 보내는 키 목록
MODIFIER_KEYS: tuple[Qt.Key, ...] = (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta)
# - PyQt6 스텁이 QTest.keyRelease를 인스턴스 메서드로 선언해 정적 호출의 형식 검사가 실패하므로 호출 가능한 형태로 받음
_send_key_release: Callable[..., None] = QTest.keyRelease
# - 창을 만들 때마다 FFmpeg를 하위 프로세스로 세 번 실행하는 비용을 프로세스 안에서 경로별 한 번으로 줄임
_PROBE_TARGET: str = "src.gui.controllers.encode_coordinator.probe_ffmpeg_capability"
_cached_probe = functools.lru_cache(maxsize=None)(probe_ffmpeg_capability)
# - 재생기 소스를 비운 뒤 백엔드가 스레드를 놓을 때까지 돌리는 이벤트 처리 횟수
_MEDIA_RELEASE_PASSES: int = 8


def release_key(
    widget: QWidget | None,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """창 시스템 경로로 키 떼기 이벤트를 보내 전역 수식키 상태를 갱신"""
    _send_key_release(widget, key, modifier)


def snapshot_top_level_widgets() -> set[QWidget]:
    """현재 최상위 위젯 객체 집합을 반환. 객체 자체를 담아 GC 뒤 id 재사용을 피함"""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return set()
    return set(app.topLevelWidgets())


def dispose_new_top_level_widgets(before: set[QWidget]) -> None:
    """기록 이후 새로 생긴 부모 없는 위젯만 미디어 소스를 놓고 지연 삭제한 뒤 즉시 처리

    - 기록 이전 위젯(setUpClass에서 만든 공유 창 등)은 같은 클래스의 다른 테스트가 쓰므로 남김
    - 부모가 있는 창(메뉴, 팝업)은 부모 삭제와 함께 정리되므로 따로 지우지 않음
    - 재생기 소스를 쥔 채 파괴하면 Qt FFmpeg 백엔드가 접근 위반으로 죽으므로 삭제 전에 비움
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    doomed = [widget for widget in app.topLevelWidgets() if widget not in before and widget.parentWidget() is None]
    release_media_sources(*doomed)
    for widget in doomed:
        # - 소스를 놓는 사이 앞서 걸어 둔 지연 삭제가 처리되어 이미 지워진 위젯이 있음
        if not sip.isdeleted(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def release_media_sources(*roots: QWidget) -> None:
    """주어진 위젯 트리의 모든 재생기에서 미디어 소스를 비우고 종료 신호를 처리

    - 잘못된 파일을 연 재생기를 소스를 쥔 채 파괴하면 Qt FFmpeg 백엔드가 종료 중에 접근 위반으로 죽음
    - 재생기 객체가 지워진 뒤에는 소스를 비울 수 없으므로 파괴 전에 호출함
    - 재생기마다 이벤트를 돌리면 비용이 커져, 모든 재생기를 비운 뒤 한 번만 처리함
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    players: list[QMediaPlayer] = []
    for root in roots:
        if sip.isdeleted(root):
            continue
        players.extend(root.findChildren(QMediaPlayer))
    for player in players:
        if sip.isdeleted(player):
            continue
        player.stop()
        player.setSource(QUrl())
    for _ in range(_MEDIA_RELEASE_PASSES):
        app.processEvents()


def release_modifier_keys() -> None:
    """수식키 떼기 이벤트를 창 시스템 경로로 보내 전역 수식키 상태를 비움

    - sendEvent로 보낸 누름 이벤트는 수식키 상태를 올리기만 하고, 같은 방식의 떼기 이벤트는 내리지 않음
    """
    holder = QWidget()
    holder.show()
    holder.activateWindow()
    QApplication.processEvents()
    try:
        for key in MODIFIER_KEYS:
            release_key(holder, key)
    finally:
        holder.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def fail_on_leftover_modifiers(case: unittest.TestCase, result: unittest.TestResult) -> None:
    """테스트 뒤에도 수식키가 눌린 상태이면 그 테스트를 실패로 기록하고 상태를 비움

    - 남은 수식키는 뒤 테스트의 선택 명령과 단축키 판정을 바꾸므로, 남긴 테스트에서 바로 드러나게 함
    """
    leftover = QGuiApplication.keyboardModifiers()
    if leftover == Qt.KeyboardModifier.NoModifier:
        return
    release_modifier_keys()
    try:
        raise AssertionError(f"테스트 뒤 수식키가 남아 있음: {leftover}")
    except AssertionError:
        result.addFailure(case, sys.exc_info())


class GuiTestCase(unittest.TestCase):
    """테스트 메서드가 만든 최상위 위젯을 테스트 뒤 정리하고, 남은 수식키 상태를 실패로 기록하는 기반 클래스

    - setUpClass 이전 위젯은 run() 기록에 포함되어 보존됨
    - 수식키가 남으면 테스트를 실패로 기록하고 상태를 비움
    - 인코딩 조정기의 FFmpeg 능력 확인은 같은 경로면 프로세스 안에서 한 번만 실행
    """

    def run(self, result: unittest.TestResult | None = None) -> unittest.TestResult:
        if result is None:
            result = self.defaultTestResult()
        before = snapshot_top_level_widgets()
        try:
            with patch(_PROBE_TARGET, _cached_probe):
                super().run(result)
        finally:
            dispose_new_top_level_widgets(before)
        fail_on_leftover_modifiers(self, result)
        return result


def isolate_app_state(case: unittest.TestCase, root: str) -> None:
    """설정 JSON, 작업 폴더 기록, 분석 캐시 DB 경로를 지정 폴더 아래로 돌리고 테스트 종료 시 복원"""
    targets = {
        "src.core.settings.app_state.get_state_path": os.path.join(root, "data", "config.json"),
        "src.core.paths.get_rootdir_path": os.path.join(root, ".rootdir"),
        "src.service.analysis_store.get_data_dir": os.path.join(root, "data"),
    }
    for target, path in targets.items():
        patcher = patch(target, return_value=path)
        patcher.start()
        case.addCleanup(patcher.stop)


def assert_independent_window(case: unittest.TestCase, window: QWidget) -> None:
    """부모 없는 비모달 독립 창으로 열려 있는지 검증"""
    case.assertTrue(window.isVisible())
    case.assertIsNone(window.parent())
    case.assertEqual(window.windowModality(), Qt.WindowModality.NonModal)
    case.assertEqual(window.windowFlags() & Qt.WindowType.WindowType_Mask, Qt.WindowType.Window)


def make_menu_callbacks() -> MenuBarCallbacks:
    """모든 메뉴 콜백이 빈 함수인 묶음. 메뉴 구성과 연결 동작만 검증할 때 씀"""
    return MenuBarCallbacks(
        on_browse_dir=lambda: None,
        on_select_recent_dir=lambda _path: None,
        on_clear_recent_dirs=lambda: None,
        on_open_explorer=lambda: None,
        on_export_csv=lambda: None,
        on_close_window=lambda: None,
        on_focus_search=lambda: None,
        on_reset_filters=lambda: None,
        on_open_exception_editor=lambda: None,
        on_open_tuner=lambda: None,
        on_open_settings=lambda: None,
        on_set_theme_mode=lambda _mode: None,
        on_reset_columns=lambda: None,
        on_show_logs=lambda: None,
        on_shortcut_analyze=lambda: None,
        on_analyze_fast=lambda: None,
        on_analyze_full=lambda: None,
        on_force_reanalyze=lambda: None,
        on_cancel=lambda: None,
        on_play_selected=lambda: None,
        on_preview_selected=lambda: None,
        on_reveal_selected=lambda: None,
        on_copy_selected_names=lambda: None,
        on_copy_selected_paths=lambda: None,
        on_add_selected_exception=lambda: None,
        on_remove_selected_exception=lambda: None,
        on_show_help=lambda: None,
        on_open_data_cleanup=lambda: None,
        on_set_manual_target=lambda _value: None,
        on_clear_manual_targets=lambda: None,
        get_selected_evaluations=lambda: [],
    )


def menu_bar_of(window: QMainWindow) -> QMenuBar:
    """메인 창의 메뉴 막대. 메뉴 막대가 없는 창은 테스트 대상이 아니므로 None을 허용하지 않음"""
    bar = window.menuBar()
    assert bar is not None
    return bar
