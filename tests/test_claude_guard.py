import io
import json
import unittest
from unittest.mock import patch

from scripts.hooks.claude_guard import BLOCK_EXIT_CODE, check_bash_command, main


class TestCheckBashCommand(unittest.TestCase):
    """pre-bash 훅의 git 명령 분해와 위반 판정 검증"""

    def test_force_push_variants_are_blocked(self) -> None:
        commands = (
            "git push --force origin main",
            "git push -f",
            "git push -fu origin main",
            "git push --force-with-lease origin main",
            "git -C sub push -f",
            "cd x && git push -f",
            "git status\ngit push --force",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))

    def test_hook_bypass_is_blocked(self) -> None:
        commands = (
            "git commit --no-verify -m x",
            "git commit -n -m x",
            "git push --no-verify",
            "git -c core.hooksPath=/dev/null commit -m x",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))

    def test_non_target_staging_is_blocked(self) -> None:
        commands = (
            "git add data/x.csv",
            "git add src/a.py; git add .env",
            "git add -A data/raw.csv",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))

    def test_quoted_text_and_normal_commands_pass(self) -> None:
        commands = (
            "git push origin main",
            "git push origin main 2>&1",
            "git status",
            "git add src/a.py",
            "git commit -m 'git push --force 설명'",
            "git commit -m \"-n 옵션\"",
            "echo git push --force",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(check_bash_command(command))

    def test_forced_worktree_removal_is_blocked(self) -> None:
        commands = (
            "git worktree remove --force .venv-wt",
            "git worktree remove -f .venv-wt",
            "git -C repo worktree remove -f wt",
            "git worktree remove wt --force",
            "cd x && git worktree remove --force y",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))

    def test_plain_worktree_commands_pass(self) -> None:
        commands = (
            "git worktree remove wt",
            "git worktree list",
            "git worktree add ../wt HEAD",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(check_bash_command(command))

    def test_windows_path_separators_are_checked(self) -> None:
        blocked = (
            r"git add .venv\Scripts\python.exe",
            r"git add data\raw.txt",
            r'git worktree remove --force "C:\tmp\wt"',
        )
        for command in blocked:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))
        self.assertIsNone(check_bash_command(r"git add src\a.py"))

    def test_main_reads_tool_input_command_for_powershell_payload(self) -> None:
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "git worktree remove --force wt"}}
        raw_err = io.BytesIO()
        err = io.TextIOWrapper(raw_err, encoding="utf-8")
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stderr", err):
            self.assertEqual(main(["claude_guard.py", "pre-bash"]), BLOCK_EXIT_CODE)
        err.flush()
        self.assertIn("rmdir", raw_err.getvalue().decode("utf-8"))
        allowed = {"tool_name": "PowerShell", "tool_input": {"command": "git worktree list"}}
        with patch("sys.stdin", io.StringIO(json.dumps(allowed))):
            self.assertEqual(main(["claude_guard.py", "pre-bash"]), 0)


if __name__ == "__main__":
    unittest.main()
