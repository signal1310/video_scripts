import pathlib
import tempfile
import unittest

from scripts.verify_module_size import (
    BASELINE_REL_PATH,
    HARD_LIMIT,
    SOFT_LIMIT,
    check_sizes,
    collect_module_sizes,
    load_baseline,
    ratchet_baseline,
    write_baseline,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ABOVE_SOFT = SOFT_LIMIT + 10
_BASELINE_CAP = SOFT_LIMIT + 50


class TestModuleSize(unittest.TestCase):
    """모듈 줄 수 상한, 기준선 검사 단위 테스트"""

    def test_under_soft_limit_passes_silently(self) -> None:
        report = check_sizes({"src/a.py": SOFT_LIMIT}, {})
        self.assertEqual((report.failures, report.warnings, report.stale), ([], [], []))

    def test_over_soft_limit_without_baseline_only_warns(self) -> None:
        report = check_sizes({"src/a.py": _ABOVE_SOFT}, {})
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.warnings), 1)

    def test_over_hard_limit_fails_even_with_baseline(self) -> None:
        over_hard = HARD_LIMIT + 1
        report = check_sizes({"src/a.py": over_hard}, {"src/a.py": over_hard})
        self.assertEqual(len(report.failures), 1)

    def test_baseline_file_growth_fails_and_equal_size_passes(self) -> None:
        baseline = {"src/a.py": _BASELINE_CAP}
        self.assertEqual(check_sizes({"src/a.py": _BASELINE_CAP}, baseline).failures, [])
        self.assertEqual(len(check_sizes({"src/a.py": _BASELINE_CAP + 1}, baseline).failures), 1)

    def test_shrunk_or_missing_baseline_entries_are_reported_as_stale(self) -> None:
        baseline = {"src/a.py": _BASELINE_CAP, "src/b.py": _BASELINE_CAP, "src/gone.py": _BASELINE_CAP}
        sizes = {"src/a.py": _BASELINE_CAP - 1, "src/b.py": SOFT_LIMIT}
        report = check_sizes(sizes, baseline)
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.stale), 3)

    def test_ratchet_lowers_and_drops_but_never_adds_or_raises(self) -> None:
        baseline = {"src/a.py": _BASELINE_CAP, "src/b.py": _BASELINE_CAP, "src/gone.py": _BASELINE_CAP}
        sizes = {
            "src/a.py": _BASELINE_CAP - 1,
            "src/b.py": SOFT_LIMIT,
            "src/new.py": _ABOVE_SOFT,
        }
        self.assertEqual(ratchet_baseline(sizes, baseline), {"src/a.py": _BASELINE_CAP - 1})
        self.assertEqual(
            ratchet_baseline({"src/a.py": _BASELINE_CAP + 1}, {"src/a.py": _BASELINE_CAP}),
            {"src/a.py": _BASELINE_CAP},
        )

    def test_collect_and_baseline_roundtrip_in_temp_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "src" / "__pycache__").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            (root / "src" / "__pycache__" / "skip.py").write_text("z = 3\n", encoding="utf-8")

            self.assertEqual(collect_module_sizes(root), {"src/a.py": 2})
            self.assertEqual(load_baseline(root), {})

            write_baseline(root, {"src/z.py": _BASELINE_CAP, "src/a.py": _ABOVE_SOFT})
            self.assertEqual(load_baseline(root), {"src/a.py": _ABOVE_SOFT, "src/z.py": _BASELINE_CAP})

    def test_repository_modules_respect_limits_and_baseline(self) -> None:
        report = check_sizes(collect_module_sizes(_REPO_ROOT), load_baseline(_REPO_ROOT))
        self.assertEqual(report.failures, [], BASELINE_REL_PATH)


if __name__ == "__main__":
    unittest.main()
