"""측정 로직 변경 시 PROBE_VERSION 올림을 강제하는 고정 입력 출력 해시 검증

고정된 가짜 ffprobe JSON에 대해 측정 결과와 호출 인자를 직렬화해 해시를 산출하고, 현재 PROBE_VERSION에 기록된 해시와 비교
해시가 바뀌면 측정 로직이 바뀐 것이므로 PROBE_VERSION을 올리고 PROBE_DIGEST_HISTORY에 새 항목을 추가해야 함
"""

import hashlib
import json
import unittest
from dataclasses import asdict
from typing import Any

from src.core.analysis.prober import FFPROBE_DEFAULT_EXTRA_ARGS, FFPROBE_KEYFRAME_EXTRA_ARGS
from src.core.probe_parser import (
    KEYFRAME_PROBE_READ_WINDOW_SEC,
    KEYFRAME_PROBE_WINDOWS_SEC,
    PROBE_VERSION,
    build_video_props,
    measure_keyframe_interval,
)

# - 버전별 산출 해시 기록, 항목은 추가만 하고 기존 항목은 수정하지 않음
PROBE_DIGEST_HISTORY: dict[int, str] = {
    1: "bde842782c18ad5740ae5cc236ee43f6f23dbcf0046f66ad366538a94716eac3",
    2: "a557564cb10614b94a4a6880408fb644c0ab9a918c9846915493df0272ea4ba1",
    3: "2819b4d6a5b03b3e2871d63383591dc466ac928c0ea4c41b4bd1026251436bb5",
    4: "d140a1c4770d9105e1566adacadc5913e89e7a505df58fb9da13d42d2f0c0d50",
    5: "2c5a6a67b8523a7e557406e40322cac523b568c2e94a72ae1fbb1db8f7c75c0d",
    6: "55562ed3e2630849f1cde2993a1c7ba9991d5e2540ceb9615eaedce63704f5cb",
}

_SIZE_BYTES: int = 10 * 1024 * 1024


def _video_stream(index: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": index,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30000/1001",
        "r_frame_rate": "30000/1001",
        "disposition": {"attached_pic": 0},
    }
    base.update(fields)
    return base


def _audio_stream(index: int) -> dict[str, Any]:
    return {"index": index, "codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"}


def _packet(stream: int, pts: float | None, dts: float | None, flags: str) -> dict[str, Any]:
    packet: dict[str, Any] = {"stream_index": stream, "flags": flags}
    packet["pts_time"] = "N/A" if pts is None else f"{pts:.3f}"
    packet["dts_time"] = "N/A" if dts is None else f"{dts:.3f}"
    return packet


def _gop_packets(stream: int, duration: float, gop_sec: int) -> list[dict[str, Any]]:
    """매 초 패킷을 만들고 키프레임 주기마다 K 플래그를 부여하며, B-프레임 역전을 흉내 내도록 한 쌍을 디코드 순서로 뒤집음"""
    packets: list[dict[str, Any]] = []
    for sec in range(int(duration)):
        flags = "K__" if sec % gop_sec == 0 else "___"
        packets.append(_packet(stream, float(sec), float(sec) - 0.5 if sec else 0.0, flags))
    if len(packets) >= 3:
        packets[1], packets[2] = packets[2], packets[1]
    return packets


def _case_bframes_gop2() -> dict[str, Any]:
    streams = [_video_stream(0, bit_rate="4000000"), _audio_stream(1)]
    packets = _gop_packets(0, 14.0, 2) + [_packet(1, float(t), float(t), "K__") for t in range(0, 14, 1)]
    return {"streams": streams, "format": {"duration": "14.0", "bit_rate": "4128000"}, "packets": packets}


def _case_cover_art_first() -> dict[str, Any]:
    cover = _video_stream(0, codec_name="mjpeg", width=500, height=500, disposition={"attached_pic": 1})
    video = _video_stream(1, width=1280, height=720, avg_frame_rate="0/0", r_frame_rate="25/1", tags={"rotate": "90"})
    packets = [_packet(0, 0.0, 0.0, "K__"), _packet(0, 0.2, 0.2, "K__")]
    packets += [_packet(1, float(t), float(t), "K__" if t in (0, 5, 10) else "___") for t in range(12)]
    return {"streams": [cover, video], "format": {"duration": "12.0"}, "packets": packets}


def _case_short_footage() -> dict[str, Any]:
    return {"streams": [_video_stream(0, duration="0.6")], "format": {"duration": "0.6"}, "packets": []}


def _case_long_without_keyframes() -> dict[str, Any]:
    packets = [_packet(0, float(t), float(t), "___") for t in range(0, 30, 3)]
    return {"streams": [_video_stream(0, duration="30.0")], "format": {"duration": "30.0"}, "packets": packets}


def _case_dts_only_hdr() -> dict[str, Any]:
    stream = _video_stream(
        0,
        color_transfer="smpte2084",
        side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}],
        duration="9.0",
    )
    packets = [_packet(0, None, 0.0, "K__"), _packet(0, None, 3.0, "K__"), _packet(0, None, 6.0, "K__")]
    return {"streams": [stream, _audio_stream(1)], "format": {"duration": "9.0"}, "packets": packets}


def _case_missing_streams() -> dict[str, Any]:
    return {"streams": [], "format": {}, "packets": []}


_CASES: dict[str, Any] = {
    "bframes_gop2": _case_bframes_gop2(),
    "cover_art_first": _case_cover_art_first(),
    "short_footage": _case_short_footage(),
    "long_without_keyframes": _case_long_without_keyframes(),
    "dts_only_hdr": _case_dts_only_hdr(),
    "missing_streams": _case_missing_streams(),
}


def _measurement_digest() -> str:
    """고정 입력의 측정 결과와 호출 인자, 단계 설정을 정규화해 SHA-256 해시 산출"""
    results: dict[str, Any] = {}
    for name, probe in _CASES.items():
        props = build_video_props(f"{name}.mp4", _SIZE_BYTES, probe)
        props.keyframe_interval = measure_keyframe_interval(probe, props.duration)
        results[name] = asdict(props)
    payload: dict[str, Any] = {
        "args": {"default": list(FFPROBE_DEFAULT_EXTRA_ARGS), "keyframe": list(FFPROBE_KEYFRAME_EXTRA_ARGS)},
        "stages": list(KEYFRAME_PROBE_WINDOWS_SEC),
        "read_window": KEYFRAME_PROBE_READ_WINDOW_SEC,
        "results": results,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestProbeMeasurementDigest(unittest.TestCase):
    """측정 로직이 바뀌면 PROBE_VERSION을 올리고 해시 기록을 추가했는지 검증"""

    def test_measurement_digest_matches_recorded_version(self) -> None:
        digest = _measurement_digest()
        self.assertIn(
            PROBE_VERSION,
            PROBE_DIGEST_HISTORY,
            msg=f"PROBE_DIGEST_HISTORY에 PROBE_VERSION {PROBE_VERSION} 항목이 없습니다",
        )
        self.assertEqual(
            digest,
            PROBE_DIGEST_HISTORY[PROBE_VERSION],
            msg=(
                "측정 로직이 바뀌었습니다. PROBE_VERSION을 올리고 "
                f"PROBE_DIGEST_HISTORY에 새 항목을 추가하세요 (현재 산출 해시: {digest})"
            ),
        )

    def test_measurement_digest_is_deterministic(self) -> None:
        self.assertEqual(_measurement_digest(), _measurement_digest())


if __name__ == "__main__":
    unittest.main()
