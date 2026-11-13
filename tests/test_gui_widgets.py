import dataclasses
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.exception_rules import validate_exception_token
from src.core.models import VideoProps
from src.gui.constants import THRESHOLD_BOUNDS
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table_model import VideoTableModel
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox, OptionalNumberSpinBox
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

    def test_filter_header_and_column_popover_interactions(self) -> None:
        """FilterHeaderView 버튼 클릭, 타입별 ColumnFilterPopover 즉시 필터링 및 FilterTuningBar 배지 연동을 검증"""
        v1 = VideoProps(
            filename="alpha_over.mp4",
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
        v2 = VideoProps(
            filename="beta_opt.mp4",
            width=1280,
            height=720,
            rotate_type=0,
            fps=30.0,
            vid_kbps=1200,
            aud_kbps=128,
            vid_size_MB=45.0,
            duration=240.0,
            codec="hevc",
            keyframe_interval=None,
        )
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [v1, v2], cfg).evaluations
        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        # 1. 헤더 필터 아이콘 영역 클릭 시 정렬 변경 없이 filter_clicked 방출 및 우하단 배치·여백 검증
        initial_sort_col = view.filter_header.sortIndicatorSection()
        view.filter_header.setFixedHeight(34)
        btn_rect = view.filter_header.filter_button_rect(VideoTableModel.COL_CODEC)
        self.assertTrue(btn_rect.isValid())
        sec_right = (
            view.filter_header.sectionViewportPosition(VideoTableModel.COL_CODEC)
            + view.filter_header.sectionSize(VideoTableModel.COL_CODEC)
            - 1
        )
        sec_bottom = view.filter_header.height() - 1
        inner_right_gap = (sec_right - 1) - btn_rect.right()
        inner_bottom_gap = (sec_bottom - 2) - btn_rect.bottom()
        self.assertEqual(inner_right_gap, 6)
        self.assertEqual(inner_bottom_gap, inner_right_gap)
        self.assertGreater(btn_rect.center().y(), 17)
        view.filter_header.set_active_filter_columns({VideoTableModel.COL_FPS})
        view.filter_header.resizeSection(VideoTableModel.COL_FPS, 55)
        view.filter_header.repaint()
        view.filter_header.set_active_filter_columns(set())
        clicked_cols: list[int] = []
        view.filter_header.filter_clicked.connect(lambda c, _pt: clicked_cols.append(c))

        center = QPointF(btn_rect.center())
        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            center,
            view.filter_header.mapToGlobal(center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            center,
            view.filter_header.mapToGlobal(center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        view.filter_header.mousePressEvent(press)
        view.filter_header.mouseReleaseEvent(release)
        self.assertEqual(clicked_cols, [VideoTableModel.COL_CODEC])
        self.assertEqual(view.filter_header.sortIndicatorSection(), initial_sort_col)

        # 2. 범주형(코덱) 팝오버 체크 해제 즉시 반영 및 해제
        pop_codec = view.open_column_filter_popover(VideoTableModel.COL_CODEC)
        assert pop_codec is not None and pop_codec.list_values is not None
        self.assertEqual(pop_codec.list_values.count(), 2)
        second_item = pop_codec.list_values.item(1)
        assert second_item is not None
        second_item.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(view.proxy_model.rowCount(), 1)
        self.assertIn(
            VideoTableModel.COL_CODEC, view.filter_header.active_filter_columns()
        )
        pop_codec.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)
        pop_codec.close()

        # 3. 수치(KF 간격) 팝오버 미측정 전용 및 초기화 검증
        pop_kf = view.open_column_filter_popover(VideoTableModel.COL_KEYFRAME)
        assert pop_kf is not None and pop_kf.combo_null_mode is not None
        idx_null = pop_kf.combo_null_mode.findData("null_only")
        pop_kf.combo_null_mode.setCurrentIndex(idx_null)
        self.assertEqual(view.proxy_model.rowCount(), 1)
        pop_kf.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)
        pop_kf.close()

        # 4. 문자열(이름) 팝오버 즉시 적용 검증
        pop_name = view.open_column_filter_popover(VideoTableModel.COL_NAME)
        assert pop_name is not None and pop_name.edit_text_query is not None
        pop_name.edit_text_query.setText("beta")
        pop_name.apply_current()
        self.assertEqual(view.proxy_model.rowCount(), 1)
        pop_name.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)
        pop_name.close()

        # 5. FilterTuningBar 활성 열 필터 배지 버튼 표시·숨김·시그널 검증
        bar = FilterTuningBar()
        self.assertTrue(bar.btn_clear_col_filters.isHidden())
        cleared: list[bool] = []
        bar.clear_column_filters_requested.connect(lambda: cleared.append(True))
        bar.set_active_column_filter_count(2)
        self.assertFalse(bar.btn_clear_col_filters.isHidden())
        self.assertIn("2개", bar.btn_clear_col_filters.text())
        bar.btn_clear_col_filters.click()
        self.assertEqual(cleared, [True])
        bar.set_active_column_filter_count(0)
        self.assertTrue(bar.btn_clear_col_filters.isHidden())

    def test_optional_number_spinbox_interactions(self) -> None:
        """OptionalNumberSpinBox의 빈 값(None)·기준점 스텝·소수/음수·휠 증감을 검증"""
        spin = OptionalNumberSpinBox(
            decimals=2,
            step=0.5,
            default_anchor=23.98,
            placeholder="제한 없음",
        )
        spin.setSuffix("fps")
        self.assertIsNone(spin.value())
        self.assertEqual(spin.suffix(), "fps")

        emitted: list[float | None] = []
        spin.valueChanged.connect(lambda v: emitted.append(v))

        # 빈 상태에서 첫 stepUp은 default_anchor를 채운 뒤 step씩 증감
        spin.stepUp()
        self.assertEqual(spin.value(), 23.98)
        spin.stepUp()
        self.assertEqual(spin.value(), 24.48)

        wheel_down = QWheelEvent(
            QPointF(10.0, 10.0),
            QPointF(10.0, 10.0),
            QPointF(0.0, 0.0).toPoint(),
            QPointF(0.0, -120.0).toPoint(),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        spin.wheelEvent(wheel_down)
        self.assertEqual(spin.value(), 23.98)

        spin.edit.setText("-2.5")
        self.assertEqual(spin.value(), -2.5)

        spin.edit.clear()
        self.assertIsNone(spin.value())
        self.assertIsNone(emitted[-1])

        int_spin = OptionalNumberSpinBox(decimals=0, step=100.0, default_anchor=1500.0)
        int_spin.stepUp()
        self.assertEqual(int_spin.edit.text(), "1500")
        int_spin.stepUp()
        self.assertEqual(int_spin.edit.text(), "1600")

    def test_number_filter_popover_spinbox_and_bucket_list(self) -> None:
        """수치 열 팝오버의 스핀박스 범위 필터와 도메인 구간 버킷 체크리스트 결합을 검증"""
        base = VideoProps(
            filename="v1.mp4",
            width=1920,
            height=1080,
            rotate_type=0,
            fps=23.976,
            vid_kbps=5000,
            aud_kbps=128,
            vid_size_MB=80.0,
            duration=100.0,
            codec="h264",
            keyframe_interval=1.0,
        )
        v2 = dataclasses.replace(base, filename="v2.mp4", fps=29.97002997)
        v3 = dataclasses.replace(base, filename="v3.mp4", fps=30.08)
        v4 = dataclasses.replace(base, filename="v4.mp4", fps=60.0)
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [base, v2, v3, v4], cfg).evaluations

        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        pop_fps = view.open_column_filter_popover(VideoTableModel.COL_FPS)
        assert pop_fps is not None
        assert pop_fps.spin_min is not None and pop_fps.spin_max is not None
        assert pop_fps.list_values is not None

        self.assertEqual(pop_fps.spin_min.suffix(), "fps")
        self.assertEqual(pop_fps.list_values.count(), 3)
        item_0 = pop_fps.list_values.item(0)
        item_1 = pop_fps.list_values.item(1)
        assert item_0 is not None and item_1 is not None
        self.assertEqual(item_0.text(), "~24 fps (1)")
        self.assertEqual(item_1.text(), "~30 fps (2)")

        # ~60 fps 해제 시 3행만 남음
        item_60 = pop_fps.list_values.item(2)
        assert item_60 is not None
        item_60.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(view.proxy_model.rowCount(), 3)

        # 스핀박스 최소값(>= 30.0)과 버킷 선택 결합 시 30.08 fps 1행만 남음
        pop_fps.spin_min.setValue(30.0)
        pop_fps.apply_current()
        self.assertEqual(view.proxy_model.rowCount(), 1)

        pop_fps.clear_filter()
        self.assertIsNone(pop_fps.spin_min.value())
        self.assertEqual(view.proxy_model.rowCount(), 4)
        pop_fps.close()

        # VFR 고유 수치가 많아도 버킷 체크리스트는 항상 유지됨
        many = [
            dataclasses.replace(base, filename=f"m_{i}.mp4", fps=29.5 + i * 0.05)
            for i in range(25)
        ]
        many_evals = VideoEvaluator.evaluate_all("C:/dummy_dir", many, cfg).evaluations
        view.table_model.set_data_source(many_evals, root_dir="C:/dummy_dir", config=cfg)
        pop_many = view.open_column_filter_popover(VideoTableModel.COL_FPS)
        assert pop_many is not None
        assert pop_many.list_values is not None
        self.assertEqual(pop_many.list_values.count(), 1)
        first_item = pop_many.list_values.item(0)
        assert first_item is not None
        self.assertEqual(first_item.text(), "~30 fps (25)")
        pop_many.close()

    def test_popover_non_grabbing_hover_and_outside_dismiss(self) -> None:
        """필터 팝오버가 마우스 그랩 없이 타 열 호버·외부 클릭·ESC·토글 닫기를 지원하는지 검증"""
        base = VideoProps(
            filename="hover.mp4",
            width=1280,
            height=720,
            rotate_type=0,
            fps=30.0,
            vid_kbps=1200,
            aud_kbps=128,
            vid_size_MB=20.0,
            duration=60.0,
            codec="h264",
            keyframe_interval=1.0,
        )
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [base], cfg).evaluations

        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        pop_fps = view.open_column_filter_popover(VideoTableModel.COL_FPS)
        assert pop_fps is not None
        self.addCleanup(pop_fps.close)
        self.assertTrue(pop_fps.isVisible())
        self.assertIsNone(QApplication.activePopupWidget())

        # 팝오버가 열린 상태에서도 다른 열 필터 버튼 호버 이벤트가 차단되지 않음
        codec_btn = view.filter_header.filter_button_rect(VideoTableModel.COL_CODEC)
        self.assertTrue(codec_btn.isValid())
        hover_pt = QPointF(codec_btn.center())
        hover_move = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            hover_pt,
            view.filter_header.mapToGlobal(hover_pt),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        view.filter_header.mouseMoveEvent(hover_move)
        self.assertTrue(pop_fps.isVisible())

        # 동일 열 필터 아이콘 재클릭 시 닫힘 및 이벤트 소비(즉시 재오픈 방지)
        fps_btn_global = view.filter_header.filter_button_global_rect(VideoTableModel.COL_FPS)
        self.assertTrue(fps_btn_global.isValid())
        same_press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(0.0, 0.0),
            QPointF(fps_btn_global.center()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(pop_fps.eventFilter(view.filter_header, same_press))
        self.assertFalse(pop_fps.isVisible())

        # 타 영역 클릭 시 닫히되 이벤트를 통과시켜 1클릭 전환 허용
        pop_fps2 = view.open_column_filter_popover(VideoTableModel.COL_FPS)
        assert pop_fps2 is not None
        other_press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            hover_pt,
            view.filter_header.mapToGlobal(hover_pt),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(pop_fps2.eventFilter(view.filter_header, other_press))
        self.assertFalse(pop_fps2.isVisible())

        # ESC 키로 닫힘 검증
        pop_codec = view.open_column_filter_popover(VideoTableModel.COL_CODEC)
        assert pop_codec is not None
        esc_ev = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            int(Qt.Key.Key_Escape),
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(pop_codec.eventFilter(pop_codec, esc_ev))
        self.assertFalse(pop_codec.isVisible())

    def test_video_table_view_excel_idle_sort_and_two_state_toggle(self) -> None:
        """초기 상태는 화살표 없음으로 시작하고 헤더 클릭 시 엑셀식 2단 토글 및 복원이 동작함을 검증"""
        cfg = ClassifierConfig()
        samples = [
            VideoProps("clip_1.mp4", 1920, 1080, 0, 30.0, 4000, 128, 50.0, 100.0, "h264", 1.0),
            VideoProps("clip_2.mp4", 1920, 1080, 0, 30.0, 4000, 128, 200.0, 100.0, "h264", 1.0),
            VideoProps("clip_3.mp4", 1920, 1080, 0, 60.0, 4000, 128, 100.0, 100.0, "hevc", 1.0),
        ]
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", samples, cfg).evaluations

        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        # 1. 초기 아이들 상태: 정렬 화살표 없음, 적재 순서 유지
        self.assertFalse(view.filter_header.isSortIndicatorShown())
        self.assertEqual(view.filter_header.sortIndicatorSection(), -1)
        self.assertEqual(view.proxy_model.sort_stack, [])

        def _click_header_text(col: int) -> None:
            x = (
                view.filter_header.sectionViewportPosition(col)
                + view.filter_header.sectionSize(col) // 2
            )
            pt = QPointF(float(x), float(view.filter_header.height() // 2))
            for ev_type, btn, btns in (
                (
                    QMouseEvent.Type.MouseButtonPress,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                ),
                (
                    QMouseEvent.Type.MouseButtonRelease,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                ),
            ):
                ev = QMouseEvent(
                    ev_type,
                    pt,
                    view.filter_header.mapToGlobal(pt),
                    btn,
                    btns,
                    Qt.KeyboardModifier.NoModifier,
                )
                if ev_type == QMouseEvent.Type.MouseButtonPress:
                    view.filter_header.mousePressEvent(ev)
                else:
                    view.filter_header.mouseReleaseEvent(ev)

        # 2. COL_SIZE 클릭 시 오름차순 -> 재클릭 시 내림차순 -> 재클릭 시 오름차순(2단 토글)
        _click_header_text(VideoTableModel.COL_SIZE)
        self.assertTrue(view.filter_header.isSortIndicatorShown())
        self.assertEqual(view.filter_header.sortIndicatorSection(), VideoTableModel.COL_SIZE)
        self.assertEqual(view.filter_header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)

        _click_header_text(VideoTableModel.COL_SIZE)
        self.assertEqual(view.filter_header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

        _click_header_text(VideoTableModel.COL_SIZE)
        self.assertEqual(view.filter_header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)

        # 3. 다른 열(COL_FPS) 클릭 후 기본 정렬 복원 호출 시 화살표 제거 및 원본 순서 복귀
        _click_header_text(VideoTableModel.COL_FPS)
        self.assertEqual(view.filter_header.sortIndicatorSection(), VideoTableModel.COL_FPS)
        view.reset_table_sort()
        self.assertFalse(view.filter_header.isSortIndicatorShown())
        self.assertEqual(view.filter_header.sortIndicatorSection(), -1)
        self.assertEqual(view.proxy_model.sort_stack, [])
        self.assertEqual(
            view.proxy_model.data(view.proxy_model.index(0, VideoTableModel.COL_NAME)),
            "clip_1.mp4",
        )

    def test_video_table_view_column_reorder_and_hover_paint(self) -> None:
        """열 드래그 순서 변경, 필터 버튼 좌표 추적, 열 복원 및 호버 페인팅 안정성을 검증"""
        cfg = ClassifierConfig()
        sample = VideoProps("clip_1.mp4", 1920, 1080, 0, 30.0, 4000, 128, 50.0, 100.0, "h264", 1.0)
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [sample], cfg).evaluations

        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        header = view.filter_header
        self.assertTrue(header.sectionsMovable())

        # 1. 열 순서 이동 후 필터 버튼 영역이 새 시각 위치를 추적함을 검증
        before_rect = header.filter_button_rect(VideoTableModel.COL_CODEC)
        header.moveSection(
            header.visualIndex(VideoTableModel.COL_CODEC),
            header.visualIndex(VideoTableModel.COL_NAME),
        )
        self.assertEqual(
            header.visualIndex(VideoTableModel.COL_CODEC),
            1,
        )
        after_rect = header.filter_button_rect(VideoTableModel.COL_CODEC)
        self.assertNotEqual(before_rect.left(), after_rect.left())
        self.assertEqual(
            after_rect.right(),
            header.sectionViewportPosition(VideoTableModel.COL_CODEC)
            + header.sectionSize(VideoTableModel.COL_CODEC)
            - 1
            - 1
            - 6,
        )

        # 2. 호버 및 활성 필터 상태 헤더 리페인트 시 예외 없이 렌더링됨을 검증
        hover_pt = QPointF(
            float(
                header.sectionViewportPosition(VideoTableModel.COL_CODEC)
                + header.sectionSize(VideoTableModel.COL_CODEC) // 2
            ),
            float(header.height() // 2),
        )
        hover_ev = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            hover_pt,
            header.mapToGlobal(hover_pt),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        header.mouseMoveEvent(hover_ev)
        header.repaint()
        header.set_active_filter_columns({VideoTableModel.COL_CODEC})
        header.repaint()

        # 3. 기본 열 복원 시 논리 열 순서까지 초기 상태로 복구됨을 검증
        view.reset_columns()
        self.assertEqual(
            header.visualIndex(VideoTableModel.COL_CODEC),
            VideoTableModel.COL_CODEC,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
