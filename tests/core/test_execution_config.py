"""실행 설정(execution) 섹션의 기본값, 로드 시 보정, 직렬화 왕복과 설정 ffprobe 경로의 사용을 검증하는 단위 테스트"""

import json
import unittest
from unittest import mock

from src.core.analysis import prober
from src.core.config import (
    CONCURRENT_JOBS_MAX,
    RESULT_POLICY_SEPARATE,
    ClassifierConfig,
    ExecutionConfig,
)
from src.core.settings.state_codec import AppState, parse_state_mapping, render_state_jsonc, strip_jsonc_comments


class TestExecutionConfig(unittest.TestCase):
    """execution 섹션의 기본값과 로드 보정"""

    def test_defaults_match_plan_values(self) -> None:
        """기본값이 계획서 4.4절(보관 후 교체, 원본 형식 유지, 절전 방지 켬, 보통 우선순위, 건너뜀)과 같음을 검증"""
        execution = ExecutionConfig()
        self.assertEqual(execution.result_policy, "archive_original")
        self.assertEqual(execution.archive_dirname, "_원본보관")
        self.assertEqual(execution.output_dirname, "_인코딩결과")
        self.assertEqual(execution.container_policy, "keep")
        self.assertEqual(execution.concurrent_jobs, 0)
        self.assertTrue(execution.prevent_sleep)
        self.assertEqual(execution.after_complete, "none")
        self.assertEqual(execution.process_priority, "normal")
        self.assertEqual(execution.name_conflict, "skip")
        self.assertEqual(execution.encode_tag_mode, "visible")
        self.assertEqual(execution.encode_tag_name, "re_encoded")

    def test_invalid_tag_settings_fall_back_to_defaults(self) -> None:
        """잘못된 태그 기록 방식과 태그 이름(세미콜론, 65자, 공백만)은 기본값으로 대체되고, 64자 이름은 유지됨을 검증"""
        bad_mode = ClassifierConfig.from_dict({"execution": {"encode_tag_mode": "loud"}})
        self.assertEqual(bad_mode.execution.encode_tag_mode, "visible")
        for raw_name in ("a;b", "x" * 65, "   "):
            with self.subTest(raw_name=raw_name):
                cfg = ClassifierConfig.from_dict({"execution": {"encode_tag_name": raw_name}})
                self.assertEqual(cfg.execution.encode_tag_name, "re_encoded")
        at_limit = ClassifierConfig.from_dict({"execution": {"encode_tag_name": "y" * 64}})
        self.assertEqual(at_limit.execution.encode_tag_name, "y" * 64)

    def test_invalid_values_fall_back_and_concurrency_is_clamped(self) -> None:
        """잘못된 선택값은 기본값으로, 동시 실행 수는 화면 상한으로 제한됨을 검증"""
        cfg = ClassifierConfig.from_dict({"execution": {
            "result_policy": "unknown", "after_complete": "hibernate", "concurrent_jobs": 99,
            "process_priority": "realtime", "name_conflict": "rename", "container_policy": "avi",
        }})
        self.assertEqual(cfg.execution.result_policy, "archive_original")
        self.assertEqual(cfg.execution.after_complete, "none")
        self.assertEqual(cfg.execution.concurrent_jobs, CONCURRENT_JOBS_MAX)
        self.assertEqual(cfg.execution.process_priority, "normal")
        self.assertEqual(cfg.execution.name_conflict, "skip")
        self.assertEqual(cfg.execution.container_policy, "keep")

    def test_folder_names_must_be_single_components(self) -> None:
        """폴더 이름에 경로 구분자가 있거나 비어 있으면 기본 이름으로 대체됨을 검증"""
        cfg = ClassifierConfig.from_dict({"execution": {"archive_dirname": "a/b", "output_dirname": "  "}})
        self.assertEqual(cfg.execution.archive_dirname, "_원본보관")
        self.assertEqual(cfg.execution.output_dirname, "_인코딩결과")

    def test_round_trip_through_state_json_keeps_values(self) -> None:
        """설정 파일 문자열로 저장한 뒤 다시 읽어도 실행 설정 값이 그대로 유지됨을 검증"""
        state = AppState()
        state.execution = ExecutionConfig(
            result_policy=RESULT_POLICY_SEPARATE, concurrent_jobs=2, name_conflict="number",
            encode_tag_mode="off", encode_tag_name="my_tag",
        )
        text = strip_jsonc_comments(render_state_jsonc(state))
        restored = parse_state_mapping(json.loads(text))
        self.assertEqual(restored.execution.result_policy, RESULT_POLICY_SEPARATE)
        self.assertEqual(restored.execution.concurrent_jobs, 2)
        self.assertEqual(restored.execution.name_conflict, "number")
        self.assertEqual(restored.execution.encode_tag_mode, "off")
        self.assertEqual(restored.execution.encode_tag_name, "my_tag")
        self.assertEqual(restored.to_classifier_config().execution.concurrent_jobs, 2)

    def test_new_name_conflict_policies_survive_state_json(self) -> None:
        """덮어쓰기, 설정별 하위 폴더 선택도 설정 파일 저장과 복원을 거쳐 그대로 유지됨을 검증"""
        for policy in ("overwrite", "settings_subdir"):
            with self.subTest(policy=policy):
                state = AppState()
                state.execution = ExecutionConfig(name_conflict=policy)
                text = strip_jsonc_comments(render_state_jsonc(state))
                restored = parse_state_mapping(json.loads(text))
                self.assertEqual(restored.execution.name_conflict, policy)


class TestConfiguredFfprobe(unittest.TestCase):
    """설정한 ffprobe 경로가 프로버 실행 명령의 첫 항목으로 쓰임을 검증"""

    def test_resolver_uses_configured_path_or_default_name(self) -> None:
        """설정 경로가 있으면 그대로, 비었으면 기본 이름을 쓰는지 검증"""
        self.assertEqual(prober.resolve_ffprobe_executable("  bin/ffprobe-custom  "), "bin/ffprobe-custom")
        self.assertEqual(prober.resolve_ffprobe_executable(" "), prober.FFPROBE_EXECUTABLE_DEFAULT)

    def test_run_ffprobe_launches_the_given_executable(self) -> None:
        """run_ffprobe가 지정한 실행 파일로 프로세스를 띄움을 검증(실행은 가짜 Popen으로 대체)"""
        with mock.patch.object(prober.subprocess, "Popen", side_effect=FileNotFoundError("missing")) as popen,              self.assertRaises(OSError):
            prober.run_ffprobe("videos/a.mp4", ffprobe="bin/ffprobe-custom")
        self.assertEqual(popen.call_args.args[0][0], "bin/ffprobe-custom")


if __name__ == "__main__":
    unittest.main(verbosity=2)
