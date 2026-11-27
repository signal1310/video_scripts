"""화질 분석 서비스의 캐시 재사용, 배치 진행과 취소, 부분 보존, 활성 화면 조회 검증"""

import os
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest import mock

from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FRAME_ANALYSIS_VERSION,
    METRIC_CROP_ONLY,
    FrameAnalysisThresholds,
    TierScore,
    analysis_version_for_mode,
)
from src.core.analysis.frame_analysis_runner import ProcessStderrRunner, StderrCommandRunner
from src.core.analysis.frame_scan import luma_frame_stats
from src.core.models import ActiveFrame
from src.service import frame_analysis_service as service_module
from src.service.frame_analysis_record import FrameAnalysisRecord, FrameAnalysisRequest
from src.service.frame_analysis_service import (
    INNER_ROUND_WORKERS_FEW,
    INNER_ROUND_WORKERS_SINGLE,
    FrameAnalysisService,
    _inner_round_workers,
    _ScanStatsPool,
)
from src.service.frame_analysis_store import FrameAnalysisStore
from tests._frame_analysis_fakes import FakeStderrRunner, make_frame_record


def _request(
    filename: str,
    *,
    size: int = 100,
    mtime: int = 1,
    width: int = 1920,
    height: int = 1080,
    duration_sec: float = 20.0,
    has_vr_metadata: bool = False,
) -> FrameAnalysisRequest:
    return FrameAnalysisRequest(
        filename=filename,
        path=filename,
        width=width,
        height=height,
        duration_sec=duration_sec,
        filesize_bytes=size,
        mtime_ns=mtime,
        has_vr_metadata=has_vr_metadata,
    )


class _ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        self.db_path = os.path.join(tmp.name, "video_cache.sqlite3")
        self.work_dir = tmp.name
        self.store = FrameAnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def make_service(
        self,
        runner: StderrCommandRunner | None = None,
        *,
        has_vmaf: bool = True,
        mode: str = "balanced",
        scale_algorithm: str = "bicubic",
        video_workers: int = 1,
        thresholds: FrameAnalysisThresholds | None = None,
        use_process_scan: bool = False,
    ) -> FrameAnalysisService:
        service = FrameAnalysisService(
            self.store,
            work_dir=self.work_dir,
            has_vmaf=has_vmaf,
            runner=runner,
            mode=mode,
            scale_algorithm=scale_algorithm,
            video_workers=video_workers,
            thresholds=thresholds or DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
            use_process_scan=use_process_scan,
        )
        self.addCleanup(service.close)
        return service

    def _relaxable_record(self) -> FrameAnalysisRecord:
        """기본 기준에서는 720p, 완화 기준에서는 540p가 후보인 전 프레임 캐시 행"""
        return replace(
            make_frame_record("a.mp4"),
            active_width=1920,
            active_height=1080,
            active_x=0,
            active_y=0,
            has_black_bars=False,
            candidate_tier="720p",
            tier_scores=(
                TierScore("900p", 0.998, 99.0),
                TierScore("720p", 0.997, 98.5),
                TierScore("540p", 0.994, 96.0),
                TierScore("480p", 0.990, 93.0),
            ),
        )


class TestFrameAnalysisService(_ServiceTestCase):
    """배치 실행, 캐시 재사용, 진행과 취소, 활성 화면 조회 검증"""

    def test_batch_measures_and_caches_each_item(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        progress: list[tuple[int, int, str]] = []
        results = service.analyze_batch(
            [_request("a.mp4"), _request("b.mp4")],
            on_progress=lambda completed, total, name, _result: progress.append((completed, total, name)),
        )
        self.assertEqual(set(results), {"a.mp4", "b.mp4"})
        self.assertEqual(progress, [(1, 2, "a.mp4"), (2, 2, "b.mp4")])
        self.assertEqual(self.store.count(), 2)
        self.assertFalse(results["a.mp4"].unmeasurable)

    def test_cache_hit_skips_runner(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        first = service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        second = service.analyze_batch([_request("a.mp4")])
        self.assertEqual(runner.commands, [])
        self.assertEqual(second["a.mp4"], first["a.mp4"])

    def test_relaxed_thresholds_reuse_scores_and_rejudge_candidate(self) -> None:
        """기준 완화 뒤 저장 점수로 후보가 확정되면 다시 재지 않고 새 후보를 쓰는지 검증"""
        self.store.save(self._relaxable_record())
        runner = FakeStderrRunner()
        service = self.make_service(
            runner, thresholds=FrameAnalysisThresholds(vmaf=95.0, ssim=0.993)
        )
        results = service.analyze_batch([_request("a.mp4")])
        self.assertEqual(runner.commands, [])
        self.assertEqual(results["a.mp4"].candidate_tier, "540p")
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)

    def test_stricter_thresholds_remeasure_when_the_next_vmaf_is_missing(self) -> None:
        """기준 강화로 위 규격의 VMAF가 필요해지면 캐시를 쓰지 않고 다시 재는지 검증"""
        record = replace(
            make_frame_record("a.mp4"),
            active_width=3840,
            active_height=2160,
            active_x=0,
            active_y=0,
            has_black_bars=False,
            candidate_tier="1080p",
            tier_scores=(TierScore("1440p", 0.999, None), TierScore("1080p", 0.998, 98.6)),
        )
        self.store.save(record)
        runner = FakeStderrRunner()
        service = self.make_service(runner, thresholds=FrameAnalysisThresholds(vmaf=99.0))
        service.analyze_batch([_request("a.mp4", width=3840, height=2160)])
        self.assertGreater(len(runner.commands), 0)

    def test_load_results_uses_current_thresholds(self) -> None:
        """표 다시 읽기가 현재 기준으로 축소 후보를 다시 판정하는지 검증"""
        self.store.save(self._relaxable_record())
        relaxed = self.make_service(
            thresholds=FrameAnalysisThresholds(vmaf=95.0, ssim=0.993)
        )
        self.assertEqual(relaxed.load_results([_request("a.mp4")])["a.mp4"].candidate_tier, "540p")
        default = self.make_service()
        self.assertEqual(default.load_results([_request("a.mp4")])["a.mp4"].candidate_tier, "720p")

    def test_load_results_reverts_the_active_frame_above_the_area_limit(self) -> None:
        """면적 상한을 낮춘 뒤 표 다시 읽기가 활성 화면을 전체 프레임으로 되돌리는지 검증"""
        self.store.save(
            replace(
                make_frame_record("a.mp4"),
                active_width=1800,
                active_height=1000,
                active_x=60,
                active_y=40,
                has_black_bars=True,
            )
        )
        service = self.make_service(
            thresholds=FrameAnalysisThresholds(active_area_no_bars_ratio=0.5)
        )
        result = service.load_results([_request("a.mp4")])["a.mp4"]
        self.assertFalse(result.active_region.has_black_bars)

    def test_count_cached_reports_only_reusable_rows(self) -> None:
        """실행 전 캐시 재사용 가능 건수를 세고 force면 0으로 보는지 검증"""
        service = self.make_service(FakeStderrRunner())
        self.assertEqual(service.count_cached([]), 0)
        self.assertEqual(service.count_cached([_request("a.mp4")]), 0)
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)
        self.assertEqual(service.count_cached([_request("a.mp4")], force=True), 0)

    def test_version_mismatch_triggers_reanalysis(self) -> None:
        self.store.save(make_frame_record("a.mp4", version=0))
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(runner.commands), 0)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)

    def test_force_reanalyzes_cached_item(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        service.analyze_batch([_request("a.mp4")], force=True)
        self.assertGreater(len(runner.commands), 0)
        self.assertEqual(self.store.count(), 1)

    def test_cancel_after_first_item_keeps_completed_cache(self) -> None:
        cancel = threading.Event()
        service = self.make_service(FakeStderrRunner())

        def _cancel_after_first(completed: int, _total: int, _name: str, _result: object) -> None:
            if completed == 1:
                cancel.set()

        with self.assertRaises(InterruptedError):
            service.analyze_batch(
                [_request("a.mp4"), _request("b.mp4"), _request("c.mp4")],
                cancel_event=cancel,
                on_progress=_cancel_after_first,
            )
        self.assertEqual(self.store.count(), 1)
        self.assertIn(("a.mp4", 100, 1), self.store.load_many([("a.mp4", 100, 1)]))

    def test_cancel_before_start_saves_nothing(self) -> None:
        cancel = threading.Event()
        cancel.set()
        service = self.make_service(FakeStderrRunner())
        with self.assertRaises(InterruptedError):
            service.analyze_batch([_request("a.mp4")], cancel_event=cancel)
        self.assertEqual(self.store.count(), 0)

    def test_duplicate_requests_are_measured_once(self) -> None:
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        calls: list[tuple[int, int]] = []
        results = service.analyze_batch(
            [_request("a.mp4"), _request("a.mp4")],
            on_progress=lambda completed, total, _name, _result: calls.append((completed, total)),
        )
        self.assertEqual(calls, [(1, 1)])
        self.assertEqual(set(results), {"a.mp4"})

    def test_load_active_frames_returns_crop_confirmed_only(self) -> None:
        """잘라내기 확정 영상만 활성 화면으로 돌려주고 설정 끔, 제외, VR 보호는 빼는지 검증"""
        runner = FakeStderrRunner(boxes=[(1600, 1000, 260, 40)])
        service = self.make_service(runner)
        requests = [_request("a.mp4"), _request("b.mp4")]
        service.analyze_batch(requests)
        self.store.save(make_frame_record("old.mp4", version=0))
        frames = service.load_active_frames(
            [*requests, _request("missing.mp4"), _request("old.mp4")]
        )
        self.assertEqual(set(frames), {"a.mp4", "b.mp4"})
        self.assertEqual(frames["a.mp4"], ActiveFrame(1600, 1000, 260, 40))
        self.assertEqual(service.load_active_frames(requests, crop_enabled=False), {})
        self.store.set_crop_exclusion("a.mp4", True)
        self.assertEqual(set(service.load_active_frames(requests)), {"b.mp4"})

    def test_load_active_frames_skips_vr_protected_and_full_frame_rows(self) -> None:
        """VR 보호 행과 검은 여백이 없는 행은 활성 화면으로 넘기지 않는지 검증"""
        service = self.make_service(FakeStderrRunner())
        service.analyze_batch([_request("full.mp4")])
        self.assertEqual(service.load_active_frames([_request("full.mp4")]), {})
        vr_service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        vr_service.analyze_batch([_request("vr.mp4", has_vr_metadata=True)])
        self.assertEqual(vr_service.load_active_frames([_request("vr.mp4", has_vr_metadata=True)]), {})

    def test_crop_review_statuses_report_flag_and_hold(self) -> None:
        """어두운 근거로 지점을 무시한 영상만 표시하고 원본 교체 여부에 따라 보류 상태를 나누는지 검증"""
        service = self.make_service(FakeStderrRunner())
        flagged = replace(make_frame_record("flagged.mp4"), dark_outliers_ignored=True)
        plain = make_frame_record("plain.mp4")
        self.store.save(flagged)
        self.store.save(plain)
        requests = [_request("flagged.mp4"), _request("plain.mp4")]

        separate = service.crop_review_statuses(requests, replace_original=False)
        self.assertEqual(set(separate), {"flagged.mp4"})
        self.assertTrue(separate["flagged.mp4"].flagged)
        self.assertFalse(separate["flagged.mp4"].hold_applies)

        replaced = service.crop_review_statuses(requests, replace_original=True)
        self.assertTrue(replaced["flagged.mp4"].hold_applies)

        # 사용자 지정 활성 화면과 잘라내기 설정 끔은 보류를 풀지만 표시는 남김
        self.store.set_manual_active_frame(
            "flagged.mp4", ActiveFrame(1280, 720, 0, 180), frame_width=1920, frame_height=1080
        )
        self.assertFalse(
            service.crop_review_statuses(requests, replace_original=True)["flagged.mp4"].hold_applies
        )
        self.assertFalse(
            service.crop_review_statuses(
                requests, replace_original=True, crop_enabled=False
            )["flagged.mp4"].hold_applies
        )

    def test_crop_exclusion_roundtrip_keeps_frame_cache(self) -> None:
        """잘라내기 제외 저장, 해제가 분석 캐시를 지우지 않고 경로별로 동작하는지 검증"""
        self.store.save(make_frame_record("clip.mp4"))
        self.store.set_crop_exclusion("C:/work/clip.mp4", True)
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/clip.mp4"]), frozenset({"C:/work/clip.mp4"}))
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/other.mp4"]), frozenset())
        self.assertEqual(self.store.count(), 1)
        self.store.set_crop_exclusion("C:/work/clip.mp4", False)
        self.assertEqual(self.store.load_crop_exclusions(["C:/work/clip.mp4"]), frozenset())
        self.assertEqual(self.store.load_crop_exclusions([]), frozenset())
        self.store.set_crop_exclusion("", True)
        self.assertEqual(self.store.count(), 1)

    def test_manual_active_frame_roundtrip_keeps_frame_cache(self) -> None:
        """사용자 지정 활성 화면 저장, 해제가 분석 캐시를 지우지 않고 경로별로 동작하는지 검증"""
        self.store.save(make_frame_record("clip.mp4"))
        manual = ActiveFrame(1600, 1000, 260, 40)
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4", manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(
            self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)}),
            {"C:/work/clip.mp4": manual},
        )
        self.assertEqual(self.store.load_manual_active_frames({"C:/work/other.mp4": (1920, 1080)}), {})
        self.assertEqual(self.store.load_manual_active_frames({}), {})
        self.assertEqual(self.store.count(), 1)
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4", None, frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)}), {})
        self.store.set_manual_active_frame(
            "", manual, frame_width=1920, frame_height=1080
        )
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_manual_active_frame_value_is_normalized_before_saving(self) -> None:
        """저장 값이 짝수 정렬되고 전체 프레임과 같은 지정은 지움으로 처리되는지 검증"""
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1601, 999, 261, 41),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(
            self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)})["C:/work/clip.mp4"],
            ActiveFrame(1600, 998, 260, 40),
        )
        self.store.set_manual_active_frame(
            "C:/work/clip.mp4",
            ActiveFrame(1920, 1080),
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(self.store.load_manual_active_frames({"C:/work/clip.mp4": (1920, 1080)}), {})

    def test_manual_active_frame_rejects_tiny_and_out_of_bounds_values(self) -> None:
        """최소 크기 미만, 영상 밖 지정은 저장하지 않고 `ValueError`"""
        with self.assertRaises(ValueError):
            self.store.set_manual_active_frame(
                "C:/work/clip.mp4",
                ActiveFrame(31, 1000),
                frame_width=1920,
                frame_height=1080,
            )
        with self.assertRaises(ValueError):
            self.store.set_manual_active_frame(
                "C:/work/clip.mp4",
                ActiveFrame(1600, 1000, 400, 40),
                frame_width=1920,
                frame_height=1080,
            )
        self.assertEqual(self.store.count_manual_active_frames(), 0)

    def test_manual_active_frame_overrides_automatic_and_exclusion(self) -> None:
        """사용자 지정 활성 화면이 자동 감지 결과와 제외를 덮어쓰고 설정 끔, VR 보호는 못 넘는지 검증"""
        service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        requests = [_request("a.mp4")]
        service.analyze_batch(requests)
        manual = ActiveFrame(1280, 720, 0, 180)
        self.store.set_manual_active_frame("a.mp4", manual, frame_width=1920, frame_height=1080)
        self.assertEqual(service.load_active_frames(requests), {"a.mp4": manual})
        self.store.set_crop_exclusion("a.mp4", True)
        self.assertEqual(service.load_active_frames(requests), {"a.mp4": manual})
        self.assertEqual(service.load_active_frames(requests, crop_enabled=False), {})

    def test_manual_active_frame_is_ignored_for_vr_protected_video(self) -> None:
        """VR 보호 영상은 직접 지정이 있어도 활성 화면을 넘기지 않는지 검증"""
        requests = [_request("vr.mp4", has_vr_metadata=True)]
        service = self.make_service(FakeStderrRunner(boxes=[(1600, 1000, 260, 40)]))
        service.analyze_batch(requests)
        self.store.set_manual_active_frame(
            "vr.mp4", ActiveFrame(1280, 720, 0, 180), frame_width=1920, frame_height=1080
        )
        self.assertEqual(service.load_active_frames(requests), {})

    def test_runner_for_returns_injected_or_default(self) -> None:
        injected = FakeStderrRunner()
        service = self.make_service(injected)
        self.assertIs(service._runner_for(None), injected)
        default_service = self.make_service()
        self.assertIsInstance(default_service._runner_for(None), ProcessStderrRunner)

    def test_ssim_only_service_records_metric_name(self) -> None:
        service = self.make_service(FakeStderrRunner(), has_vmaf=False)
        result = service.analyze_batch([_request("a.mp4")])["a.mp4"]
        self.assertEqual(result.metric_used, "ssim")
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.metric_used, "ssim")
        self.assertEqual(record.tier_scores, result.tier_scores)

    def test_crop_only_scan_saves_a_detection_row_without_metrics(self) -> None:
        """감지 전용 실행이 SSIM/VMAF 명령 없이 활성 화면만 저장하는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        service = self.make_service(runner)
        request = _request("a.mp4")
        result = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(runner.count_commands("-filter_complex"), 0)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, CROP_SCAN_VERSION)
        self.assertEqual(record.metric_used, METRIC_CROP_ONLY)
        self.assertEqual(record.tier_scores, ())
        self.assertIsNone(record.candidate_tier)
        self.assertEqual(record.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(result.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(service.load_active_frames([request]), {"a.mp4": ActiveFrame(720, 1080, 600, 0)})

    def test_crop_only_scan_failure_is_not_reused(self) -> None:
        """스캔에 실패한 감지 전용 행이 검은 여백 없음으로 재사용되지 않는지 검증"""
        failing = FakeStderrRunner(black_returncode=1)
        service = self.make_service(failing)
        request = _request("a.mp4")
        failed = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertTrue(failed.unmeasurable)
        self.assertEqual(failed.valid_segment_count, 0)
        self.assertEqual(service.count_cached([request], crop_only=True), 0)

        healthy = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        retried = self.make_service(healthy)
        result = retried.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertGreater(healthy.count_commands("pgm"), 0)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(retried.count_cached([request], crop_only=True), 1)

    def test_old_crop_only_row_without_valid_points_is_rescanned(self) -> None:
        """이전 감지 규칙이 실패를 검은 화면으로 저장한 감지 전용 행을 다시 재는지 검증"""
        request = _request("a.mp4")
        self.store.save(
            replace(
                make_frame_record("a.mp4", version=228),
                metric_used=METRIC_CROP_ONLY,
                vmaf_model="",
                tier_scores=(),
                candidate_tier=None,
                valid_segments=0,
                unmeasurable=False,
            )
        )
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        service = self.make_service(runner)
        self.assertEqual(service.count_cached([request], crop_only=True), 0)
        result = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertGreater(runner.count_commands("pgm"), 0)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(service.count_cached([request], crop_only=True), 1)

    def test_crop_only_vr_scan_failure_stays_reusable(self) -> None:
        """VR 보호 영상은 감지 결과를 쓰지 않으므로 스캔 실패 행도 캐시로 재사용하는지 검증"""
        runner = FakeStderrRunner(black_returncode=1)
        service = self.make_service(runner)
        request = _request("vr.mp4", width=3840, height=1920, has_vr_metadata=True)
        result = service.analyze_batch([request], crop_only=True)["vr.mp4"]
        self.assertTrue(result.is_vr_protected)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(service.count_cached([request], crop_only=True), 1)
        runner.commands.clear()
        service.analyze_batch([request], crop_only=True)
        self.assertEqual(runner.commands, [])

    def test_crop_only_all_black_points_stays_reusable(self) -> None:
        """모든 지점이 검은 화면인 감지 전용 행은 감지에 성공했으므로 캐시로 재사용되는지 검증"""
        runner = FakeStderrRunner(black_frame=True)
        service = self.make_service(runner)
        request = _request("a.mp4")
        result = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(service.count_cached([request], crop_only=True), 1)
        runner.commands.clear()
        reused = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(runner.commands, [])
        self.assertEqual(reused.active_region, result.active_region)
        self.assertFalse(reused.unmeasurable)

    def test_crop_only_partial_failure_with_black_points_stays_reusable(self) -> None:
        """일부 지점만 실패하고 나머지가 검은 화면이면 감지 성공으로 보고 캐시로 재사용하는지 검증"""
        runner = FakeStderrRunner(black_frame=True)
        scanned = 0

        def fail_last_point(args: list[str]) -> None:
            nonlocal scanned
            if any(token == "pgm" for token in args):
                scanned += 1
                if scanned == 9:
                    runner.black_returncode = 1

        runner.on_call = fail_last_point
        service = self.make_service(runner)
        request = _request("a.mp4")
        result = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.valid_segment_count, 0)
        self.assertEqual(service.count_cached([request], crop_only=True), 1)
        runner.commands.clear()
        service.analyze_batch([request], crop_only=True)
        self.assertEqual(runner.commands, [])

    def test_crop_only_vr_and_zero_duration_rows_stay_reusable(self) -> None:
        """VR 보호와 구간 없는 감지 전용 행은 스캔이 실패하지 않았으므로 다시 재지 않는지 검증"""
        black_runner = FakeStderrRunner(black_frame=True)
        vr_service = self.make_service(black_runner)
        vr_request = _request("vr.mp4", width=3840, height=1920, has_vr_metadata=True)
        vr_result = vr_service.analyze_batch([vr_request], crop_only=True)["vr.mp4"]
        self.assertTrue(vr_result.is_vr_protected)
        self.assertFalse(vr_result.unmeasurable)
        self.assertEqual(vr_service.count_cached([vr_request], crop_only=True), 1)

        empty_service = self.make_service(FakeStderrRunner())
        empty_request = _request("empty.mp4", duration_sec=0.0)
        empty_result = empty_service.analyze_batch([empty_request], crop_only=True)["empty.mp4"]
        self.assertFalse(empty_result.unmeasurable)
        self.assertEqual(empty_result.tier_scores, ())
        self.assertEqual(empty_service.count_cached([empty_request], crop_only=True), 1)

    def test_crop_only_rescans_a_full_analysis_row_whose_scan_failed(self) -> None:
        """9지점 스캔이 모두 실패한 전체 분석 행을 감지 전용 실행이 다시 스캔하는지 검증"""
        failing = FakeStderrRunner(black_returncode=1)
        service = self.make_service(failing)
        request = _request("a.mp4")
        failed = service.analyze_batch([request])["a.mp4"]
        self.assertTrue(failed.crop_scan_failed)
        self.assertEqual(service.count_cached([request], crop_only=True), 0)

        healthy = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        retried = self.make_service(healthy)
        result = retried.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertGreater(healthy.count_commands("pgm"), 0)
        self.assertFalse(result.crop_scan_failed)
        self.assertFalse(result.unmeasurable)
        self.assertEqual(result.active_region.to_active_frame(), ActiveFrame(720, 1080, 600, 0))
        self.assertEqual(retried.count_cached([request], crop_only=True), 1)

    def test_full_analysis_with_all_black_points_is_reused_by_crop_only(self) -> None:
        """모든 지점이 검은 화면인 전체 분석 행은 감지에 성공했으므로 감지 전용 실행이 재사용하는지 검증"""
        black = FakeStderrRunner(black_frame=True)
        service = self.make_service(black)
        request = _request("a.mp4")
        self.assertTrue(service.analyze_batch([request])["a.mp4"].unmeasurable)
        black.commands.clear()
        service.analyze_batch([request], crop_only=True)
        self.assertEqual(black.commands, [])

    def test_crop_only_reuses_a_full_analysis_row_and_keeps_its_scores(self) -> None:
        """전체 화질 분석 행 위의 감지 실행이 캐시를 재사용해 후보와 점수를 보존하는지 검증"""
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        service = self.make_service(runner)
        request = _request("a.mp4")
        service.analyze_batch([request])
        runner.commands.clear()
        reused = service.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(runner.commands, [])
        self.assertEqual(service.count_cached([request], crop_only=True), 1)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertEqual(record.candidate_tier, "360p")
        self.assertEqual(reused.candidate_tier, "360p")
        self.assertTrue(reused.tier_scores)

    def test_full_analysis_promotes_a_crop_only_row(self) -> None:
        """감지 전용 행 위의 전체 화질 분석이 캐시 미스로 측정해 전체 결과로 승격하는지 검증"""
        crop_service = self.make_service(FakeStderrRunner(boxes=[(720, 1080, 600, 0)]))
        request = _request("a.mp4")
        crop_service.analyze_batch([request], crop_only=True)
        self.assertEqual(
            self.store.load_many([request.key])[request.key].analysis_version, CROP_SCAN_VERSION
        )
        full_runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        full_service = self.make_service(full_runner)
        self.assertEqual(full_service.count_cached([request]), 0)
        result = full_service.analyze_batch([request])["a.mp4"]
        self.assertGreater(full_runner.count_commands("-filter_complex"), 0)
        record = self.store.load_many([request.key])[request.key]
        self.assertEqual(record.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertIsNotNone(record.candidate_tier)
        self.assertEqual(result.analysis_version, FRAME_ANALYSIS_VERSION)
        self.assertEqual(full_service.count_cached([request]), 1)

    def test_crop_only_row_stays_valid_after_a_quality_mode_change(self) -> None:
        """화질 분석 방식을 바꿔도 감지 전용 행의 활성 화면이 판정과 표에 남는지 검증"""
        request = _request("a.mp4")
        self.make_service(
            FakeStderrRunner(boxes=[(720, 1080, 600, 0)]), mode="balanced"
        ).analyze_batch([request], crop_only=True)
        precise = self.make_service(mode="precise")
        self.assertEqual(precise.count_cached([request], crop_only=True), 1)
        self.assertEqual(precise.load_active_frames([request]), {"a.mp4": ActiveFrame(720, 1080, 600, 0)})
        self.assertEqual(set(precise.load_results([request])), {"a.mp4"})
        again = precise.analyze_batch([request], crop_only=True)["a.mp4"]
        self.assertEqual(again.analysis_version, CROP_SCAN_VERSION)
        self.assertEqual(self.store.count(), 1)

    def test_manual_vr_or_metric_change_invalidates_cached_row(self) -> None:
        """VR 보호 설정이나 측정 지표가 캐시와 다르면 다시 분석함을 검증"""
        runner = FakeStderrRunner()
        service = self.make_service(runner)
        service.analyze_batch([_request("a.mp4")])
        runner.commands.clear()
        service.analyze_batch([_request("a.mp4", has_vr_metadata=True)])
        self.assertGreater(len(runner.commands), 0)
        runner.commands.clear()
        ssim_runner = FakeStderrRunner()
        ssim_service = self.make_service(ssim_runner, has_vmaf=False)
        ssim_service.analyze_batch([_request("a.mp4", has_vr_metadata=True)])
        self.assertGreater(len(ssim_runner.commands), 0)

    def test_analyze_creates_missing_work_dir_and_lists_results(self) -> None:
        """작업 폴더가 없으면 만들고, load_results가 캐시 결과만 돌려줌을 검증"""
        work_dir = os.path.join(self.tmp_dir, "nested", "work")
        service = FrameAnalysisService(
            self.store,
            work_dir=work_dir,
            has_vmaf=True,
            runner=FakeStderrRunner(),
            video_workers=1,
        )
        self.addCleanup(service.close)
        requests = [_request("a.mp4"), _request("missing.mp4", size=200, mtime=2)]
        service.analyze_batch([_request("a.mp4")])
        self.assertTrue(os.path.isdir(work_dir))
        self.assertEqual(set(service.load_results(requests)), {"a.mp4"})
        self.store.save(make_frame_record("old.mp4", version=0))
        self.assertEqual(service.load_results([_request("old.mp4")]), {})


    def test_mode_change_invalidates_cache_and_reanalyzes(self) -> None:
        """방식이 바뀌면 캐시 버전이 달라져 다시 재고 새 버전으로 저장하는지 검증"""
        service = self.make_service(FakeStderrRunner(), mode="balanced")
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(self.store.count(), 1)
        fast_runner = FakeStderrRunner()
        fast_service = self.make_service(fast_runner, mode="fast")
        self.assertEqual(fast_service.count_cached([_request("a.mp4")]), 0)
        fast_service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(fast_runner.commands), 0)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(record.analysis_version, analysis_version_for_mode("fast"))

    def test_scale_algorithm_change_invalidates_cache_and_changes_filters(self) -> None:
        """보간 방식을 바꾸면 캐시를 다시 재고 필터에도 그 방식이 들어가는지 검증"""
        service = self.make_service(FakeStderrRunner(), scale_algorithm="bicubic")
        service.analyze_batch([_request("a.mp4")])
        self.assertEqual(service.count_cached([_request("a.mp4")]), 1)
        lanczos_runner = FakeStderrRunner()
        lanczos_service = self.make_service(lanczos_runner, scale_algorithm="lanczos")
        self.assertEqual(lanczos_service.count_cached([_request("a.mp4")]), 0)
        lanczos_service.analyze_batch([_request("a.mp4")])
        self.assertGreater(len(lanczos_runner.commands), 0)
        chains = [
            args[args.index("-filter_complex") + 1]
            for args in lanczos_runner.commands
            if "-filter_complex" in args
        ]
        self.assertTrue(chains)
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertIn("flags=lanczos", chain)
                self.assertNotIn("flags=bicubic", chain)
        record = self.store.load_many([("a.mp4", 100, 1)])[("a.mp4", 100, 1)]
        self.assertEqual(
            record.analysis_version, analysis_version_for_mode("balanced", "lanczos")
        )

    def test_default_algorithm_keeps_the_existing_analysis_version(self) -> None:
        """기본 보간 방식은 기존 화질 분석 방식의 캐시 버전을 그대로 쓰는지 검증"""
        self.assertEqual(analysis_version_for_mode("balanced"), FRAME_ANALYSIS_VERSION)
        self.assertEqual(
            analysis_version_for_mode("balanced", "bicubic"), FRAME_ANALYSIS_VERSION
        )
        self.assertNotEqual(
            analysis_version_for_mode("balanced", "lanczos"),
            analysis_version_for_mode("balanced", "spline"),
        )

    def test_stage_callback_reports_current_video_and_stage(self) -> None:
        service = self.make_service(FakeStderrRunner())
        stages: list[tuple[str, str, int, int]] = []
        service.analyze_batch(
            [_request("a.mp4")],
            on_stage=lambda name, stage, done, total: stages.append((name, stage, done, total)),
        )
        self.assertTrue(stages)
        self.assertTrue(all(name == "a.mp4" for name, _stage, _done, _total in stages))
        self.assertEqual(stages[0][1], "scan")

    def test_parallel_video_workers_analyze_all_requests(self) -> None:
        runner = FakeStderrRunner(vmaf_by_tier={"360p": 99.6})
        service = self.make_service(runner, video_workers=2)
        results = service.analyze_batch([_request("a.mp4"), _request("b.mp4")])
        self.assertEqual(set(results), {"a.mp4", "b.mp4"})
        self.assertEqual(self.store.count(), 2)


class RecordingScanStatsPool(_ScanStatsPool):
    """종료 여부를 기록하는 스캔 풀. 테스트가 배치 뒤 풀이 닫혔는지 봄"""

    def __init__(self, workers: int, *, cancel_event: threading.Event | None = None) -> None:
        super().__init__(workers, cancel_event=cancel_event)
        self.closed: bool = False

    def close(self) -> None:
        self.closed = True
        super().close()


class TestProcessScanPool(_ServiceTestCase):
    """Y 평면 판정 프로세스 풀이 같은 결과를 내고 취소, 종료에 풀을 닫는지 검증"""

    def _payloads(self) -> list[tuple[bytes, int, int, int]]:
        letterbox = b"".join(
            [bytes([16] * 32) for _ in range(3)]
            + [bytes([120] * 32) for _ in range(18)]
            + [bytes([16] * 32) for _ in range(3)]
        )
        padded = b"".join(bytes([16] * 24) + bytes([0, 0, 0, 0, 0, 0, 0, 0]) for _ in range(16))
        return [
            (letterbox, 32, 32, 24),
            (bytes([16] * 64), 8, 8, 8),
            (padded, 32, 24, 16),
        ]

    def test_pool_and_in_process_stats_are_identical(self) -> None:
        """프로세스 풀을 거친 판정이 같은 프로세스의 판정과 완전히 같은지 검증"""
        pool = _ScanStatsPool(2)
        self.addCleanup(pool.close)
        for payload in self._payloads():
            with self.subTest(line_size=payload[1], width=payload[2]):
                self.assertEqual(pool(*payload), luma_frame_stats(*payload))

    def test_cancel_raises_and_close_releases_the_workers(self) -> None:
        """취소 신호가 서면 대기를 끊고, 종료하면 워커 풀 참조를 놓는지 검증"""
        cancel = threading.Event()
        pool = _ScanStatsPool(1, cancel_event=cancel)
        payload = self._payloads()[0]
        self.assertEqual(pool(*payload), luma_frame_stats(*payload))
        cancel.set()
        with self.assertRaises(InterruptedError):
            pool(*payload)
        pool.close()
        self.assertIsNone(pool._executor)
        pool.close()

    def test_closed_pool_is_safe_to_close_again(self) -> None:
        """한 번도 쓰지 않은 풀을 여러 번 닫아도 예외가 없는지 검증"""
        pool = _ScanStatsPool(1)
        pool.close()
        pool.close()
        self.assertIsNone(pool._executor)

    def test_service_cancel_stops_the_batch_and_closes_the_pool(self) -> None:
        """배치 도중 취소되면 스캔을 끊고 만든 풀을 닫는지 검증"""
        pool = self.make_service_with_process_scan()
        cancel = threading.Event()
        runner = FakeStderrRunner(on_call=lambda _args: cancel.set())
        service = self.make_service(runner, use_process_scan=True)
        with self.assertRaises(InterruptedError):
            service.analyze_batch(
                [_request("a.mp4")], crop_only=True, cancel_event=cancel
            )
        self.assertTrue(pool[0].closed)

    def make_service_with_process_scan(self) -> list[RecordingScanStatsPool]:
        """서비스가 만드는 스캔 풀을 기록하는 팩토리를 설치하고 만들어진 풀 목록을 돌려줌"""
        created: list[RecordingScanStatsPool] = []

        def factory(workers: int, *, cancel_event: threading.Event | None = None) -> RecordingScanStatsPool:
            created.append(RecordingScanStatsPool(workers, cancel_event=cancel_event))
            return created[-1]

        patcher = mock.patch.object(service_module, "_ScanStatsPool", factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return created

    def test_service_with_process_scan_matches_in_process_results(self) -> None:
        """프로세스 풀 스캔이 같은 프로세스 스캔과 같은 감지 결과를 내는지 검증"""
        runner = FakeStderrRunner(boxes=[(1280, 720, 0, 0)])
        in_process = self.make_service(runner).analyze_batch([_request("a.mp4")], crop_only=True)
        in_pool = self.make_service(runner, use_process_scan=True).analyze_batch(
            [_request("a.mp4")], crop_only=True
        )
        self.assertEqual(in_pool["a.mp4"].active_region, in_process["a.mp4"].active_region)
        self.assertEqual(in_pool["a.mp4"].crop_scan_failed, in_process["a.mp4"].crop_scan_failed)
        self.assertEqual(in_pool["a.mp4"].metric_used, METRIC_CROP_ONLY)


class TestInnerRoundWorkers(unittest.TestCase):
    """영상 수와 실행기 주입 여부에 따른 내부 라운드 워커 수 검증"""

    def test_worker_count_by_video_count(self) -> None:
        self.assertEqual(_inner_round_workers(1, runner_injected=False), INNER_ROUND_WORKERS_SINGLE)
        self.assertEqual(_inner_round_workers(3, runner_injected=False), INNER_ROUND_WORKERS_FEW)
        self.assertEqual(_inner_round_workers(4, runner_injected=False), 1)
        self.assertEqual(_inner_round_workers(1, runner_injected=True), 1)


if __name__ == "__main__":
    unittest.main()
