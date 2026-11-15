import json
import pathlib
import tempfile
import unittest

from scripts.verify_module_size import (
    EXCEPTIONS_REL_PATH,
    HARD_LIMIT,
    REVIEW_GROWTH_PERCENT,
    SOFT_LIMIT,
    Exceptions,
    ExemptEntry,
    check_sizes,
    collect_module_sizes,
    load_exceptions,
    ratchet_frozen,
    write_exceptions,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ABOVE_SOFT = SOFT_LIMIT + 10
_FROZEN_CAP = SOFT_LIMIT + 50


def _exempt(lines: int) -> ExemptEntry:
    return ExemptEntry(category="declarative-data", reason="테스트용 등록 사유", lines=lines)


class TestModuleSize(unittest.TestCase):
    """모듈 줄 수 상한, 동결 기준선, 화이트리스트 검사 단위 테스트"""

    def test_under_soft_limit_passes_silently(self) -> None:
        report = check_sizes({"src/a.py": SOFT_LIMIT}, Exceptions())
        self.assertEqual((report.failures, report.warnings, report.stale), ([], [], []))

    def test_over_soft_limit_without_entry_only_warns(self) -> None:
        report = check_sizes({"src/a.py": _ABOVE_SOFT}, Exceptions())
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.warnings), 1)

    def test_over_hard_limit_without_exempt_entry_fails(self) -> None:
        report = check_sizes({"src/a.py": HARD_LIMIT + 1}, Exceptions())
        self.assertEqual(len(report.failures), 1)

    def test_exempt_entry_bypasses_hard_limit_and_passes_at_recorded_size(self) -> None:
        over_hard = HARD_LIMIT + 300
        report = check_sizes({"src/a.py": over_hard}, Exceptions(exempt={"src/a.py": _exempt(over_hard)}))
        self.assertEqual((report.failures, report.warnings), ([], []))

    def test_exempt_growth_beyond_review_percent_warns_for_review(self) -> None:
        recorded = HARD_LIMIT + 300
        allowed = recorded * (100 + REVIEW_GROWTH_PERCENT) // 100
        exceptions = Exceptions(exempt={"src/a.py": _exempt(recorded)})
        self.assertEqual(check_sizes({"src/a.py": allowed}, exceptions).warnings, [])
        report = check_sizes({"src/a.py": allowed + 1}, exceptions)
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.warnings), 1)

    def test_frozen_growth_fails_and_equal_size_passes(self) -> None:
        exceptions = Exceptions(frozen={"src/a.py": _FROZEN_CAP})
        self.assertEqual(check_sizes({"src/a.py": _FROZEN_CAP}, exceptions).failures, [])
        self.assertEqual(len(check_sizes({"src/a.py": _FROZEN_CAP + 1}, exceptions).failures), 1)

    def test_shrunk_or_missing_registered_entries_are_reported_as_stale(self) -> None:
        exceptions = Exceptions(
            frozen={"src/a.py": _FROZEN_CAP, "src/gone.py": _FROZEN_CAP},
            exempt={"src/b.py": _exempt(_ABOVE_SOFT)},
        )
        sizes = {"src/a.py": _FROZEN_CAP - 1, "src/b.py": SOFT_LIMIT}
        report = check_sizes(sizes, exceptions)
        self.assertEqual(report.failures, [])
        self.assertEqual(len(report.stale), 3)

    def test_ratchet_lowers_drops_missing_but_never_adds_or_raises(self) -> None:
        frozen = {"src/a.py": _FROZEN_CAP, "src/b.py": _FROZEN_CAP, "src/gone.py": _FROZEN_CAP}
        sizes = {"src/a.py": _FROZEN_CAP - 1, "src/b.py": SOFT_LIMIT, "src/new.py": _ABOVE_SOFT}
        self.assertEqual(
            ratchet_frozen(sizes, frozen),
            {"src/a.py": _FROZEN_CAP - 1, "src/b.py": SOFT_LIMIT},
        )
        self.assertEqual(
            ratchet_frozen({"src/a.py": _FROZEN_CAP + 1}, {"src/a.py": _FROZEN_CAP}),
            {"src/a.py": _FROZEN_CAP},
        )

    def test_load_rejects_exempt_entry_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "scripts").mkdir()
            payload = {"exempt": {"src/a.py": {"category": "declarative-data", "reason": " ", "lines": 900}}}
            (root / EXCEPTIONS_REL_PATH).write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_exceptions(root)

    def test_collect_and_exceptions_roundtrip_in_temp_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "src" / "__pycache__").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            (root / "src" / "__pycache__" / "skip.py").write_text("z = 3\n", encoding="utf-8")

            self.assertEqual(collect_module_sizes(root), {"src/a.py": 2})
            self.assertEqual(load_exceptions(root), Exceptions())

            saved = Exceptions(frozen={"src/z.py": _FROZEN_CAP}, exempt={"src/a.py": _exempt(2)})
            write_exceptions(root, saved)
            self.assertEqual(load_exceptions(root), saved)

    def test_repository_modules_respect_limits_and_exceptions(self) -> None:
        report = check_sizes(collect_module_sizes(_REPO_ROOT), load_exceptions(_REPO_ROOT))
        self.assertEqual(report.failures, [], EXCEPTIONS_REL_PATH)


if __name__ == "__main__":
    unittest.main()
