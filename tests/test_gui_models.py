import dataclasses
import os
import tempfile
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.models import QualityZone, VideoProps
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel, format_duration

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class TestGUIModelsModule(unittest.TestCase):
    """
    - PyQt6 테이블 뷰모델, 자연 정렬/필터 프록시, 포맷 및 루트 파일 존재 캐싱을 검증.
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

    def test_table_model_caches_root_existence(self) -> None:
        """
        - VideoTableModel의 루트 파일 존재 여부 캐싱과 이동됨 상태 표시를 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            moved_vid = dataclasses.replace(
                self.videos[0], moved_dirname="_1_표준인코딩_CQ22"
            )
            evals = VideoEvaluator.evaluate_all(tmpdir, [moved_vid], self.cfg).evaluations
            model = VideoTableModel(config=self.cfg)
            model.set_data_source(evals, tmpdir, pseudo_mode=False)
            self.assertIn(moved_vid.filename, model._in_root_map)
            self.assertFalse(model._in_root_map[moved_vid.filename])
            self.assertIn("이동됨", model.data(model.index(0, VideoTableModel.COL_STATUS)))

            # 실제 하위 폴더에 파일이 존재하는 경우 pseudo_mode=True여도 '이동됨'으로 표시
            subdir = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            os.makedirs(subdir, exist_ok=True)
            with open(os.path.join(subdir, moved_vid.filename), "wb") as f:
                f.write(b"moved-bytes")
            model.set_data_source(evals, tmpdir, pseudo_mode=True)
            self.assertIn(moved_vid.filename, model._in_moved_map)
            self.assertTrue(model._in_moved_map[moved_vid.filename])
            self.assertIn("이동됨", model.data(model.index(0, VideoTableModel.COL_STATUS)))

    def test_table_model_dynamic_header_tooltips(self) -> None:
        """
        - VideoTableModel.set_config() 호출 시 화질 등급·키프레임 헤더 툴팁이 동적으로 갱신됨을 검증.
        """
        model = VideoTableModel(config=ClassifierConfig())
        zone_tip_default = model.headerData(
            VideoTableModel.COL_ZONE,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        kf_tip_default = model.headerData(
            VideoTableModel.COL_KEYFRAME,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )
        self.assertIsInstance(zone_tip_default, str)
        self.assertIsInstance(kf_tip_default, str)
        self.assertIn("1.20배", str(zone_tip_default))
        self.assertIn("2.2초", str(kf_tip_default))

        custom_cfg = ClassifierConfig(
            over_bitrate_ratio=1.5,
            low_bitrate_ratio=0.9,
            keyframe_threshold=3.5,
        )
        model.set_config(custom_cfg)
        zone_tip_updated = str(
            model.headerData(
                VideoTableModel.COL_ZONE,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        kf_tip_updated = str(
            model.headerData(
                VideoTableModel.COL_KEYFRAME,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        self.assertIn("1.50배", zone_tip_updated)
        self.assertIn("0.90배", zone_tip_updated)
        self.assertIn("3.5초", kf_tip_updated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
