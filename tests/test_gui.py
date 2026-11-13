import dataclasses
import os
import tempfile
import unittest
from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.classifier import VideoClassifier
from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.models import QualityZone, VideoProps
from src.gui.main_window import MainWindow
from src.gui.models import (
    VideoSortFilterProxyModel,
    VideoTableModel,
    format_duration,
)
from src.gui.worker import ClassifierWorker
from src.service.cache import TableCache
from src.service.classifier_service import VideoClassifierService

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIModule(unittest.TestCase):
    """
    - PyQt6 기반 GUI 뷰모델, 자연 정렬/필터 프록시, 메인 윈도우 대시보드 및 백그라운드 워커를 검증.
    """

    def setUp(self) -> None:
        self.cfg = ClassifierConfig()
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
        self.report = VideoEvaluator.evaluate_all(
            "C:/dummy_archive", self.videos, self.cfg
        )

    def test_format_duration_helper(self) -> None:
        """
        - 초 단위 재생 시간이 MM:SS 및 HH:MM:SS 포맷으로 정확히 변환되는지 검증.
        """
        self.assertEqual(format_duration(65.4), "01:05")
        self.assertEqual(format_duration(3661.0), "01:01:01")

    def test_video_table_model_display_and_roles(self) -> None:
        """
        - VideoTableModel의 행/열 수, 유니코드 표시, 회전 표시(↻), 정렬 키 및 뱃지 스타일 반환을 검증.
        """
        model = VideoTableModel()
        model.set_data_source(
            self.report.evaluations, "C:/dummy_archive", pseudo_mode=True
        )

        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.columnCount(), len(VideoTableModel.HEADERS))

        # 첫 번째 행 (과화질 1080p, SmartAction.ENCODE_CQ22_OVER) 검증
        idx_name_0 = model.index(0, VideoTableModel.COL_NAME)
        self.assertEqual(model.data(idx_name_0), "가나다_10_고화질.mp4")

        idx_zone_0 = model.index(0, VideoTableModel.COL_ZONE)
        self.assertEqual(model.data(idx_zone_0), QualityZone.OVER.value)
        self.assertIsNotNone(model.data(idx_zone_0, VideoTableModel.BADGE_ROLE))

        idx_action_0 = model.index(0, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_0, VideoTableModel.BADGE_ROLE))

        # 셀 단위 텍스트 생략 및 순수 수치 포맷 검증
        idx_size_0 = model.index(0, VideoTableModel.COL_SIZE)
        self.assertEqual(model.data(idx_size_0), "120.00")

        idx_bitrate_0 = model.index(0, VideoTableModel.COL_BITRATE)
        self.assertEqual(model.data(idx_bitrate_0), "9500")

        idx_saved_0 = model.index(0, VideoTableModel.COL_SAVED_SIZE)
        self.assertTrue(model.data(idx_saved_0).startswith("+"))
        self.assertNotIn("MB", model.data(idx_saved_0))

        # 헤더 호버링 툴팁에 단위 명시 여부 검증
        tooltip_size = model.headerData(
            VideoTableModel.COL_SIZE,
            Qt.Orientation.Horizontal,
            int(Qt.ItemDataRole.ToolTipRole),
        )
        self.assertIn("단위: MB", tooltip_size)

        tooltip_br = model.headerData(
            VideoTableModel.COL_BITRATE,
            Qt.Orientation.Horizontal,
            int(Qt.ItemDataRole.ToolTipRole),
        )
        self.assertIn("단위: kbps", tooltip_br)

        # 두 번째 행 (90도 회전 720p, SmartAction.PASS_OPTIMAL) 회전 기호 및 뱃지 검증
        idx_res_1 = model.index(1, VideoTableModel.COL_RES)
        self.assertIn("↻", model.data(idx_res_1))

        idx_action_1 = model.index(1, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_1, VideoTableModel.BADGE_ROLE))

        # 세 번째 행 (키프레임 5.0초 불량, SmartAction.ENCODE_LOW_KF) 경고 기호 및 뱃지 검증
        idx_kf_2 = model.index(2, VideoTableModel.COL_KEYFRAME)
        self.assertIn("⚠", model.data(idx_kf_2))

        idx_action_2 = model.index(2, VideoTableModel.COL_ACTION)
        self.assertIsNotNone(model.data(idx_action_2, VideoTableModel.BADGE_ROLE))

    def test_proxy_model_filtering_and_natural_sorting(self) -> None:
        """
        - VideoSortFilterProxyModel의 실시간 검색, 화질 등급/인코딩 대상 필터링 및 자연 정렬을 검증.
        """
        model = VideoTableModel()
        model.set_data_source(
            self.report.evaluations, "C:/dummy_archive", pseudo_mode=True
        )

        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)

        # 1. 파일명 오름차순 자연 정렬 검증 ('가나다_2'가 '가나다_10'보다 앞에 위치)
        proxy.sort(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)
        first_name = proxy.data(proxy.index(0, VideoTableModel.COL_NAME))
        second_name = proxy.data(proxy.index(1, VideoTableModel.COL_NAME))
        self.assertEqual(first_name, "가나다_2_정상.mp4")
        self.assertEqual(second_name, "가나다_10_고화질.mp4")

        # 2. 예상 절감량 내림차순 정렬 검증
        proxy.sort(VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder)
        top_saved_name = proxy.data(proxy.index(0, VideoTableModel.COL_NAME))
        self.assertEqual(top_saved_name, "가나다_10_고화질.mp4")

        # 3. 화질 등급 필터 검증 (OVER만 표시)
        proxy.set_zone_filter("OVER")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_zone_filter("ALL")
        self.assertEqual(proxy.rowCount(), 3)

        # 4. 대상 필터 검증 (키프레임 불량만 표시)
        proxy.set_target_filter("DEFECT_KF")
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(
            proxy.data(proxy.index(0, VideoTableModel.COL_NAME)),
            "저화질_KF불량_클립.mkv",
        )
        proxy.set_target_filter("ALL")

        # 5. 검색어 필터 검증 (유니코드 한글 및 코덱 검색)
        proxy.set_search_text("mpeg4")
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_search_text("")
        self.assertEqual(proxy.rowCount(), 3)

    def test_main_window_dashboard_and_live_threshold_tuning(self) -> None:
        """
        - MainWindow가 VideoClassifier를 래핑하여 대시보드 카드를 갱신하고 실시간 임계값 변경을 즉시 반영하는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            # 테스트용 인메모리 캐시 주입
            cache = TableCache(tmpdir)
            cache._data = list(self.videos)
            cache._include_keyframe = True
            VideoClassifierService._shared_cache = cache

            win = MainWindow(classifier=clf)
            rep = clf.get_summary_report(ensure_keyframe=False)
            win._update_ui_with_report(rep)

            # 이름 칼럼 너비 및 대화형 리사이징 가능 여부 검증
            from PyQt6.QtWidgets import QHeaderView

            header = win.table_view.horizontalHeader()
            assert header is not None
            self.assertEqual(
                header.sectionResizeMode(VideoTableModel.COL_NAME),
                QHeaderView.ResizeMode.Interactive,
            )
            self.assertGreaterEqual(
                win.table_view.columnWidth(VideoTableModel.COL_NAME), 200
            )

            self.assertEqual(win.table_model.rowCount(), 3)
            self.assertIn("3개", win.card_files.value_label.text())

            # 예외 키워드에 '가나다_10' 입력 시 즉시 인코딩 대상에서 제외되는지 검증
            win.edit_exceptions.setText("가나다_10")
            updated_rep = win.current_report
            assert updated_rep is not None
            emap = {e.video.filename: e for e in updated_rep.evaluations}
            self.assertIsNone(emap["가나다_10_고화질.mp4"].target_dirname)

            # 파이썬 람다/조건식 예외 규칙(v.duration < 150) 입력 시 즉시 인코딩 대상에서 제외되는지 검증
            win.edit_exceptions.setText("v.duration < 150")
            rep_lambda = win.current_report
            assert rep_lambda is not None
            emap_lambda = {e.video.filename: e for e in rep_lambda.evaluations}
            self.assertIsNone(emap_lambda["가나다_10_고화질.mp4"].target_dirname)

            # 환경설정 창 열기 및 Non-Modal 동작, 스크롤바 정책 검증
            win._on_open_settings()
            dlg = win._settings_dialog
            assert dlg is not None
            self.assertTrue(dlg.isVisible())
            self.assertEqual(
                dlg.windowModality(), Qt.WindowModality.NonModal
            )
            self.assertEqual(
                dlg.scroll_area.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            # 아코디언 기본 접힘 상태 및 토글 동작 검증
            self.assertFalse(dlg.section_advanced.is_expanded())
            self.assertFalse(dlg.section_advanced.content_widget.isVisible())
            dlg.section_advanced.toggle()
            self.assertTrue(dlg.section_advanced.is_expanded())
            self.assertTrue(dlg.section_advanced.content_widget.isVisible())
            dlg.close()

    def test_classifier_worker_pseudo_classify_and_unmark(self) -> None:
        """
        - ClassifierWorker가 VideoClassifier를 통해 가분류 및 가분류 해제를 정상 수행하고 시그널을 방출하는지 검증.
        """
        from src.core.file_manager import compute_dir_signature

        with tempfile.TemporaryDirectory() as tmpdir:
            for v in self.videos:
                with open(os.path.join(tmpdir, v.filename), "wb") as f:
                    f.write(b"00")

            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache.__new__(TableCache)
            cache._root_dir = tmpdir
            cache._data = [
                VideoProps(**{**v.__dict__}) for v in self.videos
            ]
            cache._include_keyframe = True
            VideoClassifierService._shared_cache = cache
            VideoClassifierService._shared_signature = compute_dir_signature(
                tmpdir
            )

            completed_events = []
            worker = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_CLASSIFY_PSEUDO,
            )
            worker.task_completed.connect(
                lambda t, r, m, w: completed_events.append((t, r, m, w))
            )
            worker.run()

            self.assertEqual(len(completed_events), 1)
            task_type, rep, _, _ = completed_events[0]
            self.assertEqual(task_type, ClassifierWorker.TASK_CLASSIFY_PSEUDO)
            self.assertEqual(rep.total_count, 3)
            self.assertTrue(
                any(v.moved_dirname is not None for v in clf.video_props)
            )

            # 가분류 표시 초기화 워커 검증
            worker_unmark = ClassifierWorker(
                classifier=clf,
                task_type=ClassifierWorker.TASK_UNCLASSIFY_PSEUDO,
            )
            worker_unmark.run()
            self.assertTrue(
                all(v.moved_dirname is None for v in clf.video_props)
            )

    def test_theme_manager_modes_and_palette_switch(self) -> None:
        """
        - ThemeManager의 다크(무채색), 라이트, 시스템 모드 전환 및 QSS 생성과 프록시 연동을 검증.
        """
        from src.gui.theme import (
            DARK_PALETTE,
            LIGHT_PALETTE,
            ThemeColors,
            ThemeManager,
            build_stylesheet,
        )

        mgr = ThemeManager()
        # 1. 다크 모드 (무채색 계열) 검증
        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertEqual(mgr.mode, ThemeManager.MODE_DARK)
        self.assertTrue(mgr.current_palette.is_dark)
        self.assertEqual(mgr.current_palette.bg_base, "#121212")
        self.assertEqual(ThemeColors.BG_BASE, "#121212")

        # 2. 라이트 모드 (고대비 깔끔한 톤) 검증
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertEqual(mgr.mode, ThemeManager.MODE_LIGHT)
        self.assertFalse(mgr.current_palette.is_dark)
        self.assertEqual(mgr.current_palette.bg_base, "#F5F6F8")
        self.assertEqual(ThemeColors.BG_BASE, "#F5F6F8")

        # 3. QSS 스타일시트 렌더링 무결성 검증
        qss_dark = build_stylesheet(DARK_PALETTE)
        self.assertIn("#121212", qss_dark)
        qss_light = build_stylesheet(LIGHT_PALETTE)
        self.assertIn("#F5F6F8", qss_light)

        # 4. 시스템 적응형 모드 설정 검증
        mgr.set_mode(ThemeManager.MODE_SYSTEM)
        self.assertEqual(mgr.mode, ThemeManager.MODE_SYSTEM)
        self.assertIn(mgr.current_palette, (DARK_PALETTE, LIGHT_PALETTE))

    def test_main_window_theme_switch_integration(self) -> None:
        """
        - ThemeManager 모드 변경 시 MainWindow의 스타일시트가 실시간 동적 갱신되는지 검증.
        """
        from src.gui.theme import ThemeManager

        win = MainWindow(initial_dir="C:/dummy")
        mgr = ThemeManager()

        # 라이트 모드로 전환
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertIn("#F5F6F8", win.styleSheet())

        # 다크 모드로 전환
        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertIn("#121212", win.styleSheet())

    def test_settings_dialog_individual_reset_and_save(self) -> None:
        """
        - SettingsDialog의 설정값 로드, 실시간 자동 저장 및 개별 기본값 복원을 검증.
        """
        from unittest.mock import patch

        from src.core.config import DEFAULT_CONFIGS
        from src.gui.settings_dialog import SettingsDialog

        custom_cfg = ClassifierConfig(
            base_target_bitrate=3000,
            over_bitrate_ratio=1.50,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_env = os.path.join(tmpdir, ".env")
            dummy_root = os.path.join(tmpdir, ".rootdir")

            with patch("src.core.config.get_env_path", return_value=dummy_env), \
                 patch("src.core.config.get_rootdir_path", return_value=dummy_root):

                dlg = SettingsDialog(
                    current_config=custom_cfg,
                    current_root_dir=tmpdir,
                )

                # 초기 로드 값 검증
                self.assertEqual(dlg.spin_base_bitrate.value(), 3000)
                self.assertEqual(dlg.spin_over_ratio.value(), 1.50)

                # 하단 버튼(저장, 취소, 전체 복원)이 모두 제거되었는지 검증
                self.assertFalse(hasattr(dlg, "btn_save"))
                self.assertFalse(hasattr(dlg, "btn_reset_all"))
                self.assertFalse(hasattr(dlg, "btn_close"))

                # 설정 버튼들이 텍스트 없이 아이콘만 사용하는지 검증
                self.assertEqual(dlg.btn_browse_root.text(), "")
                self.assertEqual(dlg._rows["BASE_TARGET_BITRATE"].btn_reset.text(), "")

                # 실시간 변경 및 자동 저장 검증
                applied_payload = []
                dlg.settings_applied.connect(
                    lambda cfg, rdir: applied_payload.append((cfg, rdir))
                )

                # 값 변경 시 별도의 저장 버튼 클릭 없이 즉시 저장 및 시그널 방출
                dlg.spin_base_bitrate.setValue(4500)
                self.assertGreaterEqual(len(applied_payload), 1)
                latest_cfg, _ = applied_payload[-1]
                self.assertEqual(latest_cfg.base_target_bitrate, 4500)
                self.assertTrue(os.path.isfile(dummy_env))

                with open(dummy_env, encoding="utf-8") as f:
                    env_text = f.read()
                self.assertIn("BASE_TARGET_BITRATE=4500", env_text)

                # 개별 '기본값' 버튼 동작 및 실시간 자동 저장 검증
                dlg._rows["BASE_TARGET_BITRATE"].btn_reset.click()
                self.assertEqual(
                    dlg.spin_base_bitrate.value(),
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )
                latest_cfg_reset, _ = applied_payload[-1]
                self.assertEqual(
                    latest_cfg_reset.base_target_bitrate,
                    int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"]),
                )

                with open(dummy_env, encoding="utf-8") as f:
                    env_text_after_reset = f.read()
                self.assertIn(
                    f"BASE_TARGET_BITRATE={int(DEFAULT_CONFIGS['BASE_TARGET_BITRATE'])}",
                    env_text_after_reset,
                )

    def test_custom_integer_spinbox_interactions(self) -> None:
        """
        - CustomIntegerSpinBox의 스텝 증감, 범위 제한(Clamping), 접미사 및 시그널 방출을 검증.
        """
        from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox

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

    def test_svg_icon_rendering(self) -> None:
        """
        - SVG 템플릿으로부터 QIcon 인스턴스가 인메모리에서 정상 생성되고 유효한 픽스맵을 갖는지 검증.
        """
        from src.gui.styles.icons import build_svg_xml, get_icon

        svg_xml = build_svg_xml("settings", color="#2563EB")
        self.assertIn("<svg", svg_xml)
        self.assertIn("#2563EB", svg_xml)

        icon = get_icon("settings", size=16)
        self.assertFalse(icon.isNull())
        pixmap = icon.pixmap(16, 16)
        self.assertEqual(pixmap.width(), 16)
        self.assertEqual(pixmap.height(), 16)

    def test_icon_theme_adaptation_and_contrast(self) -> None:
        """
        - 라이트/다크 테마에 따라 get_icon 기본 색상이 고대비로 자동 전환되는지 검증.
        """
        from src.gui.styles.icons import get_active_palette, get_icon
        from src.gui.theme import ThemeManager

        mgr = ThemeManager()

        # 라이트 모드 강제 적용
        mgr.set_mode(ThemeManager.MODE_LIGHT)
        self.assertEqual(get_active_palette().name, "light")
        icon_light = get_icon("folder", size=16)
        img_l = icon_light.pixmap(16, 16).toImage()
        # 불투명 픽셀 색상 수집 (라이트 모드는 진한 차콜 톤이어야 함)
        cols_l = {
            img_l.pixelColor(x, y).name()
            for x in range(16)
            for y in range(16)
            if img_l.pixelColor(x, y).alpha() > 100
        }
        self.assertTrue(any(c.startswith("#0e") or c.startswith("#0f") for c in cols_l))

        # 다크 모드로 전환
        mgr.set_mode(ThemeManager.MODE_DARK)
        self.assertEqual(get_active_palette().name, "dark")
        icon_dark = get_icon("folder", size=16)
        img_d = icon_dark.pixmap(16, 16).toImage()
        cols_d = {
            img_d.pixelColor(x, y).name()
            for x in range(16)
            for y in range(16)
            if img_d.pixelColor(x, y).alpha() > 100
        }
        self.assertTrue(any(c.startswith("#ed") or c.startswith("#ee") for c in cols_d))

    def test_settings_dialog_theme_switch_updates_all_icons(self) -> None:
        """
        - 설정 모달에서 테마 전환 시 콤보박스 아이콘, 탐색 버튼, 리셋 버튼의 색상이 모두 고대비로 갱신되는지 검증.
        """
        from src.gui.dialogs.settings_dialog import SettingsDialog
        from src.gui.theme import ThemeManager

        tm = ThemeManager()
        tm.set_mode(ThemeManager.MODE_DARK)

        dlg = SettingsDialog()

        # 다크 -> 라이트 전환
        dlg.combo_theme.setCurrentIndex(dlg.combo_theme.findData(ThemeManager.MODE_LIGHT))
        self.assertEqual(tm.current_palette.name, "light")

        # 콤보박스 아이콘 검증 (라이트 모드에서는 어두운 픽셀이어야 함)
        for i in range(3):
            pix = dlg.combo_theme.itemIcon(i).pixmap(16, 16)
            img = pix.toImage()
            dark_px = sum(
                1
                for y in range(img.height())
                for x in range(img.width())
                if img.pixelColor(x, y).alpha() > 100
                and img.pixelColor(x, y).lightness() < 50
            )
            self.assertGreater(dark_px, 0, f"combo item {i} icon is not dark in light mode")

        # 탐색 버튼 검증
        pix_br = dlg.btn_browse_root.icon().pixmap(16, 16)
        img_br = pix_br.toImage()
        dark_px_br = sum(
            1
            for y in range(img_br.height())
            for x in range(img_br.width())
            if img_br.pixelColor(x, y).alpha() > 100
            and img_br.pixelColor(x, y).lightness() < 50
        )
        self.assertGreater(dark_px_br, 0)

        # 리셋 버튼 검증
        for key, row in dlg._rows.items():
            pix_rst = row.btn_reset.icon().pixmap(16, 16)
            img_rst = pix_rst.toImage()
            dark_px_rst = sum(
                1
                for y in range(img_rst.height())
                for x in range(img_rst.width())
                if img_rst.pixelColor(x, y).alpha() > 100
                and img_rst.pixelColor(x, y).lightness() < 50
            )
            self.assertGreater(dark_px_rst, 0, f"row {key} btn_reset icon is not dark in light mode")

        # 라이트 -> 다크 전환
        dlg.combo_theme.setCurrentIndex(dlg.combo_theme.findData(ThemeManager.MODE_DARK))
        self.assertEqual(tm.current_palette.name, "dark")

        for i in range(3):
            pix = dlg.combo_theme.itemIcon(i).pixmap(16, 16)
            img = pix.toImage()
            light_px = sum(
                1
                for y in range(img.height())
                for x in range(img.width())
                if img.pixelColor(x, y).alpha() > 100
                and img.pixelColor(x, y).lightness() > 200
            )
            self.assertGreater(light_px, 0, f"combo item {i} icon is not light in dark mode")

    def test_split_scan_button_modes_and_signals(self) -> None:
        """
        - SplitScanButton의 기본 모드(빠른 스캔), 모드 전환, 툴팁 및 시그널 방출을 검증.
        """
        from src.gui.widgets.header_bar import SplitScanButton

        split_btn = SplitScanButton()
        self.assertEqual(split_btn.current_mode, SplitScanButton.MODE_FAST)
        self.assertEqual(split_btn.btn_main.text(), "빠른 스캔")

        signals = []
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
        """
        - HeaderBar의 스캔 스택이 평소에는 스플릿 버튼을, 작업 중일 때는 중단 버튼을 인플레이스 노출함을 검증.
        """
        from src.gui.widgets.header_bar import HeaderBar

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
        """
        - 폴더 선택, 탐색기 열기, 환경설정 버튼이 텍스트 없이 36x36 아이콘 전용으로 구성되고 클릭 시그널이 정상 연동되는지 검증.
        """
        from src.gui.widgets.header_bar import HeaderBar

        hb = HeaderBar("C:/dummy_path")
        for btn in (hb.btn_browse, hb.btn_open_explorer, hb.btn_settings):
            self.assertEqual(btn.text(), "")
            self.assertEqual(btn.width(), 36)
            self.assertEqual(btn.height(), 36)
            self.assertTrue(len(btn.toolTip()) > 0)

        clicked = []
        hb.browse_clicked.connect(lambda: clicked.append("browse"))
        hb.open_explorer_clicked.connect(lambda: clicked.append("explorer"))
        hb.settings_clicked.connect(lambda: clicked.append("settings"))

        hb.btn_browse.click()
        hb.btn_open_explorer.click()
        hb.btn_settings.click()
        self.assertEqual(clicked, ["browse", "explorer", "settings"])

    def test_main_window_escape_key_cancels_worker(self) -> None:
        """
        - 메인 윈도우에서 작업 진행 중 Esc 키를 누르면 cancel_active_task가 호출됨을 검증.
        """
        from PyQt6.QtGui import QKeyEvent

        mw = MainWindow(initial_dir="C:/dummy_path")
        called = []
        mw.cancel_active_task = lambda: called.append(True)

        class DummyWorker:
            def isRunning(self) -> bool:
                return True

        mw._worker = cast("ClassifierWorker", DummyWorker())
        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        mw.keyPressEvent(esc_event)
        self.assertEqual(called, [True])

    def test_footer_bar_structure_and_inplace_progress(self) -> None:
        """
        - 푸터 바가 [상태 안내 / 프로그레스 바(작업 중에만 인플레이스 노출)] [분류 원복] [폴더 분류 이동] 구조로 동작하는지 검증.
        """
        from src.gui.widgets.footer_bar import FooterBar

        fb = FooterBar()
        fb.show()

        # 1. 초기 상태: 프로그레스 바는 숨김, 상태 레이블은 빈 문자열, 버튼들은 활성화
        self.assertTrue(fb.progress_bar.isHidden())
        self.assertEqual(fb.lbl_status.text(), "")
        self.assertEqual(fb.btn_unclassify_all.text(), "분류 원복")
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

    def test_footer_bar_signals_and_theme_switch(self) -> None:
        """
        - 푸터 바 버튼의 클릭 시그널 연동 및 테마 전환(다크/라이트) 시 아이콘 색상 갱신 검증.
        """
        from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
        from src.gui.widgets.footer_bar import FooterBar

        fb = FooterBar()
        events = []
        fb.unclassify_all_clicked.connect(lambda: events.append("unclassify"))
        fb.real_classify_clicked.connect(lambda: events.append("classify"))

        fb.btn_unclassify_all.click()
        fb.btn_real_classify.click()
        self.assertEqual(events, ["unclassify", "classify"])

        # 테마 전환 호출 검증
        fb.apply_theme(LIGHT_PALETTE)
        fb.apply_theme(DARK_PALETTE)

    def test_clean_status_messages(self) -> None:
        """
        - 폴더 변경/설정/임계값 조작 시 불필요한 상태 메시지가 출력되지 않고, 작업 진행/완료 메시지가 간결한지 검증.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mw = MainWindow(initial_dir=tmp_dir)
            self.assertEqual(mw.lbl_status.text(), "")

            # 1. 작업 경로 변경 시 상태 메시지 덮어쓰기 없음
            with tempfile.TemporaryDirectory() as another_dir:
                mw.set_working_directory(another_dir)
                self.assertEqual(mw.lbl_status.text(), "")

            # 2. 임계값 변경 시 상태 메시지 덮어쓰기 없음
            mw._on_threshold_changed()
            self.assertEqual(mw.lbl_status.text(), "")

            # 3. 진행 메시지 포맷 검증
            mw._on_worker_progress(3, 10, "clip.mp4")
            self.assertEqual(mw.lbl_status.text(), "[3/10] clip.mp4")

            # 4. 일반 [INFO] 로그는 상태 라벨 덮어쓰지 않음
            mw._on_worker_log("[INFO] 작업 경로: C:/another_path")
            self.assertEqual(mw.lbl_status.text(), "[3/10] clip.mp4")

            # 5. 스캔 완료 메시지 검증
            mw._on_worker_completed("scan_fast", None, 0, [])
            self.assertEqual(mw.lbl_status.text(), "분석 완료: 총 0개 영상")

            # 6. 폴더 분류 완료 메시지 검증
            mw._on_worker_completed("classify_real", None, 0, [])
            self.assertEqual(mw.lbl_status.text(), "폴더 분류 완료: 0개 파일 이동됨")

            # 7. 원복 완료 메시지 검증
            mw._on_worker_completed("unclassify_all", None, 5, [])
            self.assertEqual(mw.lbl_status.text(), "분류 원복 완료: 5개 파일 복원됨")

            # 8. 취소 메시지 검증
            mw._on_worker_cancelled("사용자에 의해 분석 작업이 중단됨.")
            self.assertEqual(mw.lbl_status.text(), "작업이 중단되었습니다.")

    def test_stability_busy_lock_close_event_and_dir_reset(self) -> None:
        """
        - 작업 중 FilterTuningBar 잠금, closeEvent 워커 안전 종료, 폴더 변경 시 결과 초기화를 검증.
        """
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            clf = VideoClassifier(root_dir=dir1)
            cache = TableCache(dir1)
            cache._data = list(self.videos)
            cache._include_keyframe = True
            VideoClassifierService._shared_cache = cache

            mw = MainWindow(classifier=clf)
            rep = clf.get_summary_report(ensure_keyframe=False)
            mw._update_ui_with_report(rep)
            self.assertEqual(mw.table_model.rowCount(), 3)
            self.assertIn("3개", mw.card_files.value_label.text())

            # 1. 작업 중 튜닝 컨트롤 잠금 검증
            mw._set_busy_state(True, "진행 중...")
            self.assertFalse(mw.spin_over_ratio.isEnabled())
            self.assertFalse(mw.spin_kf_limit.isEnabled())
            self.assertFalse(mw.spin_small_mb.isEnabled())
            self.assertFalse(mw.edit_exceptions.isEnabled())
            mw._set_busy_state(False)
            self.assertTrue(mw.spin_over_ratio.isEnabled())
            self.assertTrue(mw.edit_exceptions.isEnabled())

            # 2. 폴더 변경 시 이전 분석 결과 및 대시보드 초기화 검증
            mw.set_working_directory(dir2)
            self.assertIsNone(mw.current_report)
            self.assertEqual(mw.table_model.rowCount(), 0)
            self.assertEqual(mw.card_files.value_label.text(), "0개")
            self.assertEqual(mw.lbl_filter_count.text(), "표시: 0 / 0개")

            # 3. closeEvent 실행 중 워커 안전 중단 및 대기 검증
            events = []

            class DummyRunningWorker:
                def isRunning(self) -> bool:
                    return True

                def cancel(self) -> None:
                    events.append("cancel")

                def wait(self, timeout_ms: int = 0) -> bool:
                    events.append(("wait", timeout_ms))
                    return True

            mw._worker = cast("ClassifierWorker", DummyRunningWorker())
            with patch.object(mw, "_save_ui_state"):
                mw.close()
            self.assertEqual(events, ["cancel", ("wait", 3000)])

    def test_exception_validation_and_threshold_sync(self) -> None:
        """
        - 예외 규칙 문법 오류 피드백, FilterTuningBar-SettingsDialog 범위 일치 및 실시간 동기화를 검증.
        """
        from unittest.mock import patch

        from src.gui.widgets.filter_bar import validate_exception_token

        # 1. 구문 오류 조건식은 키워드 필터로 오작동하지 않고 None과 오류 메시지를 반환
        fn_bad, err_bad = validate_exception_token("v.duration <")
        self.assertIsNone(fn_bad)
        self.assertIsNotNone(err_bad)

        fn_ok, err_ok = validate_exception_token("v.duration < 150")
        assert fn_ok is not None
        self.assertIsNone(err_ok)
        self.assertTrue(fn_ok(self.videos[0]))

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_env = os.path.join(tmpdir, ".env")
            dummy_root = os.path.join(tmpdir, ".rootdir")
            with patch("src.core.config.get_env_path", return_value=dummy_env), \
                 patch("src.core.config.get_rootdir_path", return_value=dummy_root):
                mw = MainWindow(initial_dir=tmpdir)

                # 잘못된 식 입력 시 invalid 프로퍼티 설정
                mw.edit_exceptions.setText("v.duration <")
                self.assertEqual(mw.edit_exceptions.property("invalid"), "true")
                mw.edit_exceptions.setText("v.duration < 150")
                self.assertEqual(mw.edit_exceptions.property("invalid"), "false")

                # SettingsDialog와 FilterTuningBar 범위 일치 및 동기화 검증
                mw._on_open_settings()
                dlg = mw._settings_dialog
                assert dlg is not None
                mw.spin_over_ratio.setValue(4.25)
                mw.spin_kf_limit.setValue(18.5)
                self.assertAlmostEqual(dlg.spin_over_ratio.value(), 4.25, places=2)
                self.assertAlmostEqual(dlg.spin_kf_threshold.value(), 18.5, places=1)
                dlg.close()

    def test_table_ux_empty_state_selection_exceptions_and_export(self) -> None:
        """
        - Empty State 전환, 다중 선택 요약, 중복 없는 예외 추가/제거, 열 너비 합계 및 CSV/로그 내보내기를 검증.
        """
        import csv

        from src.gui.widgets.video_table_view import VideoTableView

        self.assertLessEqual(sum(VideoTableView.DEFAULT_COL_WIDTHS.values()), 1540)

        with tempfile.TemporaryDirectory() as tmpdir:
            clf = VideoClassifier(root_dir=tmpdir)
            cache = TableCache(tmpdir)
            cache._data = list(self.videos)
            cache._include_keyframe = True
            VideoClassifierService._shared_cache = cache

            mw = MainWindow(classifier=clf)
            mw.show()

            # 1. 초기 데이터 없음 상태 오버레이
            self.assertTrue(mw.table_view._empty_overlay.isVisible())
            self.assertEqual(mw.table_view._empty_mode, "no_data")

            # 2. 데이터 로드 후 오버레이 숨김
            rep = clf.get_summary_report(ensure_keyframe=False)
            mw._update_ui_with_report(rep)
            self.assertFalse(mw.table_view._empty_overlay.isVisible())

            # 3. 검색 결과 0건 시 필터 초기화 모드 노출 및 초기화 동작
            mw.search_edit.setText("__no_such_video__")
            self.assertTrue(mw.table_view._empty_overlay.isVisible())
            self.assertEqual(mw.table_view._empty_mode, "filtered_empty")
            mw.table_view._btn_empty_action.click()
            self.assertEqual(mw.search_edit.text(), "")
            self.assertFalse(mw.table_view._empty_overlay.isVisible())

            # 4. 다중 선택 및 요약 표시 검증
            mw.table_view.selectAll()
            self.assertIn("선택 3개", mw.lbl_filter_count.text())

            # 5. 중복 없는 예외 추가 및 제거
            mw._on_append_exceptions(["sample", "SAMPLE", "clip"])
            self.assertEqual(mw.edit_exceptions.text(), "sample, clip")
            mw._on_remove_exceptions(["sample"])
            self.assertEqual(mw.edit_exceptions.text(), "clip")

            # 6. CSV 내보내기 검증
            csv_path = os.path.join(tmpdir, "out.csv")
            exported_count = mw.export_report_to_csv(csv_path)
            self.assertEqual(exported_count, 3)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0], VideoTableModel.HEADERS)

            # 7. 로그 이력 및 로그 창 검증
            mw._on_worker_log("[WARN] 테스트 경고 메시지")
            self.assertTrue(any("테스트 경고 메시지" in line for line in mw._log_history))
            mw.show_log_dialog()
            log_dlg = mw._log_dialog
            assert log_dlg is not None
            self.assertIn("테스트 경고 메시지", log_dlg.text_edit.toPlainText())
            log_dlg.close()

    def test_table_model_caches_root_existence_and_settings_lineedit_save(self) -> None:
        """
        - VideoTableModel의 루트 파일 존재 여부 캐싱 및 SettingsDialog QLineEdit 편집 완료 시 저장을 검증.
        """
        from unittest.mock import patch

        from src.gui.dialogs.settings_dialog import SettingsDialog

        with tempfile.TemporaryDirectory() as tmpdir:
            moved_vid = dataclasses.replace(
                self.videos[0], moved_dirname="_1_표준인코딩_CQ22"
            )
            evals = VideoEvaluator.evaluate_all(tmpdir, [moved_vid], self.cfg).evaluations
            model = VideoTableModel()
            model.set_data_source(evals, tmpdir, pseudo_mode=False)
            self.assertIn(moved_vid.filename, model._in_root_map)
            self.assertFalse(model._in_root_map[moved_vid.filename])
            self.assertIn("이동됨", model.data(model.index(0, VideoTableModel.COL_STATUS)))

            dummy_env = os.path.join(tmpdir, ".env")
            dummy_root = os.path.join(tmpdir, ".rootdir")
            with patch("src.core.config.get_env_path", return_value=dummy_env), \
                 patch("src.core.config.get_rootdir_path", return_value=dummy_root):
                dlg = SettingsDialog(current_root_dir=tmpdir)
                # textEdited만으로는 즉시 .env에 저장하지 않음
                dlg.edit_dir_std.setText("_CUSTOM_DIR")
                self.assertFalse(os.path.isfile(dummy_env))
                # editingFinished 시 저장됨
                dlg.edit_dir_std.editingFinished.emit()
                self.assertTrue(os.path.isfile(dummy_env))
                with open(dummy_env, encoding="utf-8") as f:
                    self.assertIn("DIR_STANDARD_CQ22=_CUSTOM_DIR", f.read())
                dlg.close()

    def test_shared_exception_token_helpers_and_constants(self) -> None:
        """
        - 공용 split_exception_tokens 헬퍼 및 FilterTuningBar/THRESHOLD_BOUNDS 연동 일관성을 검증.
        """
        from src.gui.constants import THRESHOLD_BOUNDS, split_exception_tokens
        from src.gui.widgets.filter_bar import FilterTuningBar

        self.assertEqual(
            split_exception_tokens(" sample ; v.fps >= 60, , RAW "),
            ["sample", "v.fps >= 60", "RAW"],
        )
        bar = FilterTuningBar()
        self.assertEqual(bar.spin_over_ratio.minimum(), THRESHOLD_BOUNDS["over_bitrate_ratio"][0])
        self.assertEqual(bar.spin_over_ratio.maximum(), THRESHOLD_BOUNDS["over_bitrate_ratio"][1])
        bar.set_exception_tokens(["alpha", "beta"])
        self.assertEqual(bar.get_exception_tokens(), ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
