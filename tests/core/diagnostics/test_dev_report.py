"""진단 보고서 zip: 파일 수집, 이름 충돌, 보관 정리, 실패 처리 검증"""

import os
import tempfile
import unittest
import zipfile
from datetime import datetime

from src.core.diagnostics.dev_report import (
    APP_TAIL_NAME,
    BREADCRUMBS_TAIL_NAME,
    DEV_TAIL_NAME,
    MAX_KEPT_ARCHIVES,
    NATIVE_TAIL_NAME,
    REPORT_STEM,
    SESSION_FILENAME,
    SUMMARY_NAME,
    create_diagnostic_report,
    list_archives,
    prune_diagnostic_archives,
    report_archive_name,
    resolve_archive_path,
)
from src.core.diagnostics.paths import (
    APP_LOG_NAME,
    BREADCRUMBS_NAME,
    DEV_LOG_NAME,
    NATIVE_LOG_NAME,
    diagnostics_file,
)

_WHEN = datetime(2026, 6, 7, 8, 9, 10)


def _write(path: str, text: str) -> None:
    """테스트용 로그 파일 생성"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class ArchiveNameTests(unittest.TestCase):
    """보고서 zip 이름 규칙 검증"""

    def test_name_uses_timestamp_stem_and_suffix(self) -> None:
        """이름이 시각, 고정 접두사, zip 확장자로 만들어지는지 검증"""
        self.assertEqual(report_archive_name(_WHEN), f"{REPORT_STEM}20260607-080910.zip")

    def test_conflicting_name_gets_a_number_suffix(self) -> None:
        """같은 이름이 있으면 번호를 붙인 경로를 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            first = resolve_archive_path(tmp, _WHEN)
            _write(first, "이미 있음")
            second = resolve_archive_path(tmp, _WHEN)
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("_2.zip"))


class CreateReportTests(unittest.TestCase):
    """zip 생성과 포함 파일 검증"""

    def test_report_contains_log_tails_and_session_copy(self) -> None:
        """앱, 개발, 브레드크럼, 네이티브 로그와 표식, 요약이 zip에 들어가는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            _write(diagnostics_file(APP_LOG_NAME, tmp), "앱 로그 한 줄\n")
            _write(diagnostics_file(DEV_LOG_NAME, tmp), "개발 로그 한 줄\n")
            _write(diagnostics_file(BREADCRUMBS_NAME, tmp), "동작 기록 한 줄\n")
            _write(diagnostics_file(NATIVE_LOG_NAME, tmp), "네이티브 한 줄\n")
            _write(diagnostics_file(SESSION_FILENAME, tmp), '{"pid": 1}')

            report = create_diagnostic_report(tmp, now=_WHEN)

            self.assertEqual(report.folder, tmp)
            self.assertTrue(os.path.isfile(report.archive))
            with zipfile.ZipFile(report.archive) as bundle:
                names = set(bundle.namelist())
                app_text = bundle.read(APP_TAIL_NAME).decode("utf-8")
                session_text = bundle.read(SESSION_FILENAME).decode("utf-8")
                summary = bundle.read(SUMMARY_NAME).decode("utf-8")
            self.assertEqual(
                names,
                {
                    SUMMARY_NAME,
                    APP_TAIL_NAME,
                    DEV_TAIL_NAME,
                    BREADCRUMBS_TAIL_NAME,
                    NATIVE_TAIL_NAME,
                    SESSION_FILENAME,
                },
            )
            self.assertIn("앱 로그 한 줄", app_text)
            self.assertIn('"pid": 1', session_text)
            self.assertIn("만든 시각: 2026-06-07 08:09:10", summary)
            self.assertIn("로컬 파일 경로", summary)

    def test_missing_logs_still_produce_a_report_with_a_summary(self) -> None:
        """로그가 하나도 없어도 요약만 담은 zip을 만드는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            report = create_diagnostic_report(tmp, now=_WHEN)
            with zipfile.ZipFile(report.archive) as bundle:
                empty = bundle.read(APP_TAIL_NAME).decode("utf-8")
            self.assertEqual(empty, "")

    def test_second_report_keeps_both_archives(self) -> None:
        """같은 시각에 두 번 만들어도 앞 보고서를 덮어쓰지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            first = create_diagnostic_report(tmp, now=_WHEN)
            second = create_diagnostic_report(tmp, now=_WHEN)
            self.assertNotEqual(first.archive, second.archive)
            self.assertEqual(len(list_archives(tmp)), 2)

    def test_no_staging_folder_is_left_behind(self) -> None:
        """zip을 만든 뒤 임시 모음 폴더가 남지 않는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            create_diagnostic_report(tmp, now=_WHEN)
            leftovers = [name for name in os.listdir(tmp) if name.startswith(".report_")]
        self.assertEqual(leftovers, [])


class PruneTests(unittest.TestCase):
    """보관 개수 정리 검증"""

    def test_prune_keeps_the_newest_archives(self) -> None:
        """상한을 넘으면 이름이 이른 zip부터 지우는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index in range(MAX_KEPT_ARCHIVES + 2):
                path = os.path.join(tmp, f"{REPORT_STEM}2026010{index}-000000.zip")
                _write(path, "내용")
                paths.append(path)
            removed = prune_diagnostic_archives(tmp, MAX_KEPT_ARCHIVES)
            remaining = list_archives(tmp)
        self.assertEqual(removed, paths[:2])
        self.assertEqual(len(remaining), MAX_KEPT_ARCHIVES)

    def test_create_report_keeps_only_the_limit(self) -> None:
        """보고서를 상한보다 많이 만들어도 최근 것만 남는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(MAX_KEPT_ARCHIVES + 2):
                create_diagnostic_report(tmp, now=datetime(2026, 1, 1 + index))
            self.assertEqual(len(list_archives(tmp)), MAX_KEPT_ARCHIVES)

    def test_prune_ignores_unrelated_files_and_zero_keep(self) -> None:
        """다른 파일은 건드리지 않고 keep 0이면 모두 지우는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, f"{REPORT_STEM}20260607-080910.zip")
            other = os.path.join(tmp, "app.log")
            _write(archive, "내용")
            _write(other, "내용")
            self.assertEqual(prune_diagnostic_archives(tmp, 0), [archive])
            self.assertTrue(os.path.isfile(other))

    def test_prune_tolerates_a_missing_directory(self) -> None:
        """없는 폴더를 정리해도 예외 없이 빈 목록을 돌려주는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "none")
            self.assertEqual(list_archives(missing), [])
            self.assertEqual(prune_diagnostic_archives(missing), [])


if __name__ == "__main__":
    unittest.main()
