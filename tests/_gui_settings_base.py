"""설정 대화상자 테스트의 공용 기반, 격리 대화상자와 기준 패널 생성 도우미"""

import json
import os
import tempfile
from typing import Any, cast

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QIcon, QWheelEvent
from PyQt6.QtWidgets import QApplication

from src.core.app_state import CONFIG_FILENAME, DATA_DIR_NAME
from src.core.config import BitrateModelConfig, ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.core.state_codec import strip_jsonc_comments
from src.gui.dialogs.criteria_tuner_panel import CriteriaTunerPanel
from src.gui.dialogs.settings_dialog import SettingsDialog
from tests._gui_helpers import GuiTestCase, isolate_app_state

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def count_pixels_by_lightness(icon: QIcon, *, min_lightness: int | None = None, max_lightness: int | None = None) -> int:
    img = icon.pixmap(16, 16).toImage()
    count = 0
    for y in range(img.height()):
        for x in range(img.width()):
            color = img.pixelColor(x, y)
            if color.alpha() <= 100:
                continue
            lightness = color.lightness()
            if min_lightness is not None and lightness <= min_lightness:
                continue
            if max_lightness is not None and lightness >= max_lightness:
                continue
            count += 1
    return count


class GuiSettingsTestCase(GuiTestCase):
    """설정 대화상자 테스트의 공용 기반, 격리 대화상자와 기준 패널 생성 도우미"""

    def _isolated_dialog(self) -> tuple[SettingsDialog, str, str]:
        """격리된 폴더에서 3000kbps, 키프레임 오버헤드 0.08 설정으로 연 설정 창과 (루트, 저장 경로)를 반환"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        isolate_app_state(self, tmp.name)
        config = ClassifierConfig(
            thresholds=ThresholdsConfig(keyframe_1s_increase_rate=0.08),
            bitrate_model=BitrateModelConfig(base_target_kbps=3000),
        )
        dialog = SettingsDialog(current_config=config, current_root_dir=tmp.name)
        self.addCleanup(dialog.close)
        return dialog, tmp.name, os.path.join(tmp.name, DATA_DIR_NAME, CONFIG_FILENAME)

    @staticmethod
    def _read_saved(state_path: str) -> dict[str, Any]:
        with open(state_path, encoding="utf-8") as f:
            return cast("dict[str, Any]", json.loads(strip_jsonc_comments(f.read())))

    def _tuner_panel(self) -> CriteriaTunerPanel:
        panel = CriteriaTunerPanel(
            ThresholdsConfig(over_bitrate_ratio=1.45, low_bitrate_ratio=0.70, keyframe_interval_sec=3.0),
            SavingsConfig(min_saved_mb=12.0, min_mb_per_min=4.5, exempt_saved_mb=80.0),
        )
        panel.show()
        self.addCleanup(panel.close)
        return panel

    @staticmethod
    def _wheel_event() -> QWheelEvent:
        return QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    def _criteria_dialog(self) -> tuple[SettingsDialog, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        isolate_app_state(self, tmp.name)
        dialog = SettingsDialog(current_root_dir=tmp.name, active_tab=SettingsDialog.TAB_CRITERIA)
        self.addCleanup(dialog.close)
        return dialog, tmp.name

    def _advanced_dialog(self) -> SettingsDialog:
        """고급 섹션을 펼친 표시 상태의 설정 창을 반환"""
        dlg, _, _ = self._isolated_dialog()
        dlg.section_advanced.set_expanded(True)
        dlg.show()
        _APP.processEvents()
        return dlg
