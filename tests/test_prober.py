import subprocess
import tempfile
import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch

from src.core.models import VideoProps
from src.core.probe_parser import (
    get_rotate_type,
    parse_fps,
    parse_mean_keyframe_interval,
    safe_dict,
)
from src.core.prober import (
    _init_prober_worker,
    extract_keyframe_interval,
    extract_video_props,
    populate_keyframes,
    probe_directory,
    run_ffprobe,
)


class TestProberModule(unittest.TestCase):
    """
    - ffprobe 출력 파싱, 분수형 프레임레이트 계산, 회전 각도 추출 및 스트림 필터링의 예외 방어력을 검증.
    """

    def test_safe_dict_helper(self) -> None:
        """
        - safe_dict가 딕셔너리가 아닌 객체나 None 값에 대해 기본값을 안전하게 반환하는지 검증.
        """
        self.assertEqual(safe_dict({"a": 1}, "a"), 1)
        self.assertEqual(safe_dict({"a": None}, "a", default=10), 10)
        self.assertEqual(safe_dict(None, "a", default=20), 20)
        self.assertEqual(safe_dict(cast("Any", "not_a_dict"), "a", default=30), 30)

    def test_parse_fps_valid_and_malformed(self) -> None:
        """
        - 다양한 분수형 문자열, 0으로 나누기, 음수, 문자열 오류 등에 대한 parse_fps 방어력을 검증.
        """
        self.assertAlmostEqual(parse_fps("30000/1001"), 29.97003, places=4)
        self.assertAlmostEqual(parse_fps("60/1"), 60.0)
        self.assertAlmostEqual(parse_fps("24"), 24.0)

        # 비정상 포맷 방어: 기본값 30.0 반환
        self.assertEqual(parse_fps("0/0"), 30.0)
        self.assertEqual(parse_fps("30/0"), 30.0)
        self.assertEqual(parse_fps("-30/1"), 30.0)
        self.assertEqual(parse_fps("invalid"), 30.0)
        self.assertEqual(parse_fps(""), 30.0)
        self.assertEqual(parse_fps(None), 30.0)
        self.assertEqual(parse_fps(0), 30.0)

    def test_get_rotate_type_side_data_and_tags(self) -> None:
        """
        - 비디오 스트림의 side_data_list(Display Matrix) 및 tags 회전 메타데이터 파싱을 검증.
        """
        # side_data_list 회전 감지
        stream_side = {
            "side_data_list": [{"rotation": -90.0}],
            "tags": {"rotate": "180"},
        }
        self.assertEqual(get_rotate_type(stream_side), -90)

        # tags 회전 감지
        stream_tags = {"tags": {"rotate": "270"}}
        self.assertEqual(get_rotate_type(stream_tags), 270)

        # 메타데이터 없음
        self.assertEqual(get_rotate_type({}), 0)
        self.assertEqual(get_rotate_type(None), 0)

    @patch("src.core.prober.run_ffprobe")
    @patch("os.path.getsize", return_value=10 * 1024 * 1024)
    def test_extract_video_props_attached_pic_filter(
        self, _mock_getsize: MagicMock, mock_probe: MagicMock
    ) -> None:
        """
        - 앨범 아트 이미지(attached_pic=1) 스트림을 필터링하고 실제 동영상 스트림을 정확히 선택하는지 검증.
        """
        mock_probe.return_value = {
            "streams": [
                {
                    "codec_type": "video",
                    "disposition": {"attached_pic": 1},
                    "width": 500,
                    "height": 500,
                    "codec_name": "mjpeg",
                },
                {
                    "codec_type": "video",
                    "disposition": {"attached_pic": 0},
                    "width": 1920,
                    "height": 1080,
                    "codec_name": "h264",
                    "bit_rate": "3000000",
                    "duration": "10.0",
                },
                {
                    "codec_type": "audio",
                    "bit_rate": "128000",
                },
            ],
            "format": {"duration": "10.0", "bit_rate": "3128000"},
        }

        props = extract_video_props("C:/dummy/test.mp4", "test.mp4", include_keyframe_interval=False)
        self.assertEqual(props.width, 1920)
        self.assertEqual(props.height, 1080)
        self.assertEqual(props.codec, "h264")
        self.assertEqual(props.vid_kbps, 3000)

    @patch("src.core.prober.run_ffprobe")
    @patch("os.path.getsize", return_value=10 * 1024 * 1024)
    def test_extract_video_props_rotation_dimension_swap(
        self, _mock_getsize: MagicMock, mock_probe: MagicMock
    ) -> None:
        """
        - 90도/270도 회전 시 해상도가 반전되고 180도 회전 시에는 반전되지 않는지 검증.
        """
        # 90도 회전
        mock_probe.return_value = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "side_data_list": [{"rotation": 90}],
                }
            ],
            "format": {"duration": "5.0"},
        }
        props_90 = extract_video_props("C:/dummy/rot90.mp4", "rot90.mp4", False)
        self.assertEqual(props_90.width, 1080)
        self.assertEqual(props_90.height, 1920)

        # 180도 회전
        mock_probe.return_value = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "side_data_list": [{"rotation": 180}],
                }
            ],
            "format": {"duration": "5.0"},
        }
        props_180 = extract_video_props("C:/dummy/rot180.mp4", "rot180.mp4", False)
        self.assertEqual(props_180.width, 1920)
        self.assertEqual(props_180.height, 1080)

    @patch("src.core.prober.run_ffprobe")
    @patch("os.path.getsize", return_value=5 * 1024 * 1024)
    def test_extract_video_props_bitrate_fallback_chain(
        self, _mock_getsize: MagicMock, mock_probe: MagicMock
    ) -> None:
        """
        - 스트림 비트레이트 누락 시 포맷 비트레이트 및 파일 크기/duration 기반 역산 로직을 검증.
        """
        # 비디오 스트림 비트레이트 누락, 포맷 비트레이트 존재 (총 1000kbps, 오디오 128kbps)
        mock_probe.return_value = {
            "streams": [
                {"codec_type": "video", "width": 1280, "height": 720},
                {"codec_type": "audio", "bit_rate": "128000"},
            ],
            "format": {"duration": "10.0", "bit_rate": "1000000"},
        }
        props_fmt = extract_video_props("C:/dummy/test.mp4", "test.mp4", False)
        self.assertEqual(props_fmt.vid_kbps, 872)

        # 둘 다 누락: filesize(5MB) * 8 / 10s = 4,000,000 bps - 128k = 3872 kbps
        mock_probe.return_value = {
            "streams": [
                {"codec_type": "video", "width": 1280, "height": 720},
                {"codec_type": "audio", "bit_rate": "128000"},
            ],
            "format": {"duration": "10.0"},
        }
        props_calc = extract_video_props("C:/dummy/test.mp4", "test.mp4", False)
        self.assertGreater(props_calc.vid_kbps, 3800)

    def test_extract_keyframe_interval_short_clip(self) -> None:
        """
        - duration이 1.0초 이하인 초단편 영상의 경우 probe 호출 없이 duration 자체를 즉시 반환하는지 검증.
        """
        interval = extract_keyframe_interval("C:/dummy/short.mp4", duration=0.8)
        self.assertEqual(interval, 0.8)

    def test_run_ffprobe_timeout_kills_process_and_raises(self) -> None:
        """
        - ffprobe 응답 지연 시 타임아웃으로 자식 프로세스를 종료하고 extract_keyframe_interval은 폴백을 반환하는지 검증.
        """
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=0.05),
            (b"", b""),
        ]

        with (
            patch("src.core.prober.subprocess.Popen", return_value=mock_proc),
            patch("src.core.prober.time.monotonic", side_effect=[0.0, 0.01, 0.05]),
            self.assertRaises(TimeoutError),
        ):
            run_ffprobe("C:/dummy/hung.mp4", timeout_sec=0.04)
        mock_proc.kill.assert_called_once()

        with patch(
            "src.core.prober.run_ffprobe",
            side_effect=TimeoutError("ffprobe timeout"),
        ):
            self.assertEqual(
                extract_keyframe_interval("C:/dummy/hung.mp4", duration=12.0),
                10.0,
            )

    def test_run_ffprobe_cancel_event_kills_process_and_propagates(self) -> None:
        """
        - 워커 중단 이벤트 활성화 시 실행 중인 ffprobe를 종료하고 InterruptedError를 전파하는지 검증.
        """
        import src.core.prober as prober_mod

        class _StubCancelEvent:
            def __init__(self, initial: bool) -> None:
                self._flag = initial

            def is_set(self) -> bool:
                return self._flag

            def set(self) -> None:
                self._flag = True

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.communicate.return_value = (b"", b"")
        prev_event = prober_mod._worker_cancel_event
        try:
            _init_prober_worker(_StubCancelEvent(True))
            with (
                patch("src.core.prober.subprocess.Popen", return_value=mock_proc),
                self.assertRaises(InterruptedError),
            ):
                run_ffprobe("C:/dummy/cancel.mp4", timeout_sec=1.0)
            mock_proc.kill.assert_called_once()

            with (
                patch(
                    "src.core.prober.run_ffprobe",
                    side_effect=InterruptedError("cancelled"),
                ),
                self.assertRaises(InterruptedError),
            ):
                extract_keyframe_interval("C:/dummy/cancel.mp4", duration=12.0)
        finally:
            prober_mod._worker_cancel_event = prev_event

    def test_pool_cancellation_signals_event_and_shuts_down(self) -> None:
        """
        - probe_directory 및 populate_keyframes에서 is_cancelled 참 반환 시 즉시 중단 및 풀 정리를 수행하는지 검증.
        """
        mock_exe = MagicMock()
        mock_future = MagicMock()
        mock_exe.submit.return_value = mock_future

        with tempfile.TemporaryDirectory() as tmpdir:
            sample = f"{tmpdir}/clip.mp4"
            with open(sample, "wb") as f:
                f.write(b"0")

            with (
                patch("src.core.prober.ProcessPoolExecutor", return_value=mock_exe),
                self.assertRaises(InterruptedError),
            ):
                probe_directory(tmpdir, is_cancelled=lambda: True)

            mock_future.cancel.assert_called_once()
            mock_exe.shutdown.assert_called_with(wait=False, cancel_futures=True)

            mock_exe.reset_mock()
            mock_future.reset_mock()
            table = [
                VideoProps(
                    filename="clip.mp4",
                    width=1280,
                    height=720,
                    rotate_type=0,
                    fps=30.0,
                    vid_kbps=1500,
                    aud_kbps=128,
                    vid_size_MB=10.0,
                    duration=10.0,
                    codec="h264",
                )
            ]
            with (
                patch("src.core.prober.ProcessPoolExecutor", return_value=mock_exe),
                self.assertRaises(InterruptedError),
            ):
                populate_keyframes(table, tmpdir, is_cancelled=lambda: True)

            mock_future.cancel.assert_called_once()
            mock_exe.shutdown.assert_called_with(wait=False, cancel_futures=True)

    def test_parse_mean_keyframe_interval_variants_and_defenses(self) -> None:
        """
        - parse_mean_keyframe_interval의 fallback 키(best_effort_timestamp_time), 무효 값 무시, 비단조/표본 부족 방어를 검증.
        """
        # pts_time 우선 사용 + N/A/None/문자열 오류 건너뜀 + 간격 2.0, 4.0 평균 -> 3.0
        probe_valid = {
            "frames": [
                {"pts_time": "0.0"},
                {"pts_time": "N/A"},
                {"pts_time": None, "best_effort_timestamp_time": "2.0"},
                {"pts_time": "invalid"},
                {"pts_time": "6.0"},
            ]
        }
        mean_val = parse_mean_keyframe_interval(probe_valid)
        self.assertIsNotNone(mean_val)
        assert mean_val is not None
        self.assertAlmostEqual(mean_val, 3.0)

        # 표본 1개 이하거나 타임스탬프가 증가하지 않으면 None 반환
        self.assertIsNone(parse_mean_keyframe_interval({"frames": [{"pts_time": "1.0"}]}))
        self.assertIsNone(
            parse_mean_keyframe_interval(
                {"frames": [{"pts_time": "5.0"}, {"pts_time": "5.0"}, {"pts_time": "3.0"}]}
            )
        )

    def test_run_ffprobe_nonzero_exit_and_invalid_json_type(self) -> None:
        """
        - run_ffprobe가 0이 아닌 종료 코드나 JSON 객체가 아닌 출력에 대해 RuntimeError를 발생시키는지 검증.
        """
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.communicate.return_value = (b"", b"invalid file")
        proc_fail.poll.return_value = 1
        with (
            patch("src.core.prober.subprocess.Popen", return_value=proc_fail),
            self.assertRaises(RuntimeError) as ctx_fail,
        ):
            _ = run_ffprobe("broken.mp4")
        self.assertIn("invalid file", str(ctx_fail.exception))

        proc_list = MagicMock()
        proc_list.returncode = 0
        proc_list.communicate.return_value = (b"[]", b"")
        proc_list.poll.return_value = 0
        with (
            patch("src.core.prober.subprocess.Popen", return_value=proc_list),
            self.assertRaises(RuntimeError),
        ):
            _ = run_ffprobe("list_output.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
