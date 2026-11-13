import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.exception_rules import validate_exception_token
from src.core.models import VideoProps
from src.gui.constants import THRESHOLD_BOUNDS
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.footer_bar import FooterBar
from src.gui.widgets.header_bar import HeaderBar, SplitScanButton
from src.gui.widgets.video_table_view import VideoTableView

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

    def test_filter_bar_exception_editor_button_and_busy_lock(self) -> None:
        """FilterTuningBar 예외 규칙 편집 버튼 시그널, 비활성 건수 표기 및 작업 중 잠금을 검증"""
        bar = FilterTuningBar()
        clicks: list[str] = []
        bar.exception_editor_requested.connect(lambda: clicks.append("open"))

        self.assertEqual(bar.btn_edit_exceptions.text(), "목록 편집...")
        bar.btn_edit_exceptions.click()
        self.assertEqual(clicks, ["open"])

        bar.set_disabled_exception_count(3)
        self.assertEqual(bar.btn_edit_exceptions.text(), "목록 편집 (비활성 3)")
        bar.set_disabled_exception_count(0)
        self.assertEqual(bar.btn_edit_exceptions.text(), "목록 편집...")

        bar.set_busy(True)
        self.assertFalse(bar.btn_edit_exceptions.isEnabled())
        bar.set_busy(False)
        self.assertTrue(bar.btn_edit_exceptions.isEnabled())

    def test_exception_rules_dialog_crud_validation_and_toggle(self) -> None:
        """ExceptionRulesDialog의 규칙 추가·중복/구문 검증·순서 이동·삭제·전체 토글을 검증"""
        dlg = ExceptionRulesDialog(["alpha", "beta"], ["gamma"])
        self.addCleanup(dlg.close)

        self.assertEqual(dlg.get_tokens(), (["alpha", "beta"], ["gamma"]))

        # 중복 규칙 및 AST 구문 오류 차단
        self.assertFalse(dlg.add_rule("ALPHA"))
        self.assertFalse(dlg.lbl_error.isHidden())
        self.assertFalse(dlg.add_rule("v.duration <"))
        self.assertFalse(dlg.lbl_error.isHidden())

        # 정상 AST 조건식 추가
        self.assertTrue(dlg.add_rule("v.duration < 60"))
        self.assertTrue(dlg.btn_apply.isEnabled())

        # 위로 이동
        dlg.rules_list.setCurrentRow(3)
        dlg.move_selected(-1)
        active, disabled = dlg.get_tokens()
        self.assertEqual(active, ["alpha", "beta", "v.duration < 60"])
        self.assertEqual(disabled, ["gamma"])

        # 전체 비활성 -> 전체 활성 전환
        dlg.set_all_enabled(False)
        self.assertEqual(dlg.get_tokens()[0], [])
        self.assertEqual(len(dlg.get_tokens()[1]), 4)
        dlg.set_all_enabled(True)
        self.assertEqual(len(dlg.get_tokens()[0]), 4)

        # 선택 항목 삭제
        dlg.rules_list.setCurrentRow(0)
        dlg.remove_selected_rule()
        self.assertEqual(len(dlg.get_tokens()[0]), 3)

    def test_video_table_view_selection_actions(self) -> None:
        """VideoTableView의 다중 선택 경로·파일명 복사, 일괄 재생 및 예외 키워드 시그널을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            v1 = VideoProps(
                filename="clip_a.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=5000,
                aud_kbps=128,
                vid_size_MB=50.0,
                duration=60.0,
                codec="h264",
                keyframe_interval=1.0,
            )
            v2 = VideoProps(
                filename="clip_b.mp4",
                width=1920,
                height=1080,
                rotate_type=0,
                fps=30.0,
                vid_kbps=5000,
                aud_kbps=128,
                vid_size_MB=60.0,
                duration=60.0,
                codec="h264",
                keyframe_interval=1.0,
            )
            for name in (v1.filename, v2.filename):
                with open(os.path.join(tmpdir, name), "wb") as f:
                    f.write(b"0")

            cfg = ClassifierConfig()
            evals = VideoEvaluator.evaluate_all(tmpdir, [v1, v2], cfg).evaluations
            curr_exc = ["clip_b"]
            view = VideoTableView(
                root_dir_provider=lambda: tmpdir,
                exceptions_provider=lambda: curr_exc,
            )
            self.addCleanup(view.close)
            view.table_model.set_data_source(evals, root_dir=tmpdir, config=cfg)
            view.selectAll()

            # 1. 파일명 및 전체 절대 경로 복사
            self.assertEqual(view.copy_selected_names(), 2)
            cb = QApplication.clipboard()
            assert cb is not None
            self.assertEqual(set(cb.text().splitlines()), {"clip_a.mp4", "clip_b.mp4"})

            self.assertEqual(view.copy_selected_paths(), 2)
            expected_paths = {
                os.path.normpath(os.path.join(tmpdir, "clip_a.mp4")),
                os.path.normpath(os.path.join(tmpdir, "clip_b.mp4")),
            }
            self.assertEqual(set(cb.text().splitlines()), expected_paths)

            # 2. 다중 선택 일괄 재생
            played: list[str] = []
            with patch(
                "os.startfile", side_effect=lambda p: played.append(p), create=True
            ):
                self.assertEqual(view.play_selected(), 2)
            self.assertEqual({os.path.normpath(p) for p in played}, expected_paths)

            # 3. 예외 키워드 추가/제거 시그널 분리
            added_single: list[str] = []
            removed: list[list[str]] = []
            view.exception_keyword_added.connect(added_single.append)
            view.exception_keywords_removed.connect(removed.append)
            self.assertEqual(view.add_selected_to_exceptions(), 1)
            self.assertEqual(view.remove_selected_from_exceptions(), 1)
            self.assertEqual(added_single, ["clip_a"])
            self.assertEqual(removed, [["clip_b"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
