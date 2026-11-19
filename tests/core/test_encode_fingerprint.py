"""재인코딩 이력 결과 지문이 moov 변경, 이동, 이름 변경에 흔들리지 않고 mdat 내용 변경에는 바뀌는지 검증하는 단위 테스트"""

import os
import shutil
import struct
import tempfile
import unittest

from src.core.encode_fingerprint import compute_encode_fingerprint

_FTYP_PAYLOAD: bytes = b"isom" + struct.pack(">I", 512) + b"isomiso2"
_MIB: int = 1024 * 1024


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _mp4(media: bytes, moov: bytes = b"moov-v1") -> bytes:
    """ftyp, mdat, 파일 끝 moov 순서의 합성 MP4(FFmpeg 결과와 같은 배치)"""
    return _box(b"ftyp", _FTYP_PAYLOAD) + _box(b"mdat", media) + _box(b"moov", moov)


class TestMp4Fingerprint(unittest.TestCase):
    """MP4 계열 지문: mdat 크기와 mdat 앞뒤 구간 해시"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_moov_only_change_keeps_fingerprint(self) -> None:
        """탐색기 태그 편집처럼 파일 끝 moov만 커지면 지문이 같음을 검증(파일 크기는 달라짐을 함께 확인)"""
        media = b"m" * 5000
        before = self._write("before.mp4", _mp4(media, b"moov-v1"))
        after = self._write("after.mp4", _mp4(media, b"moov-v1-with-longer-tags-and-rating"))
        self.assertNotEqual(os.path.getsize(before), os.path.getsize(after))
        self.assertEqual(compute_encode_fingerprint(before), compute_encode_fingerprint(after))

    def test_moving_or_renaming_keeps_fingerprint(self) -> None:
        """경로와 이름이 바뀌어도 같은 지문이 나옴을 검증"""
        data = _mp4(b"m" * 4096)
        original = self._write("a/original.mp4", data)
        moved = self._write("b/renamed.mov", data)
        self.assertEqual(compute_encode_fingerprint(original), compute_encode_fingerprint(moved))

    def test_media_content_change_changes_digest(self) -> None:
        """mdat 본문이 달라지면 크기는 같아도 해시가 달라짐을 검증"""
        first = self._write("first.mp4", _mp4(b"a" * 4096))
        second = self._write("second.mp4", _mp4(b"b" * 4096))
        a = compute_encode_fingerprint(first)
        b = compute_encode_fingerprint(second)
        self.assertEqual(a.size, b.size)
        self.assertNotEqual(a.digest, b.digest)

    def test_media_size_is_the_key_size_not_file_size(self) -> None:
        """MP4 계열의 조회 크기는 mdat 상자 크기이며, moov가 바뀐 파일 크기와 무관함을 검증"""
        short = self._write("short.mp4", _mp4(b"x" * 100, b"moov"))
        longer_media = self._write("longer.mp4", _mp4(b"x" * 200, b"moov"))
        self.assertEqual(compute_encode_fingerprint(short).size, 8 + 100)
        self.assertEqual(compute_encode_fingerprint(longer_media).size, 8 + 200)

    def test_large_media_hashes_only_edges(self) -> None:
        """2MiB를 넘는 mdat은 앞뒤 1MiB만 해시에 들어감을 검증(가운데 변화는 무시, 가장자리 변화는 반영)"""
        base = bytearray(3 * _MIB)
        middle = bytearray(base)
        middle[3 * _MIB // 2] = 1
        head = bytearray(base)
        head[0] = 1
        tail = bytearray(base)
        tail[-1] = 1
        base_fp = compute_encode_fingerprint(self._write("base.mp4", _mp4(bytes(base))))
        middle_fp = compute_encode_fingerprint(self._write("middle.mp4", _mp4(bytes(middle))))
        head_fp = compute_encode_fingerprint(self._write("head.mp4", _mp4(bytes(head))))
        tail_fp = compute_encode_fingerprint(self._write("tail.mp4", _mp4(bytes(tail))))
        self.assertEqual(base_fp, middle_fp)
        self.assertNotEqual(base_fp.digest, head_fp.digest)
        self.assertNotEqual(base_fp.digest, tail_fp.digest)

    def test_mp4_without_media_box_is_rejected(self) -> None:
        """mdat이 없는 MP4 계열은 파일 크기 지문으로 바꾸지 않고 ValueError임을 검증"""
        path = self._write("no_media.mp4", _box(b"ftyp", _FTYP_PAYLOAD) + _box(b"moov", b"moov-v1"))
        with self.assertRaises(ValueError):
            compute_encode_fingerprint(path)


class TestGenericFingerprint(unittest.TestCase):
    """MP4 계열이 아닌 파일: 파일 크기와 앞뒤 구간 해시"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def test_non_iso_file_uses_file_size_and_content(self) -> None:
        """파일 크기가 조회 크기이고, 내용이 바뀌면 해시가 바뀜을 검증"""
        data = b"RIFF" + b"\x00" * 60 + b"tail"
        path = os.path.join(self.root, "clip.mkv")
        with open(path, "wb") as f:
            f.write(data)
        first = compute_encode_fingerprint(path)
        self.assertEqual(first.size, len(data))
        changed = bytearray(data)
        changed[10] = 0x41
        with open(path, "wb") as f:
            f.write(bytes(changed))
        self.assertNotEqual(first.digest, compute_encode_fingerprint(path).digest)

    def test_short_file_is_treated_as_generic(self) -> None:
        """머리말보다 짧은 파일은 MP4 계열로 보지 않고 파일 크기 지문을 만듦을 검증"""
        path = os.path.join(self.root, "tiny.bin")
        with open(path, "wb") as f:
            f.write(b"abc")
        self.assertEqual(compute_encode_fingerprint(path).size, 3)

    def test_copy_keeps_identical_fingerprint(self) -> None:
        """내용이 같은 복사본은 같은 지문을 가짐을 검증"""
        path = os.path.join(self.root, "src.bin")
        with open(path, "wb") as f:
            f.write(b"payload" * 100)
        copied = os.path.join(self.root, "copy.bin")
        shutil.copyfile(path, copied)
        self.assertEqual(compute_encode_fingerprint(path), compute_encode_fingerprint(copied))


if __name__ == "__main__":
    unittest.main(verbosity=2)
