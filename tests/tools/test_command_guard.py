import io
import json
import os
import unittest
from unittest.mock import patch

from scripts.hooks.command_guard import (
    BLOCK_EXIT_CODE,
    check_bash_command,
    check_cline_payload,
    evaluate_agy_payload,
    evaluate_bash_command,
    is_read_only_command,
    main,
)


class TestCheckBashCommand(unittest.TestCase):
    """pre-bash 훅의 git 및 셸 명령 분해와 자동 대체, 차단 판정 검증"""

    def setUp(self) -> None:
        environ = patch.dict("os.environ")
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("GIT_PAGER", None)

    def test_force_push_variants_are_blocked(self) -> None:
        commands = (
            "git push --force origin main",
            "git push -f",
            "git push -fu origin main",
            "git push --force-with-lease origin main",
            "git -C sub push -f",
            "cd x && git push -f",
            "git status\ngit push --force",
            "git push origin +main",
            "bash -c 'git push --force'",
            "pwsh -NoProfile -c git push -f",
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
            "git merge --no-verify feature",
            "SKIP=ruff git commit -m x",
            "$env:SKIP='ruff'; git commit -m x",
            "export SKIP=ruff",
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
            'git commit -m "-n 옵션"',
            "git commit --amend --no-edit",
            "git --no-pager log -n 5",
            "git --no-pager diff --stat",
            "git -P show HEAD",
            "GIT_PAGER=cat git log -n 3",
            "echo git push --force",
            'cmd /c "python -m unittest -b tests.tools.test_command_guard 2>&1"',
            # - 금지 명령을 따옴표 안 글자로만 언급하는 검색, 커밋 메시지는 통과
            'grep -n "unittest discover -s tests/gui" AGENTS.md',
            'git commit -m "docs: unittest discover, ruff check --fix, Start-Process 금지"',
            'git commit -m "set X=1 && y"',
            'set "QT_QPA_PLATFORM=offscreen" && python -m unittest -b tests.gui.test_app',
            "set QT_QPA_PLATFORM=offscreen&& python -m unittest -b tests.gui.test_app",
            "$env:QT_QPA_PLATFORM='offscreen'; python -m unittest -b tests.gui.test_app",
            # - 편집기를 건너뛰는 Git, 재귀 없는 링크 해제, 수정 없는 Ruff
            "git -c core.editor=true rebase --continue",
            "GIT_EDITOR=true git cherry-pick --continue",
            "git revert --no-edit HEAD",
            "git tag -a v1 -m release",
            "git tag v1",
            "git commit --fixup HEAD",
            r"rmdir ..\worktrees\task\.venv",
            'cmd /c "rmdir ..\\worktrees\\task\\.venv"',
            "ruff check src",
            "Start-Sleep -Seconds 1",
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

    def test_interactive_git_stays_blocked(self) -> None:
        blocked = (
            "git add -p src/a.py",
            "git rebase -i HEAD~3",
            "git commit",
            "git commit --amend",
            "git commit -c HEAD",
            "git rebase --continue",
            "git cherry-pick --continue",
            "git revert HEAD",
            "git tag -a v1",
        )
        for command in blocked:
            with self.subTest(command=command):
                self.assertEqual(evaluate_bash_command(command).action, "deny")

    def test_pager_commands_are_auto_rewritten(self) -> None:
        cases = (
            ("git log -n 5", "git --no-pager log -n 5"),
            ('git -C "C:\\repo" diff --format="%h %s"', 'git --no-pager -C "C:\\repo" diff --format="%h %s"'),
            ("cd sub && git show HEAD", "cd sub && git --no-pager show HEAD"),
            ("git log -n 10 | less", "git --no-pager log -n 10"),
            ("git status | more", "git status"),
            ("less AGENTS.md", "cat AGENTS.md"),
            ("more AGENTS.md REVIEW.md", "cat AGENTS.md REVIEW.md"),
        )
        for raw, expected in cases:
            with self.subTest(command=raw):
                decision = evaluate_bash_command(raw)
                self.assertEqual(decision.action, "rewrite")
                self.assertEqual(decision.rewritten_command, expected)

    def test_read_only_command_detection(self) -> None:
        for command in ("git --no-pager log -n 5", "cd sub && git --no-pager show HEAD", "cat AGENTS.md", "git status"):
            with self.subTest(command=command):
                self.assertTrue(is_read_only_command(command))
        for command in (
            "git --no-pager log > out.txt",
            "git --no-pager diff --output=x.patch",
            "git --no-pager log && npm install",
            "git commit -m x",
            "",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_read_only_command(command))

    def test_git_pager_passes_when_hook_environment_sets_git_pager(self) -> None:
        with patch.dict("os.environ", {"GIT_PAGER": "cat"}):
            self.assertEqual(evaluate_bash_command("git log -n 5").action, "allow")
            # - 직접 부른 페이저와 다른 위반은 환경과 무관하게 그대로 처리
            self.assertEqual(evaluate_bash_command("git log -n 5 | less").action, "rewrite")
            self.assertEqual(evaluate_bash_command("git log && git push -f").action, "deny")
        with patch.dict("os.environ", {"GIT_PAGER": ""}):
            self.assertEqual(evaluate_bash_command("git log -n 5").action, "rewrite")

    def test_hard_block_overrides_pager_rewrite_and_unrewritable_pager_denies(self) -> None:
        self.assertEqual(evaluate_bash_command("git log -n 5 && git push --force").action, "deny")
        self.assertEqual(evaluate_bash_command("less -N AGENTS.md").action, "deny")
        self.assertEqual(evaluate_bash_command("less").action, "deny")
        self.assertEqual(evaluate_bash_command('cmd /c "git diff --stat"').action, "deny")

    def test_new_window_cmd_set_discovery_ruff_fix_and_link_delete_are_blocked(self) -> None:
        blocked = (
            "Start-Process notepad",
            "start notepad",
            "cmd /c start notepad",
            'powershell -Command "Start-Process notepad"',
            'cmd /c "set QT_QPA_PLATFORM=offscreen && python -m unittest -b tests.gui.test_app"',
            "set QT_QPA_PLATFORM=offscreen && python -m unittest -b tests.gui.test_app",
            "python -m unittest discover -s tests/gui",
            "python -m unittest discover -s tests",
            r".venv\Scripts\python.exe -m unittest discover",
            "python -m unittest -b",
            "ruff check --fix src",
            "python -m ruff check --fix",
            "rm -rf ../worktrees/task",
            "Remove-Item -Recurse -Force .venv",
            r"Remove-Item -Rec -Force ..\worktrees\task",
            r"rd /s /q ..\worktrees\task",
            'cmd /c "rmdir /s /q ..\\worktrees\\task"',
        )
        for command in blocked:
            with self.subTest(command=command):
                self.assertIsNotNone(check_bash_command(command))

    def test_main_reads_tool_input_command_for_powershell_payload(self) -> None:
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "git worktree remove --force wt"}}
        raw_err = io.BytesIO()
        err = io.TextIOWrapper(raw_err, encoding="utf-8")
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stderr", err):
            self.assertEqual(main(["command_guard.py", "pre-bash"]), BLOCK_EXIT_CODE)
        err.flush()
        self.assertIn("rmdir", raw_err.getvalue().decode("utf-8"))

        # - 읽기 전용 페이저 명령은 바꿔 허용하고 다른 입력 필드는 유지
        read_only = {"tool_name": "Bash", "tool_input": {"command": "git diff --stat | less", "description": "d"}}
        raw_out = io.BytesIO()
        allow_out = io.TextIOWrapper(raw_out, encoding="utf-8")
        with patch("sys.stdin", io.StringIO(json.dumps(read_only))), patch("sys.stdout", allow_out):
            self.assertEqual(main(["command_guard.py", "pre-bash"]), 0)
        allow_out.flush()
        hook_out = json.loads(raw_out.getvalue().decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual(hook_out["permissionDecision"], "allow")
        self.assertEqual(hook_out["updatedInput"], {"command": "git --no-pager diff --stat", "description": "d"})

        # - 읽기 전용이 아닌 명령이 섞이면 권한 확인을 건너뛰지 않도록 대체 명령을 안내하며 차단
        rewrite_payload = {"tool_name": "Bash", "tool_input": {"command": "git diff --stat && npm install"}}
        raw_hint = io.BytesIO()
        hint = io.TextIOWrapper(raw_hint, encoding="utf-8")
        out = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(rewrite_payload))),
            patch("sys.stderr", hint),
            patch("sys.stdout", out),
        ):
            self.assertEqual(main(["command_guard.py", "pre-bash"]), BLOCK_EXIT_CODE)
        hint.flush()
        self.assertIn("git --no-pager diff --stat", raw_hint.getvalue().decode("utf-8"))
        self.assertEqual(out.getvalue(), "")

        allowed = {"tool_name": "PowerShell", "tool_input": {"command": "git worktree list"}}
        with patch("sys.stdin", io.StringIO(json.dumps(allowed))):
            self.assertEqual(main(["command_guard.py", "pre-bash"]), 0)

    def test_pre_agy_adapter_returns_allow_overwrite_and_deny_json(self) -> None:
        pager = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git diff --stat"},
            }
        }
        self.assertEqual(evaluate_agy_payload(pager).action, "rewrite")
        raw_out = io.BytesIO()
        out = io.TextIOWrapper(raw_out, encoding="utf-8")
        with patch("sys.stdin", io.StringIO(json.dumps(pager))), patch("sys.stdout", out):
            self.assertEqual(main(["command_guard.py", "pre-agy"]), 0)
        out.flush()
        parsed = json.loads(raw_out.getvalue().decode("utf-8"))
        self.assertEqual(parsed["decision"], "allow")
        self.assertEqual(parsed["overwrite"]["CommandLine"], "git --no-pager diff --stat")

        hard_blocked = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push --force"},
            }
        }
        raw_deny = io.BytesIO()
        deny_out = io.TextIOWrapper(raw_deny, encoding="utf-8")
        with patch("sys.stdin", io.StringIO(json.dumps(hard_blocked))), patch("sys.stdout", deny_out):
            self.assertEqual(main(["command_guard.py", "pre-agy"]), 0)
        deny_out.flush()
        self.assertEqual(json.loads(raw_deny.getvalue().decode("utf-8"))["decision"], "deny")

    def test_pre_cline_adapter_blocks_with_rewrite_suggestion(self) -> None:
        blocked = {
            "hookName": "tool_call",
            "tool_call": {"name": "run_commands"},
            "preToolUse": {
                "toolName": "run_commands",
                "parameters": json.dumps({"commands": ["git status", "git log -n 3"]}),
            },
        }
        msg = check_cline_payload(blocked)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("git --no-pager log -n 3", msg)

        non_shell = {
            "hookName": "tool_call",
            "tool_call": {"name": "read_file"},
            "preToolUse": {"toolName": "read_file", "parameters": {"path": "AGENTS.md"}},
        }
        self.assertIsNone(check_cline_payload(non_shell))


if __name__ == "__main__":
    unittest.main()
