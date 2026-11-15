import os
import subprocess
import sys
import unittest

import src
from src.classifier import VideoClassifier

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestClassifierModule(unittest.TestCase):
    """최상위 src 및 src.classifier의 VideoClassifier 엔트리포인트 구성을 검증"""

    def test_top_level_and_classifier_exports(self) -> None:
        self.assertIs(src.VideoClassifier, VideoClassifier)

    def test_notebook_star_import_exposes_public_api(self) -> None:
        """노트북 기본 템플릿의 from src import * 가 __all__ 전체 심볼을 노출함을 검증"""
        ns: dict[str, object] = {}
        exec("from src import *", ns)
        self.assertLessEqual(set(src.__all__), set(ns))
        self.assertIs(ns["VideoClassifier"], VideoClassifier)

    def test_core_import_does_not_load_facade_or_cui(self) -> None:
        """spawn 워커가 임포트하는 core 프로버가 파사드와 CUI, tabulate를 로드하지 않음을 검증"""
        code = (
            "import sys, src.core.prober; "
            "print('src.classifier' in sys.modules, 'tabulate' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False False")

    def test_gui_import_does_not_load_cui(self) -> None:
        """GUI 패키지 임포트가 CUI 계층을 로드하지 않음을 검증"""
        code = "import sys, src.gui; print('src.cui' in sys.modules)"
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        self.assertEqual(out.stdout.strip(), "False")

    def test_unclassify_files_logs_by_issue_kind_and_returns_text(self) -> None:
        """실패 이슈는 ERROR, 충돌 이슈는 WARNING으로 기록하고 반환값은 기존 문구 리스트인지 검증"""
        import tempfile
        from unittest.mock import patch

        from src.core.file_manager import MoveIssue, MoveIssueKind
        from src.core.messages import format_move_issue

        issues = [
            MoveIssue(MoveIssueKind.UNCLASSIFY_ROOT_EXISTS, "a.mp4", "_1_x"),
            MoveIssue(
                MoveIssueKind.UNCLASSIFY_MOVE_FAILED,
                "b.mp4",
                "_1_x",
                src_path="/src/b.mp4",
                dst_path="/root/b.mp4",
                error="disk full",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir)
            with (
                patch(
                    "src.service.classifier_service.VideoClassifierService.unclassify_files",
                    return_value=(0, issues),
                ),
                self.assertLogs("src.service.session", level="WARNING") as cm,
            ):
                moved, texts = vc.unclassify_files()

        self.assertEqual(moved, 0)
        self.assertEqual(texts, [format_move_issue(issue) for issue in issues])
        self.assertEqual([r.levelname for r in cm.records], ["WARNING", "ERROR"])

    def test_facade_public_state_properties_and_set_config(self) -> None:
        """VideoClassifier 파사드가 내부 service 속성을 직접 노출하지 않고 상태 조회/설정 메서드를 제공함을 검증"""
        import tempfile

        from src.core.config import ClassifierConfig, CuiConfig, ThresholdsConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertFalse(vc.is_pseudo_classify)
            self.assertFalse(vc.include_keyframe)
            self.assertFalse(vc.has_cache)

            vc.pseudo_classify_mode(True)
            self.assertTrue(vc.is_pseudo_classify)

            new_cfg = ClassifierConfig(
                thresholds=ThresholdsConfig(over_bitrate_ratio=1.45),
                cui=CuiConfig(filename_maxlen=60),
            )
            vc.set_config(new_cfg)
            self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.45)
            self.assertEqual(vc.filename_maxlen, 60)

    def test_cui_does_not_import_service_layer(self) -> None:
        """CUI 계층이 Service 계층을 직접 임포트하지 않고 도메인/프로토콜 경계를 유지함을 검증"""
        import ast
        import os

        cui_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "src", "cui")
        )
        for fname in os.listdir(cui_root):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(cui_root, fname)
            with open(fpath, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("src.service"),
                        f"CUI->Service 역방향 임포트 발견: {fpath} ({node.module})",
                    )

    def test_save_reload_state_and_custom_exception_preservation(self) -> None:
        """CUI 사용자 정의 람다 예외 규칙 보존 및 save_state, reload_state 계층 연동을 검증"""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        from src.core.app_state import (
            CONFIG_FILENAME,
            DATA_DIR_NAME,
            load_app_state,
            save_app_state,
        )
        from src.core.exceptions import DomainFileNotFoundError
        from src.core.models import VideoProps
        from src.core.paths import ROOTDIR_FILENAME, get_root_dir

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            tempfile.TemporaryDirectory() as legacy_dir,
            tempfile.TemporaryDirectory() as rootfile_dir,
        ):
            root_path = os.path.join(tmpdir, DATA_DIR_NAME, ROOTDIR_FILENAME)
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.paths.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                # 경로 미해결 시 도메인 예외 발생
                with self.assertRaises(DomainFileNotFoundError):
                    VideoClassifier()

                # 구형 JSON에 root_dir이 남아있어도 data/rootdir.txt만 작업 경로로 사용됨
                os.makedirs(os.path.dirname(state_path), exist_ok=True)
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "paths": {"root_dir": legacy_dir},
                            "analysis": {"thresholds": {"over_bitrate_ratio": 1.35}},
                        },
                        f,
                    )
                with open(root_path, "w", encoding="utf-8") as f:
                    f.write(rootfile_dir)

                vc = VideoClassifier()
                self.assertEqual(vc.root_dir, rootfile_dir)
                self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.35)

                # CUI 람다 예외 규칙은 set_exception_tokens 갱신 시에도 유지됨
                from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache

                cache = TableCache(rootfile_dir, [
                    VideoProps(
                        filename="keep_cui_clip.hevc.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9500,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="hevc",
                        keyframe_interval=1.0,
                    ),
                    VideoProps(
                        filename="regular.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9500,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="h264",
                        keyframe_interval=1.0,
                    ),
                ], include_keyframe=True)
                DEFAULT_CACHE_REGISTRY.put(rootfile_dir, cache)

                vc.add_exception_rule(lambda v: "keep_cui" in v.filename)
                vc.set_exception_tokens(["v.codec == 'h264'"])
                self.assertEqual(vc.exception_tokens, ["v.codec == 'h264'"])
                rep = vc.get_summary_report(ensure_keyframe=False)
                emap = {e.video.filename: e for e in rep.evaluations}
                self.assertIsNone(emap["keep_cui_clip.hevc.mp4"].target_dirname)
                self.assertIsNone(emap["regular.mp4"].target_dirname)

                vc.set_over_bitrate_ratio(1.75)
                vc.save_state(save_root_dir=False)
                saved = load_app_state()
                self.assertAlmostEqual(saved.analysis.thresholds.over_bitrate_ratio, 1.75)
                self.assertEqual(saved.ui.exception_tokens, ["v.codec == 'h264'"])

                # save_root_dir=True 시 data/rootdir.txt도 함께 갱신됨
                vc.save_state(save_root_dir=True)
                self.assertEqual(get_root_dir(), rootfile_dir)

                # 외부에서 JSON 설정을 수정 후 reload_state 반영 확인
                saved.analysis.thresholds.over_bitrate_ratio = 1.90
                save_app_state(saved)
                vc.reload_state()
                self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.90)

                import io
                from contextlib import redirect_stdout

                sum_buf = io.StringIO()
                with redirect_stdout(sum_buf):
                    vc.print_summary()
                sum_out = sum_buf.getvalue()
                self.assertNotIn("regular.mp4", sum_out)
                self.assertIn("판별 기준 요약", sum_out)

                tbl_buf = io.StringIO()
                with redirect_stdout(tbl_buf):
                    vc.print_tables(limit=1)
                tbl_out = tbl_buf.getvalue()
                self.assertIn("keep_cui_clip", tbl_out)
                self.assertIn("외 1개 생략", tbl_out)
                self.assertNotIn("판별 기준 요약", tbl_out)

                no_sum_buf = io.StringIO()
                with redirect_stdout(no_sum_buf):
                    vc.print(include_summary=False)
                no_sum_out = no_sum_buf.getvalue()
                self.assertIn("regular.mp4", no_sum_out)
                self.assertNotIn("판별 기준 요약", no_sum_out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
