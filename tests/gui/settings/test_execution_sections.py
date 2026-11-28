"""인코딩 설정 탭의 행 구성, 값 왕복, FFmpeg 상태 확인 시점, 목표 인코딩 방식 연결, 출력 형식 설명, 태그 이름 검증, 대화상자 자동 저장과 전체 초기화 검증"""

import dataclasses
import os
import time
import unittest
from collections.abc import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QIcon, QImage
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QMessageBox

from src.core.analysis.system_probe import SystemSpeedRecommendation
from src.core.config import (
    CONTAINER_POLICY_MP4,
    NAME_CONFLICT_SKIP,
    PRECISE_QUALITY_MODE_CHOICES,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
    EncodeConfig,
    ExecutionConfig,
)
from src.core.encode.encoder_trial import TRIAL_FAILED_REASON, EncoderTrialCache
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN
from src.gui.settings import settings_dialog as dialog_module
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.execution_sections import ExecutionTabPanel
from src.gui.settings.execution_texts import (
    CODEC_AVAILABLE_TEXT,
    CODEC_CHECKING_TEXT,
    CODEC_UNAVAILABLE_PREFIX,
    TOOLTIP_SPEED_DETECT,
)
from src.gui.settings.setting_controls import RESET_ICON_SIZE
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.palettes import LIGHT_PALETTE
from tests._gui_helpers import GuiTestCase
from tests._gui_settings_base import GuiSettingsTestCase

_APP = QApplication.instance() or QApplication([])
_TAB_TITLE: str = "인코딩"
_CODEC_KEY: str = "analysis.encode.encode_codec"
_SPEED_KEY: str = "analysis.encode.encode_speed_factor"
_PRECISE_MODE_KEY: str = "analysis.encode.precise_quality_mode"
_ENCODE_KEYS: frozenset[str] = frozenset({_CODEC_KEY, _SPEED_KEY, _PRECISE_MODE_KEY})
_CONTAINER_KEY: str = "execution.container_policy"
_EXECUTION_KEYS: frozenset[str] = frozenset(f"execution.{f.name}" for f in dataclasses.fields(ExecutionConfig))
_RECOMMEND_TARGET: str = "src.gui.settings.execution_sections.detect_recommended_encode_speed"
_POLL_WAIT_SEC: float = 3.0
_POLL_STEP_SEC: float = 0.01
_COLOR_TOLERANCE: int = 24
_SAME_ROW_TOLERANCE_PX: int = 4


def _trial_returning(returncode: int = 0) -> EncoderTrialCache:
    """시험 인코딩을 바로 끝내고 지정한 종료 코드를 결과로 쓰는 가짜 캐시(FFmpeg 없이 동작)"""

    def runner(_command: list[str], _timeout_sec: float) -> int | None:
        return returncode

    return EncoderTrialCache(runner=runner, spawn=_run_now)


def _run_now(task: Callable[[], None]) -> None:
    task()


def _wait_for(condition: Callable[[], bool]) -> bool:
    """주기 확인 타이머가 조건을 만족할 때까지 이벤트를 처리하며 기다리고, 시간 안에 만족하면 참을 돌려줌"""
    waited = 0.0
    while not condition() and waited < _POLL_WAIT_SEC:
        _APP.processEvents()
        time.sleep(_POLL_STEP_SEC)
        waited += _POLL_STEP_SEC
    return condition()


def _probe_returning(capability: FfmpegCapability) -> Callable[[str], FfmpegCapability]:
    """경로와 관계없이 지정한 확인 결과를 돌려주는 가짜 FFmpeg 확인 함수"""

    def probe(_path: str) -> FfmpegCapability:
        return capability

    return probe


def _icon_image(icon: QIcon) -> QImage:
    """아이콘을 픽셀 이미지로 그려 색을 검사할 수 있게 함"""
    return icon.pixmap(RESET_ICON_SIZE, RESET_ICON_SIZE).toImage()


def _contains_color(image: QImage, color: str) -> bool:
    """이미지에 지정한 색(투명 제외) 픽셀이 있는지 확인"""
    target = QColor(color)
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() <= 100:
                continue
            if (
                abs(pixel.red() - target.red()) <= _COLOR_TOLERANCE
                and abs(pixel.green() - target.green()) <= _COLOR_TOLERANCE
                and abs(pixel.blue() - target.blue()) <= _COLOR_TOLERANCE
            ):
                return True
    return False


def _fake_recommend(target_codec: str = "hevc_nvenc") -> SystemSpeedRecommendation:
    """시스템 자원을 조회하지 않고 코덱별로 고정 배속을 돌려주는 추천 함수"""
    speed = 1.9 if target_codec == "libx264" else 4.5
    return SystemSpeedRecommendation(
        recommended_speed_factor=speed,
        encoder_label=target_codec,
        detail_summary=f"추천: {target_codec} -> {speed:.1f}배속",
    )


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


def _non_default_encode() -> EncodeConfig:
    return EncodeConfig(encode_codec="libx264", encode_speed_factor=2.5, precise_quality_mode="all")


class ExecutionTabPanelTests(GuiTestCase):
    """패널 단위의 행 구성, 값 왕복, 상태 확인, 목표 인코딩 방식 연결, 검증 동작을 검증"""

    def setUp(self) -> None:
        """추천 배속은 실제 시스템을 조회하지 않도록 고정 함수로 바꿈"""
        patcher = mock.patch(_RECOMMEND_TARGET, side_effect=_fake_recommend)
        self.recommend = patcher.start()
        self.addCleanup(patcher.stop)

    def _panel(self, cfg: ExecutionConfig | None = None, encode: EncodeConfig | None = None) -> ExecutionTabPanel:
        return ExecutionTabPanel(
            cfg or ExecutionConfig(), encode or EncodeConfig(), probe=_probe_returning(_usable()), trial=_trial_returning(),
        )

    def test_rows_cover_every_execution_key_and_the_two_encode_keys(self) -> None:
        """실행 설정 17개 필드마다 행이 있고, 목표 인코딩 방식, 기준 배속, 품질값 정밀 결정 행이 추가로 있는지 검증"""
        panel = self._panel()
        self.assertEqual(panel.row_keys, _EXECUTION_KEYS | _ENCODE_KEYS)
        self.assertEqual(len(_EXECUTION_KEYS), 17)

    def test_every_row_has_a_tooltip(self) -> None:
        """모든 설정 행에 세부 설명 툴팁이 있는지 검증"""
        panel = self._panel()
        for key, row in panel.rows.items():
            with self.subTest(key=key):
                self.assertTrue(row.toolTip() or row.editor.toolTip())

    def test_load_then_read_round_trips_every_value(self) -> None:
        """입력 위젯에 값을 불러온 뒤 다시 읽으면 같은 설정이 나오는지 검증"""
        cfg = _non_default_config()
        panel = self._panel()

        panel.load(cfg, _non_default_encode())

        self.assertEqual(panel.read(), cfg)
        self.assertEqual(
            panel.encode_values(),
            {"encode_codec": "libx264", "encode_speed_factor": 2.5, "precise_quality_mode": "all"},
        )

    def test_load_does_not_emit_changed_or_recommend_speed(self) -> None:
        """값을 불러오는 동안에는 변경 신호를 보내지 않고, 저장된 배속을 추천값으로 바꾸지 않는지 검증"""
        emitted: list[bool] = []
        panel = self._panel()
        panel.changed.connect(lambda: emitted.append(True))

        panel.load(_non_default_config(), _non_default_encode())

        self.assertEqual(emitted, [])
        self.assertEqual(self.recommend.call_count, 0)
        self.assertAlmostEqual(panel.spin_encode_speed.value(), 2.5)

    def test_changing_the_codec_applies_recommended_speed_and_saves_once(self) -> None:
        """목표 인코딩 방식을 바꾸면 추천 배속을 먼저 맞추고 변경 신호를 한 번만 보내는지 검증"""
        panel = self._panel()
        emitted: list[float] = []
        panel.changed.connect(lambda: emitted.append(panel.spin_encode_speed.value()))

        panel.combo_encode_codec.setCurrentIndex(panel.combo_encode_codec.findData("libx264"))

        self.assertEqual(emitted, [1.9])
        self.assertAlmostEqual(panel.spin_encode_speed.value(), 1.9)
        self.recommend.assert_called_with("libx264")

    def test_recommend_button_sets_the_speed_and_saves(self) -> None:
        """시스템 추천 버튼을 누르면 추천 배속을 입력하고 저장 신호를 보내는지 검증"""
        panel = self._panel()
        emitted: list[bool] = []
        panel.changed.connect(lambda: emitted.append(True))

        panel.btn_detect_encode_speed.click()

        self.assertAlmostEqual(panel.spin_encode_speed.value(), 4.5)
        self.assertEqual(emitted, [True])

    def test_output_format_description_follows_the_target_codec(self) -> None:
        """출력 형식 설명이 목표 인코딩 방식에 따라 바뀌고, 비트맵 자막 문장을 항상 포함하는지 검증"""
        panel = self._panel()
        row = panel.rows[_CONTAINER_KEY]

        panel.load(ExecutionConfig(), EncodeConfig(encode_codec="hevc_nvenc"))
        self.assertIn("현재 목표 인코딩 방식(HEVC)", row.desc)
        self.assertIn("AVI(HEVC 코덱을 표준으로 담지 못함)", row.desc)
        self.assertIn("TS(인코딩 태그를 기록할 수 없음)", row.desc)
        self.assertIn("비트맵 자막(PGS, VobSub 등)이 있는 영상은 두 방식 모두 MKV로 저장합니다", row.desc)
        self.assertIn("WMV", row.desc)

        panel.combo_encode_codec.setCurrentIndex(panel.combo_encode_codec.findData("libx264"))
        self.assertIn("현재 목표 인코딩 방식(H.264)", row.desc)
        self.assertIn("WMV(H.264 코덱을 표준으로 담지 못함)", row.desc)
        self.assertIn("AVI(인코딩 태그를 기록할 수 없음)", row.desc)
        self.assertIn("WebM(H.264 코덱을 표준으로 담지 못함)", row.desc)
        self.assertIn("비트맵 자막", row.desc)

    def test_folder_name_row_shows_only_the_one_in_use(self) -> None:
        """결과 파일 처리 방식에 따라 쓰이는 폴더 이름 행만 보이고, 원본 교체에서는 두 행 모두 숨는지 검증"""
        panel = self._panel()
        hidden = lambda: panel.hidden_row_keys  # noqa: E731

        panel.combo_result_policy.setCurrentIndex(panel.combo_result_policy.findData(RESULT_POLICY_ARCHIVE))
        self.assertFalse(panel.rows["execution.archive_dirname"].isHidden())
        self.assertTrue(panel.rows["execution.output_dirname"].isHidden())
        self.assertEqual(hidden(), {"execution.output_dirname"})

        panel.combo_result_policy.setCurrentIndex(panel.combo_result_policy.findData(RESULT_POLICY_SEPARATE))
        self.assertTrue(panel.rows["execution.archive_dirname"].isHidden())
        self.assertFalse(panel.rows["execution.output_dirname"].isHidden())
        self.assertEqual(hidden(), {"execution.archive_dirname"})

        panel.combo_result_policy.setCurrentIndex(panel.combo_result_policy.findData(RESULT_POLICY_REPLACE))
        self.assertTrue(panel.rows["execution.archive_dirname"].isHidden())
        self.assertTrue(panel.rows["execution.output_dirname"].isHidden())
        self.assertEqual(hidden(), {"execution.archive_dirname", "execution.output_dirname"})

    def test_hints_follow_the_show_hints_setting(self) -> None:
        """쉬운 설명 보기 설정에 따라 인코딩 탭의 설명 줄이 켜지고 꺼지는지 검증"""
        panel = self._panel()
        row = panel.rows[_CONTAINER_KEY]

        panel.set_hints_visible(True)
        self.assertFalse(row.lbl_hint.isHidden())

        panel.set_hints_visible(False)
        self.assertTrue(row.lbl_hint.isHidden())

    def test_baseline_label_and_criteria_link_share_one_row(self) -> None:
        """적용 화질 기준값 라벨과 판별 기준 이동 버튼이 한 줄에 나란히 놓이는지 검증"""
        panel = self._panel()
        panel.show()
        self.addCleanup(panel.close)
        _APP.processEvents()

        label_pos = panel.lbl_baseline.mapTo(panel, panel.lbl_baseline.rect().topLeft())
        button_pos = panel.btn_edit_criteria.mapTo(
            panel, panel.btn_edit_criteria.rect().topLeft()
        )

        self.assertLessEqual(abs(label_pos.y() - button_pos.y()), _SAME_ROW_TOLERANCE_PX)
        self.assertGreater(button_pos.x(), label_pos.x())

    def test_status_badge_icons_follow_the_palette(self) -> None:
        """상태 배지 아이콘이 정상은 저장 증가 색, 오류는 위험 테두리 색으로 그려지는지 검증"""
        panel = ExecutionTabPanel(
            ExecutionConfig(), EncodeConfig(), probe=_probe_returning(_usable((6, 1))), trial=_trial_returning(),
        )
        self.addCleanup(panel.close)
        # - 활성 팔레트는 OS 색상 모드를 따르므로 라이트 팔레트로 고정해 렌더링 색을 검사
        with mock.patch(
            "src.gui.settings.execution_sections.get_active_palette",
            return_value=LIGHT_PALETTE,
        ):
            panel.refresh_ffmpeg_status()

            ok_image = _icon_image(panel.btn_ffmpeg_status.icon())
            self.assertTrue(_contains_color(ok_image, LIGHT_PALETTE.fg_saved_pos))

            missing = FfmpegCapability("ffmpeg", "", None, usable=False, reason="찾을 수 없습니다")
            panel._probe = _probe_returning(missing)
            panel.refresh_ffmpeg_status()
            error_image = _icon_image(panel.btn_ffmpeg_status.icon())
            self.assertTrue(_contains_color(error_image, LIGHT_PALETTE.action_danger_border))

    def test_auto_quality_card_has_help_anchor(self) -> None:
        """자동 품질 조절 카드가 내부 기준값 앵커 도움말 버튼을 가지는지 검증"""
        panel = self._panel()
        card = _ancestor_of_type(panel.rows["execution.protect_cq_delta_max"], SettingsGroupCard)
        assert isinstance(card, SettingsGroupCard)

        self.assertEqual(card.help_anchor, "internal-thresholds")
        self.assertIsNotNone(card.btn_help)

        anchors: list[str] = []
        card.help_requested.connect(anchors.append)
        assert card.btn_help is not None
        card.btn_help.click()
        self.assertEqual(anchors, ["internal-thresholds"])

    def test_precise_quality_mode_row_is_in_the_auto_quality_card(self) -> None:
        """품질값 정밀 결정 행이 자동 품질 조절 카드 안에 있고 선택지 세 개를 그대로 노출하는지 검증"""
        panel = self._panel()
        row = panel.rows[_PRECISE_MODE_KEY]
        card = _ancestor_of_type(row, SettingsGroupCard)
        self.assertIsInstance(card, SettingsGroupCard)
        combo = panel.combo_precise_mode
        self.assertEqual(
            [(combo.itemText(index), combo.itemData(index)) for index in range(combo.count())],
            list(PRECISE_QUALITY_MODE_CHOICES),
        )

    def test_priority_and_name_conflict_are_in_the_advanced_section(self) -> None:
        """인코딩 우선순위, 같은 이름, 태그 이름, FFmpeg 경로가 고급 설정 접이식 영역 안에 있는지 검증"""
        panel = self._panel()

        for key in (
            "execution.process_priority",
            "execution.name_conflict",
            "execution.encode_tag_name",
            "execution.ffmpeg_path",
        ):
            with self.subTest(key=key):
                self.assertIsNotNone(_ancestor_of_type(panel.rows[key], CollapsibleSection))
        self.assertIsNone(
            _ancestor_of_type(panel.rows["execution.concurrent_jobs"], CollapsibleSection)
        )

    def test_usable_ffmpeg_shows_version_on_the_status_badge(self) -> None:
        """사용 가능한 FFmpeg는 상태 배지에 확인한 버전을 보여 주는지 검증"""
        panel = ExecutionTabPanel(
            ExecutionConfig(), EncodeConfig(), probe=_probe_returning(_usable((6, 1))), trial=_trial_returning(),
        )

        panel.refresh_ffmpeg_status()

        self.assertEqual(panel.btn_ffmpeg_status.text(), "사용 가능: FFmpeg 6.1")
        self.assertEqual(panel.btn_ffmpeg_status.property("statusKind"), "ok")
        self.assertFalse(panel.btn_ffmpeg_status.icon().isNull())

    def test_unusable_ffmpeg_shows_reason_and_expands_advanced(self) -> None:
        """사용할 수 없는 FFmpeg는 사유를 오류 상태로 보이고 고급 설정을 펼치는지 검증"""
        missing = FfmpegCapability(
            "ffmpeg", "", None, usable=False, reason="FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요"
        )
        panel = ExecutionTabPanel(ExecutionConfig(), EncodeConfig(), probe=_probe_returning(missing), trial=_trial_returning())

        panel.refresh_ffmpeg_status()

        self.assertEqual(panel.btn_ffmpeg_status.text(), "사용할 수 없음: FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요")
        self.assertEqual(panel.btn_ffmpeg_status.property("statusKind"), "error")
        assert panel.section_advanced is not None
        self.assertTrue(panel.section_advanced.is_expanded())

    def test_status_badge_click_rechecks_and_expands_advanced(self) -> None:
        """상태 배지를 누르면 FFmpeg를 다시 확인하고 고급 설정을 펼치는지 검증"""
        calls: list[str] = []

        def recording(path: str) -> FfmpegCapability:
            calls.append(path)
            return _usable()

        panel = ExecutionTabPanel(ExecutionConfig(), EncodeConfig(), probe=recording, trial=_trial_returning())
        panel.btn_ffmpeg_status.click()

        self.assertEqual(calls, [""])
        assert panel.section_advanced is not None
        self.assertTrue(panel.section_advanced.is_expanded())

    def test_codec_status_follows_the_encoder_list_and_the_trial_result(self) -> None:
        """목표 인코딩 방식이 목록에 있고 시험에 성공하면 사용 가능을, 목록에 없으면 목록 사유를 오류 스타일로 보여 주는지 검증"""
        capability = FfmpegCapability(
            "ffmpeg", "ffmpeg version 6.1", (6, 1), encoders=frozenset({"hevc_nvenc"}), usable=True,
        )
        panel = ExecutionTabPanel(
            ExecutionConfig(), EncodeConfig(encode_codec="hevc_nvenc"),
            probe=_probe_returning(capability), trial=_trial_returning(),
        )

        panel.refresh_ffmpeg_status()
        self.assertEqual(panel.lbl_codec_status.text(), CODEC_AVAILABLE_TEXT)
        self.assertFalse(panel.lbl_codec_status.isHidden())

        panel.combo_encode_codec.setCurrentIndex(panel.combo_encode_codec.findData("libx264"))
        self.assertEqual(panel.lbl_codec_status.text(), f"{CODEC_UNAVAILABLE_PREFIX}FFmpeg에 이 인코더가 없습니다")
        self.assertEqual(panel.lbl_codec_status.objectName(), "ErrorHintLabel")

    def test_trial_failure_shows_its_reason_in_error_style(self) -> None:
        """시험 인코딩에 실패한 목표 인코딩 방식은 시험 실패 사유를 오류 스타일로 보여 주는지 검증"""
        capability = FfmpegCapability(
            "ffmpeg", "ffmpeg version 6.1", (6, 1), encoders=frozenset({"hevc_nvenc"}), usable=True,
        )
        panel = ExecutionTabPanel(
            ExecutionConfig(), EncodeConfig(encode_codec="hevc_nvenc"),
            probe=_probe_returning(capability), trial=_trial_returning(returncode=1),
        )

        panel.refresh_ffmpeg_status()

        self.assertEqual(panel.lbl_codec_status.text(), f"{CODEC_UNAVAILABLE_PREFIX}{TRIAL_FAILED_REASON}")
        self.assertEqual(panel.lbl_codec_status.objectName(), "ErrorHintLabel")

    def test_pending_trial_shows_checking_and_updates_when_it_finishes(self) -> None:
        """시험이 끝나기 전에는 확인 중을 보이고, 끝나면 주기 확인으로 결과가 바뀌는지 검증"""
        capability = FfmpegCapability(
            "ffmpeg", "ffmpeg version 6.1", (6, 1), encoders=frozenset({"hevc_nvenc"}), usable=True,
        )
        pending_tasks: list[Callable[[], None]] = []
        trial = EncoderTrialCache(runner=lambda _command, _timeout: 0, spawn=pending_tasks.append)
        panel = ExecutionTabPanel(
            ExecutionConfig(), EncodeConfig(encode_codec="hevc_nvenc"),
            probe=_probe_returning(capability), trial=trial,
        )

        panel.refresh_ffmpeg_status()
        self.assertEqual(panel.lbl_codec_status.text(), CODEC_CHECKING_TEXT)
        self.assertFalse(panel.lbl_codec_status.isHidden())

        for task in pending_tasks:
            task()

        self.assertTrue(_wait_for(lambda: panel.lbl_codec_status.text() == CODEC_AVAILABLE_TEXT))

    def test_unusable_ffmpeg_gives_its_reason_to_the_codec_status(self) -> None:
        """FFmpeg를 쓸 수 없으면 목표 인코딩 방식 줄에도 FFmpeg 사유를 오류 스타일로 보여 주는지 검증"""
        missing = FfmpegCapability(
            "ffmpeg", "", None, usable=False, reason="FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요"
        )
        panel = ExecutionTabPanel(ExecutionConfig(), EncodeConfig(), probe=_probe_returning(missing), trial=_trial_returning())

        panel.refresh_ffmpeg_status()

        self.assertEqual(
            panel.lbl_codec_status.text(),
            f"{CODEC_UNAVAILABLE_PREFIX}FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하세요",
        )
        self.assertEqual(panel.lbl_codec_status.objectName(), "ErrorHintLabel")

    def test_speed_value_and_recommend_button_share_one_line(self) -> None:
        """기준 인코딩 배속 값과 시스템 추천 버튼이 한 가로 줄에 있고, 버튼은 아이콘만 두되 접근성 이름과 툴팁을 가지는지 검증"""
        panel = self._panel()
        spin = panel.spin_encode_speed
        button = panel.btn_detect_encode_speed
        row_box = button.parentWidget()
        assert row_box is not None
        self.assertIs(spin.parentWidget(), row_box)
        self.assertIsInstance(row_box.layout(), QHBoxLayout)
        self.assertEqual(button.text(), "")
        self.assertEqual(button.accessibleName(), "시스템 자동 감지")
        self.assertEqual(button.toolTip(), TOOLTIP_SPEED_DETECT)

    def test_advanced_cards_group_paths_auto_stop_and_run_details(self) -> None:
        """고급 설정 안 카드가 FFmpeg 경로, 자동 품질 조절, 실행 세부 순으로 놓이는지 검증"""
        panel = self._panel()
        titles: list[str] = []
        for key in (
            "execution.ffmpeg_path",
            "execution.protect_cq_delta_max",
            "execution.process_priority",
        ):
            card = _ancestor_of_type(panel.rows[key], SettingsGroupCard)
            assert isinstance(card, SettingsGroupCard)
            titles.append(card.title_text)

        self.assertEqual(
            titles,
            ["FFmpeg / FFprobe 경로", "자동 품질 조절 및 중단 기준", "실행 및 파일 세부"],
        )

    def test_ffmpeg_is_checked_only_when_the_tab_is_shown(self) -> None:
        """탭을 보이기 전에는 FFmpeg를 실행하지 않고, 보이는 순간 한 번 확인하는지 검증"""
        calls: list[str] = []

        def recording(path: str) -> FfmpegCapability:
            calls.append(path)
            return _usable()

        panel = ExecutionTabPanel(_non_default_config(), _non_default_encode(), probe=recording, trial=_trial_returning())
        self.assertEqual(calls, [])

        panel.show()

        self.assertEqual(calls, ["tools/ffmpeg.exe"])

    def test_editing_the_ffmpeg_path_rechecks_only_when_the_tab_is_visible(self) -> None:
        """FFmpeg 경로 편집이 끝났을 때 탭이 보이면 새 경로로 다시 확인하고, 숨겨진 상태면 확인하지 않는지 검증"""
        calls: list[str] = []

        def recording(path: str) -> FfmpegCapability:
            calls.append(path)
            return _usable()

        panel = ExecutionTabPanel(ExecutionConfig(), EncodeConfig(), probe=recording, trial=_trial_returning())
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
        panel = self._panel()
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


def _ancestor_of_type(widget: object, cls: type) -> object | None:
    """위젯의 부모 사슬에서 지정한 클래스의 첫 조상을 찾음"""
    parent = getattr(widget, "parentWidget", lambda: None)()
    while parent is not None:
        if isinstance(parent, cls):
            return parent
        parent = parent.parentWidget()
    return None


class ExecutionSettingsDialogTests(GuiSettingsTestCase):
    """설정 대화상자 안의 인코딩 탭 등록, 자동 저장, 목표 인코딩 방식 저장, 전체 초기화 동작을 검증"""

    def setUp(self) -> None:
        """추천 배속은 실제 시스템을 조회하지 않도록 고정 함수로 바꿈"""
        patcher = mock.patch(_RECOMMEND_TARGET, side_effect=_fake_recommend)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_execution_page_and_tab_registration(self) -> None:
        """인코딩 범주가 판별 기준 뒤, 단축키 앞에 놓이고 범주 이름이 유지되는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()

        titles = [dialog.tab_widget.tabText(i) for i in range(dialog.tab_widget.count())]

        self.assertEqual(
            titles,
            ["일반", "해상도 및 분석", "판별 기준", _TAB_TITLE, "단축키", "전체 결과"],
        )
        self.assertEqual(dialog.tab_widget.tabText(dialog.TAB_EXECUTION), _TAB_TITLE)
        self.assertEqual(dialog.tab_widget.tabText(dialog.TAB_SHORTCUTS), "단축키")

    def test_execution_tab_index_is_remembered(self) -> None:
        """인코딩 탭 번호가 탭 기억 정규화를 통과해 다음에 같은 탭으로 열리는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()

        self.assertEqual(dialog.normalize_tab_index(dialog.TAB_EXECUTION), dialog.TAB_EXECUTION)
        dialog.set_active_tab(dialog.TAB_EXECUTION)
        self.assertEqual(dialog.tab_widget.currentIndex(), dialog.TAB_EXECUTION)
        self.assertEqual(dialog._app_state.ui.layout.settings_tab, dialog.TAB_EXECUTION)

    def test_changing_an_execution_row_is_saved_to_the_state_file(self) -> None:
        """인코딩 탭의 값을 바꾸면 설정 파일에 바로 저장되는지 검증"""
        dialog, _root, state_path = self._isolated_dialog()
        combo = dialog.execution_panel.combo_result_policy

        combo.setCurrentIndex(combo.findData(RESULT_POLICY_REPLACE))

        saved = self._read_saved(state_path)
        self.assertEqual(saved["execution"]["result_policy"], RESULT_POLICY_REPLACE)

    def test_changing_the_codec_is_saved_with_the_recommended_speed(self) -> None:
        """목표 인코딩 방식 변경이 인코딩 설정 키로 저장되고 추천 배속과 함께 기록되는지 검증"""
        dialog, _root, state_path = self._isolated_dialog()
        panel = dialog.execution_panel

        panel.combo_encode_codec.setCurrentIndex(panel.combo_encode_codec.findData("libx264"))

        saved = self._read_saved(state_path)
        encode = saved["analysis"]["encode"]
        self.assertEqual(encode["encode_codec"], "libx264")
        self.assertAlmostEqual(encode["encode_speed_factor"], 1.9)

    def test_reset_defaults_restores_execution_and_encode_values(self) -> None:
        """전체 초기화가 인코딩 탭의 실행 값과 목표 인코딩 방식, 배속까지 기본값으로 되돌리는지 검증"""
        dialog, _root, _state_path = self._isolated_dialog()
        dialog.execution_panel.spin_concurrent.setValue(3)
        dialog.execution_panel.combo_encode_codec.setCurrentIndex(
            dialog.execution_panel.combo_encode_codec.findData("libx264")
        )
        self.assertEqual(dialog.execution_panel.read().concurrent_jobs, 3)

        with mock.patch.object(dialog_module.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as ask:
            dialog._on_reset_defaults_clicked()

        self.assertIn("인코딩 설정", ask.call_args.args[2])
        self.assertEqual(dialog.execution_panel.read(), ExecutionConfig())
        self.assertEqual(dialog.execution_panel.encode_values()["encode_codec"], EncodeConfig().encode_codec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
