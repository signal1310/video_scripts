import os
import tempfile
import unittest

from src.core.file_manager import (
    MoveIssue,
    MoveIssueKind,
    classify_files_batch,
    compute_dir_signature,
    get_dirnames,
    get_video_filenames,
    is_video_file,
    preview_unclassify_all,
    resolve_video_filepath,
    scan_video_stats,
    unclassify_all_files,
)
from src.core.messages import format_move_issue
from src.core.models import VideoProps


class TestFileManagerModule(unittest.TestCase):
    """파일 확장자 감지, 폴더명 오인 방지, 파일 이동 및 충돌 보호 원복의 무결성을 검증"""

    def test_format_move_issue_keeps_legacy_text(self) -> None:
        """이동 이슈 구조체를 문구로 변환할 때 기존 로그 문구와 글자 단위로 같은지 검증"""
        failed = MoveIssue(
            MoveIssueKind.UNCLASSIFY_MOVE_FAILED,
            "a.mp4",
            "_1_x",
            src_path="/src/a.mp4",
            dst_path="/root/a.mp4",
            error="disk full",
        )
        self.assertTrue(failed.is_failure)
        self.assertEqual(
            format_move_issue(failed),
            "파일 이동 실패: /src/a.mp4 -> /root/a.mp4 (disk full)",
        )
        self.assertEqual(
            format_move_issue(MoveIssue(MoveIssueKind.UNCLASSIFY_ROOT_EXISTS, "a.mp4", "_1_x")),
            "'a.mp4' 파일이 이미 작업 폴더에 존재하여 '_1_x' 폴더에 유지했습니다.",
        )

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

            dir_with_ext = os.path.join(tmpdir, "folder.mp4")
            os.makedirs(dir_with_ext, exist_ok=True)

            fnames = get_video_filenames(tmpdir)
            self.assertEqual(fnames, ["sample.mp4"])

            dirnames = get_dirnames(tmpdir)
            self.assertEqual(dirnames, ["folder.mp4"])

    def test_resolve_video_filepath(self) -> None:
        """비디오가 루트 경로에 있거나 하위 이동 폴더에 위치한 경우 정확한 물리 경로를 찾아내는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"video")

            vid = VideoProps("vid.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            resolved = resolve_video_filepath(tmpdir, vid)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(fpath))

            sub_dir = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            os.makedirs(sub_dir, exist_ok=True)
            sub_fpath = os.path.join(sub_dir, "moved_vid.mp4")
            with open(sub_fpath, "wb") as f:
                f.write(b"moved-video")

            vid_moved = VideoProps(
                "moved_vid.mp4",
                1280,
                720,
                0,
                30.0,
                3000,
                128,
                20.0,
                5.0,
                "h264",
                moved_dirname="_1_표준인코딩_CQ22",
            )
            resolved_moved = resolve_video_filepath(tmpdir, vid_moved)
            self.assertEqual(os.path.abspath(resolved_moved), os.path.abspath(sub_fpath))

    def test_unclassify_all_files_collision_protection(self) -> None:
        """원복 시 루트 경로에 동일 파일이 존재할 경우 덮어쓰지 않고 하위 폴더에 보존함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "_1_sub")
            os.makedirs(sub, exist_ok=True)

            sub_file = os.path.join(sub, "collision.mp4")
            with open(sub_file, "wb") as f:
                f.write(b"SUB_DATA")

            root_file = os.path.join(tmpdir, "collision.mp4")
            with open(root_file, "wb") as f:
                f.write(b"ROOT_DATA")

            moved_cnt, issues = unclassify_all_files(tmpdir)
            self.assertEqual(moved_cnt, 0)
            self.assertEqual(len(issues), 1)
            self.assertIs(issues[0].kind, MoveIssueKind.UNCLASSIFY_ROOT_EXISTS)

            # 루트 파일 내용 보존 확인
            with open(root_file, "rb") as f:
                self.assertEqual(f.read(), b"ROOT_DATA")
            # 하위 폴더 파일도 유지 확인
            self.assertTrue(os.path.isfile(sub_file))

    def test_preview_unclassify_all_matches_restore_without_moving(self) -> None:
        """preview_unclassify_all이 복원 예정 수와 충돌 수를 파일 이동 없이 실제 복원 결과와 같게 집계하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub1 = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            sub2 = os.path.join(tmpdir, "_2_저화질_키프레임교정")
            os.makedirs(os.path.join(sub1, "nested"), exist_ok=True)
            os.makedirs(sub2, exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "user_folder"), exist_ok=True)

            with open(os.path.join(sub1, "clean1.mp4"), "wb") as f:
                f.write(b"clean1")
            with open(os.path.join(sub1, "nested", "clean3.mkv"), "wb") as f:
                f.write(b"clean3")
            with open(os.path.join(sub1, "notes.txt"), "wb") as f:
                f.write(b"not-video")
            with open(os.path.join(sub2, "clean2.mp4"), "wb") as f:
                f.write(b"clean2")
            with open(os.path.join(sub2, "dup.mp4"), "wb") as f:
                f.write(b"moved-skip")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"root-keep")
            with open(os.path.join(tmpdir, "user_folder", "user.mp4"), "wb") as f:
                f.write(b"user")

            dirnames = ["_1_표준인코딩_CQ22", "_2_저화질_키프레임교정"]
            restorable, conflicts = preview_unclassify_all(tmpdir, dirnames=dirnames)
            self.assertEqual((restorable, conflicts), (3, 1))

            # - 미리보기는 어떤 파일도 옮기지 않아야 함
            self.assertTrue(os.path.isfile(os.path.join(sub1, "clean1.mp4")))
            self.assertTrue(os.path.isfile(os.path.join(sub2, "dup.mp4")))

            moved_cnt, issues = unclassify_all_files(tmpdir, dirnames=dirnames)
            self.assertEqual(moved_cnt, restorable)
            self.assertEqual(len(issues), conflicts)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "clean3.mkv")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "user_folder", "user.mp4")))

    def test_unclassify_all_files_restores_clean_files_and_removes_empty_dirs(self) -> None:
        """unclassify_all_files가 분류 하위 폴더의 영상을 루트로 복구하고 빈 폴더를 삭제하며, 루트 충돌 파일은 덮어쓰지 않고 경고하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub1 = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            sub2 = os.path.join(tmpdir, "_2_저화질_키프레임교정")
            os.makedirs(sub1, exist_ok=True)
            os.makedirs(sub2, exist_ok=True)

            with open(os.path.join(sub1, "clean1.mp4"), "wb") as f:
                f.write(b"clean1")
            with open(os.path.join(sub2, "clean2.mp4"), "wb") as f:
                f.write(b"clean2")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"root-keep")
            with open(os.path.join(sub1, "dup.mp4"), "wb") as f:
                f.write(b"moved-skip")

            cached: list[VideoProps] = [
                VideoProps(
                    "clean1.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    3000,
                    128,
                    120.0,
                    10.0,
                    "h264",
                    moved_dirname="_1_표준인코딩_CQ22",
                ),
                VideoProps(
                    "clean2.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    800,
                    128,
                    80.0,
                    10.0,
                    "h264",
                    5.0,
                    moved_dirname="_2_저화질_키프레임교정",
                ),
                VideoProps(
                    "dup.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    3000,
                    128,
                    100.0,
                    10.0,
                    "h264",
                    moved_dirname="_1_표준인코딩_CQ22",
                ),
            ]

            moved_cnt, issues = unclassify_all_files(
                tmpdir, cached_videos=cached
            )
            self.assertEqual(moved_cnt, 2)
            self.assertGreaterEqual(len(issues), 1)
            for issue in issues:
                self.assertIs(issue.kind, MoveIssueKind.UNCLASSIFY_ROOT_EXISTS)
                self.assertIn("작업 폴더", format_move_issue(issue))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "clean1.mp4")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "clean2.mp4")))
            self.assertFalse(os.path.isdir(sub2))
            self.assertTrue(os.path.isdir(sub1))

            with open(os.path.join(tmpdir, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"root-keep")
            with open(os.path.join(sub1, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"moved-skip")

            self.assertIsNone(cached[0].moved_dirname)
            self.assertIsNone(cached[1].moved_dirname)

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

    def test_classify_files_batch_collision_skip_and_rollback(self) -> None:
        """배치 파일 이동 시 기존 대상 파일 충돌은 덮어쓰지 않고 건너뛰며, I/O 오류 발생 시 이전 이동을 역순 원복함을 검증"""
        import shutil
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "_1_target")
            os.makedirs(sub, exist_ok=True)
            # 이미 대상 경로에 존재하는 충돌 파일
            with open(os.path.join(sub, "dup.mp4"), "wb") as f:
                f.write(b"KEEP_TARGET")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"SRC_DUP")
            with open(os.path.join(tmpdir, "ok.mp4"), "wb") as f:
                f.write(b"SRC_OK")

            vid_dup = VideoProps("dup.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)
            vid_ok = VideoProps("ok.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)

            moved, issues = classify_files_batch(
                tmpdir, [(vid_dup, "_1_target"), (vid_ok, "_1_target")]
            )
            self.assertEqual(moved, 1)
            self.assertEqual(len(issues), 1)
            self.assertIs(issues[0].kind, MoveIssueKind.CLASSIFY_TARGET_EXISTS)
            self.assertEqual(
                format_move_issue(issues[0]),
                "대상 폴더에 '_1_target/dup.mp4' 파일이 이미 존재하여 덮어쓰지 않고 건너뛰었습니다.",
            )
            self.assertIsNone(vid_dup.moved_dirname)
            self.assertEqual(vid_ok.moved_dirname, "_1_target")
            with open(os.path.join(sub, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"KEEP_TARGET")

        with tempfile.TemporaryDirectory() as tmpdir2:
            f1 = os.path.join(tmpdir2, "a.mp4")
            f2 = os.path.join(tmpdir2, "b.mp4")
            with open(f1, "wb") as f:
                f.write(b"A")
            with open(f2, "wb") as f:
                f.write(b"B")

            va = VideoProps("a.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)
            vb = VideoProps("b.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)

            orig_move = shutil.move
            step = 0

            def fail_on_second(src: str, dst: str) -> str:
                nonlocal step
                step += 1
                if step == 2:
                    raise OSError("disk full")
                return orig_move(src, dst)

            with patch("src.core.file_manager.shutil.move", side_effect=fail_on_second), self.assertRaises(RuntimeError):
                classify_files_batch(tmpdir2, [(va, "_2_new_dir"), (vb, "_2_new_dir")])

            self.assertTrue(os.path.isfile(f1))
            self.assertTrue(os.path.isfile(f2))
            self.assertIsNone(va.moved_dirname)
            self.assertIsNone(vb.moved_dirname)
            self.assertFalse(os.path.exists(os.path.join(tmpdir2, "_2_new_dir")))


class TestVideoScanEquivalence(unittest.TestCase):
    """단일 디렉터리 조회의 크기, 수정 시각이 os.stat 결과와 같고 기존 열거 규칙을 유지하는지 검증"""

    def test_scan_matches_os_stat_and_walk_rules(self) -> None:
        """직하위와 재귀 열거의 이름, 크기, 수정 시각이 os.stat 기준과 같고 제외 규칙이 적용되는지 확인"""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub", "deep"))
            os.makedirs(os.path.join(tmp, "_1_class"))
            os.makedirs(os.path.join(tmp, ".hidden"))
            os.makedirs(os.path.join(tmp, "_2_scaled"))
            for rel in ("a.mp4", "sub/b.mkv", "sub/deep/c.avi", "_1_class/x.mp4", ".hidden/y.mp4",
                        "_2_scaled/z.mp4", "note.txt"):
                with open(os.path.join(tmp, *rel.split("/")), "wb") as f:
                    f.write(b"x" * (len(rel) * 7))

            direct = scan_video_stats(tmp)
            self.assertEqual([s.logical_name for s in direct], ["a.mp4"])
            recursive = scan_video_stats(
                tmp,
                recursive=True,
                excluded_dirnames=["_1_class"],
                downscale_prefix="_2_",
            )
            self.assertEqual(
                [s.logical_name for s in recursive], ["a.mp4", "sub/b.mkv", "sub/deep/c.avi"]
            )
            self.assertEqual(
                [s.logical_name for s in recursive],
                get_video_filenames(tmp, recursive=True, excluded_dirnames=["_1_class"], downscale_prefix="_2_"),
            )
            for stat in recursive:
                st = os.stat(os.path.join(tmp, *stat.logical_name.split("/")))
                self.assertEqual(stat.filesize_bytes, st.st_size)
                self.assertEqual(stat.mtime_ns, st.st_mtime_ns)

    def test_resolve_video_filepath_without_move_record_returns_source(self) -> None:
        """분류 이동 기록이 없으면 파일이 없어도 원래 위치 경로를 반환하는지 확인"""
        with tempfile.TemporaryDirectory() as tmp:
            vid = VideoProps("missing.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            self.assertEqual(
                resolve_video_filepath(tmp, vid), os.path.join(tmp, "missing.mp4")
            )


class TestFileManagerSafety(unittest.TestCase):
    """분류 폴더명 검증과 복원 범위 한정, 원자적 파일 기록을 검증"""

    def test_classify_rejects_path_escaping_dirname_before_any_move(self) -> None:
        """작업 폴더 밖을 가리키는 분류 폴더명은 이동 전에 도메인 예외로 거부함을 검증"""
        from src.core.exceptions import DomainValueError

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "a.mp4"), "wb") as f:
                f.write(b"a")
            vid = VideoProps("a.mp4", 1280, 720, 0, 30.0, 3000, 128, 10.0, 10.0, "h264")
            for bad in ("..", "../x", "sub\\x", "C:\\x", ""):
                with self.assertRaises(DomainValueError):
                    classify_files_batch(tmpdir, [(vid, bad)])
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "a.mp4")))

    def test_unclassify_limited_to_given_dirnames_keeps_user_folders(self) -> None:
        """dirnames로 한정하면 사용자가 만든 일반 폴더의 영상과 빈 폴더는 건드리지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cls_dir = os.path.join(tmpdir, "_1_cls")
            user_dir = os.path.join(tmpdir, "my_backup")
            empty_user_dir = os.path.join(tmpdir, "empty_keep")
            for d in (cls_dir, user_dir, empty_user_dir):
                os.makedirs(d)
            with open(os.path.join(cls_dir, "x.mp4"), "wb") as f:
                f.write(b"x")
            with open(os.path.join(user_dir, "y.mp4"), "wb") as f:
                f.write(b"y")

            moved, _ = unclassify_all_files(tmpdir, dirnames=["_1_cls", "../escape"])
            self.assertEqual(moved, 1)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "x.mp4")))
            self.assertFalse(os.path.isdir(cls_dir))
            self.assertTrue(os.path.isfile(os.path.join(user_dir, "y.mp4")))
            self.assertTrue(os.path.isdir(empty_user_dir))

    def test_unclassify_restores_downscale_folders_without_overwriting(self) -> None:
        """설정 분류 폴더 목록에 포함된 축소 폴더를 복원하고, 같은 이름의 작업 폴더 파일은 덮어쓰지 않음을 검증"""
        from src.core.config import ClassifierConfig

        cfg = ClassifierConfig()
        with tempfile.TemporaryDirectory() as tmpdir:
            down_dir = os.path.join(tmpdir, f"{cfg.dir_downscale_prefix}720p")
            os.makedirs(down_dir)
            with open(os.path.join(down_dir, "x.mp4"), "wb") as f:
                f.write(b"x")
            with open(os.path.join(down_dir, "dup.mp4"), "wb") as f:
                f.write(b"in-class")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"root")

            moved, issues = unclassify_all_files(tmpdir, dirnames=cfg.class_dirnames())
            self.assertEqual(moved, 1)
            self.assertEqual(len(issues), 1)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "x.mp4")))
            with open(os.path.join(tmpdir, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"root")
            with open(os.path.join(down_dir, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"in-class")

    def test_write_utf8_file_replaces_atomically_without_leftovers(self) -> None:
        """기록 실패 시 기존 파일 내용이 보존되고 임시 파일이 남지 않음을 검증"""
        from unittest.mock import patch

        from src.core.paths import write_utf8_file

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

if __name__ == "__main__":
    unittest.main(verbosity=2)
