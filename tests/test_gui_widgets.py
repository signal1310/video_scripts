"""GUI 위젯 모듈 단위 테스트"""

import dataclasses
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMessageBox,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.exception_rules import validate_exception_token
from src.core.models import VideoProps
from src.gui.constants import make_ui_font
from src.gui.delegates import ClipTextDelegate
from src.gui.dialogs.exception_rules_dialog import ExceptionRulesDialog
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table_model import VideoTableModel
from src.gui.widgets.column_filter_popover import ColumnFilterPopover
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox, OptionalNumberSpinBox
from src.gui.widgets.filter_bar import FilterTuningBar
from src.gui.widgets.folder_path_button import fit_path_items, split_path_parts
from src.gui.widgets.footer_bar import FooterBar
from src.gui.widgets.header_bar import HeaderBar, SplitAnalyzeButton
from src.gui.widgets.video_table_view import VideoTableView
from tests._factories import make_video

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIWidgetsModule(unittest.TestCase):
    """HeaderBar, FooterBar, FilterTuningBar, SplitAnalyzeButton, CustomIntegerSpinBox 위젯 동작을 검증"""

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

    def test_split_analyze_button_modes_and_signals(self) -> None:
        """SplitAnalyzeButton의 기본 모드(빠른 스캔), 모드 전환, 툴팁 및 시그널 방출을 검증"""
        split_btn = SplitAnalyzeButton()
        self.assertEqual(split_btn.current_mode, SplitAnalyzeButton.MODE_FAST)
        self.assertEqual(split_btn.btn_main.text(), "빠른 분석")

        signals: list[str] = []
        split_btn.analyze_fast_requested.connect(lambda: signals.append("fast"))
        split_btn.analyze_full_requested.connect(lambda: signals.append("full"))

        # 기본 빠른 스캔 클릭
        split_btn.btn_main.click()
        self.assertEqual(signals, ["fast"])

        # 정밀 분석으로 모드 변경
        split_btn.set_mode(SplitAnalyzeButton.MODE_FULL)
        self.assertEqual(split_btn.current_mode, SplitAnalyzeButton.MODE_FULL)
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
        self.assertEqual(hb.analyze_stack.currentIndex(), 0)
        self.assertFalse(hb.btn_cancel.isEnabled())
        self.assertTrue(hb.btn_browse.isEnabled())

        # 작업 실행 중 상태 전환
        hb.set_busy(True)
        self.assertEqual(hb.analyze_stack.currentIndex(), 1)
        self.assertTrue(hb.btn_cancel.isEnabled())
        self.assertFalse(hb.btn_browse.isEnabled())
        self.assertFalse(hb.btn_open_explorer.isEnabled())
        self.assertFalse(hb.split_analyze.btn_main.isEnabled())

        # 작업 완료/중단 복귀
        hb.set_busy(False)
        self.assertEqual(hb.analyze_stack.currentIndex(), 0)
        self.assertFalse(hb.btn_cancel.isEnabled())
        self.assertTrue(hb.btn_browse.isEnabled())
        self.assertTrue(hb.btn_open_explorer.isEnabled())
        self.assertTrue(hb.split_analyze.btn_main.isEnabled())

    def test_header_bar_folder_buttons_emit_signals(self) -> None:
        """작업 폴더 선택, 탐색기 열기 버튼은 36x36 아이콘 전용이고 기존 시그널을 내는지 검증"""
        hb = HeaderBar("C:/dummy_path")
        for btn in (hb.btn_browse, hb.btn_open_explorer):
            self.assertEqual(btn.text(), "")
            self.assertEqual(btn.width(), 36)
            self.assertEqual(btn.height(), 36)
            self.assertTrue(len(btn.toolTip()) > 0)

        clicked: list[str] = []
        hb.browse_clicked.connect(lambda: clicked.append("browse"))
        hb.open_explorer_clicked.connect(lambda: clicked.append("explorer"))

        hb.btn_browse.click()
        hb.btn_open_explorer.click()
        self.assertEqual(clicked, ["browse", "explorer"])

    def test_split_path_parts_and_fit_keeps_leaf_when_narrow(self) -> None:
        """경로 구성 요소 분리와 폭 부족 시 앞쪽만 말줄임 처리되고 폴더 이름은 남는지 검증"""
        self.assertEqual(split_path_parts(os.path.join("base", "videos")), ["base", "videos"])

        font = QFont()
        normal = QFontMetrics(font)
        bold = QFontMetrics(QFont(font))
        parts = ["C:", "users", "videos", "leaf"]
        full = fit_path_items(parts, 10_000, normal, bold)
        self.assertEqual([item.text for item in full if not item.is_separator], parts)

        leaf_width = bold.horizontalAdvance("leaf")
        narrow = fit_path_items(parts, leaf_width + 1, normal, bold)
        texts = [item.text for item in narrow if not item.is_separator]
        self.assertEqual(texts, ["…", "leaf"])
        self.assertTrue(narrow[0].text == "…" and narrow[1].is_separator)

    def test_path_input_font_puts_latin_family_first(self) -> None:
        """경로 입력 글꼴이 역슬래시를 원화 기호로 그리지 않는 라틴 글꼴을 첫 순위로 두는지 검증"""
        header = HeaderBar("committed_dir")
        self.assertEqual(header.path_edit.font().families()[0], "Segoe UI")

    def test_header_path_input_accepts_won_sign_as_separator(self) -> None:
        """한국어 키보드에서 입력된 원화 기호가 경로 구분자로 바뀌어 확정되는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            header = HeaderBar("committed_dir")
            submitted: list[str] = []
            header.path_submitted.connect(submitted.append)
            header.path_edit.setText(tmpdir.replace("\\", "₩"))
            header.path_edit.returnPressed.emit()
            self.assertEqual(submitted, [tmpdir])

    def test_header_folder_icon_stays_visible_while_editing_path(self) -> None:
        """경로 입력 중에도 폴더 아이콘이 스택 바깥에 남아 보이는지 검증"""
        header = HeaderBar("C:/dummy_path")
        header.show()
        self.assertFalse(header.folder_icon.pixmap().isNull())
        header.begin_path_edit()
        self.assertIs(header.path_stack.currentWidget(), header.path_edit)
        self.assertTrue(header.folder_icon.isVisible())
        header.close()

    def test_header_folder_name_toggles_path_edit(self) -> None:
        """폴더 이름을 클릭하면 경로 입력으로 바뀌고, Esc 취소와 Enter 제출 뒤에는 다시 폴더 이름으로 돌아가는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            header = HeaderBar(tmpdir)
            self.assertEqual(header.folder_name_btn.path, tmpdir)
            self.assertIs(header.path_stack.currentWidget(), header.folder_name_btn)

            header.folder_name_btn.click()
            self.assertIs(header.path_stack.currentWidget(), header.path_edit)

            header.path_edit.setText("typed_dir")
            escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            header.path_edit.keyPressEvent(escape)
            self.assertIs(header.path_stack.currentWidget(), header.folder_name_btn)
            self.assertEqual(header.path_edit.text(), tmpdir)

            header.begin_path_edit()
            header.path_edit.returnPressed.emit()
            self.assertIs(header.path_stack.currentWidget(), header.folder_name_btn)

    def test_footer_bar_structure_and_inplace_progress(self) -> None:
        """푸터 바가 [상태 안내 / 프로그레스 바(작업 중에만 인플레이스 노출)] [분류 복원] [폴더 분류 이동] 구조로 동작하는지 검증"""
        fb = FooterBar()
        fb.show()

        # 1. 초기 상태: 요약, 상태, 프로그레스 바 모두 숨김, 버튼들은 활성화
        self.assertEqual(fb.lbl_summary.text(), "")
        self.assertTrue(fb.lbl_summary.isHidden())
        self.assertFalse(hasattr(fb, "btn_clear_selection"))
        self.assertTrue(fb.lbl_status.isHidden())
        self.assertTrue(fb.progress_bar.isHidden())
        self.assertEqual(fb.lbl_status.text(), "")
        self.assertEqual(fb.btn_unclassify_all.text(), "작업 폴더로 복원")
        self.assertEqual(fb.btn_real_classify.text(), "분류 폴더로 이동")
        self.assertTrue(fb.btn_unclassify_all.isEnabled())
        self.assertTrue(fb.btn_real_classify.isEnabled())

        from src.gui.footer_summary import (
            EMPTY_FOOTER_SUMMARY,
            FooterSummaryGroup,
            FooterSummarySpec,
        )

        fb.set_summary_spec(
            FooterSummarySpec(
                groups=(
                    FooterSummaryGroup("선택 2개", (("용량", "120.0 MB"), ("예상 시간", "1분"))),
                ),
                plain_text="선택 2개, 용량 120.0 MB, 예상 시간 1분",
                tooltip="범위 요약",
            )
        )
        self.assertFalse(fb.lbl_summary.isHidden())
        self.assertNotIn("|", fb.lbl_summary.text())
        self.assertIn(",&nbsp;", fb.lbl_summary.text())
        self.assertIn("선택 2개", fb.lbl_summary.text())
        self.assertNotIn("<b>", fb.lbl_summary.text())
        self.assertNotIn("<table", fb.lbl_summary.text())

        # 필터와 선택이 함께 있으면 반대쪽 건수만 앞에 두고 기본은 선택 상세를 표시하며, 글씨 클릭 시 전환
        fb.set_summary_spec(
            FooterSummarySpec(
                groups=(
                    FooterSummaryGroup("표시 5개", (("용량", "1.0 GB"),)),
                    FooterSummaryGroup("선택 2개", (("용량", "120.0 MB"),)),
                ),
                plain_text="표시 5개, 용량 1.0 GB / 선택 2개, 용량 120.0 MB",
                tooltip="범위 요약",
            )
        )
        self.assertFalse(fb.lbl_summary.isHidden())
        self.assertNotIn("<table", fb.lbl_summary.text())
        self.assertEqual(fb.lbl_summary.text().count("용량"), 1)
        self.assertIn("표시 5개", fb.lbl_summary.text())
        self.assertIn("선택 2개", fb.lbl_summary.text())
        self.assertIn("120.0 MB", fb.lbl_summary.text())

        click_pt = QPointF(4.0, 4.0)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            click_pt,
            fb.lbl_summary.mapToGlobal(click_pt.toPoint()).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(fb.lbl_summary, press)
        self.assertEqual(fb.lbl_summary.text().count("용량"), 1)
        self.assertIn("1.0 GB", fb.lbl_summary.text())

        QApplication.sendEvent(fb.lbl_summary, press)
        self.assertIn("120.0 MB", fb.lbl_summary.text())

        fb.set_summary_spec(EMPTY_FOOTER_SUMMARY)
        self.assertTrue(fb.lbl_summary.isHidden())

        # 2. 작업 중(busy=True, 상태 문구 없음 포함): 프로그레스 바 노출, 버튼 크기 확장 없음 검증
        fb.resize(900, 48)
        fb.set_busy(True, "")
        QApplication.processEvents()
        self.assertFalse(fb.progress_bar.isHidden())
        self.assertLess(fb.btn_unclassify_all.width(), 220)
        self.assertLess(fb.btn_real_classify.width(), 220)
        fb.set_busy(True, "분석 작업 진행 중...")
        self.assertFalse(fb.progress_bar.isHidden())
        self.assertFalse(fb.lbl_status.isHidden())
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
        fb.set_status_text("")
        self.assertTrue(fb.lbl_status.isHidden())

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
        """FilterTuningBar의 기준 요약 배지, 중복 조정 버튼 제거, 예외 토큰 입출력 및 구문 오류 판별을 검증"""
        bar = FilterTuningBar()
        self.assertFalse(hasattr(bar, "btn_open_tuner"))
        self.assertFalse(hasattr(bar, "spin_over_ratio"))

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

    def test_filter_bar_exception_editor_button_and_single_row_layout(self) -> None:
        """FilterTuningBar 1줄 구성, 예외 목록 아이콘, 건수 배지, 툴팁, 시그널 및 작업 중 잠금을 검증"""
        bar = FilterTuningBar()
        self.assertFalse(hasattr(bar, "edit_exceptions"))
        self.assertFalse(hasattr(bar, "lbl_criteria_summary"))

        clicks: list[str] = []
        changed: list[str] = []
        bar.exception_editor_requested.connect(lambda: clicks.append("open"))
        bar.exceptions_changed.connect(lambda: changed.append("changed"))

        self.assertEqual(bar.btn_edit_exceptions.text(), "예외 규칙")
        self.assertFalse(bar.btn_edit_exceptions.icon().isNull())
        bar.btn_edit_exceptions.click()
        self.assertEqual(clicks, ["open"])

        bar.set_exception_tokens(["@hevc", "v.duration < 10"])
        self.assertEqual(changed, ["changed"])
        self.assertEqual(bar.btn_edit_exceptions.text(), "예외 규칙 (2)")
        self.assertIn("@hevc", bar.btn_edit_exceptions.toolTip())

        bar.set_disabled_exception_count(1)
        self.assertEqual(bar.btn_edit_exceptions.text(), "예외 규칙 (2, 비활성 1)")

        bar.sync_filter_state(search="clip", exception_tokens=[])
        self.assertEqual(len(changed), 1)
        self.assertEqual(bar.btn_edit_exceptions.text(), "예외 규칙 (비활성 1)")

        bar.set_disabled_exception_count(0)
        self.assertEqual(bar.btn_edit_exceptions.text(), "예외 규칙")

        bar.set_busy(True)
        self.assertFalse(bar.btn_edit_exceptions.isEnabled())
        bar.set_busy(False)
        self.assertTrue(bar.btn_edit_exceptions.isEnabled())

    def test_bento_dashboard_four_cards_and_savings_metrics(self) -> None:
        """BentoDashboard 4카드 구성, 작업 분량, 효율 제외 집계 표기 및 초기화를 검증"""
        from src.core.models import QualityZone, SavingsSummary, SmartAction, SummaryReport, VideoEvaluation
        from src.gui.widgets.bento_dashboard import BentoDashboard

        dash = BentoDashboard()
        self.assertEqual(dash.card_files.value_label.text(), "0개")
        self.assertEqual(dash.card_workload.value_label.text(), "0분")
        self.assertEqual(dash.card_excluded.value_label.text(), "0분 절약")

        v_enc = VideoProps("enc.mp4", 1280, 720, 0, 30.0, 3000, 128, 100.0, 120.0, "h264", 1.0)
        v_low = VideoProps("low.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 600.0, "h264", 1.0)
        report = SummaryReport(
            root_dir="C:/dummy",
            total_count=2,
            encoded_count=1,
            passed_count=1,
            total_orig_mb=120.0,
            total_expected_after_mb=70.0,
            gross_saved_mb=50.0,
            keyframe_increase_mb=0.0,
            net_saved_mb=50.0,
            savings_summary=SavingsSummary(
                min_saved_mb=5.0,
                min_mb_per_min=3.0,
                exempt_saved_mb=50.0,
                encode_duration_sec=120.0,
                low_saved_count=0,
                low_per_min_count=1,
                excluded_over_duration_sec=600.0,
                excluded_over_potential_saved_mb=10.0,
            ),
            evaluations=[
                VideoEvaluation(
                    video=v_enc,
                    optimal_kbps=1500,
                    bitrate_ratio=2.0,
                    quality_zone=QualityZone.OVER,
                    is_keyframe_exceeded=False,
                    expected_after_mb=50.0,
                    expected_delta_mb=50.0,
                    smart_action=SmartAction.ENCODE_CQ22_OVER,
                    target_dirname="_1_표준인코딩_CQ22",
                ),
                VideoEvaluation(
                    video=v_low,
                    optimal_kbps=1500,
                    bitrate_ratio=2.0,
                    quality_zone=QualityZone.OVER,
                    is_keyframe_exceeded=False,
                    expected_after_mb=20.0,
                    expected_delta_mb=0.0,
                    smart_action=SmartAction.PASS_LOW_PER_MIN,
                    target_dirname=None,
                ),
            ],
        )
        dash.update_from_report(report)
        self.assertEqual(dash.card_files.value_label.text(), "총 2개")
        self.assertEqual(dash.card_workload.value_label.text(), "2분")
        self.assertIn("25.0 MB/분", dash.card_workload.sub_label.text())
        self.assertEqual(dash.card_excluded.value_label.text(), "10분 절약")
        self.assertIn("제외분 절약 가능", dash.card_excluded.sub_label.text())
        self.assertIn("분당 미달: 1개", dash.card_excluded.toolTip())

        dash.reset()
        self.assertEqual(dash.card_workload.value_label.text(), "0분")

    def test_exception_rules_dialog_crud_validation_and_toggle(self) -> None:
        """ExceptionRulesDialog의 규칙 추가, 중복/구문 검증, 순서 이동, 삭제, 전체 토글을 검증"""
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
        """VideoTableView의 다중 선택 경로, 파일명 복사, 일괄 재생 및 예외 규칙 시그널을 검증"""
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

            # 3. 예외 규칙 추가/제거 시그널 분리
            added_single: list[str] = []
            removed: list[list[str]] = []
            view.exception_keyword_added.connect(added_single.append)
            view.exception_keywords_removed.connect(removed.append)
            self.assertEqual(view.add_selected_to_exceptions(), 1)
            self.assertEqual(view.remove_selected_from_exceptions(), 1)
            self.assertEqual(added_single, ["clip_a"])
            self.assertEqual(removed, [["clip_b"]])

    def test_video_table_view_name_width_measured_once_per_data_load(self) -> None:
        """이름 열 내용 너비는 데이터 로드당 1회만 측정하고 리사이즈 때는 재측정하지 않음을 검증"""
        videos = [
            VideoProps(
                filename=f"v{i}.mp4",
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
            for i in range(50)
        ]
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("x", videos, cfg).evaluations
        view = VideoTableView()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="x", config=cfg)
        _APP.processEvents()

        with patch.object(
            view.table_model, "index", wraps=view.table_model.index
        ) as index_spy:
            view.resize(900, 400)
            view.fit_name_column_to_contents(force=True)
            view.fit_name_column_to_contents(force=True)
            self.assertEqual(index_spy.call_count, 0)

        view.table_model.set_data_source(evals[:10], root_dir="x", config=cfg)
        _APP.processEvents()
        with patch.object(
            view.table_model, "index", wraps=view.table_model.index
        ) as index_spy:
            view.fit_name_column_to_contents(force=True)
            self.assertEqual(index_spy.call_count, 0)

    def test_default_column_order_puts_decision_columns_first(self) -> None:
        """기본 시각 순서가 이름, 권장 작업, 분류 상태, 절감 열 순으로 시작하고 모든 열을 한 번씩 포함하는지 검증"""
        view = VideoTableView()
        self.addCleanup(view.close)
        order = VideoTableView.DEFAULT_COL_ORDER
        self.assertEqual(sorted(order), list(range(len(VideoTableModel.HEADERS))))
        self.assertEqual(
            order[:3],
            (VideoTableModel.COL_NAME, VideoTableModel.COL_ACTION, VideoTableModel.COL_STATUS),
        )
        header = view.filter_header
        self.assertEqual(
            [header.logicalIndex(v) for v in range(header.count())], list(order)
        )

    def test_default_columns_show_content_without_ellipsis(self) -> None:
        """기본 창 크기에서 표시되는 열이 샘플 행의 표시 텍스트를 말줄임 없이 담는지 검증"""
        cfg = ClassifierConfig()
        videos = [
            VideoProps(
                filename=name,
                width=width,
                height=height,
                rotate_type=0,
                fps=fps,
                vid_kbps=kbps,
                aud_kbps=128,
                vid_size_MB=size,
                duration=duration,
                codec=codec,
                keyframe_interval=kf,
            )
            for name, width, height, fps, kbps, size, duration, codec, kf in (
                ("short.mp4", 1280, 720, 24.0, 1500, 20.0, 45.0, "h264", 1.0),
                ("gameplay_clip_long_name_0123.mp4", 3840, 2160, 60.0, 35000, 2563.5, 1800.0, "hevc", 10.0),
                ("mid_length_clip.mp4", 1920, 1080, 30.0, 9000, 300.0, 600.0, "vp9", 5.0),
            )
        ]
        evals = VideoEvaluator.evaluate_all("x", videos, cfg).evaluations
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        original_font = app.font()
        app.setFont(make_ui_font())
        self.addCleanup(app.setFont, original_font)
        view = VideoTableView(root_dir_provider=lambda: "x")
        self.addCleanup(view.close)
        view.resize(1280, 820)
        view.show()
        view.table_model.set_data_source(evals, root_dir="x", config=cfg)
        QApplication.processEvents()

        bold_font = QFont(view.font())
        bold_font.setBold(True)
        bold_metrics = QFontMetrics(bold_font)
        for col in range(len(VideoTableModel.HEADERS)):
            if col == VideoTableModel.COL_NAME or view.isColumnHidden(col):
                continue
            for row in range(view.table_model.rowCount()):
                index = view.table_model.index(row, col)
                text = index.data(Qt.ItemDataRole.DisplayRole)
                if not text:
                    continue
                cell_font = index.data(Qt.ItemDataRole.FontRole)
                cell_metrics = QFontMetrics(
                    cell_font if isinstance(cell_font, QFont) else view.font()
                )
                needed = max(
                    bold_metrics.horizontalAdvance(str(text)),
                    cell_metrics.horizontalAdvance(str(text)),
                )
                # QSS 셀 패딩 좌우 16px과 델리게이트 텍스트 여백을 합한 최소 여유
                with self.subTest(column=col, row=row, text=str(text)):
                    self.assertLessEqual(needed + 22, view.columnWidth(col))

    def test_video_table_view_bulk_play_requires_confirmation(self) -> None:
        """일괄 재생 건수가 상한을 넘으면 확인 후에만 실행하는지 검증"""
        count = VideoTableView.BULK_PLAY_CONFIRM_THRESHOLD + 1
        with tempfile.TemporaryDirectory() as tmpdir:
            videos = [
                VideoProps(
                    filename=f"clip_{i:03d}.mp4",
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
                for i in range(count)
            ]
            for v in videos:
                with open(os.path.join(tmpdir, v.filename), "wb") as f:
                    f.write(b"0")
            cfg = ClassifierConfig()
            evals = VideoEvaluator.evaluate_all(tmpdir, videos, cfg).evaluations
            view = VideoTableView(root_dir_provider=lambda: tmpdir)
            self.addCleanup(view.close)
            view.table_model.set_data_source(evals, root_dir=tmpdir, config=cfg)
            view.selectAll()

            played: list[str] = []
            no = QMessageBox.StandardButton.No
            yes = QMessageBox.StandardButton.Yes
            with patch(
                "os.startfile", side_effect=lambda p: played.append(p), create=True
            ):
                with patch.object(QMessageBox, "question", return_value=no):
                    self.assertEqual(view.play_selected(), 0)
                self.assertEqual(played, [])
                with patch.object(QMessageBox, "question", return_value=yes):
                    self.assertEqual(view.play_selected(), count)
                self.assertEqual(len(played), count)

    def test_icon_only_buttons_expose_accessible_names(self) -> None:
        """아이콘 전용 버튼이 텍스트 없이 접근성 이름을 가지는지 검증"""
        header = HeaderBar()
        footer = FooterBar()
        for btn in (
            header.btn_browse,
            header.btn_open_explorer,
            header.split_analyze.btn_arrow,
            footer.btn_logs,
        ):
            with self.subTest(button=btn.objectName()):
                self.assertFalse(btn.text())
                self.assertTrue(btn.accessibleName())

    def test_header_path_edit_is_editable_and_locked_while_busy(self) -> None:
        """작업 폴더 경로를 직접 입력해 제출할 수 있고 작업 중에는 잠기는지 검증"""
        header = HeaderBar("committed_dir")
        self.assertFalse(header.path_edit.isReadOnly())
        with tempfile.TemporaryDirectory() as tmpdir:
            submitted: list[str] = []
            header.path_submitted.connect(submitted.append)
            header.path_edit.setText(f"  {tmpdir}  ")
            header.path_edit.returnPressed.emit()
            self.assertEqual(submitted, [tmpdir])

        header.set_busy(True)
        self.assertFalse(header.path_edit.isEnabled())
        header.set_busy(False)
        self.assertTrue(header.path_edit.isEnabled())

    def test_header_path_edit_rejects_missing_dir_and_keeps_input(self) -> None:
        """존재하지 않는 경로는 제출하지 않고 거부 안내와 함께 입력을 남기며 확정 경로는 유지하는지 검증"""
        header = HeaderBar("committed_dir")
        submitted: list[str] = []
        rejected: list[str] = []
        header.path_submitted.connect(submitted.append)
        header.path_rejected.connect(rejected.append)

        header.path_edit.setText("missing_dir_for_test")
        header.path_edit.returnPressed.emit()
        self.assertEqual(submitted, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(header.path_edit.text(), "missing_dir_for_test")
        self.assertEqual(header.path_edit.property("invalid"), "true")
        self.assertEqual(header.committed_path, "committed_dir")

    def test_header_path_edit_empty_input_reverts_silently(self) -> None:
        """빈 입력은 거부 안내 없이 확정 경로로 되돌리는지 검증"""
        header = HeaderBar("committed_dir")
        rejected: list[str] = []
        header.path_rejected.connect(rejected.append)
        header.path_edit.setText("   ")
        header.path_edit.returnPressed.emit()
        self.assertEqual(rejected, [])
        self.assertEqual(header.path_edit.text(), "committed_dir")

    def test_header_path_edit_escape_cancels_and_focus_out_keeps_uncommitted_input(self) -> None:
        """Esc 입력은 확정하지 않은 경로 입력을 버리고, 포커스 이탈은 입력을 남기되 제출하지 않음을 검증"""
        header = HeaderBar("committed_dir")
        submitted: list[str] = []
        header.path_submitted.connect(submitted.append)

        header.path_edit.setText("typed_dir")
        escape = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        header.path_edit.keyPressEvent(escape)
        self.assertEqual(header.path_edit.text(), "committed_dir")

        header.path_edit.setText("typed_dir")
        header.path_edit.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))
        self.assertEqual(header.path_edit.text(), "typed_dir")
        self.assertEqual(submitted, [])

    def test_header_set_path_emits_committed_path_changed(self) -> None:
        """확정 경로가 바뀌면 committed_path_changed가 새 경로와 함께 방출되어 창 제목 갱신에 쓰이는지 검증"""
        header = HeaderBar("")
        changed: list[str] = []
        header.committed_path_changed.connect(changed.append)
        header.set_path("new_dir")
        self.assertEqual(changed, ["new_dir"])

    def test_header_committed_path_follows_set_path(self) -> None:
        """set_path 호출 시 확정 경로와 입력창 텍스트가 함께 갱신되고 restore가 그 값을 쓰는지 검증"""
        header = HeaderBar("committed_dir")
        header.path_edit.setText("typed_dir")
        header.restore_committed_path()
        self.assertEqual(header.path_edit.text(), "committed_dir")

        header.set_path("next_dir")
        self.assertEqual(header.committed_path, "next_dir")
        self.assertEqual(header.path_edit.text(), "next_dir")

    def _two_video_table(self) -> VideoTableView:
        """h264(키프레임 1.0초)와 hevc(키프레임 미측정) 두 영상을 적재한 표시 상태의 테이블 뷰를 반환"""
        alpha = make_video(filename="alpha_over.mp4", fps=60.0, vid_kbps=9500, aud_kbps=192, vid_size_MB=120.0, duration=100.0)
        beta = make_video(
            filename="beta_opt.mp4",
            width=1280,
            height=720,
            vid_kbps=1200,
            vid_size_MB=45.0,
            duration=240.0,
            codec="hevc",
            keyframe_interval=None,
        )
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [alpha, beta], cfg).evaluations
        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)
        return view

    def test_header_filter_button_sits_bottom_right_and_click_keeps_sort(self) -> None:
        """헤더 필터 아이콘이 우하단 여백 6px에 배치되고, 클릭 시 정렬 변경 없이 filter_clicked만 방출됨을 검증"""
        view = self._two_video_table()
        header = view.filter_header
        initial_sort_col = header.sortIndicatorSection()
        header.setFixedHeight(34)
        _APP.processEvents()
        col = VideoTableModel.COL_CODEC
        rect = header.filter_button_rect(col)
        self.assertTrue(rect.isValid())
        section_right = header.sectionViewportPosition(col) + header.sectionSize(col) - 1
        right_gap = (section_right - 1) - rect.right()
        bottom_gap = (header.height() - 1 - 2) - rect.bottom()
        self.assertEqual(right_gap, 6)
        self.assertEqual(bottom_gap, right_gap)
        self.assertGreater(rect.center().y(), 17)

        header.set_active_filter_columns({VideoTableModel.COL_FPS})
        header.resizeSection(VideoTableModel.COL_FPS, 55)
        header.repaint()
        header.set_active_filter_columns(set())

        clicked: list[int] = []
        header.filter_clicked.connect(lambda c, _pt: clicked.append(c))
        center = QPointF(rect.center())
        global_center = header.mapToGlobal(center)
        header.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                center,
                global_center,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        header.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                center,
                global_center,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(clicked, [col])
        self.assertEqual(header.sortIndicatorSection(), initial_sort_col)

    def test_category_popover_unchecking_value_filters_rows_immediately(self) -> None:
        """범주형(코덱) 팝오버의 체크 해제가 즉시 행과 헤더 활성 표시에 반영되고 초기화로 복원됨을 검증"""
        view = self._two_video_table()
        popover = view.open_column_filter_popover(VideoTableModel.COL_CODEC)
        assert popover is not None and popover.list_values is not None
        self.addCleanup(popover.close)
        self.assertEqual(popover.list_values.count(), 2)
        item = popover.list_values.item(1)
        assert item is not None
        item.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(view.proxy_model.rowCount(), 1)
        self.assertIn(VideoTableModel.COL_CODEC, view.filter_header.active_filter_columns())

        popover.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)

    def test_number_popover_null_only_filters_unmeasured_rows(self) -> None:
        """수치(키프레임) 팝오버의 미측정 전용 모드가 해당 행만 남기고 초기화로 복원됨을 검증"""
        view = self._two_video_table()
        popover = view.open_column_filter_popover(VideoTableModel.COL_KEYFRAME)
        assert popover is not None and popover.combo_null_mode is not None
        self.addCleanup(popover.close)
        popover.combo_null_mode.setCurrentIndex(popover.combo_null_mode.findData("null_only"))
        self.assertEqual(view.proxy_model.rowCount(), 1)
        popover.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)

    def test_text_popover_applies_query_and_clears(self) -> None:
        """문자열(이름) 팝오버가 검색어를 적용하고 초기화로 복원함을 검증"""
        view = self._two_video_table()
        popover = view.open_column_filter_popover(VideoTableModel.COL_NAME)
        assert popover is not None and popover.edit_text_query is not None
        self.addCleanup(popover.close)
        popover.edit_text_query.setText("beta")
        popover.apply_current()
        self.assertEqual(view.proxy_model.rowCount(), 1)
        popover.clear_filter()
        self.assertEqual(view.proxy_model.rowCount(), 2)

    def test_filter_bar_shows_reset_buttons_only_while_filters_are_active(self) -> None:
        """필터 활성 여부에 따라 초기화 버튼이 표시되고, 클릭 시 요청 시그널이 방출됨을 검증"""
        bar = FilterTuningBar()
        self.addCleanup(bar.close)
        self.assertTrue(bar.btn_clear_col_filters.isHidden())
        self.assertTrue(bar.btn_reset_filters.isHidden())
        reset_events: list[bool] = []
        cleared: list[bool] = []
        bar.reset_filters_requested.connect(lambda: reset_events.append(True))
        bar.clear_column_filters_requested.connect(lambda: cleared.append(True))

        bar.set_any_filter_active(True)
        self.assertFalse(bar.btn_reset_filters.isHidden())
        bar.btn_reset_filters.click()
        self.assertEqual(reset_events, [True])
        bar.set_any_filter_active(False)
        self.assertTrue(bar.btn_reset_filters.isHidden())

        bar.set_active_column_filter_count(2)
        self.assertFalse(bar.btn_clear_col_filters.isHidden())
        self.assertIn("2개", bar.btn_clear_col_filters.text())
        bar.btn_clear_col_filters.click()
        self.assertEqual(cleared, [True])
        bar.set_active_column_filter_count(0)
        self.assertTrue(bar.btn_clear_col_filters.isHidden())

    def test_optional_number_spinbox_interactions(self) -> None:
        """OptionalNumberSpinBox의 빈 값(None), 기준점 스텝, 소수/음수, 휠 증감을 검증"""
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

    def _view_with_fps_popover(self) -> tuple[VideoTableView, ColumnFilterPopover]:
        """FPS 열 필터 팝오버가 열린 테이블 뷰와 팝오버를 반환"""
        view = self._two_video_table()
        popover = view.open_column_filter_popover(VideoTableModel.COL_FPS)
        assert popover is not None
        self.addCleanup(popover.close)
        return view, popover

    @staticmethod
    def _press(global_pos: QPointF) -> QMouseEvent:
        return QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(0.0, 0.0),
            global_pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    def test_popover_does_not_grab_mouse_and_keeps_header_hover_alive(self) -> None:
        """팝오버가 마우스를 그랩하지 않고, 열린 동안에도 다른 열 필터 버튼 호버가 막히지 않음을 검증"""
        view, popover = self._view_with_fps_popover()
        self.assertTrue(popover.isVisible())
        self.assertIsNone(QApplication.activePopupWidget())

        button = view.filter_header.filter_button_rect(VideoTableModel.COL_CODEC)
        self.assertTrue(button.isValid())
        point = QPointF(button.center())
        hover = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            point,
            view.filter_header.mapToGlobal(point),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        view.filter_header.mouseMoveEvent(hover)
        self.assertTrue(popover.isVisible())

    def test_clicking_same_column_icon_closes_popover_and_consumes_event(self) -> None:
        """같은 열 필터 아이콘을 다시 누르면 닫히고 이벤트를 소비해 즉시 재오픈되지 않음을 검증"""
        view, popover = self._view_with_fps_popover()
        icon = view.filter_header.filter_button_global_rect(VideoTableModel.COL_FPS)
        self.assertTrue(icon.isValid())
        self.assertTrue(popover.eventFilter(view.filter_header, self._press(QPointF(icon.center()))))
        self.assertFalse(popover.isVisible())

    def test_clicking_elsewhere_closes_popover_but_passes_event_through(self) -> None:
        """다른 영역 클릭은 팝오버를 닫되 이벤트를 통과시켜 한 번에 다른 열로 전환할 수 있음을 검증"""
        view, popover = self._view_with_fps_popover()
        other = view.filter_header.filter_button_global_rect(VideoTableModel.COL_CODEC)
        self.assertFalse(popover.eventFilter(view.filter_header, self._press(QPointF(other.center()))))
        self.assertFalse(popover.isVisible())

    def test_escape_closes_popover(self) -> None:
        """ESC 키가 팝오버를 닫고 이벤트를 소비함을 검증"""
        _view, popover = self._view_with_fps_popover()
        escape = QKeyEvent(QKeyEvent.Type.KeyPress, int(Qt.Key.Key_Escape), Qt.KeyboardModifier.NoModifier)
        self.assertTrue(popover.eventFilter(popover, escape))
        self.assertFalse(popover.isVisible())

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
        target_visual = header.visualIndex(VideoTableModel.COL_NAME)
        header.moveSection(
            header.visualIndex(VideoTableModel.COL_CODEC),
            target_visual,
        )
        self.assertEqual(
            header.visualIndex(VideoTableModel.COL_CODEC),
            target_visual,
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

        # 3. 기본 열 복원 시 논리 열 순서 및 기본 숨김 열까지 초기 상태로 복구됨을 검증
        for col in VideoTableView.DEFAULT_HIDDEN_COLS:
            self.assertTrue(view.isColumnHidden(col))
            view.setColumnHidden(col, False)
        view.reset_columns()
        self.assertEqual(
            header.visualIndex(VideoTableModel.COL_CODEC),
            VideoTableView.DEFAULT_COL_ORDER.index(VideoTableModel.COL_CODEC),
        )
        for col in VideoTableView.DEFAULT_HIDDEN_COLS:
            self.assertTrue(view.isColumnHidden(col))

    def test_filter_header_wraps_multiline_headers_and_expands_height(self) -> None:
        """좁은 열에서 공백 포함 헤더가 줄바꿈되며 헤더 높이가 확장됨을 검증"""
        cfg = ClassifierConfig()
        sample = VideoProps("clip_1.mp4", 1920, 1080, 0, 30.0, 4000, 128, 50.0, 100.0, "h264", 1.0)
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [sample], cfg).evaluations

        view = VideoTableView()
        view.resize(1300, 400)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        header = view.filter_header
        single_line_h = header.sectionSizeFromContents(VideoTableModel.COL_NAME).height()
        header.resizeSection(VideoTableModel.COL_KEYFRAME, 68)
        wrapped_h = header.sectionSizeFromContents(VideoTableModel.COL_KEYFRAME).height()
        self.assertGreater(wrapped_h, single_line_h)
        self.assertGreaterEqual(header.sizeHint().height(), wrapped_h)

        header.setSortIndicator(VideoTableModel.COL_KEYFRAME, Qt.SortOrder.DescendingOrder)
        header.set_active_filter_columns({VideoTableModel.COL_KEYFRAME})
        header.repaint()

    def _sized_table_view(self, width: int = 900) -> VideoTableView:
        view = VideoTableView()
        view.resize(width, 400)
        view.show()
        self.addCleanup(view.close)
        _APP.processEvents()
        return view

    @staticmethod
    def _load_names(view: VideoTableView, *filenames: str) -> None:
        cfg = ClassifierConfig()
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [make_video(filename=name) for name in filenames], cfg).evaluations
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)
        # 열 폭 계산은 데이터 반영 뒤 이벤트 루프에서 이루어짐
        _APP.processEvents()

    @staticmethod
    def _visible_width(view: VideoTableView) -> int:
        return sum(view.columnWidth(c) for c in range(view.table_model.columnCount()) if not view.isColumnHidden(c))

    def test_default_column_widths_use_configured_minimums(self) -> None:
        """기본 열 너비가 지정 최소 폭이고, 기본 숨김 열도 표시 전환 시 지정 폭을 유지함을 검증"""
        view = self._sized_table_view()
        for col in (VideoTableModel.COL_NAME, VideoTableModel.COL_FPS, VideoTableModel.COL_BITRATE):
            self.assertEqual(view.columnWidth(col), VideoTableView.MIN_COL_WIDTHS[col])
        view.setColumnHidden(VideoTableModel.COL_OPT_BITRATE, False)
        self.assertEqual(
            view.columnWidth(VideoTableModel.COL_OPT_BITRATE),
            VideoTableView.MIN_COL_WIDTHS[VideoTableModel.COL_OPT_BITRATE],
        )

    def test_name_column_expands_with_content_up_to_cap_and_resets_when_empty(self) -> None:
        """이름 열이 파일명 길이에 따라 기본 폭 이상으로 늘고 360px에서 멈추며, 빈 테이블이면 기본 폭으로 돌아옴을 검증"""
        view = self._sized_table_view()
        default_width = VideoTableView.MIN_COL_WIDTHS[VideoTableModel.COL_NAME]

        self._load_names(view, "medium_length_archive_clip_2026_1080p.mp4")
        expanded = view.columnWidth(VideoTableModel.COL_NAME)
        self.assertGreater(expanded, default_width)
        self.assertLessEqual(expanded, 360)

        self._load_names(view, f"{'very_long_filename_' * 6}.mp4")
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), 360)

        self._load_names(view)
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), default_width)

    def test_name_column_fills_wide_window_and_follows_resize(self) -> None:
        """창이 넓으면 이름 열이 남는 여백 없이 창 폭에 맞게 늘고 창 크기를 따라감을 검증"""
        view = self._sized_table_view()
        view.reset_columns()
        viewport = view.viewport()
        assert viewport is not None

        view.resize(1400, 400)
        _APP.processEvents()
        self.assertEqual(self._visible_width(view), viewport.width())
        name_at_1400 = view.columnWidth(VideoTableModel.COL_NAME)

        view.resize(1600, 400)
        _APP.processEvents()
        self.assertEqual(self._visible_width(view), viewport.width())
        self.assertGreater(view.columnWidth(VideoTableModel.COL_NAME), name_at_1400)

    def test_unhiding_column_keeps_content_width_instead_of_squeezing(self) -> None:
        """숨김 열을 켜면 이름 열을 줄이되 내용 폭이 창보다 넓어지면 가로 스크롤로 유지함을 검증"""
        view = self._sized_table_view(1600)
        viewport = view.viewport()
        assert viewport is not None
        view.setColumnHidden(VideoTableModel.COL_AFTER_SIZE, False)
        _APP.processEvents()
        self.assertGreaterEqual(self._visible_width(view), viewport.width())

    def test_manual_name_width_survives_reload_until_columns_reset(self) -> None:
        """사용자가 조절한 이름 열 폭은 재로드해도 유지되고, 열 초기화 후 다시 자동 맞춤됨을 검증"""
        view = self._sized_table_view()
        viewport = view.viewport()
        assert viewport is not None
        long_name = f"{'very_long_filename_' * 6}.mp4"

        view.setColumnWidth(VideoTableModel.COL_NAME, 250)
        self._load_names(view, long_name)
        self.assertEqual(view.columnWidth(VideoTableModel.COL_NAME), 250)

        view.reset_columns()
        _APP.processEvents()
        self.assertGreaterEqual(self._visible_width(view), viewport.width())
        self.assertNotEqual(view.columnWidth(VideoTableModel.COL_NAME), 250)

    def test_video_table_view_smooth_horizontal_scroll(self) -> None:
        """가로 스크롤 모드가 ScrollPerPixel이며 트랙패드 pixelDelta, 미세 angleDelta로 부드럽게 이동함을 검증"""
        view = VideoTableView()
        view.resize(700, 400)
        view.show()
        self.addCleanup(view.close)
        _APP.processEvents()

        self.assertEqual(
            view.horizontalScrollMode(), QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        hbar = view.horizontalScrollBar()
        assert hbar is not None
        self.assertEqual(hbar.singleStep(), 20)
        self.assertGreater(hbar.maximum(), 50)

        # 1. 트랙패드 pixelDelta 가로 스크롤: 1:1 픽셀 이동 검증
        vp = view.viewport()
        assert vp is not None
        ev_px = QWheelEvent(
            QPointF(100, 100),
            QPointF(100, 100),
            QPoint(-15, 0),
            QPoint(0, 0),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        _APP.sendEvent(vp, ev_px)
        self.assertEqual(hbar.value(), 15)

        # 2. Windows Precision Touchpad 식 미세 angleDelta 연속 입력 시 한 열씩 튀지 않고 미세 증가
        ev_ang = QWheelEvent(
            QPointF(100, 100),
            QPointF(100, 100),
            QPoint(0, 0),
            QPoint(-8, 0),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        _APP.sendEvent(vp, ev_ang)
        self.assertGreater(hbar.value(), 15)
        self.assertLess(hbar.value(), 30)

    def test_video_table_view_name_column_clips_without_ellipsis(self) -> None:
        """이름 열 델리게이트가 말줄임표(...), 줄바꿈을 끄고 열 경계에서 잘리도록 설정됨을 검증"""
        cfg = ClassifierConfig()
        sample = VideoProps(
            "very_long_filename_without_ellipsis_clipping_check.mp4",
            1920,
            1080,
            0,
            30.0,
            4000,
            128,
            50.0,
            100.0,
            "h264",
            1.0,
        )
        evals = VideoEvaluator.evaluate_all("C:/dummy_dir", [sample], cfg).evaluations
        view = VideoTableView()
        self.addCleanup(view.close)
        view.table_model.set_data_source(evals, root_dir="C:/dummy_dir", config=cfg)

        delegate = view.itemDelegateForColumn(VideoTableModel.COL_NAME)
        self.assertIsInstance(delegate, ClipTextDelegate)
        assert isinstance(delegate, ClipTextDelegate)
        opt = QStyleOptionViewItem()
        opt.textElideMode = Qt.TextElideMode.ElideRight
        opt.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        delegate.initStyleOption(
            opt, view.proxy_model.index(0, VideoTableModel.COL_NAME)
        )
        self.assertEqual(opt.textElideMode, Qt.TextElideMode.ElideNone)
        self.assertFalse(
            bool(opt.features & QStyleOptionViewItem.ViewItemFeature.WrapText)
        )
        # 타 열 기본 말줄임 동작은 유지됨을 함께 확인
        self.assertEqual(view.textElideMode(), Qt.TextElideMode.ElideRight)

    def test_video_table_view_empty_guide_card_visibility(self) -> None:
        """빈 데이터 상태에서는 3단계 온보딩 가이드를 보이고 필터 미일치 상태에서는 숨김을 검증"""
        view = VideoTableView()
        view.resize(760, 480)
        view.show()
        self.addCleanup(view.close)
        _APP.processEvents()

        vp = view.viewport()
        assert vp is not None
        self.assertIs(view.empty_overlay.parent(), vp)
        self.assertEqual(view.empty_overlay.geometry(), vp.rect())
        self.assertGreater(view.empty_overlay.height(), 100)
        self.assertTrue(view.empty_overlay.isVisible())
        self.assertTrue(view.empty_overlay.guide_card.isVisible())

        # 테이블을 가로 스크롤, 가로 휠 조작해도 스크롤이 동작하고 빈 상태 오버레이는 뷰포트 중앙에 고정됨
        hbar = view.horizontalScrollBar()
        assert hbar is not None
        self.assertGreater(hbar.maximum(), 0)
        hbar.setValue(min(180, hbar.maximum()))
        _APP.processEvents()
        self.assertEqual(view.empty_overlay.geometry(), vp.rect())

        hbar.setValue(0)
        wheel_ev = QWheelEvent(
            QPointF(100, 100),
            QPointF(100, 100),
            QPoint(-40, 0),
            QPoint(0, 0),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        _APP.sendEvent(vp, wheel_ev)
        _APP.processEvents()
        self.assertEqual(hbar.value(), 40)
        self.assertEqual(view.empty_overlay.geometry(), vp.rect())

        view.update_empty_state(5, 0)
        self.assertTrue(view.empty_overlay.isVisible())
        self.assertFalse(view.empty_overlay.guide_card.isVisible())
        self.assertFalse(view.empty_overlay.btn_analyze.isVisible())
        self.assertTrue(view.empty_overlay.btn_action.isVisible())

        view.update_empty_state(5, 3)
        self.assertFalse(view.empty_overlay.isVisible())

    def test_video_table_view_empty_overlay_syncs_on_initial_show_in_layout(self) -> None:
        """최초 창 표시 및 리사이즈 시 빈 상태 오버레이가 뷰포트 크기와 창 가로 중앙에 동기화됨을 검증"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        view = VideoTableView(parent=container)
        layout.addWidget(view)
        container.resize(820, 460)
        container.show()
        self.addCleanup(container.close)
        _APP.processEvents()

        vp = view.viewport()
        assert vp is not None
        self.assertGreater(vp.height(), 100)
        self.assertEqual(view.empty_overlay.geometry(), vp.rect())
        self.assertTrue(view.empty_overlay.guide_card.isVisible())
        self.assertTrue(view.empty_overlay.btn_analyze.isVisible())
        self.assertFalse(view.empty_overlay.btn_action.isVisible())

        container.resize(1120, 620)
        _APP.processEvents()
        self.assertEqual(view.empty_overlay.geometry(), vp.rect())
        overlay_center_x = view.empty_overlay.mapTo(
            container, view.empty_overlay.rect().center()
        ).x()
        self.assertLessEqual(abs(overlay_center_x - container.rect().center().x()), 1)

    def test_bento_dashboard_cards_have_concept_tooltips(self) -> None:
        """BentoDashboard 4개 카드가 대기, 집계 상태 모두에서 배경 개념 툴팁을 제공함을 검증"""
        from src.core.models import SavingsSummary, SummaryReport
        from src.gui.widgets.bento_dashboard import BentoDashboard

        dash = BentoDashboard()
        self.addCleanup(dash.close)

        for card in (
            dash.card_files,
            dash.card_net_saved,
            dash.card_workload,
            dash.card_excluded,
        ):
            self.assertTrue(bool(card.toolTip().strip()))
        self.assertIn("키프레임", dash.card_net_saved.toolTip())
        self.assertIn("총 재생 길이", dash.card_workload.toolTip())
        self.assertIn("절약 미달", dash.card_excluded.toolTip())

        report = SummaryReport(
            root_dir="C:/dummy",
            total_count=3,
            encoded_count=2,
            passed_count=1,
            total_orig_mb=300.0,
            total_expected_after_mb=220.0,
            gross_saved_mb=90.0,
            keyframe_increase_mb=10.0,
            net_saved_mb=80.0,
            savings_summary=SavingsSummary(
                min_saved_mb=5.0,
                min_mb_per_min=3.0,
                exempt_saved_mb=50.0,
                encode_duration_sec=180.0,
                low_saved_count=1,
                low_per_min_count=0,
                excluded_over_duration_sec=60.0,
                excluded_over_potential_saved_mb=3.0,
            ),
            evaluations=[],
        )
        dash.update_from_report(report)
        self.assertIn("인코딩 대상: 2개", dash.card_files.toolTip())
        self.assertIn("과화질 재인코딩 절약: +90.0 MB", dash.card_net_saved.toolTip())
        self.assertIn("키프레임 재인코딩 증가: -10.0 MB", dash.card_net_saved.toolTip())
        self.assertIn("총 재생 길이", dash.card_workload.toolTip())

    def test_video_table_empty_overlay_scrolls_vertically_without_squishing(self) -> None:
        """빈 상태 안내가 세로 축소 시 찌그러지지 않고 스크롤바로 전환됨을 검증"""
        from src.gui.widgets.video_table_view import VideoTableView

        view = VideoTableView()
        self.addCleanup(view.close)
        view.resize(960, 560)
        view.show()
        view.update_empty_state(0, 0)
        _APP.processEvents()

        v_bar = view.empty_overlay.scroll_area.verticalScrollBar()
        assert v_bar is not None
        guide_natural_h = view.empty_overlay.guide_card.sizeHint().height()
        self.assertEqual(v_bar.maximum(), 0)
        self.assertGreaterEqual(view.empty_overlay.guide_card.height(), guide_natural_h)

        view.resize(960, 180)
        _APP.processEvents()
        self.assertGreater(v_bar.maximum(), 0)
        self.assertGreaterEqual(view.empty_overlay.guide_card.height(), guide_natural_h)

    def test_bento_card_text_not_clipped_on_narrow_width(self) -> None:
        """긴 카드 텍스트가 창 폭 축소 시 잘리지 않고 카드 폭 확보 또는 가로 스크롤로 보존됨을 검증"""
        from src.gui.widgets.bento_dashboard import BentoDashboard

        dash = BentoDashboard()
        self.addCleanup(dash.close)
        dash.card_net_saved.update_content(
            "12.82 GB 절약 (27.2%)", "47.18 → 34.36 GB, 순 절약 기준 예상 용량"
        )
        dash.resize(500, 120)
        dash.show()
        for _ in range(3):
            _APP.processEvents()

        for card in (dash.card_net_saved, dash.card_workload):
            for label in (card.value_label, card.sub_label):
                with self.subTest(card=card.objectName(), label=label.objectName()):
                    self.assertGreaterEqual(
                        card.width(), label.sizeHint().width() + 28
                    )

    def test_bento_dashboard_horizontal_scroll_fade_and_theme(self) -> None:
        """BentoDashboard가 좁은 폭에서 스크롤바 없이 휠 스크롤, 경계 페이드, 테마 색상을 갱신함을 검증"""
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QColor, QWheelEvent

        from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
        from src.gui.theme import ThemeManager
        from src.gui.widgets.bento_dashboard import BentoDashboard

        theme_mgr = ThemeManager()
        prev_mode = theme_mgr.mode
        self.addCleanup(lambda: theme_mgr.set_mode(prev_mode))

        dash = BentoDashboard()
        self.addCleanup(dash.close)
        dash.resize(1100, 120)
        dash.show()
        _APP.processEvents()

        h_bar = dash._scroll_area.horizontalScrollBar()
        assert h_bar is not None
        assert dash._fade_overlay is not None
        self.assertEqual(
            dash._scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(h_bar.maximum(), 0)
        self.assertFalse(dash._fade_overlay._can_scroll_left)
        self.assertFalse(dash._fade_overlay._can_scroll_right)

        dash.resize(500, 120)
        _APP.processEvents()
        self.assertGreater(h_bar.maximum(), 0)
        self.assertFalse(dash._fade_overlay._can_scroll_left)
        self.assertTrue(dash._fade_overlay._can_scroll_right)

        viewport = dash._scroll_area.viewport()
        assert viewport is not None
        wheel = QWheelEvent(
            QPointF(viewport.rect().center()),
            QPointF(viewport.mapToGlobal(viewport.rect().center())),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        _APP.sendEvent(viewport, wheel)
        _APP.processEvents()
        self.assertGreater(h_bar.value(), 0)
        self.assertTrue(dash._fade_overlay._can_scroll_left)

        h_bar.setValue(h_bar.maximum())
        _APP.processEvents()
        self.assertTrue(dash._fade_overlay._can_scroll_left)
        self.assertFalse(dash._fade_overlay._can_scroll_right)

        theme_mgr.set_mode("dark")
        self.assertEqual(dash._fade_overlay._fade_color, QColor(DARK_PALETTE.bg_base))
        theme_mgr.set_mode("light")
        self.assertEqual(dash._fade_overlay._fade_color, QColor(LIGHT_PALETTE.bg_base))

    def test_video_table_view_horizontal_edge_fade_overlays(self) -> None:
        """테이블 뷰포트, 헤더 경계 페이드가 빈 상태, 행 표시, 가로 스크롤, 테마 전환에 맞춰 동작함을 검증"""
        from PyQt6.QtGui import QColor

        from src.core.config import ClassifierConfig
        from src.core.models import QualityZone, SmartAction, VideoEvaluation
        from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
        from src.gui.theme import ThemeManager
        from src.gui.widgets.video_table_view import VideoTableView

        theme_mgr = ThemeManager()
        prev_mode = theme_mgr.mode
        self.addCleanup(lambda: theme_mgr.set_mode(prev_mode))

        view = VideoTableView()
        self.addCleanup(view.close)
        view.resize(520, 320)
        view.show()
        _APP.processEvents()

        h_bar = view.horizontalScrollBar()
        assert h_bar is not None
        assert view.vp_fade_overlay is not None
        assert view.header_fade_overlay is not None
        self.assertGreater(h_bar.maximum(), 0)

        # 빈 상태 오버레이 표시 중에는 뷰포트 본문 페이드를 끄고 헤더만 유지
        self.assertFalse(view.vp_fade_overlay._can_scroll_left)
        self.assertFalse(view.vp_fade_overlay._can_scroll_right)
        self.assertTrue(view.header_fade_overlay._can_scroll_right)

        video = VideoProps(
            "sample_video.mp4", 1920, 1080, 0, 30.0, 12000, 128, 180.0, 120.0, "h264", 1.0
        )
        row = VideoEvaluation(
            video=video,
            optimal_kbps=4000,
            bitrate_ratio=3.0,
            quality_zone=QualityZone.OVER,
            is_keyframe_exceeded=False,
            expected_after_mb=60.0,
            expected_delta_mb=120.0,
            smart_action=SmartAction.ENCODE_CQ22_OVER,
            target_dirname="_1_표준인코딩_CQ22",
        )
        view.table_model.set_data_source([row], root_dir="C:/dummy_dir", config=ClassifierConfig())
        view.update_empty_state(total_count=1, visible_count=1)
        _APP.processEvents()

        self.assertFalse(view.vp_fade_overlay._can_scroll_left)
        self.assertTrue(view.vp_fade_overlay._can_scroll_right)
        self.assertTrue(view.header_fade_overlay._can_scroll_right)

        _APP.processEvents()
        h_bar.setValue(h_bar.maximum())
        _APP.processEvents()
        self.assertTrue(view.vp_fade_overlay._can_scroll_left)
        self.assertFalse(view.vp_fade_overlay._can_scroll_right)
        self.assertTrue(view.header_fade_overlay._can_scroll_left)
        self.assertFalse(view.header_fade_overlay._can_scroll_right)

        theme_mgr.set_mode("dark")
        self.assertEqual(view.vp_fade_overlay._fade_color, QColor(DARK_PALETTE.bg_card))
        self.assertEqual(view.header_fade_overlay._fade_color, QColor(DARK_PALETTE.header_bg))
        theme_mgr.set_mode("light")
        self.assertEqual(view.vp_fade_overlay._fade_color, QColor(LIGHT_PALETTE.bg_card))
        self.assertEqual(view.header_fade_overlay._fade_color, QColor(LIGHT_PALETTE.header_bg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
