import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch

from src.core.prober import (
    _get_rotate_type,
    _parse_fps,
    extract_keyframe_interval,
    extract_video_props,
    safe_dict,
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
        - 다양한 분수형 문자열, 0으로 나누기, 음수, 문자열 오류 등에 대한 _parse_fps 방어력을 검증.
        """
        self.assertAlmostEqual(_parse_fps("30000/1001"), 29.97003, places=4)
        self.assertAlmostEqual(_parse_fps("60/1"), 60.0)
        self.assertAlmostEqual(_parse_fps("24"), 24.0)

        # 비정상 포맷 방어: 기본값 30.0 반환
        self.assertEqual(_parse_fps("0/0"), 30.0)
        self.assertEqual(_parse_fps("30/0"), 30.0)
        self.assertEqual(_parse_fps("-30/1"), 30.0)
        self.assertEqual(_parse_fps("invalid"), 30.0)
        self.assertEqual(_parse_fps(""), 30.0)
        self.assertEqual(_parse_fps(None), 30.0)
        self.assertEqual(_parse_fps(0), 30.0)

    def test_get_rotate_type_side_data_and_tags(self) -> None:
        """
        - 비디오 스트림의 side_data_list(Display Matrix) 및 tags 회전 메타데이터 파싱을 검증.
        """
        # side_data_list 회전 감지
        stream_side = {
            "side_data_list": [{"rotation": -90.0}],
            "tags": {"rotate": "180"},
        }
        self.assertEqual(_get_rotate_type(stream_side), -90)

        # tags 회전 감지
        stream_tags = {"tags": {"rotate": "270"}}
        self.assertEqual(_get_rotate_type(stream_tags), 270)

        # 메타데이터 없음
        self.assertEqual(_get_rotate_type({}), 0)
        self.assertEqual(_get_rotate_type(None), 0)

    @patch("ffmpeg.probe")
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

    @patch("ffmpeg.probe")
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

    @patch("ffmpeg.probe")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
