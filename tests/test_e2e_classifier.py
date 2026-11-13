import os
import subprocess
import tempfile
import unittest

from src import (
    QualityZone,
    VideoClassifier,
    WindowsNameOrder,
    col,
    desc,
    t,
)


def _create_real_video(
    filepath: str,
    width: int = 640,
    height: int = 360,
    duration: float = 3.0,
    fps: int = 30,
    codec: str = "libx264",
    bitrate_k: int = 1000,
    keyint: int = 30,
    rotate: int = 0,
    add_noise: bool = False,
) -> None:
    """ffmpeg CLI를 실행하여 테스트용 가상 비디오 파일을 생성"""
    vf_args = ["-vf", "noise=alls=60:allf=t"] if add_noise else []
    target_out = filepath if rotate == 0 else filepath + ".norot.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size={width}x{height}:rate={fps}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        *vf_args,
        "-c:v",
        codec,
        "-b:v",
        f"{bitrate_k}k",
        "-minrate",
        f"{bitrate_k}k",
        "-maxrate",
        f"{bitrate_k}k",
        "-bufsize",
        f"{bitrate_k * 2}k",
        "-g",
        str(keyint),
        "-keyint_min",
        str(keyint),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        target_out,
    ]
    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    if rotate != 0:
        rot_cmd = [
            "ffmpeg",
            "-y",
            "-display_rotation:v:0",
            str(rotate),
            "-i",
            target_out,
            "-c",
            "copy",
            filepath,
        ]
        subprocess.run(
            rot_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if os.path.exists(target_out):
            os.remove(target_out)


class TestEndToEndRealFFmpeg(unittest.TestCase):
    """실제 ffmpeg CLI를 실행하여 생성된 파일들로 프로빙, 가분류, 테이블 출력, 실제 이동, 충돌 보호, 원복을 총괄 검증"""

    def test_real_ffmpeg_probing_mkv_short_small_footage_and_unclassify_safety(self) -> None:
        """짧은 소형 영상의 단일 키프레임 감지, MKV 비트레이트 역산, 회전 정규화 및 분류 복원 안전성을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            short_bad_kf_path = os.path.join(tmpdir, "short_bad_kf.mp4")
            _create_real_video(
                short_bad_kf_path,
                width=640,
                height=360,
                duration=3.5,
                fps=30,
                bitrate_k=450,
                keyint=250,
            )

            over_mkv_path = os.path.join(tmpdir, "over_good_kf.mkv")
            _create_real_video(
                over_mkv_path,
                width=640,
                height=360,
                duration=3.0,
                fps=30,
                bitrate_k=1500,
                keyint=30,
                add_noise=True,
            )

            rot_path = os.path.join(tmpdir, "rotated_vertical.mp4")
            _create_real_video(
                rot_path,
                width=640,
                height=360,
                duration=2.5,
                fps=30,
                bitrate_k=450,
                keyint=30,
                rotate=90,
            )

            with open(os.path.join(tmpdir, "subtitle.srt"), "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\nHello")
            with open(os.path.join(tmpdir, "desktop.ini"), "w", encoding="utf-8") as f:
                f.write("[.ShellClassInfo]")

            classifier = VideoClassifier(root_dir=tmpdir)
            classifier.pseudo_classify_mode(True)
            report = classifier.classify()
            self.assertEqual(report.total_count, 3)

            emap = {e.video.filename: e for e in report.evaluations}
            self.assertTrue(emap["short_bad_kf.mp4"].is_keyframe_defective)
            self.assertGreater(emap["short_bad_kf.mp4"].video.keyframe_interval or 0, 2.2)

            self.assertGreater(emap["over_good_kf.mkv"].video.vid_kbps, 800)
            self.assertEqual(emap["over_good_kf.mkv"].quality_zone, QualityZone.OVER)

            rot_vid = emap["rotated_vertical.mp4"].video
            self.assertEqual((rot_vid.width, rot_vid.height), (360, 640))
            self.assertAlmostEqual(rot_vid.aspect_ratio, 0.562, places=2)

            classifier.pseudo_classify_mode(False)
            classifier.classify()

            target_dir_1 = emap["short_bad_kf.mp4"].target_dirname
            assert target_dir_1 is not None
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, target_dir_1, "short_bad_kf.mp4"))
            )

            conflict_file = os.path.join(tmpdir, "short_bad_kf.mp4")
            with open(conflict_file, "wb") as f:
                f.write(b"ROOT_ORIGINAL_MUST_NOT_BE_OVERWRITTEN")

            classifier.unclassify_files()
            with open(conflict_file, "rb") as f:
                self.assertEqual(f.read(), b"ROOT_ORIGINAL_MUST_NOT_BE_OVERWRITTEN")
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "over_good_kf.mkv")))

    def test_real_ffmpeg_incremental_probing_and_cache_reload(self) -> None:
        """증분 키프레임 분석 시 기존 키프레임 값 유지 및 디렉터리 파일 변경 감지 캐시 자동 갱신을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            v1_path = os.path.join(tmpdir, "v1.mp4")
            _create_real_video(v1_path, width=640, height=360, duration=3.0, fps=30, bitrate_k=500, keyint=30)

            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertEqual(len(vc.video_props), 1)
            self.assertIsNone(vc.video_props[0].keyframe_interval)

            vc.include_keyframe_interval(True)
            self.assertIsNotNone(vc.video_props[0].keyframe_interval)
            first_kf = vc.video_props[0].keyframe_interval

            vc.include_keyframe_interval(True)
            self.assertEqual(vc.video_props[0].keyframe_interval, first_kf)

            v2_path = os.path.join(tmpdir, "v2.mp4")
            _create_real_video(v2_path, width=640, height=360, duration=2.5, fps=30, bitrate_k=500, keyint=30)
            vc_reloaded = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=True)
            self.assertEqual(len(vc_reloaded.video_props), 2)

    def test_real_ffmpeg_exception_rules_sorting_and_pseudo_unmark(self) -> None:
        """사용자 정의 예외 규칙 등록, 윈도우 자연 정렬 다중 출력 및 가분류 표시 해제 기능을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = os.path.join(tmpdir, "vid_2.mp4")
            p2 = os.path.join(tmpdir, "vid_10.mp4")
            _create_real_video(p1, width=640, height=360, duration=3.0, fps=30, bitrate_k=1500, keyint=30, add_noise=True)
            _create_real_video(p2, width=640, height=360, duration=3.0, fps=30, bitrate_k=1500, keyint=30, add_noise=True)

            vc = VideoClassifier(root_dir=tmpdir)
            vc.set_small_footage_mb(0.0)
            vc.pseudo_classify_mode(True)

            vc.add_exception_rule(lambda p: "vid_2" in p.filename)
            rep = vc.classify()
            emap = {e.video.filename: e for e in rep.evaluations}
            self.assertIsNone(emap["vid_2.mp4"].target_dirname)
            self.assertEqual(emap["vid_10.mp4"].target_dirname, vc.config.dir_standard_cq22)

            vc.print(
                sanitize_emoji=True,
                sort_key=lambda x: [
                    col(x[t["예상 절감"]], desc),
                    col(WindowsNameOrder(x[t["이름"]])),
                ],
            )

            self.assertTrue(all(v.moved_dirname is None for v in vc.video_props))


if __name__ == "__main__":
    unittest.main(verbosity=2)
