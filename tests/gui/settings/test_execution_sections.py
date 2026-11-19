"""재인코딩 설정 탭의 행 구성, 값 왕복, FFmpeg 상태 확인 시점, 태그 이름 검증, 대화상자 자동 저장과 전체 초기화 검증"""

import dataclasses
import os
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.core.config import (
    CONTAINER_POLICY_MP4,
    NAME_CONFLICT_SKIP,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
    ExecutionConfig,
)
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN
from src.gui.settings import settings_dialog as dialog_module
from src.gui.settings.execution_sections import ExecutionTabPanel
from tests._gui_helpers import GuiTestCase
from tests._gui_settings_base import GuiSettingsTestCase

_APP = QApplication.instance() or QApplication([])
_TAB_TITLE: str = "재인코딩"


def _probe_returning(capability: FfmpegCapability) -> Callable[[str], FfmpegCapability]:
    """경로와 관계없이 지정한 확인 결과를 돌려주는 가짜 FFmpeg 확인 함수"""

    def probe(_path: str) -> FfmpegCapability:
        return capability

    return probe


def _usable(version: tuple[int, int] | None = (6, 1)) -> FfmpegCapability:
    return FfmpegCapability("ffmpeg", "ffmpeg version 6.1", version, usable=True)


def _non_default_config() -> ExecutionConfig:
    return ExecutionConfig(
        ffmpeg_path="tools/ffmpeg.exe",
        ffprobe_path="tools/ffprobe.exe",
        result_policy=RESULT_POLICY_SEPARATE,
        archive_dirname="archive_test",
        output_dirname="output_test",
        container_policy=CONTAINER_POLICY_MP4,
        concurrent_jobs=3,
        prevent_sleep=False,
        after_complete="sleep",
        process_priority="below_normal",
        name_conflict=NAME_CONFLICT_SKIP,
        encode_tag_mode=ENCODE_TAG_MODE_HIDDEN,
        encode_tag_name="sample_tag",
    )


class ExecutionTabPanelTests(GuiTestCase):
    """패널 단위의 행 구성, 값 왕복, 상태 확인, 검증 동작을 검증"""

    def test_rows_cover_every_execution_key(self) -> None:
        """설정 키 13개마다 행이 하나씩 있는지 검증"""
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))
        self.assertEqual(panel.row_keys, frozenset(f.name for f in dataclasses.fields(ExecutionConfig)))
        self.assertEqual(len(panel.row_keys), 13)

    def test_every_row_has_a_tooltip(self) -> None:
        """모든 설정 행에 세부 설명 툴팁이 있는지 검증"""
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))
        for key, row in panel.rows.items():
            with self.subTest(key=key):
                self.assertTrue(row.toolTip() or row.editor.toolTip())

    def test_load_then_read_round_trips_every_value(self) -> None:
        """입력 위젯에 값을 불러온 뒤 다시 읽으면 같은 설정이 나오는지 검증"""
        cfg = _non_default_config()
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))

        panel.load(cfg)

        self.assertEqual(panel.read(), cfg)

    def test_load_does_not_emit_changed(self) -> None:
        """값을 불러오는 동안에는 변경 신호를 보내지 않는지 검증(자동 저장 중복 방지)"""
        emitted: list[bool] = []
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))
        panel.changed.connect(lambda: emitted.append(True))

        panel.load(_non_default_config())

        self.assertEqual(emitted, [])

    def test_zero_concurrent_jobs_shows_auto_suffix(self) -> None:
        """동시 재인코딩 수 0은 자동 표기로 보이고, 다른 값에서는 표기가 없는지 검증"""
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))
        self.assertEqual(panel.spin_concurrent.suffix(), " (자동)")

        panel.spin_concurrent.setValue(2)

        self.assertEqual(panel.spin_concurrent.suffix(), "")

    def test_usable_ffmpeg_shows_version(self) -> None:
        """사용 가능한 FFmpeg는 확인한 버전을 상태 문구로 보여 주는지 검증"""
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable((6, 1))))

        panel.refresh_ffmpeg_status()

        self.assertEqual(panel.lbl_status.text(), "사용 가능: FFmpeg 6.1")
        self.assertEqual(panel.lbl_status.objectName(), "SettingDesc")

    def test_unusable_ffmpeg_shows_reason_in_error_style(self) -> None:
        """사용할 수 없는 FFmpeg는 사유를 오류 스타일로 보여 주는지 검증"""
        missing = FfmpegCapability(
            "ffmpeg", "", None, usable=False, reason="FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요"
        )
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(missing))

        panel.refresh_ffmpeg_status()

        self.assertEqual(panel.lbl_status.text(), "사용할 수 없음: FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요")
        self.assertEqual(panel.lbl_status.objectName(), "ErrorHintLabel")

    def test_ffmpeg_is_checked_only_when_the_tab_is_shown(self) -> None:
        """탭을 보이기 전에는 FFmpeg를 실행하지 않고, 보이는 순간 한 번 확인하는지 검증"""
        calls: list[str] = []

        def recording(path: str) -> FfmpegCapability:
            calls.append(path)
            return _usable()

        panel = ExecutionTabPanel(_non_default_config(), probe=recording)
        self.assertEqual(calls, [])

        panel.show()

        self.assertEqual(calls, ["tools/ffmpeg.exe"])

    def test_editing_the_ffmpeg_path_rechecks_only_when_the_tab_is_visible(self) -> None:
        """FFmpeg 경로 편집이 끝났을 때 탭이 보이면 새 경로로 다시 확인하고, 숨겨진 상태면 확인하지 않는지 검증"""
        calls: list[str] = []

        def recording(path: str) -> FfmpegCapability:
            calls.append(path)
            return _usable()

        panel = ExecutionTabPanel(ExecutionConfig(), probe=recording)
        panel.edit_ffmpeg.setText("tools/other_ffmpeg.exe")
        panel.edit_ffmpeg.editingFinished.emit()
        self.assertEqual(calls, [])

        panel.show()
        self.assertEqual(calls, ["tools/other_ffmpeg.exe"])
        panel.edit_ffmpeg.setText("tools/newer_ffmpeg.exe")
        panel.edit_ffmpeg.editingFinished.emit()

        self.assertEqual(calls, ["tools/other_ffmpeg.exe", "tools/newer_ffmpeg.exe"])

    def test_invalid_tag_name_is_not_saved_and_keeps_the_last_valid_name(self) -> None:
        """검증에 실패한 태그 이름은 저장 대상에 넣지 않고 오류를 보이며, 정상 이름은 공백을 뗀 뒤 반영하는지 검증"""
        emitted: list[bool] = []
        panel = ExecutionTabPanel(ExecutionConfig(), probe=_probe_returning(_usable()))
        panel.changed.connect(lambda: emitted.append(True))

        panel.edit_tag_name.setText("bad;name")
        panel.edit_tag_name.editingFinished.emit()

        self.assertEqual(emitted, [])
        self.assertFalse(panel.lbl_tag_error.isHidden())
        self.assertEqual(panel.read().encode_tag_name, ExecutionConfig().encode_tag_name)

        panel.edit_tag_name.setText("  ok_tag  ")
        panel.edit_tag_name.editingFinished.emit()

        self.assertTrue(panel.lbl_tag_error.isHidden())
        self.assertEqual(panel.read().encode_tag_name, "ok_tag")
        self.assertEqual(emitted, [True])


class ExecutionSettingsDialogTests(GuiSettingsTestCase):
    """설정 대화상자 안의 재인코딩 탭 등록, 자동 저장, 전체 초기화 동작을 검증"""

    def test_execution_tab_is_added_after_the_existing_tabs(self) -> None:
        """기존 탭 인덱스를 바꾸지 않도록 재인코딩 탭이 단축키 탭 뒤에 붙는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()

        titles = [dialog.tab_widget.tabText(i) for i in range(dialog.tab_widget.count())]

        self.assertEqual(titles, ["환경 설정", "판별 기준", "단축키", _TAB_TITLE])

    def test_changing_an_execution_row_is_saved_to_the_state_file(self) -> None:
        """재인코딩 탭의 값을 바꾸면 설정 파일에 바로 저장되는지 검증"""
        dialog, _root, state_path = self._isolated_dialog()
        combo = dialog.execution_panel.combo_result_policy

        combo.setCurrentIndex(combo.findData(RESULT_POLICY_REPLACE))

        saved = self._read_saved(state_path)
        self.assertEqual(saved["execution"]["result_policy"], RESULT_POLICY_REPLACE)

    def test_reset_defaults_restores_execution_values(self) -> None:
        """전체 초기화가 재인코딩 탭의 값까지 기본값으로 되돌리는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        dialog.execution_panel.spin_concurrent.setValue(3)
        self.assertEqual(dialog.execution_panel.read().concurrent_jobs, 3)

        with mock.patch.object(dialog_module.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_reset_defaults_clicked()

        self.assertEqual(dialog.execution_panel.read(), ExecutionConfig())


if __name__ == "__main__":
    unittest.main(verbosity=2)
