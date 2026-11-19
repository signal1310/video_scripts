"""MainWindow 창 테스트의 공용 기반, 샌드박스 초기화와 창 조작 도우미"""

import os
import tempfile
import time

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from src.classifier import VideoClassifier
from src.core.models import VideoProps
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.main_window import MainWindow
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._gui_helpers import GuiTestCase, isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])
ASYNC_WAIT_SECONDS: float = 5.0


def settle_reevaluation(mw: "MainWindow") -> None:
    """설정 변경 뒤 백그라운드 재평가 결과가 화면에 적용될 때까지 이벤트를 처리하며 대기"""
    deadline = time.monotonic() + ASYNC_WAIT_SECONDS
    while mw._evaluator.pending and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def wait_for_flag(flag: list[bool]) -> None:
    """백그라운드 작업의 완료 콜백이 flag에 값을 넣을 때까지 이벤트를 처리하며 대기"""
    deadline = time.monotonic() + ASYNC_WAIT_SECONDS
    while not flag and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    if not flag:
        raise AssertionError("백그라운드 작업 완료 신호를 받지 못함")


def left_click(widget: QWidget) -> None:
    center = QPointF(widget.rect().center())
    global_center = widget.mapToGlobal(center)
    for event_type, buttons in (
        (QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            event_type, center, global_center, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(widget, event)


class GuiWindowTestCase(GuiTestCase):
    """MainWindow 창 테스트의 공용 기반, 샌드박스 초기화와 창 조작 도우미"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        self.videos = [
            VideoProps(
                filename="가나다_10_고화질.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=60.0,
                vid_kbps=9500,
                aud_kbps=192,
                vid_size_MB=120.0,
                duration=100.0,
                codec="h264",
                keyframe_interval=1.0,
            ),
            VideoProps(
                filename="가나다_2_정상.mp4",
                width=1280,
                height=720,
                rotate_type=90,
                fps=30.0,
                vid_kbps=1200,
                aud_kbps=128,
                vid_size_MB=45.0,
                duration=240.0,
                codec="hevc",
                keyframe_interval=1.5,
            ),
            VideoProps(
                filename="저화질_KF불량_클립.mkv",
                width=854,
                height=480,
                rotate_type=0,
                fps=30.0,
                vid_kbps=400,
                aud_kbps=96,
                vid_size_MB=25.0,
                duration=400.0,
                codec="mpeg4",
                keyframe_interval=5.0,
            ),
        ]

    def _loaded_window(self, *, with_report: bool = True) -> tuple[MainWindow, str]:
        """3개 영상 캐시가 있는 분류기에 바인딩된 표시 상태의 창과 작업 폴더를 반환"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clf = VideoClassifier(root_dir=tmp.name)
        DEFAULT_CACHE_REGISTRY.put(tmp.name, TableCache(tmp.name, list(self.videos), include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.show()
        if with_report:
            mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        return mw, tmp.name

    def _persist_ui_changes(self) -> str:
        """분류기 없는 창에서 임계값, 예외, 검색어, 스캔 모드, 작업 폴더를 바꾼 뒤 닫고 작업 폴더를 반환"""
        state_root = tempfile.TemporaryDirectory()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(state_root.cleanup)
        self.addCleanup(work.cleanup)
        isolate_app_state(self, state_root.name)

        mw = MainWindow()
        self.addCleanup(mw.close)
        self.assertIsNone(mw.classifier)
        mw.open_tuner()
        tuner = mw.dialogs.settings_dialog
        assert tuner is not None
        tuner.criteria_panel.spin_over_ratio.setValue(1.55)
        tuner.tab_widget.setCurrentIndex(SettingsDialog.TAB_ENVIRONMENT)
        tuner.close()
        mw.filter_bar.set_exception_tokens(["@hevc"])
        mw.filter_bar.search_edit.setText("clip")
        mw.header_bar.split_analyze.set_mode("full", emit_signal=True)
        mw.set_working_directory(work.name)
        mw.close()
        return work.name

    def _menu_window(self) -> tuple[MainWindow, str, str]:
        """격리된 임시 폴더 두 개를 오간 MainWindow를 만들어 (창, dir_a, dir_b)를 반환"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dir_a, dir_b = (os.path.join(tmp.name, name) for name in ("dir_a", "dir_b"))
        os.makedirs(dir_a)
        os.makedirs(dir_b)
        isolate_app_state(self, tmp.name)
        mw = MainWindow(initial_dir=dir_a)
        self.addCleanup(mw.close)
        mw.set_working_directory(dir_a)
        mw.set_working_directory(dir_b)
        return mw, dir_a, dir_b
