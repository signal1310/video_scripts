"""메모리 상주 PyAV 디코더 검증: 순차 읽기, 시크, 취소, 회전 대체, 축소 왕복, 닫기

- 실제 미디어 대신 가짜 컨테이너와 프레임을 주입해 결정적으로 확인
"""

import threading
import unittest
from fractions import Fraction
from io import BytesIO
from typing import cast
from unittest import mock

import av
from av.codec.context import ThreadType
from av.container import InputContainer
from av.video.frame import VideoFrame

from src.core.models import ActiveFrame
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.service.frame_compare_decoder import (
    CANCEL_MESSAGE,
    FRAME_MISSING_TEXT,
    NO_VIDEO_STREAM_TEXT,
    ROTATION_UNSUPPORTED_TEXT,
    ContainerFactory,
    DecodedFrame,
    FrameDecodeError,
    PersistentFrameDecoder,
    RotationUnsupportedError,
    crop_decoded_frame,
    decoded_frame_to_video,
    pyav_interpolation,
    to_decoded_frame,
)

_STEP_SEC: float = 1.0 / 30.0


class _Plane:
    """행 바이트 수와 빈 픽셀 바이트를 가진 가짜 평면"""

    def __init__(self, line_size: int, height: int) -> None:
        self.line_size: int = line_size
        self._data: bytes = bytes(line_size * height)

    def __bytes__(self) -> bytes:
        return self._data


class _Frame:
    """시점과 크기를 가진 가짜 RGB 프레임"""

    def __init__(self, seconds: float, width: int = 4, height: int = 2, rotation: int = 0) -> None:
        self.time: float = seconds
        self.width: int = width
        self.height: int = height
        self.rotation: int = rotation
        self.planes = (_Plane(width * 3, height),)
        self.reformats: list[dict[str, object]] = []

    def reformat(
        self,
        width: int | None = None,
        height: int | None = None,
        format: str | None = None,
        interpolation: str | None = None,
    ) -> "_Frame":
        self.reformats.append(
            {"width": width, "height": height, "format": format, "interpolation": interpolation}
        )
        return _Frame(
            self.time,
            width or self.width,
            height or self.height,
            self.rotation if width is None and height is None else 0,
        )


class _Stream:
    """스트림 메타데이터와 시간 단위만 가진 가짜 스트림"""

    def __init__(self, metadata: dict[str, str] | None = None) -> None:
        self.metadata: dict[str, str] = {} if metadata is None else metadata
        self.time_base: Fraction = Fraction(1, 1000)
        self.thread_type: ThreadType = ThreadType.NONE


class _Streams:
    def __init__(self, streams: list[_Stream]) -> None:
        self.video = tuple(streams)


class _Container:
    """시크 위치부터 프레임을 순서대로 내보내는 가짜 컨테이너"""

    def __init__(
        self,
        times: list[float],
        metadata: dict[str, str] | None = None,
        rotation: int = 0,
        width: int = 4,
        height: int = 2,
    ) -> None:
        self._times = times
        self._index = 0
        self._rotation = rotation
        self._width = width
        self._height = height
        self.seeks: list[int] = []
        self.closed: bool = False
        self.last_frame: _Frame | None = None
        self.streams = _Streams([_Stream(metadata)])

    def seek(
        self,
        offset: int,
        *,
        backward: bool = True,
        any_frame: bool = False,
        stream: object | None = None,
    ) -> None:
        del backward, any_frame, stream
        self.seeks.append(offset)
        target = offset / 1000
        self._index = next(
            (index for index, seconds in enumerate(self._times) if seconds >= target),
            len(self._times),
        )

    def decode(self, video: int = 0) -> object:
        del video
        while self._index < len(self._times):
            index = self._index
            self._index += 1
            frame = _Frame(self._times[index], width=self._width, height=self._height, rotation=self._rotation)
            self.last_frame = frame
            yield frame

    def close(self) -> None:
        self.closed = True


def _factory(container: _Container) -> ContainerFactory:
    def build(_path: str) -> InputContainer:
        return cast("InputContainer", container)

    return build


def _times(count: int) -> list[float]:
    return [index * _STEP_SEC for index in range(count)]

class SequentialAndSeekTests(unittest.TestCase):
    """컨테이너 재사용, 순차 읽기, 임의 시점 시크, 취소와 끝 도달"""

    def test_container_is_opened_once_and_uses_auto_threads(self) -> None:
        """같은 파일 요청이 컨테이너를 다시 열지 않고 AUTO 스레드를 쓰는지 검증"""
        container = _Container(_times(600))
        calls: list[str] = []

        def build(path: str) -> InputContainer:
            calls.append(path)
            return cast("InputContainer", container)

        decoder = PersistentFrameDecoder("a.mp4", container_factory=build)
        self.addCleanup(decoder.close)
        self.assertEqual(decoder.path, "a.mp4")
        decoder.decode(0.05, cancel_event=threading.Event())
        decoder.decode(5.0, cancel_event=threading.Event())
        self.assertEqual(calls, ["a.mp4"])
        self.assertEqual(container.streams.video[0].thread_type, ThreadType.AUTO)

    def test_next_frame_within_one_second_skips_the_seek(self) -> None:
        """직전 프레임 바로 뒤 요청이 시크 없이 다음 프레임만 읽는지 검증"""
        container = _Container(_times(60))
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        decoder.decode(0.1, cancel_event=threading.Event())
        seeks_after_jump = len(container.seeks)
        decoder.decode(0.1 + _STEP_SEC, cancel_event=threading.Event())
        self.assertEqual(len(container.seeks), seeks_after_jump)
        self.assertGreater(decoder._last_pts_sec or 0.0, 0.13)

    def test_jump_further_than_one_second_seeks_backward(self) -> None:
        """1초를 넘는 점프가 목표 시점 틱으로 뒤로 시크하는지 검증"""
        container = _Container(_times(600))
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        decoder.decode(0.1, cancel_event=threading.Event())
        decoder.decode(10.0, cancel_event=threading.Event())
        self.assertEqual(len(container.seeks), 2)
        self.assertEqual(container.seeks[-1], 10000)

    def test_backward_step_seeks_again(self) -> None:
        """뒤로 이동하면 순차 읽기 대신 다시 시크하는지 검증"""
        container = _Container(_times(60))
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        decoder.decode(1.0, cancel_event=threading.Event())
        decoder.decode(0.5, cancel_event=threading.Event())
        self.assertEqual(len(container.seeks), 2)

    def test_cancel_before_decode_raises_interrupted(self) -> None:
        """취소 신호가 서 있으면 프레임을 읽지 않고 즉시 중단하는지 검증"""
        cancel = threading.Event()
        cancel.set()
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(_Container(_times(5))))
        self.addCleanup(decoder.close)
        with self.assertRaises(InterruptedError) as raised:
            decoder.decode(0.0, cancel_event=cancel)
        self.assertEqual(str(raised.exception), CANCEL_MESSAGE)

    def test_end_of_stream_raises_decode_error(self) -> None:
        """영상 끝을 넘는 시점은 프레임 없음 오류로 올려 FFmpeg 대체를 부르는지 검증"""
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(_Container(_times(3))))
        self.addCleanup(decoder.close)
        with self.assertRaises(FrameDecodeError) as raised:
            decoder.decode(5.0, cancel_event=threading.Event())
        self.assertEqual(str(raised.exception), FRAME_MISSING_TEXT)

    def test_missing_video_stream_raises_and_closes(self) -> None:
        """비디오 스트림이 없으면 컨테이너를 닫고 예외를 올리는지 검증"""
        container = _Container(_times(3))
        container.streams = _Streams([])
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        with self.assertRaises(FrameDecodeError) as raised:
            decoder.decode(0.0, cancel_event=threading.Event())
        self.assertEqual(str(raised.exception), NO_VIDEO_STREAM_TEXT)
        self.assertTrue(container.closed)



class RotationAndPairTests(unittest.TestCase):
    """회전 영상 대체와 메모리 내 bicubic 왕복"""

    def test_stream_rotation_metadata_raises_and_closes(self) -> None:
        """메타데이터 회전이 있으면 컨테이너를 닫고 FFmpeg 대체 예외를 올리는지 검증"""
        container = _Container(_times(10), metadata={"rotate": "90"})
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        with self.assertRaises(RotationUnsupportedError) as raised:
            decoder.decode(0.0, cancel_event=threading.Event())
        self.assertEqual(str(raised.exception), ROTATION_UNSUPPORTED_TEXT)
        self.assertTrue(container.closed)

    def test_unreadable_rotation_metadata_is_treated_as_zero(self) -> None:
        """회전 값이 숫자가 아니면 회전 없음으로 보고 정상 디코딩하는지 검증"""
        decoder = PersistentFrameDecoder(
            "a.mp4", container_factory=_factory(_Container(_times(5), metadata={"rotate": "abc"}))
        )
        self.addCleanup(decoder.close)
        self.assertEqual(decoder.decode(0.0, cancel_event=threading.Event()).width, 4)

    def test_frame_rotation_raises(self) -> None:
        """프레임 자체의 표시 회전도 같은 예외로 대체를 부르는지 검증"""
        decoder = PersistentFrameDecoder(
            "a.mp4", container_factory=_factory(_Container(_times(5), rotation=180))
        )
        self.addCleanup(decoder.close)
        with self.assertRaises(RotationUnsupportedError):
            decoder.decode(0.0, cancel_event=threading.Event())

    def test_decode_crops_the_active_region_when_requested(self) -> None:
        """결과 비교용 원본 프레임이 요청한 활성 화면 크기로 잘리는지 검증"""
        container = _Container(_times(5), width=1600, height=1000)
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        frame = decoder.decode(
            0.0, cancel_event=threading.Event(), crop=ActiveFrame(800, 600, 100, 100)
        )
        self.assertEqual((frame.width, frame.height, frame.bytes_per_line), (800, 600, 2400))
        self.assertEqual(len(frame.data), 800 * 600 * 3)

    def test_decode_returns_the_full_frame_when_the_crop_is_out_of_range(self) -> None:
        """활성 화면이 프레임 범위를 벗어나면 원본 프레임을 그대로 돌려주는지 검증"""
        container = _Container(_times(5), width=1600, height=1000)
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        frame = decoder.decode(
            0.0, cancel_event=threading.Event(), crop=ActiveFrame(800, 600, 1200, 0)
        )
        self.assertEqual((frame.width, frame.height), (1600, 1000))

    def test_decode_pair_scales_down_and_back_with_bicubic(self) -> None:
        """같은 디코딩 프레임을 규격으로 줄였다 원본 크기로 되돌리는지 검증"""
        container = _Container(_times(10))
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        original, compared = decoder.decode_pair(
            0.0,
            tier=RESOLUTION_TIER_BY_KEY["480p"],
            frame_width=1280,
            frame_height=720,
            cancel_event=threading.Event(),
        )
        self.assertEqual((original.width, original.height), (4, 2))
        self.assertEqual((compared.width, compared.height), (1280, 720))
        frame = container.last_frame
        assert frame is not None
        self.assertEqual(len(frame.reformats), 2)
        self.assertEqual(frame.reformats[0]["format"], "rgb24")
        self.assertEqual(frame.reformats[1]["interpolation"], "BICUBIC")
        self.assertEqual((frame.reformats[1]["width"], frame.reformats[1]["height"]), (852, 480))

    def test_decode_pair_maps_every_scale_algorithm_to_pyav(self) -> None:
        """11개 보간 방식이 모두 PyAV Interpolation 이름으로 변환되는지 검증"""
        expected = {
            "fast_bilinear": "FAST_BILINEAR",
            "bilinear": "BILINEAR",
            "bicubic": "BICUBIC",
            "experimental": "X",
            "neighbor": "POINT",
            "area": "AREA",
            "bicublin": "BICUBLIN",
            "gauss": "GAUSS",
            "sinc": "SINC",
            "lanczos": "LANCZOS",
            "spline": "SPLINE",
        }
        for algorithm, name in expected.items():
            with self.subTest(algorithm=algorithm):
                self.assertEqual(pyav_interpolation(algorithm), name)
        self.assertEqual(pyav_interpolation("bogus"), "BICUBIC")

    def test_decode_pair_uses_the_requested_interpolation(self) -> None:
        """지정한 보간 방식이 두 번의 reformat에 모두 적용되는지 검증"""
        container = _Container(_times(10))
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        decoder.decode_pair(
            0.0,
            tier=RESOLUTION_TIER_BY_KEY["480p"],
            frame_width=1280,
            frame_height=720,
            cancel_event=threading.Event(),
            algorithm="neighbor",
        )
        frame = container.last_frame
        assert frame is not None
        self.assertEqual(frame.reformats[1]["interpolation"], "POINT")

    def test_decode_pair_without_reference_size_returns_the_same_frame(self) -> None:
        """기준 크기를 모르면 원본과 비교 프레임이 같은지 검증"""
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(_Container(_times(5))))
        self.addCleanup(decoder.close)
        original, compared = decoder.decode_pair(
            0.0,
            tier=RESOLUTION_TIER_BY_KEY["480p"],
            frame_width=0,
            frame_height=0,
            cancel_event=threading.Event(),
        )
        self.assertIs(original, compared)

    def test_to_decoded_frame_reads_the_first_plane(self) -> None:
        """RGB24 평면의 크기와 행 바이트 수를 그대로 옮기는지 검증"""
        frame = cast("VideoFrame", _Frame(1.0, width=4, height=3))
        decoded: DecodedFrame = to_decoded_frame(frame)
        self.assertEqual((decoded.width, decoded.height, decoded.bytes_per_line), (4, 3, 12))
        self.assertEqual(len(decoded.data), 36)

    def test_decode_pair_with_crop_roundtrips_the_active_region(self) -> None:
        """활성 화면 크롭이 두 프레임에 모두 적용되고 그 크기 기준으로 왕복하는지 검증"""
        container = _Container(_times(5), width=1600, height=1000)
        decoder = PersistentFrameDecoder("a.mp4", container_factory=_factory(container))
        self.addCleanup(decoder.close)
        crop = ActiveFrame(800, 600, 100, 100)
        original, compared = decoder.decode_pair(
            0.0,
            tier=RESOLUTION_TIER_BY_KEY["480p"],
            frame_width=800,
            frame_height=600,
            cancel_event=threading.Event(),
            crop=crop,
        )
        self.assertEqual((original.width, original.height), (800, 600))
        self.assertEqual((compared.width, compared.height), (800, 600))
        frame = container.last_frame
        assert frame is not None
        self.assertEqual(frame.reformats[0]["format"], "rgb24")

    def test_crop_decoded_frame_keeps_only_the_requested_rows_and_columns(self) -> None:
        """RGB24 바이트에서 활성 화면 영역만 남기고 행 폭을 좁히는지 검증"""
        data = bytes(range(48))
        frame = DecodedFrame(width=4, height=4, bytes_per_line=12, data=data)
        cropped = crop_decoded_frame(frame, ActiveFrame(2, 2, 1, 1))
        self.assertEqual((cropped.width, cropped.height, cropped.bytes_per_line), (2, 2, 6))
        self.assertEqual(
            cropped.data,
            data[12 + 3 : 12 + 9] + data[24 + 3 : 24 + 9],
        )
        self.assertIs(crop_decoded_frame(frame, ActiveFrame(4, 4, 1, 0)), frame)

    def test_decoded_frame_to_video_rebuilds_a_convertible_frame(self) -> None:
        """잘라낸 RGB24 바이트를 PyAV 프레임으로 되살려 다시 변환할 수 있는지 검증"""
        frame = DecodedFrame(width=2, height=2, bytes_per_line=6, data=bytes(range(12)))
        video = decoded_frame_to_video(frame)
        self.assertEqual((video.width, video.height), (2, 2))
        self.assertGreaterEqual(video.planes[0].line_size, 6)
        small = video.reformat(width=1, height=1, format="rgb24", interpolation="BICUBIC")
        self.assertEqual((small.width, small.height), (1, 1))


class LifecycleTests(unittest.TestCase):
    """닫기와 다시 열기, 공유 핸들 정리"""

    def test_close_releases_the_container_and_reopens_on_demand(self) -> None:
        """닫은 뒤 다음 요청에서 컨테이너를 새로 여는지 검증"""
        containers: list[_Container] = []

        def build(_path: str) -> InputContainer:
            container = _Container(_times(10))
            containers.append(container)
            return cast("InputContainer", container)

        decoder = PersistentFrameDecoder("a.mp4", container_factory=build)
        decoder.decode(0.0, cancel_event=threading.Event())
        decoder.close()
        self.assertTrue(containers[0].closed)
        decoder.decode(0.0, cancel_event=threading.Event())
        decoder.close()
        self.assertEqual(len(containers), 2)
        self.assertTrue(containers[1].closed)

    def test_open_failure_closes_the_shared_handle(self) -> None:
        """컨테이너 열기가 실패하면 공유 핸들을 닫고 예외를 그대로 올리는지 검증"""
        source = BytesIO(b"x")
        decoder = PersistentFrameDecoder("a.mp4")
        with (
            mock.patch("src.service.frame_compare_decoder.open_shared_binary", return_value=source),
            mock.patch.object(av, "open", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            decoder.decode(0.0, cancel_event=threading.Event())
        self.assertTrue(source.closed)
