import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.analysis.file_manager import (
    compute_dir_signature,
    get_video_filenames,
    is_video_file,
    resolve_video_filepath,
    scan_video_stats,
)
from src.core.models import VideoProps
from src.core.paths import write_utf8_file


class TestFileManagerModule(unittest.TestCase):
    """파일 확장자 감지, 폴더명 오인 방지, 작업 폴더 시그니처와 원자적 기록을 검증"""

    def test_is_video_file_extensions(self) -> None:
        """대소문자 혼합 비디오 확장자 인식 및 비비디오 포맷 배제를 검증"""
        for ext in (".mp4", ".MKV", ".Avi", ".webm", ".flv", ".ts", ".mov", ".wmv", ".m2ts"):
            self.assertTrue(is_video_file(f"clip{ext}"))

        for non_ext in (".srt", ".txt", ".jpg", ".ini", ".png", ".nfo", ""):
            self.assertFalse(is_video_file(f"file{non_ext}"))

    def test_get_video_filenames_excludes_directories_and_non_videos(self) -> None:
        """디렉터리 이름이 'folder.mp4'여도 비디오 파일로 취급하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = os.path.join(tmpdir, "sample.mp4")
            with open(real_file, "wb") as f:
                f.write(b"data")

            srt_file = os.path.join(tmpdir, "sample.srt")
            with open(srt_file, "wb") as f:
                f.write(b"sub")

            os.makedirs(os.path.join(tmpdir, "folder.mp4"), exist_ok=True)

            self.assertEqual(get_video_filenames(tmpdir), ["sample.mp4"])

    def test_resolve_video_filepath(self) -> None:
        """작업 폴더 기준 물리 경로를 반환하고 파일 존재 여부와 무관하게 같은 규칙을 적용하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"video")

            vid = VideoProps("vid.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            resolved = resolve_video_filepath(tmpdir, vid)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(fpath))

            missing = VideoProps("missing.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            self.assertEqual(resolve_video_filepath(tmpdir, missing), os.path.join(tmpdir, "missing.mp4"))

    def test_compute_dir_signature_consistency(self) -> None:
        """디렉터리 내 파일 수정 또는 추가 시 시그니처가 정확히 변경되는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sig1 = compute_dir_signature(tmpdir)
            self.assertEqual(sig1, ())

            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"0123456789")

            sig2 = compute_dir_signature(tmpdir)
            self.assertEqual(len(sig2), 1)
            self.assertEqual(sig2[0][0], "vid.mp4")
            self.assertEqual(sig2[0][1], 10)

            # 파일 추가 시 변경
            fpath2 = os.path.join(tmpdir, "vid2.mp4")
            with open(fpath2, "wb") as f:
                f.write(b"9876543210")
            sig3 = compute_dir_signature(tmpdir)
            self.assertNotEqual(sig2, sig3)


class TestVideoScanEquivalence(unittest.TestCase):
    """단일 디렉터리 조회의 크기, 수정 시각이 os.stat 결과와 같고 열거 규칙을 유지하는지 검증"""

    def test_scan_matches_os_stat_and_walk_rules(self) -> None:
        """직하위와 재귀 열거의 이름, 크기, 수정 시각이 os.stat 기준과 같고 숨김, 제외 폴더 규칙이 적용되는지 확인"""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub", "deep"))
            os.makedirs(os.path.join(tmp, "_1_excluded"))
            os.makedirs(os.path.join(tmp, ".hidden"))
            os.makedirs(os.path.join(tmp, "_2_scaled"))
            for rel in ("a.mp4", "sub/b.mkv", "sub/deep/c.avi", "_1_excluded/x.mp4", ".hidden/y.mp4",
                        "_2_scaled/z.mp4", "note.txt"):
                with open(os.path.join(tmp, *rel.split("/")), "wb") as f:
                    f.write(b"x" * (len(rel) * 7))

            direct = scan_video_stats(tmp)
            self.assertEqual([s.logical_name for s in direct], ["a.mp4"])
            recursive = scan_video_stats(tmp, recursive=True, excluded_dirnames=["_1_excluded"])
            self.assertEqual(
                [s.logical_name for s in recursive],
                ["_2_scaled/z.mp4", "a.mp4", "sub/b.mkv", "sub/deep/c.avi"],
            )
            self.assertEqual(
                [s.logical_name for s in recursive],
                get_video_filenames(tmp, recursive=True, excluded_dirnames=["_1_excluded"]),
            )
            for stat in recursive:
                st = os.stat(os.path.join(tmp, *stat.logical_name.split("/")))
                self.assertEqual(stat.filesize_bytes, st.st_size)
                self.assertEqual(stat.mtime_ns, st.st_mtime_ns)


class TestWriteUtf8File(unittest.TestCase):
    """설정 파일 기록의 원자성을 검증"""

    def test_write_utf8_file_replaces_atomically_without_leftovers(self) -> None:
        """기록 실패 시 기존 파일 내용이 보존되고 임시 파일이 남지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data", "config.json")
            write_utf8_file(path, "old")
            with (
                patch("src.core.paths.os.replace", side_effect=OSError("boom")),
                self.assertRaises(OSError),
            ):
                write_utf8_file(path, "new")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "old")
            write_utf8_file(path, "new")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "new")
            self.assertEqual(os.listdir(os.path.dirname(path)), ["config.json"])
