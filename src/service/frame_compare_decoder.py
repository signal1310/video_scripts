"""화질 비교 시점 프레임을 메모리에 상주시켜 빠르게 꺼내는 PyAV 디코더

- 같은 파일의 컨테이너와 비디오 스트림을 한 번만 열어 두고 재사용해 매 요청의 컨테이너 열기 비용을 없앰
- 직전 프레임 바로 뒤 요청은 시크 없이 다음 프레임만 읽어 1프레임 이동을 수 밀리초로 줄임
- 축소 비교는 같은 디코딩 프레임에서 swscale bicubic으로 줄였다 되돌려 디스크 쓰기 없이 만듦
- 표시 회전이 있는 영상은 프레임이 어긋나므로 회전 예외를 올려 서비스가 FFmpeg 경로로 대체하게 함
- Windows에서는 삭제와 이름 바꾸기를 허용하는 공유 핸들로 열어 창이 열린 동안에도 파일을 옮길 수 있음
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, cast

from src.core.file_sharing import open_shared_binary
from src.core.models import ActiveFrame
from src.core.resolution_tiers import ResolutionTier, calc_scaled_dimensions

if TYPE_CHECKING:
    from av.container import InputContainer
    from av.video.frame import VideoFrame
    from av.video.stream import VideoStream

logger = logging.getLogger(__name__)

SEQUENTIAL_WINDOW_SEC: float = 1.0
TIME_EPSILON_SEC: float = 0.0005
FALLBACK_TIME_BASE_SEC: float = 1.0 / 1000.0
FULL_TURN_DEGREES: int = 360
RGB24_FORMAT: str = "rgb24"
RGB24_BYTES_PER_PIXEL: int = 3
BICUBIC_INTERPOLATION: str = "BICUBIC"
CANCEL_MESSAGE: str = "화질 비교 프레임 디코딩이 취소되었습니다"
NO_VIDEO_STREAM_TEXT: str = "영상 스트림이 없어 프레임을 읽지 못했습니다."
FRAME_MISSING_TEXT: str = "요청한 시점의 프레임이 없습니다."
ROTATION_UNSUPPORTED_TEXT: str = "회전된 영상은 FFmpeg 경로로 프레임을 읽습니다."

ContainerFactory = Callable[[str], "InputContainer"]


class FrameDecodeError(RuntimeError):
    """PyAV로 요청 시점의 프레임을 만들지 못했을 때"""


class RotationUnsupportedError(FrameDecodeError):
    """표시 회전이 있어 메모리 프레임을 그대로 쓰면 방향이 어긋날 때"""


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """RGB24 평면 한 장의 크기, 행 바이트 수와 픽셀 바이트"""

    width: int
    height: int
    bytes_per_line: int
    data: bytes


def to_decoded_frame(frame: VideoFrame) -> DecodedFrame:
    """RGB24로 바꾼 프레임의 첫 평면을 픽셀 바이트로 읽음"""
    plane = frame.planes[0]
    return DecodedFrame(
        width=frame.width,
        height=frame.height,
        bytes_per_line=plane.line_size,
        data=bytes(plane),
    )


def crop_decoded_frame(frame: DecodedFrame, crop: ActiveFrame) -> DecodedFrame:
    """RGB24 프레임에서 활성 화면 영역만 남기고 행 폭을 좁힌 프레임 반환

    - 캐시의 활성 화면이 실제 디코딩 프레임 범위를 벗어나면 자르지 않고 원본을 돌려줌
    """
    if (
        crop.width <= 0
        or crop.height <= 0
        or crop.x < 0
        or crop.y < 0
        or crop.x + crop.width > frame.width
        or crop.y + crop.height > frame.height
    ):
        return frame
    row_bytes = crop.width * RGB24_BYTES_PER_PIXEL
    start = crop.x * RGB24_BYTES_PER_PIXEL
    rows = [
        frame.data[row * frame.bytes_per_line + start : row * frame.bytes_per_line + start + row_bytes]
        for row in range(crop.y, crop.y + crop.height)
    ]
    return DecodedFrame(
        width=crop.width,
        height=crop.height,
        bytes_per_line=row_bytes,
        data=b"".join(rows),
    )


def decoded_frame_to_video(frame: DecodedFrame) -> VideoFrame:
    """RGB24 픽셀 바이트를 PyAV 프레임으로 되살려 다시 변환할 수 있게 함

    - PyAV 평면 행 폭에 맞춰 남는 바이트를 0으로 채움
    """
    import av

    video = av.VideoFrame(frame.width, frame.height, RGB24_FORMAT)
    plane = video.planes[0]
    line_size = plane.line_size
    if frame.bytes_per_line == line_size:
        plane.update(frame.data)
        return video
    padding = bytes(max(0, line_size - frame.bytes_per_line))
    rows = b"".join(
        frame.data[start : start + frame.bytes_per_line] + padding
        for start in range(0, len(frame.data), frame.bytes_per_line)
    )
    plane.update(rows)
    return video


class PersistentFrameDecoder:
    """영상 한 파일의 컨테이너를 열어 둔 채 시점 프레임을 꺼내는 디코더

    - 컨테이너 열기와 스트림 검사는 첫 요청에서 한 번만 함
    - 디코딩과 시크는 잠금으로 감싸 같은 디코더에 여러 스레드가 들어와도 순서가 깨지지 않게 함
    """

    def __init__(self, path: str, *, container_factory: ContainerFactory | None = None) -> None:
        self._path: str = path
        self._container_factory: ContainerFactory | None = container_factory
        self._container: InputContainer | None = None
        self._stream: VideoStream | None = None
        self._source_file: BinaryIO | None = None
        self._frames: Iterator[VideoFrame] | None = None
        self._last_pts_sec: float | None = None
        # - 열기 실패 정리에서 같은 잠금을 다시 잡으므로 재진입 가능한 잠금을 씀
        self._lock = threading.RLock()

    @property
    def path(self) -> str:
        """디코더가 열어 둔 영상 파일 경로"""
        return self._path

    def decode(self, time_sec: float, *, cancel_event: threading.Event) -> DecodedFrame:
        """지정 시점의 첫 프레임을 RGB24로 읽음, 취소되면 `InterruptedError`"""
        with self._lock:
            frame = self._frame_at(time_sec, cancel_event)
            return to_decoded_frame(frame.reformat(format=RGB24_FORMAT))

    def decode_pair(
        self,
        time_sec: float,
        *,
        tier: ResolutionTier,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
        crop: ActiveFrame | None = None,
    ) -> tuple[DecodedFrame, DecodedFrame]:
        """원본 프레임과 규격으로 줄였다 되돌린 프레임을 같은 디코딩에서 만듦

        - 한 번 디코딩한 프레임을 swscale bicubic으로 두 번 변환해 파일을 다시 읽지 않음
        - crop이 있으면 두 프레임 모두 활성 화면 영역만 남긴 뒤 그 크기를 기준으로 왕복함
        - 기준 크기를 모르면 두 프레임이 같음
        """
        with self._lock:
            frame = self._frame_at(time_sec, cancel_event)
            original = to_decoded_frame(frame.reformat(format=RGB24_FORMAT))
            if crop is None:
                if frame_width <= 0 or frame_height <= 0:
                    return original, original
                source_frame = frame
                base_width, base_height = frame_width, frame_height
            else:
                original = crop_decoded_frame(original, crop)
                source_frame = decoded_frame_to_video(original)
                base_width, base_height = original.width, original.height
            scaled_width, scaled_height = calc_scaled_dimensions(base_width, base_height, tier)
            small = source_frame.reformat(
                width=scaled_width,
                height=scaled_height,
                format=RGB24_FORMAT,
                interpolation=BICUBIC_INTERPOLATION,
            )
            restored = small.reformat(
                width=base_width,
                height=base_height,
                format=RGB24_FORMAT,
                interpolation=BICUBIC_INTERPOLATION,
            )
            return original, to_decoded_frame(restored)

    def close(self) -> None:
        """열어 둔 컨테이너와 파일 핸들을 닫고 다음 요청에서 다시 열게 함"""
        with self._lock:
            container = self._container
            source = self._source_file
            self._container = None
            self._stream = None
            self._frames = None
            self._source_file = None
            self._last_pts_sec = None
        if container is not None:
            container.close()
        if source is not None:
            source.close()

    def _ensure_open(self) -> None:
        """컨테이너와 비디오 스트림을 열고 회전 메타데이터를 확인"""
        if self._container is not None:
            return
        import av
        from av.codec.context import ThreadType

        source: BinaryIO | None = None
        if self._container_factory is not None:
            container = self._container_factory(self._path)
        else:
            source = open_shared_binary(self._path)
            try:
                opened = av.open(self._path) if source is None else av.open(source)
                container = cast("InputContainer", opened)
            except Exception:
                if source is not None:
                    source.close()
                raise
        streams = container.streams.video
        if not streams:
            container.close()
            if source is not None:
                source.close()
            raise FrameDecodeError(NO_VIDEO_STREAM_TEXT)
        stream = streams[0]
        stream.thread_type = ThreadType.AUTO
        self._container = container
        self._stream = stream
        self._source_file = source
        self._frames = container.decode(video=0)
        if _declared_rotation(stream) % FULL_TURN_DEGREES != 0:
            self.close()
            raise RotationUnsupportedError(ROTATION_UNSUPPORTED_TEXT)

    def _frame_at(self, time_sec: float, cancel_event: threading.Event) -> VideoFrame:
        """목표 시점에 닿는 첫 프레임, 직전 프레임 뒤 1초 이내면 시크 없이 이어 읽음"""
        self._ensure_open()
        last = self._last_pts_sec
        if last is None or not 0.0 < time_sec - last <= SEQUENTIAL_WINDOW_SEC:
            self._seek(time_sec)
        return self._read_forward(time_sec, cancel_event)

    def _seek(self, time_sec: float) -> None:
        """직전 키프레임으로 점프하고 그 지점부터 디코더를 다시 엶"""
        container = self._container
        stream = self._stream
        if container is None or stream is None:
            raise FrameDecodeError(NO_VIDEO_STREAM_TEXT)
        time_base = stream.time_base
        seconds_per_tick = FALLBACK_TIME_BASE_SEC if time_base is None else float(time_base)
        offset = max(0, round(time_sec / seconds_per_tick))
        container.seek(offset, backward=True, any_frame=False, stream=stream)
        self._frames = container.decode(video=0)
        self._last_pts_sec = None

    def _read_forward(self, time_sec: float, cancel_event: threading.Event) -> VideoFrame:
        """목표 시점 이상의 첫 프레임까지 읽으며 매 프레임 취소를 확인"""
        frames = self._frames
        if frames is None:
            raise FrameDecodeError(NO_VIDEO_STREAM_TEXT)
        while True:
            if cancel_event.is_set():
                raise InterruptedError(CANCEL_MESSAGE)
            try:
                frame = next(frames)
            except StopIteration as error:
                raise FrameDecodeError(FRAME_MISSING_TEXT) from error
            if int(frame.rotation) % FULL_TURN_DEGREES != 0:
                raise RotationUnsupportedError(ROTATION_UNSUPPORTED_TEXT)
            seconds = float(frame.time)
            if seconds + TIME_EPSILON_SEC >= time_sec:
                self._last_pts_sec = seconds
                return frame


def _declared_rotation(stream: VideoStream) -> int:
    """스트림 메타데이터의 회전 각도, 없거나 읽지 못하면 0"""
    rotate = stream.metadata.get("rotate")
    if rotate is None:
        return 0
    try:
        return int(rotate)
    except ValueError:
        return 0
