import unittest

from scripts.hooks.claude_guard import check_bash_command


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


if __name__ == "__main__":
    unittest.main()
