import os
import unittest

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.exception_rules import validate_exception_token
from src.core.models import VideoProps
from src.gui.constants import THRESHOLD_BOUNDS
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.footer_bar import FooterBar
from src.gui.widgets.header_bar import HeaderBar, SplitScanButton

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIWidgetsModule(unittest.TestCase):
    """HeaderBar, FooterBar, FilterTuningBar, SplitScanButton, CustomIntegerSpinBox 위젯 동작을 검증"""

    def test_custom_integer_spinbox_interactions(self) -> None:
        """CustomIntegerSpinBox의 스텝 증감, 범위 제한(Clamping), 접미사 및 시그널 방출을 검증"""
        spin = CustomIntegerSpinBox(minimum=10, maximum=100, step=5, value=20)
        spin.setSuffix("px")

        self.assertEqual(spin.value(), 20)
        self.assertEqual(spin.suffix(), "px")
        self.assertEqual(spin.edit.text(), "20")

        # Step Up / Down
        spin.stepUp()
        self.assertEqual(spin.value(), 25)
        spin.stepDown()
        self.assertEqual(spin.value(), 20)

        # Clamping
        spin.setValue(200)
        self.assertEqual(spin.value(), 100)
        spin.setValue(0)
        self.assertEqual(spin.value(), 10)

        # 직접 텍스트 입력
        spin.edit.setText("50")
        self.assertEqual(spin.value(), 50)

    def test_split_scan_button_modes_and_signals(self) -> None:
        """SplitScanButton의 기본 모드(빠른 스캔), 모드 전환, 툴팁 및 시그널 방출을 검증"""
        split_btn = SplitScanButton()
        self.assertEqual(split_btn.current_mode, SplitScanButton.MODE_FAST)
        self.assertEqual(split_btn.btn_main.text(), "빠른 스캔")

        signals: list[str] = []
        split_btn.scan_fast_requested.connect(lambda: signals.append("fast"))
        split_btn.scan_full_requested.connect(lambda: signals.append("full"))

        # 기본 빠른 스캔 클릭
        split_btn.btn_main.click()
        self.assertEqual(signals, ["fast"])

        # 정밀 분석으로 모드 변경
        split_btn.set_mode(SplitScanButton.MODE_FULL)
        self.assertEqual(split_btn.current_mode, SplitScanButton.MODE_FULL)
        self.assertEqual(split_btn.btn_main.text(), "정밀 분석")

        # 정밀 분석 클릭
        split_btn.btn_main.click()
        self.assertEqual(signals, ["fast", "full"])

        # setEnabled 상태 전파
        split_btn.setEnabled(False)
        self.assertFalse(split_btn.btn_main.isEnabled())
        self.assertFalse(split_btn.btn_arrow.isEnabled())

    def test_header_bar_inplace_cancel_toggle(self) -> None:
        """HeaderBar의 스캔 스택이 평소에는 스플릿 버튼을, 작업 중일 때는 중단 버튼을 인플레이스 노출함을 검증"""
        hb = HeaderBar("C:/dummy_path")
        self.assertEqual(hb.scan_stack.currentIndex(), 0)
        self.assertFalse(hb.btn_cancel.isEnabled())
        self.assertTrue(hb.btn_browse.isEnabled())

        # 작업 실행 중 상태 전환
        hb.set_busy(True)
        self.assertEqual(hb.scan_stack.currentIndex(), 1)
        self.assertTrue(hb.btn_cancel.isEnabled())
        self.assertFalse(hb.btn_browse.isEnabled())
        self.assertFalse(hb.split_scan.btn_main.isEnabled())

        # 작업 완료/중단 복귀
        hb.set_busy(False)
        self.assertEqual(hb.scan_stack.currentIndex(), 0)
        self.assertFalse(hb.btn_cancel.isEnabled())
        self.assertTrue(hb.btn_browse.isEnabled())
        self.assertTrue(hb.split_scan.btn_main.isEnabled())

    def test_header_bar_action_buttons_minimal(self) -> None:
        """폴더 선택, 탐색기 열기, 환경설정 버튼이 텍스트 없이 36x36 아이콘 전용으로 구성되고 클릭 시그널이 정상 연동되는지 검증"""
        hb = HeaderBar("C:/dummy_path")
        for btn in (hb.btn_browse, hb.btn_open_explorer, hb.btn_settings):
            self.assertEqual(btn.text(), "")
            self.assertEqual(btn.width(), 36)
            self.assertEqual(btn.height(), 36)
            self.assertTrue(len(btn.toolTip()) > 0)

        clicked: list[str] = []
        hb.browse_clicked.connect(lambda: clicked.append("browse"))
        hb.open_explorer_clicked.connect(lambda: clicked.append("explorer"))
        hb.settings_clicked.connect(lambda: clicked.append("settings"))

        hb.btn_browse.click()
        hb.btn_open_explorer.click()
        hb.btn_settings.click()
        self.assertEqual(clicked, ["browse", "explorer", "settings"])

    def test_footer_bar_structure_and_inplace_progress(self) -> None:
        """푸터 바가 [상태 안내 / 프로그레스 바(작업 중에만 인플레이스 노출)] [분류 복원] [폴더 분류 이동] 구조로 동작하는지 검증"""
        fb = FooterBar()
        fb.show()

        # 1. 초기 상태: 프로그레스 바는 숨김, 상태 레이블은 빈 문자열, 버튼들은 활성화
        self.assertTrue(fb.progress_bar.isHidden())
        self.assertEqual(fb.lbl_status.text(), "")
        self.assertEqual(fb.btn_unclassify_all.text(), "분류 복원")
        self.assertEqual(fb.btn_real_classify.text(), "폴더 분류 이동")
        self.assertTrue(fb.btn_unclassify_all.isEnabled())
        self.assertTrue(fb.btn_real_classify.isEnabled())

        # 2. 작업 중(busy=True): 프로그레스 바 인플레이스 노출, 버튼 비활성화
        fb.set_busy(True, "분석 작업 진행 중...")
        self.assertFalse(fb.progress_bar.isHidden())
        self.assertFalse(fb.btn_unclassify_all.isEnabled())
        self.assertFalse(fb.btn_real_classify.isEnabled())
        self.assertEqual(fb.lbl_status.text(), "분석 작업 진행 중...")

        # 3. 작업 완료(busy=False): 프로그레스 바 다시 숨김, 버튼 재활성화
        fb.set_busy(False, "분석 완료")
        self.assertTrue(fb.progress_bar.isHidden())
        self.assertEqual(fb.progress_bar.value(), 0)
        self.assertTrue(fb.btn_unclassify_all.isEnabled())
        self.assertTrue(fb.btn_real_classify.isEnabled())
        self.assertEqual(fb.lbl_status.text(), "분석 완료")

        # 4. 실행 가능 여부에 따른 액션 버튼 개별 제어
        fb.set_action_availability(can_classify=False, can_unclassify=True)
        self.assertFalse(fb.btn_real_classify.isEnabled())
        self.assertTrue(fb.btn_unclassify_all.isEnabled())
        fb.set_action_availability(can_classify=True, can_unclassify=False)
        self.assertTrue(fb.btn_real_classify.isEnabled())
        self.assertFalse(fb.btn_unclassify_all.isEnabled())

    def test_footer_bar_signals_and_theme_switch(self) -> None:
        """푸터 바 버튼의 클릭 시그널 연동 및 테마 전환(다크/라이트) 시 아이콘 색상 갱신 검증"""
        fb = FooterBar()
        events: list[str] = []
        fb.unclassify_all_clicked.connect(lambda: events.append("unclassify"))
        fb.real_classify_clicked.connect(lambda: events.append("classify"))

        fb.btn_unclassify_all.click()
        fb.btn_real_classify.click()
        self.assertEqual(events, ["unclassify", "classify"])

        # 테마 전환 호출 검증
        fb.apply_theme(LIGHT_PALETTE)
        fb.apply_theme(DARK_PALETTE)

    def test_filter_bar_threshold_bounds_and_tokens(self) -> None:
        """FilterTuningBar의 THRESHOLD_BOUNDS 연동, 토큰 입출력 및 구문 오류 콜백 판별을 검증"""
        bar = FilterTuningBar()
        self.assertEqual(bar.spin_over_ratio.minimum(), THRESHOLD_BOUNDS["over_bitrate_ratio"][0])
        self.assertEqual(bar.spin_over_ratio.maximum(), THRESHOLD_BOUNDS["over_bitrate_ratio"][1])
        bar.set_exception_tokens(["alpha", "beta"])
        self.assertEqual(bar.get_exception_tokens(), ["alpha", "beta"])

        sample_vid = VideoProps(
            filename="sample.mp4",
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
        )
        fn_bad, err_bad = validate_exception_token("v.duration <")
        self.assertIsNone(fn_bad)
        self.assertIsNotNone(err_bad)

        fn_ok, err_ok = validate_exception_token("v.duration < 150")
        assert fn_ok is not None
        self.assertIsNone(err_ok)
        self.assertTrue(fn_ok(sample_vid))

    def test_filter_bar_sync_from_config_updates_dynamic_tooltips(self) -> None:
        """FilterTuningBar.sync_from_config() 호출 시 스핀박스 툴팁이 현재 기준 해상도/FPS/비트레이트/배수에 맞게 갱신됨을 검증"""
        bar = FilterTuningBar()
        self.assertIn("1280x720 30fps H.264 기준 > 1,800 kbps", bar.spin_over_ratio.toolTip())
        self.assertIn("기본 30.0 MB", bar.spin_small_mb.toolTip())

        from src.core.config import (
            BitrateModelConfig,
            SmallFootageConfig,
            ThresholdsConfig,
        )

        custom_cfg = ClassifierConfig(
            thresholds=ThresholdsConfig(
                over_bitrate_ratio=1.5,
                keyframe_interval_sec=3.0,
            ),
            small_footage=SmallFootageConfig(size_mb=50.0),
            bitrate_model=BitrateModelConfig(
                base_width=1920,
                base_height=1080,
                base_fps=60.0,
                base_target_kbps=2400,
            ),
        )
        bar.sync_from_config(custom_cfg)
        over_tip = bar.spin_over_ratio.toolTip()
        self.assertIn("1920x1080 60fps H.264 기준 > 3,600 kbps", over_tip)
        self.assertIn("기본 2.2초", bar.spin_kf_limit.toolTip())
        self.assertEqual(bar.spin_kf_limit.value(), 3.0)
        self.assertEqual(bar.spin_small_mb.value(), 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
