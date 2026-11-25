"""인코딩 창 요약 탭(encode_summary_view)의 브리핑, 상태 칩, 성적, 설정 링크와 테마 검증"""

import dataclasses
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QWidget

from src.core.config import CONTAINER_POLICY_KEEP, RESULT_POLICY_REPLACE, ClassifierConfig
from src.core.encode.encode_impact import summarize_encode_impact
from src.core.encode.encode_progress_values import ResultRow
from src.core.encode.encode_state import EncodeState
from src.core.encode.encode_summary import BRIEF_NET_CAPTION
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.gui.dialogs.encode_summary_view import (
    BRIEF_ENCODER_TIP,
    BRIEF_NET_TIP,
    BRIEF_NET_VALUE_PX,
    BRIEF_TARGET_TIP,
    FINAL_NET_SAVED_CAPTION,
    SETTINGS_LINK_TEXT,
    EncodeStartPlan,
    EncodeSummaryView,
    result_policy_label,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.theme import ThemeAwareMixin
from src.service.encode.encode_job import EncodeSettings
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_LARGE_FONT_PT: float = 13.0
# - 바뀌기 전 2x2 카드의 기본 글꼴 높이와 13pt 최소 폭. 새 카드가 넘지 않아야 함
_OLD_CARD_HEIGHT_PX: int = 170
_OLD_VIEW_MIN_WIDTH_13PT_PX: int = 447
# - 12개 영상 계획(24.2GB -> 21.2GB)의 기대 문구
_EXPECTED_TARGET_LINE: str = "대상 12개, 24.2 GB -> 21.2 GB"
_EXPECTED_ENCODER_LINE: str = "HEVC (NVENC), 화질 기준값 28.0"


class _FakeMedia:
    """요약 탭 트리의 비교 요청을 기록하는 대역"""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.previewed: list[str] = []
        self.compared: list[str] = []

    def open_file(self, path: str) -> None:
        self.opened.append(path)

    def preview_file(self, path: str) -> None:
        self.previewed.append(path)

    def compare_files(self, source: str) -> None:
        self.compared.append(source)


def _plan(
    path: str, *, result_policy: str = "archive_original", tag_mode: str = ENCODE_TAG_MODE_VISIBLE
) -> EncodeStartPlan:
    """시작 전 브리핑 테스트용 계획"""
    ev = make_eval(
        video=make_video(filename=os.path.basename(path), width=1920, height=1080),
        expected_after_mb=50.0,
        expected_delta_mb=50.0,
    )
    settings = dataclasses.replace(
        EncodeSettings.from_config(ClassifierConfig()), result_policy=result_policy, encode_tag_mode=tag_mode
    )
    summary = summarize_encode_impact([ev], CONTAINER_POLICY_KEEP, "h264")
    return EncodeStartPlan(
        evaluations=(ev,),
        paths=(path,),
        summary=summary,
        settings=settings,
        root_dir=os.path.dirname(path),
        target_codec_label="HEVC NVENC",
        baseline_quality=22.0,
    )


def _multi_plan(root: str, count: int) -> EncodeStartPlan:
    """여러 영상을 담은 계획. 대상 줄과 용량 변화 문구를 확인할 때 씀"""
    paths = [os.path.join(root, f"clip{index}.mp4") for index in range(count)]
    for path in paths:
        open(path, "wb").close()
    evaluations = [
        make_eval(
            video=make_video(filename=os.path.basename(path), vid_size_MB=2066.0),
            expected_after_mb=1808.0,
            expected_delta_mb=258.0,
        )
        for path in paths
    ]
    summary = summarize_encode_impact(evaluations, CONTAINER_POLICY_KEEP, "h264")
    return EncodeStartPlan(
        evaluations=tuple(evaluations),
        paths=tuple(paths),
        summary=summary,
        settings=EncodeSettings.from_config(ClassifierConfig()),
        root_dir=root,
        target_codec_label="HEVC (NVENC)",
        baseline_quality=28.0,
    )


class EncodeSummaryViewTests(unittest.TestCase):
    """요약 탭이 시작 전, 진행 중, 완료 후 상태를 올바른 문구와 표시로 바꾸는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.path = os.path.join(self.root, "clip.mp4")
        open(self.path, "wb").close()
        self.media = _FakeMedia()
        self.view = EncodeSummaryView(self.media)
        self.addCleanup(self.view.deleteLater)

    def test_set_plan_fills_brief_tag_tree_and_settings_link(self) -> None:
        """시작 전 계획이 브리핑 타일, 보조 두 줄, 태그 안내, 영상 변환 트리, 설정 링크를 채움"""
        plan = _plan(self.path)
        self.view.set_plan(plan)
        self.assertFalse(self.view.brief_card.isHidden())
        self.assertEqual(self.view.brief_net_caption.text(), BRIEF_NET_CAPTION)
        self.assertIn("절약", self.view.brief_net_value.text())
        self.assertEqual(self.view.brief_target_line.text(), "대상 1개, 100.0 MB -> 50.0 MB")
        self.assertEqual(self.view.brief_encoder_line.text(), "HEVC NVENC, 화질 기준값 22.0")
        self.assertIn("인코딩 태그를 기록합니다", self.view.tag_label.text())
        self.assertIn("인코딩 이력:", self.view.tag_label.toolTip())
        self.assertFalse(self.view.tree.isHidden())
        self.assertFalse(self.view.tree_head.isHidden())
        self.assertEqual(self.view.btn_settings.text(), SETTINGS_LINK_TEXT)
        self.assertIn("화질 기준값 22.0", self.view.btn_settings.toolTip())
        self.assertIn("HEVC NVENC", self.view.btn_settings.toolTip())
        self.assertTrue(self.view.status_host.isHidden())
        self.assertTrue(self.view.attention.isHidden())
        self.assertTrue(self.view.badge.isHidden())

    def test_brief_card_has_one_highlighted_tile_and_two_secondary_lines(self) -> None:
        """배치 요약 카드는 예상 순 절약 타일 하나와 대상, 인코더 보조 두 줄만 둠"""
        self.view.set_plan(_multi_plan(self.root, 12))
        self.assertEqual(self.view.brief_net_caption.text(), BRIEF_NET_CAPTION)
        self.assertEqual(self.view.brief_net_value.text(), "3.0 GB 절약")
        self.assertEqual(self.view.brief_target_line.text(), _EXPECTED_TARGET_LINE)
        self.assertEqual(self.view.brief_encoder_line.text(), _EXPECTED_ENCODER_LINE)
        # - 결과 파일 처리와 옛 2x2 타일 값은 더 이상 카드에 없음
        self.assertFalse(hasattr(self.view, "brief_values"))
        self.assertFalse(hasattr(self.view, "_brief_captions"))

    def test_two_video_plan_shows_the_full_tile_and_line_texts(self) -> None:
        """영상 두 개 계획에서 타일 이름과 값, 보조 두 줄이 승인된 문장 그대로 나옴"""
        plan = _multi_plan(self.root, 2)
        self.view.set_plan(plan)
        self.assertEqual(self.view.brief_net_caption.text(), "예상 순 절약")
        self.assertEqual(self.view.brief_net_value.text(), "516.0 MB 절약")
        self.assertEqual(self.view.brief_target_line.text(), "대상 2개, 4.0 GB -> 3.5 GB")
        self.assertEqual(self.view.brief_encoder_line.text(), "HEVC (NVENC), 화질 기준값 28.0")

    def test_brief_net_value_font_uses_the_approved_pixel_size(self) -> None:
        """큰 순 절약 값 글꼴은 승인된 20px 크기를 씀"""
        self.assertEqual(BRIEF_NET_VALUE_PX, 20)
        font = self.view.brief_net_value.font()
        self.assertEqual(font.pixelSize(), BRIEF_NET_VALUE_PX)
        self.assertTrue(font.bold())

    def test_brief_widgets_carry_the_exact_tooltips(self) -> None:
        """큰 타일과 보조 두 줄의 툴팁이 승인된 문장과 같음"""
        self.view.set_plan(_plan(self.path))
        self.assertEqual(
            self.view.brief_tile.toolTip(),
            "이번 인코딩으로 줄어들 것으로 예상하는 용량입니다. 음수는 오히려 늘어남을 뜻합니다.",
        )
        self.assertEqual(self.view.brief_net_caption.toolTip(), BRIEF_NET_TIP)
        self.assertEqual(self.view.brief_net_value.toolTip(), BRIEF_NET_TIP)
        self.assertEqual(
            self.view.brief_target_line.toolTip(),
            "이번 인코딩 대상 영상 수와, 대상 영상의 현재 용량 합계 및 판정 단계의 예상 용량입니다.",
        )
        self.assertEqual(
            self.view.brief_encoder_line.toolTip(),
            "이번 배치에 쓸 목표 인코딩 방식과 화질 기준값입니다. 환경 설정의 인코딩 탭에서 바꿀 수 있습니다.",
        )
        self.assertEqual(self.view.brief_target_line.toolTip(), BRIEF_TARGET_TIP)
        self.assertEqual(self.view.brief_encoder_line.toolTip(), BRIEF_ENCODER_TIP)

    def test_new_card_is_shorter_and_not_wider_than_the_old_grid(self) -> None:
        """새 카드는 기본 글꼴에서 옛 2x2 카드보다 낮고, 13pt 최소 폭이 옛 값보다 넓지 않음"""
        self.view.set_plan(_multi_plan(self.root, 12))
        self.assertLess(self.view.brief_card.sizeHint().height(), 110)
        self.assertLess(self.view.brief_card.sizeHint().height(), _OLD_CARD_HEIGHT_PX)
        # - 옛 최소 폭을 잰 조건과 같이 테마를 먼저 적용하고 13pt 글꼴로 다시 잼
        self.view.apply_theme(DARK_PALETTE)
        font = QFont(self.view.font())
        font.setPointSizeF(_LARGE_FONT_PT)
        self.view.setFont(font)
        self.view.set_plan(_multi_plan(self.root, 12))
        _APP.processEvents()
        self.assertLessEqual(self.view.minimumSizeHint().width(), _OLD_VIEW_MIN_WIDTH_13PT_PX)
        # - 13pt에서도 보조 줄은 줄바꿈으로 좁은 폭을 견딤
        self.assertTrue(self.view.brief_target_line.wordWrap())
        self.assertTrue(self.view.brief_encoder_line.wordWrap())

    def test_live_running_state_shows_chips_savings_speed_attention_and_badge(self) -> None:
        """진행 중에는 상태 칩, 완료분 절약 대조, 현재 속도, 주의 안내, 진행 배지를 보여 줌"""
        self.view.set_plan(_plan(self.path))
        rows = {
            self.path: ResultRow(
                source_path=self.path,
                output_path=os.path.join(self.root, "out.mp4"),
                source_bytes=100 * 1024 * 1024,
                output_bytes=40 * 1024 * 1024,
                expected_delta_mb=50.0,
            )
        }
        items = [
            (self.path, EncodeState.DONE, ""),
            ("b.mp4", EncodeState.FAILED, "오류"),
            ("c.mp4", EncodeState.PENDING, ""),
        ]
        self.view.set_live(items, rows, "현재 1.25배, 평균 0.50배", 0.5, "00:30", finished=False)
        self.assertFalse(self.view.status_host.isHidden())
        self.assertEqual(list(self.view._status_chips), ["완료", "대기", "실패"])
        self.assertEqual(self.view._status_chips["완료"].text(), "완료 1")
        self.assertIn("완료분 절약: 실제", self.view.live_saving.text())
        self.assertEqual(self.view.live_speed.text(), "현재 1.25배, 평균 0.50배")
        self.assertFalse(self.view.attention.isHidden())
        self.assertEqual(self.view.lbl_attention.text(), "주의가 필요한 영상 1개: 실패 1")
        self.assertEqual(self.view.badge.text(), "1/3개 완료")
        self.assertFalse(self.view.badge.isHidden())

    def test_finished_state_shows_the_final_scorecard_and_drops_zero_chips(self) -> None:
        """완료 후에는 최종 성적과 총 소요를 쓰고, 0개가 된 상태 칩을 지움"""
        self.view.set_plan(_plan(self.path))
        self.view.set_live(
            [(self.path, EncodeState.DONE, ""), ("b.mp4", EncodeState.FAILED, "오류")],
            {},
            "현재 정보 없음, 평균 2.00배",
            2.0,
            "02:30",
            finished=False,
        )
        self.assertEqual(list(self.view._status_chips), ["완료", "실패"])
        self.view.set_live(
            [(self.path, EncodeState.DONE, "")],
            {
                self.path: ResultRow(
                    source_path=self.path,
                    output_path=os.path.join(self.root, "out.mp4"),
                    source_bytes=100 * 1024 * 1024,
                    output_bytes=40 * 1024 * 1024,
                    expected_delta_mb=None,
                )
            },
            "현재 정보 없음, 평균 2.00배",
            2.0,
            "02:30",
            finished=True,
        )
        self.assertEqual(list(self.view._status_chips), ["완료"])
        self.assertEqual(
            self.view.live_saving.text(),
            "최종 성적: 100.0 MB -> 40.0 MB, 실제 60.0 MB, 예상 정보 없음",
        )
        self.assertEqual(self.view.live_speed.text(), "총 소요 02:30, 평균 2.00배")
        self.assertTrue(self.view.attention.isHidden())

    def test_chips_and_banner_emit_their_requests(self) -> None:
        """상태 칩, 설정 링크, 주의 안내 버튼이 각각 이동과 설정 열기 요청을 보냄"""
        received: list[tuple[str, str]] = []
        self.view.status_requested.connect(lambda status: received.append(("status", status)))
        self.view.settings_requested.connect(lambda: received.append(("settings", "")))
        self.view.results_requested.connect(lambda: received.append(("results", "")))
        self.view.set_plan(_plan(self.path))
        self.view.btn_settings.click()
        self.view.set_live([(self.path, EncodeState.FAILED, "오류")], {}, "", None, "00:10", finished=False)
        self.view._status_chips["실패"].click()
        self.view.btn_attention.click()
        self.assertEqual(
            received,
            [("settings", ""), ("status", "실패"), ("results", "")],
        )
        self.assertEqual(self.view.btn_settings.accessibleName(), SETTINGS_LINK_TEXT)

    def test_excluded_count_and_resumed_view(self) -> None:
        """제외한 영상 수를 대상 수에 반영하고, 이어서 시작한 배치는 브리핑과 설정 링크를 숨김"""
        self.view.set_plan(_plan(self.path))
        self.view.set_excluded_count(0, 1)
        self.assertEqual(self.view.target_text, "0/1개")
        self.assertEqual(self.view.brief_target_line.text(), "대상 0/1개, 100.0 MB -> 50.0 MB")
        self.view.set_excluded_count(1, 2)
        self.assertEqual(self.view.brief_target_line.text(), "대상 1/2개, 100.0 MB -> 50.0 MB")
        self.view.set_resumed()
        self.assertTrue(self.view.brief_card.isHidden())
        self.assertTrue(self.view.tree.isHidden())
        self.assertTrue(self.view.tree_head.isHidden())
        self.view.set_live([(self.path, EncodeState.RUNNING, "")], {}, "", None, "00:01", finished=False)
        self.assertTrue(self.view.badge.isHidden())

    def test_compare_current_uses_the_tree_row(self) -> None:
        """현재 트리 영상의 화질 비교가 미디어 대역으로 넘어감"""
        self.view.set_plan(_plan(self.path))
        group = self.view.tree.tree.topLevelItem(0)
        assert group is not None
        child = group.child(0)
        assert child is not None
        self.view.tree.tree.setCurrentItem(child)
        self.view.compare_current()
        self.assertEqual(self.media.compared, [self.path])

    def test_theme_applies_palette_tokens_and_icons(self) -> None:
        """다크와 라이트 팔레트 모두에서 카드와 칩 스타일, 순 절약 초록, 경고 아이콘이 팔레트 토큰을 씀"""
        self.view.set_plan(_plan(self.path, result_policy=RESULT_POLICY_REPLACE))
        self.view.set_live([(self.path, EncodeState.FAILED, "오류")], {}, "", None, "00:01", finished=False)
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(palette=palette.name):
                self.view.apply_theme(palette)
                self.assertIn(palette.bg_card, self.view.styleSheet())
                self.assertIn(palette.fg_maxrate_warn, self.view.styleSheet())
                self.assertFalse(self.view.lbl_warning_icon.pixmap().isNull())
                self.assertFalse(self.view.lbl_attention_icon.pixmap().isNull())
                self.assertIn(palette.accent_blue, self.view.btn_settings.styleSheet())
                self.assertIn(palette.bg_card, self.view._status_chips["실패"].styleSheet())
                self.assertEqual(
                    self.view.brief_net_value.styleSheet(),
                    f"color: {palette.fg_saved_pos};",
                )

    def test_net_saved_tile_turns_green_and_uses_the_final_value_after_finish(self) -> None:
        """예상 순 절약 타일은 절약이면 초록, 완료 뒤에는 이름과 값을 최종 순 절약으로 바꿈"""
        self.view.apply_theme(DARK_PALETTE)
        self.view.set_plan(_plan(self.path))
        self.assertEqual(
            self.view.brief_net_value.styleSheet(),
            f"color: {DARK_PALETTE.fg_saved_pos};",
        )
        self.view.set_live(
            [(self.path, EncodeState.DONE, "")],
            {
                self.path: ResultRow(
                    source_path=self.path,
                    output_path=os.path.join(self.root, "out.mp4"),
                    source_bytes=100 * 1024 * 1024,
                    output_bytes=40 * 1024 * 1024,
                    expected_delta_mb=50.0,
                )
            },
            "",
            None,
            "01:00",
            finished=True,
        )
        self.assertEqual(self.view.brief_net_caption.text(), FINAL_NET_SAVED_CAPTION)
        self.assertEqual(self.view.brief_net_value.text(), "60.0 MB 절약")
        self.assertEqual(
            self.view.brief_net_value.styleSheet(),
            f"color: {DARK_PALETTE.fg_saved_pos};",
        )

    def test_grown_final_net_saved_uses_the_warning_color_and_restart_restores_estimate(self) -> None:
        """완료 결과가 늘어났으면 순 절약 타일을 주의색으로 칠하고, 새 계획은 예상 이름과 초록으로 되돌림"""
        self.view.apply_theme(LIGHT_PALETTE)
        self.view.set_plan(_plan(self.path))
        self.view.set_live(
            [(self.path, EncodeState.DONE, "")],
            {
                self.path: ResultRow(
                    source_path=self.path,
                    output_path=os.path.join(self.root, "out.mp4"),
                    source_bytes=40 * 1024 * 1024,
                    output_bytes=100 * 1024 * 1024,
                    expected_delta_mb=None,
                )
            },
            "",
            None,
            "01:00",
            finished=True,
        )
        self.assertEqual(self.view.brief_net_value.text(), "60.0 MB 증가")
        self.assertEqual(
            self.view.brief_net_value.styleSheet(),
            f"color: {LIGHT_PALETTE.fg_maxrate_warn};",
        )
        self.view.set_plan(_plan(self.path))
        self.assertEqual(self.view.brief_net_caption.text(), BRIEF_NET_CAPTION)
        self.assertEqual(
            self.view.brief_net_value.styleSheet(),
            f"color: {LIGHT_PALETTE.fg_saved_pos};",
        )

    def test_estimated_growth_paints_the_caution_color_before_any_result(self) -> None:
        """시작 전 예상이 늘어남이면 타일 값을 주의색으로 칠함"""
        self.view.apply_theme(DARK_PALETTE)
        plan = dataclasses.replace(
            _plan(self.path),
            evaluations=(make_eval(video=make_video(filename="clip.mp4", vid_size_MB=100.0), expected_after_mb=150.0, expected_delta_mb=-50.0),),
        )
        self.view.set_plan(plan)
        self.assertEqual(self.view.brief_net_value.text(), "50.0 MB 증가")
        self.assertEqual(
            self.view.brief_net_value.styleSheet(),
            f"color: {DARK_PALETTE.fg_maxrate_warn};",
        )

    def test_replace_plan_shows_the_warning_and_hidden_tag_uses_its_notice(self) -> None:
        """원본 교체 계획만 경고를 보이고, 숨은 기록 방식은 그에 맞는 태그 안내를 씀"""
        self.view.set_plan(_plan(self.path, result_policy=RESULT_POLICY_REPLACE, tag_mode=ENCODE_TAG_MODE_HIDDEN))
        self.assertFalse(self.view.warning_box.isHidden())
        self.assertEqual(
            self.view.lbl_warning.text(),
            "원본 1개가 결과 파일로 바뀝니다. 원본은 보관되지 않아 되돌릴 수 없습니다.",
        )
        self.assertIn("숨은 기록만", self.view.tag_label.text())
        self.assertIn("이 방식에서는 숨은 기록과 인코딩 이력이 남습니다.", self.view.tag_label.toolTip())

    def test_archive_plan_hides_the_warning(self) -> None:
        """원본 보관 계획은 경고 상자를 숨김"""
        self.view.set_plan(_plan(self.path))
        self.assertTrue(self.view.warning_box.isHidden())

    def test_settings_link_tooltip_follows_the_record_mode(self) -> None:
        """설정 링크 툴팁의 인코딩 태그 문구가 기록 방식마다 다름"""
        plan = _plan(self.path, tag_mode=ENCODE_TAG_MODE_VISIBLE)
        self.view.set_plan(plan)
        self.assertIn(f"인코딩 태그 {plan.settings.encode_tag_name}", self.view.btn_settings.toolTip())
        self.view.set_plan(_plan(self.path, tag_mode=ENCODE_TAG_MODE_HIDDEN))
        self.assertIn("인코딩 태그 숨은 기록만", self.view.btn_settings.toolTip())
        self.view.set_plan(_plan(self.path, tag_mode=ENCODE_TAG_MODE_OFF))
        self.assertIn("인코딩 태그 기록 안 함", self.view.btn_settings.toolTip())
        self.assertTrue(self.view.btn_settings.toolTip().startswith("환경 설정의 인코딩 탭을 엽니다."))

    def test_finished_hides_the_warning_and_shows_the_final_size_change(self) -> None:
        """완료 후에는 원본 교체 경고를 숨기고 최종 성적에 원본 총합과 결과 총합을 함께 씀"""
        self.view.set_plan(_plan(self.path, result_policy=RESULT_POLICY_REPLACE))
        self.assertFalse(self.view.warning_box.isHidden())
        self.view.set_live(
            [(self.path, EncodeState.DONE, "")],
            {
                self.path: ResultRow(
                    source_path=self.path,
                    output_path=os.path.join(self.root, "out.mp4"),
                    source_bytes=100 * 1024 * 1024,
                    output_bytes=40 * 1024 * 1024,
                    expected_delta_mb=50.0,
                )
            },
            "현재 정보 없음, 평균 정보 없음",
            None,
            "01:00",
            finished=True,
        )
        self.assertTrue(self.view.warning_box.isHidden())
        self.assertEqual(
            self.view.live_saving.text(),
            "최종 성적: 100.0 MB -> 40.0 MB, 실제 60.0 MB, 예상 50.0 MB",
        )
        self.assertEqual(self.view.live_speed.text(), "총 소요 01:00, 평균 정보 없음")

    def test_finished_speed_uses_the_given_average_instead_of_the_summary_text(self) -> None:
        """완료 후 평균 속도는 넘겨받은 값을 포맷하고 전체 속도 문구를 파싱하지 않음"""
        self.view.set_plan(_plan(self.path))
        self.view.set_live(
            [(self.path, EncodeState.DONE, "")], {}, "현재 정보 없음, 평균 9.99배", 1.5, "02:00", finished=True
        )
        self.assertEqual(self.view.live_speed.text(), "총 소요 02:00, 평균 1.50배")

    def test_widgets_have_parents_and_the_view_has_no_theme_listener(self) -> None:
        """요약 탭 위젯은 모두 부모가 있고, 테마 반영은 진행 창 한 경로로 통일됨"""
        self.assertNotIsInstance(self.view, ThemeAwareMixin)
        for widget in self.view.findChildren(QWidget):
            self.assertIsNotNone(widget.parentWidget())


class SummaryHelpersTests(unittest.TestCase):
    """결과 파일 처리 표기 도우미 검증"""

    def test_result_policy_labels(self) -> None:
        """결과 파일 처리 세 값을 승인된 화면 표기로 바꿈"""
        self.assertEqual(result_policy_label(RESULT_POLICY_REPLACE), "원본 교체")
        self.assertEqual(result_policy_label("separate_output"), "별도 폴더에 저장")
        self.assertEqual(result_policy_label("archive_original"), "원본 보관 후 교체")


if __name__ == "__main__":
    unittest.main(verbosity=2)
