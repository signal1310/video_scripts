"""에이전트 공통 명령어 훅 가드 모듈

- `AGENTS.md` 6절, 7절의 금지 명령 차단: Git 훅 우회(`--no-verify`, `core.hooksPath`, pre-commit `SKIP`), 강제 푸시(`+` refspec 포함), 비대상 스테이징, 강제 워크트리 삭제, 입력을 기다리는 Git, 새 창 실행, cmd `set` 공백 함정, `unittest` 디렉터리 탐색 실행, 링크 폴더 재귀 삭제, 별도 `ruff check --fix`
- 다른 셸에 넘긴 명령(`cmd /c`, `pwsh -c`, `powershell -Command`, `bash -c`)도 같은 규칙으로 검사
- 페이저 명령(`git log`/`diff`/`show` 등, `| more`/`| less`, 단독 `more`/`less`)은 비대화형 대체 명령을 계산해 안내
- 실행 모드:
  - `pre-bash`: Claude Code `PreToolUse`(`Bash|PowerShell`), 차단은 종료 코드 2와 stderr. 페이저 대체는 읽기 전용 명령이면 `updatedInput`으로 바꿔 허용하고, 그 밖은 권한 확인을 건너뛰지 않도록 대체 명령을 안내하며 차단
  - `pre-agy`: Antigravity `PreToolUse`(`run_command`), stdout JSON(`decision`, `reason`, `overwrite`)
- Cline은 전역 `PreToolUse` 훅이 `check_cline_payload`를 직접 불러 씀
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from typing import Any, cast

BLOCK_EXIT_CODE = 2

PUNCTUATION_CHARS = "();<>|&"
RE_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
RE_SHORT_FLAGS = re.compile(r"-[A-Za-z]+")
# - PowerShell은 매개변수 이름의 앞부분만 써도 받으므로 `-r`, `-rec`, `-Recurse:$true`를 모두 재귀 옵션으로 봄
RE_PS_RECURSE = re.compile(r"-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?(?::\$true)?")
RE_PS_SKIP_ENV = re.compile(r"(?i)\$env:SKIP\b")
# - 역슬래시 경로가 셸 이스케이프로 훼손되어 금지 패턴을 놓치지 않도록 `/`로 바꿈, `\"`는 따옴표 이스케이프로 유지
BACKSLASH_NOT_BEFORE_QUOTE = re.compile(r'\\(?!")')
GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({"-C", "-c"})
GIT_PAGER_SUBCOMMANDS = frozenset({"log", "diff", "show", "blame", "shortlog", "reflog"})
GIT_CONTINUE_SUBCOMMANDS = frozenset({"rebase", "cherry-pick", "revert", "merge"})
INTERACTIVE_PAGERS = frozenset({"more", "less"})
NEW_WINDOW_COMMANDS = frozenset({"start-process", "start", "saps"})
RECURSIVE_DELETE_COMMANDS = frozenset({"remove-item", "ri", "del", "erase", "rd", "rmdir", "rm"})
# - `bash -c`는 다음 인자 하나가 명령이고, `cmd /c`와 `pwsh -c`는 나머지 인자 전체가 명령
NESTED_SHELL_FLAGS: dict[str, frozenset[str]] = {
    "cmd": frozenset({"/c", "/k"}),
    "pwsh": frozenset({"-c", "-command"}),
    "powershell": frozenset({"-c", "-command"}),
    "bash": frozenset({"-c"}),
    "sh": frozenset({"-c"}),
}
SINGLE_ARGUMENT_SHELLS = frozenset({"bash", "sh"})
UNITTEST_OPTIONS_WITH_VALUE = frozenset({"-k", "--durations"})
RUFF_FIX_OPTIONS = frozenset({"--fix", "--fix-only", "--unsafe-fixes"})
CMD_SET_TRAP_SEPARATORS = frozenset({"&&", "&", "||"})
CLINE_SHELL_TOOLS = frozenset({"execute_command", "run_commands", "run_command", "bash", "shell", "powershell"})
PROTECTED_RECURSIVE_TARGETS = ("worktree", ".venv")
REDIRECTION_PREFIXES = (">&", ">>", "<<", "2>", "1>", "&>", ">", "<")
# - 자동 대체 뒤 권한 확인 없이 허용해도 되는 읽기 전용 명령(리다이렉션은 구분자로 잘려 파일명이 명령으로 남으므로 거부됨)
READ_ONLY_GIT_SUBCOMMANDS = GIT_PAGER_SUBCOMMANDS | {"status"}
READ_ONLY_COMMANDS = frozenset({"cd", "set-location", "cat", "head", "tail", "wc", "get-content", "select-object"})

NO_VERIFY_MESSAGE = "`--no-verify`로 pre-commit 훅을 우회할 수 없습니다."
HOOKS_PATH_MESSAGE = "`core.hooksPath` 재지정으로 pre-commit 훅을 우회할 수 없습니다."
SKIP_MESSAGE = "pre-commit `SKIP` 환경 변수로 훅을 건너뛸 수 없습니다."
FORCE_PUSH_MESSAGE = "강제 푸시(`--force`/`-f`/`--force-with-lease`/`+` refspec)는 금지되어 있습니다."
WORKTREE_FORCE_MESSAGE = (
    "`git worktree remove --force`(`-f`)는 사용할 수 없습니다. "
    "정션이나 심볼릭 링크를 먼저 `rmdir`로 끊은 뒤 `--force` 없이 지워 주세요."
)
GIT_PAGER_MESSAGE = "`git log`, `git diff`, `git show` 등은 페이저 멈춤 방지를 위해 `git --no-pager`로 실행해 주세요."
INTERACTIVE_PAGER_MESSAGE = "페이저 명령(`more`, `less`)은 출력 없이 멈추므로 사용할 수 없습니다."
INTERACTIVE_GIT_MESSAGE = "입력을 기다리는 대화형 Git 명령(`git add -p`, `git rebase -i`)은 사용할 수 없습니다."
GIT_EDITOR_MESSAGE = (
    "편집기를 여는 Git 명령(메시지 없는 `commit`, `tag -a`, `revert`, `--continue`)은 출력 없이 멈춥니다. "
    "`-m`으로 메시지를 주거나 `--no-edit`, `git -c core.editor=true ...`로 편집기를 건너뛰어 주세요."
)
NEW_WINDOW_MESSAGE = "새 창을 띄우는 `Start-Process`(`start`)와 `cmd /c start`는 사용할 수 없습니다."
CMD_SET_SPACE_MESSAGE = (
    "cmd `set NAME=값 &&`는 공백이 값에 들어가므로 사용할 수 없습니다. PowerShell `$env:NAME='값'`을 사용해 주세요."
)
UNITTEST_DISCOVERY_MESSAGE = (
    "`unittest discover`나 모듈 없는 `unittest`로 디렉터리 전체를 한 프로세스에서 실행할 수 없습니다. "
    "테스트 모듈을 명시하거나 `scripts\\check.ps1`을 사용해 주세요."
)
RUFF_FIX_MESSAGE = (
    "`check.ps1`이 Ruff 안전 수정을 먼저 적용하므로 `ruff check --fix`를 따로 실행하지 않습니다. "
    "`check.ps1`을 실행해 주세요."
)
LINK_RECURSIVE_DELETE_MESSAGE = (
    "정션이나 심볼릭 링크가 포함된 워크트리/`.venv` 폴더에 재귀 삭제(`rm -rf`, `Remove-Item -Recurse`, `rd /s`)를 바로 쓸 수 없습니다. "
    "링크를 먼저 `rmdir`로 끊고 `git worktree remove`(강제 옵션 없이)로 지워 주세요."
)
FORBIDDEN_ADD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)data(/|$)"),
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"(^|/)\.rootdir"),
    re.compile(r"(^|/)\.venv(/|$)"),
    re.compile(r"(^|/)classify\.ipynb$"),
    re.compile(r"\.sqlite3"),
    re.compile(r"\.video_cache"),
    re.compile(r"\.csv$"),
)


@dataclass(frozen=True)
class _SpanToken:
    """원본 문자열 위치를 유지하는 셸 토큰"""

    raw: str
    value: str
    start: int
    end: int
    quoted: bool
    separator: bool


@dataclass(frozen=True)
class HookDecision:
    """훅 판정 결과

    - `action`: `allow`, `rewrite`, `deny` 중 하나
    - `reason`: 차단 또는 대체 사유
    - `rewritten_command`: `rewrite`일 때의 비대화형 대체 명령
    """

    action: str
    reason: str = ""
    rewritten_command: str = ""


def _load_payload() -> dict[str, Any]:
    """표준 입력 JSON 페이로드 파싱"""
    try:
        raw = cast("object", json.load(sys.stdin))
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _as_dict(value: object) -> dict[str, Any]:
    """매핑 또는 JSON 문자열을 dict로 정규화"""
    if isinstance(value, str):
        try:
            value = cast("object", json.loads(value))
        except json.JSONDecodeError:
            return {}
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _is_separator(word: str) -> bool:
    """명령 구분 토큰(`;`, `&&`, `|`, 괄호, 리다이렉션 등) 여부"""
    return all(char in PUNCTUATION_CHARS for char in word)


def _split_segments(command: str) -> list[list[str]]:
    """셸 명령을 단순 명령 단위의 단어 목록으로 분할

    - 따옴표 안의 텍스트는 하나의 단어로 유지해 커밋 메시지 등의 내용을 명령으로 오인하지 않음
    - 줄바꿈은 명령 구분으로 치환하며, 따옴표 안이면 리터럴로 남아 단어 내용에만 영향
    - 닫히지 않은 따옴표는 분할 불가이므로 공백 기준 단일 단위로 대체
    - Windows 경로 구분자(역슬래시)는 `/`로 바꿔 이스케이프로 훼손되지 않게 함
    """
    unified = BACKSLASH_NOT_BEFORE_QUOTE.sub("/", command.replace("\n", " ; "))
    lexer = shlex.shlex(unified, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    try:
        for word in lexer:
            if _is_separator(word):
                segments.append([])
            else:
                segments[-1].append(word)
    except ValueError:
        return [unified.split()]
    return segments


def _scan_span_tokens(command: str) -> list[_SpanToken] | None:
    """원본 문자열 구간을 보존하며 토큰 분할, 닫히지 않은 따옴표는 `None`"""
    tokens: list[_SpanToken] = []
    n = len(command)
    i = 0
    while i < n:
        ch = command[i]
        if ch in (" ", "\t", "\r"):
            i += 1
            continue
        if ch == "\n":
            tokens.append(_SpanToken("\n", "\n", i, i + 1, False, True))
            i += 1
            continue
        if command.startswith(("&&", "||"), i):
            sep = command[i : i + 2]
            tokens.append(_SpanToken(sep, sep, i, i + 2, False, True))
            i += 2
            continue
        if ch in (";", "|", "(", ")", "&"):
            tokens.append(_SpanToken(ch, ch, i, i + 1, False, True))
            i += 1
            continue

        start = i
        buf: list[str] = []
        quoted = False
        while i < n:
            cur = command[i]
            if cur in (" ", "\t", "\r", "\n", ";", "|", "(", ")", "&"):
                break
            if cur in ("'", '"'):
                quoted = True
                quote_char = cur
                i += 1
                closed = False
                while i < n:
                    inner = command[i]
                    if quote_char == '"' and inner == "\\" and i + 1 < n and command[i + 1] in ('"', "\\"):
                        buf.append(command[i + 1])
                        i += 2
                        continue
                    if inner == quote_char:
                        i += 1
                        closed = True
                        break
                    buf.append(inner)
                    i += 1
                if not closed:
                    return None
                continue
            buf.append(cur)
            i += 1
        raw = command[start:i]
        tokens.append(_SpanToken(raw, "".join(buf), start, i, quoted, False))
    return tokens


def _split_span_segments(tokens: list[_SpanToken]) -> list[tuple[_SpanToken | None, list[_SpanToken]]]:
    """스캔된 토큰을 (앞선 구분자 토큰, 세그먼트 토큰 목록) 리스트로 분할"""
    segments: list[tuple[_SpanToken | None, list[_SpanToken]]] = []
    leading_sep: _SpanToken | None = None
    current: list[_SpanToken] = []
    for tok in tokens:
        if tok.separator:
            segments.append((leading_sep, current))
            leading_sep = tok
            current = []
        else:
            current.append(tok)
    segments.append((leading_sep, current))
    return segments


def _strip_env_prefix(words: list[str]) -> tuple[list[str], list[str]]:
    """선행 환경 변수 대입(`VAR=x`)과 실제 명령 단어 분리"""
    start = 0
    while start < len(words) and RE_ENV_ASSIGNMENT.match(words[start]):
        start += 1
    return words[:start], words[start:]


def _command_name(word: str) -> str:
    """경로와 `.exe`를 뺀 소문자 명령 이름"""
    return word.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")


def _module_args(words: list[str], module: str) -> list[str] | None:
    """`python -m <module>` 호출이면 모듈 뒤 인자 목록, 아니면 `None`"""
    for idx in range(len(words) - 1):
        if words[idx] == "-m" and words[idx + 1] == module:
            return words[idx + 2 :]
    return None


def _parse_git(words: list[str]) -> tuple[list[str], list[str], str, list[str]] | None:
    """`git` 호출을 (환경 변수, 전역 옵션, 서브커맨드, 인자)로 분해, `git` 호출이 아니면 `None`

    - 선행 환경 변수 대입(`VAR=x git ...`)과 `-C`/`-c` 값을 건너뛰어야 서브커맨드를 식별 가능
    """
    env_vars, rest = _strip_env_prefix(words)
    if not rest or _command_name(rest[0]) != "git":
        return None
    index = 1
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in GIT_GLOBAL_OPTIONS_WITH_VALUE else 1
    if index >= len(rest):
        return None
    return env_vars, rest[1:index], rest[index], rest[index + 1 :]


def _has_force_flag(args: list[str]) -> bool:
    """강제 옵션(`--force`, `-f`, `--force-with-lease`)이 있는지 판정, `push`와 `worktree remove`가 공용"""
    for arg in args:
        if arg.startswith("--force"):
            return True
        if RE_SHORT_FLAGS.fullmatch(arg) and "f" in arg:
            return True
    return False


def _has_short_flag(args: list[str], flag_char: str) -> bool:
    """단축 플래그 묶음(예: `-am`, `-pi`)에 특정 문자가 포함되어 있는지 판정"""
    return any(RE_SHORT_FLAGS.fullmatch(arg) and flag_char in arg for arg in args)


def _has_message_source(args: list[str]) -> bool:
    """`commit`, `tag`가 편집기 없이 메시지를 받는 인자를 가졌는지 판정

    - `-c`(`--reedit-message`)는 편집기를 열므로 제외
    """
    for arg in args:
        if arg in ("-m", "--message", "-F", "--file", "--no-edit", "-C", "--reuse-message"):
            return True
        if arg.startswith(("-m", "--message=", "-F", "--file=", "--reuse-message=", "--fixup")):
            return True
        if RE_SHORT_FLAGS.fullmatch(arg) and ("m" in arg or "F" in arg):
            return True
    return False


def _opens_editor(subcommand: str, args: list[str]) -> bool:
    """편집기를 열어 입력을 기다리는 Git 호출인지 판정"""
    if subcommand == "commit":
        return not _has_message_source(args)
    if subcommand in GIT_CONTINUE_SUBCOMMANDS and "--continue" in args:
        return "--no-edit" not in args
    if subcommand == "revert":
        skips_editor = ("--no-edit", "--no-commit", "--abort", "--quit", "--skip")
        return not (any(arg in skips_editor for arg in args) or _has_short_flag(args, "n"))
    if subcommand == "tag":
        annotated = any(arg in ("--annotate", "--sign") for arg in args) or any(
            _has_short_flag(args, flag) for flag in "asu"
        )
        return annotated and not _has_message_source(args)
    return False


def _has_editor_override(env_vars: list[str], global_options: list[str]) -> bool:
    """`GIT_EDITOR=`나 `-c core.editor=`로 편집기를 건너뛰는지 판정"""
    return any(var.startswith("GIT_EDITOR=") for var in env_vars) or any(
        option.lower().startswith("core.editor=") for option in global_options
    )


def _check_add_paths(args: list[str]) -> str | None:
    """`add` 인자 중 비대상 파일 패턴이나 대화형 옵션이 있으면 사유 반환"""
    if "--patch" in args or "--interactive" in args or _has_short_flag(args, "p") or _has_short_flag(args, "i"):
        return INTERACTIVE_GIT_MESSAGE
    for arg in args:
        if arg.startswith("-"):
            continue
        normalized = arg.replace("\\", "/")
        if any(p.search(normalized) for p in FORBIDDEN_ADD_PATTERNS):
            return f"비대상 파일 스테이징 금지: {normalized}"
    return None


def _check_git_hard_block(words: list[str]) -> str | None:
    """자동 대체할 수 없는 git 규칙 위반 사유 반환"""
    parsed = _parse_git(words)
    if parsed is None:
        return None
    env_vars, global_options, subcommand, args = parsed
    if any("core.hookspath" in option.lower() for option in global_options):
        return HOOKS_PATH_MESSAGE
    if "--no-verify" in args or (subcommand == "commit" and _has_short_flag(args, "n")):
        return NO_VERIFY_MESSAGE
    if any(var.startswith("SKIP=") for var in env_vars):
        return SKIP_MESSAGE
    if subcommand == "rebase" and ("--interactive" in args or _has_short_flag(args, "i")):
        return INTERACTIVE_GIT_MESSAGE
    if subcommand == "push" and (_has_force_flag(args) or any(arg.startswith("+") for arg in args)):
        return FORCE_PUSH_MESSAGE
    if subcommand == "worktree" and args[:1] == ["remove"] and _has_force_flag(args[1:]):
        return WORKTREE_FORCE_MESSAGE
    if subcommand == "add":
        return _check_add_paths(args)
    if _opens_editor(subcommand, args) and not _has_editor_override(env_vars, global_options):
        return GIT_EDITOR_MESSAGE
    return None


def _check_git_pager(words: list[str]) -> str | None:
    """`git` 페이저 누락 위반 사유 반환

    - 훅 프로세스 환경에 `GIT_PAGER`가 있으면(브리지 워커는 `cat`) 실행할 명령도 같은 환경을 물려받아 멈추지 않으므로 통과
    """
    if os.environ.get("GIT_PAGER"):
        return None
    parsed = _parse_git(words)
    if parsed is None:
        return None
    env_vars, global_options, subcommand, _ = parsed
    if subcommand in GIT_PAGER_SUBCOMMANDS:
        has_no_pager = any(opt in ("--no-pager", "-P") for opt in global_options) or any(
            v.startswith("GIT_PAGER=") for v in env_vars
        )
        if not has_no_pager:
            return GIT_PAGER_MESSAGE
    return None


def _nested_shell_command(name: str, args: list[str]) -> str | None:
    """다른 셸에 넘긴 명령 문자열(`cmd /c ...`, `pwsh -c ...`, `bash -c ...`) 반환"""
    flags = NESTED_SHELL_FLAGS.get(name)
    if flags is None:
        return None
    for idx, arg in enumerate(args[:-1]):
        if arg.lower() in flags:
            nested = args[idx + 1 :]
            return nested[0] if name in SINGLE_ARGUMENT_SHELLS else " ".join(nested)
    return None


def _skips_pre_commit(env_vars: list[str], rest: list[str]) -> bool:
    """pre-commit `SKIP` 환경 변수를 지정하는 문장인지 판정(`SKIP=x`, `$env:SKIP=`, `export`/`set SKIP=`)"""
    if any(var.startswith("SKIP=") for var in env_vars):
        return True
    if not rest:
        return False
    if RE_PS_SKIP_ENV.match(rest[0]):
        return True
    return _command_name(rest[0]) in ("export", "set") and rest[1:2] != [] and rest[1].startswith("SKIP=")


def _runs_unittest_discovery(rest: list[str]) -> bool:
    """`unittest`를 `discover`나 모듈 없이 실행해 디렉터리 전체를 한 프로세스에서 도는지 판정"""
    args = _module_args(rest, "unittest")
    if args is None:
        return False
    if args[:1] == ["discover"]:
        return True
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
        elif arg in UNITTEST_OPTIONS_WITH_VALUE:
            skip_value = True
        elif not arg.startswith("-"):
            return False
    return True


def _runs_ruff_fix(name: str, rest: list[str]) -> bool:
    """`ruff`를 수정 옵션과 함께 따로 실행하는지 판정"""
    args = rest[1:] if name == "ruff" else _module_args(rest, "ruff")
    return args is not None and any(arg in RUFF_FIX_OPTIONS for arg in args)


def _is_recursive_delete(name: str, args: list[str]) -> bool:
    """재귀 삭제 명령(`rm -rf`, `Remove-Item -Recurse`, `rd /s`)인지 판정"""
    if name not in RECURSIVE_DELETE_COMMANDS:
        return False
    for arg in (a.lower() for a in args):
        if arg in ("/s", "--recursive") or RE_PS_RECURSE.fullmatch(arg):
            return True
        if name == "rm" and RE_SHORT_FLAGS.fullmatch(arg) and "r" in arg:
            return True
    return False


def _check_shell_hard_block(words: list[str]) -> str | None:
    """자동 대체할 수 없는 비-Git 셸 규칙 위반 사유 반환"""
    env_vars, rest = _strip_env_prefix(words)
    if _skips_pre_commit(env_vars, rest):
        return SKIP_MESSAGE
    if not rest:
        return None
    name = _command_name(rest[0])
    args = rest[1:]

    if name in NEW_WINDOW_COMMANDS:
        return NEW_WINDOW_MESSAGE
    nested = _nested_shell_command(name, args)
    if nested is not None:
        nested_decision = evaluate_bash_command(nested)
        if nested_decision.action == "deny":
            return nested_decision.reason
    if _runs_unittest_discovery(rest):
        return UNITTEST_DISCOVERY_MESSAGE
    if _runs_ruff_fix(name, rest):
        return RUFF_FIX_MESSAGE
    if _is_recursive_delete(name, args) and any(
        target in arg.lower() for arg in args for target in PROTECTED_RECURSIVE_TARGETS
    ):
        return LINK_RECURSIVE_DELETE_MESSAGE
    return None


def _check_shell_pager(words: list[str]) -> str | None:
    """비-Git 페이저(`more`, `less` 또는 다른 셸에 넘긴 명령 안의 페이저) 위반 사유 반환"""
    _, rest = _strip_env_prefix(words)
    if not rest:
        return None
    name = _command_name(rest[0])
    if name in INTERACTIVE_PAGERS:
        return INTERACTIVE_PAGER_MESSAGE
    nested = _nested_shell_command(name, rest[1:])
    if nested is not None:
        nested_decision = evaluate_bash_command(nested)
        if nested_decision.action != "allow":
            return with_rewrite_hint(nested_decision)
    return None


def _has_cmd_set_trap(command: str) -> bool:
    """따옴표 밖 cmd `set NAME=값 &&`처럼 구분자 앞 공백이 값에 들어가는 문장이 있는지 판정

    - `set "NAME=값" &&`처럼 대입 전체를 따옴표로 감싸면 공백이 값에 들어가지 않으므로 허용
    """
    tokens = _scan_span_tokens(command)
    if tokens is None:
        return False
    for idx, tok in enumerate(tokens[:-1]):
        if tok.separator or tok.value.lower() != "set" or (idx > 0 and not tokens[idx - 1].separator):
            continue
        assignment = tokens[idx + 1]
        if assignment.separator or assignment.raw.startswith('"') or not RE_ENV_ASSIGNMENT.match(assignment.value):
            continue
        end = idx + 1
        while end < len(tokens) and not tokens[end].separator:
            end += 1
        if (
            end < len(tokens)
            and tokens[end].value in CMD_SET_TRAP_SEPARATORS
            and tokens[end].start > tokens[end - 1].end
        ):
            return True
    return False


def _find_hard_block(command: str) -> str | None:
    """치명적 규칙이나 대화형 명령 위반 탐색(페이저보다 항상 우선)"""
    if _has_cmd_set_trap(command):
        return CMD_SET_SPACE_MESSAGE
    for words in _split_segments(command):
        reason = _check_git_hard_block(words) or _check_shell_hard_block(words)
        if reason is not None:
            return reason
    return None


def _is_plain_pager_cat_args(args: list[str]) -> bool:
    """단독 `more`/`less`를 `cat`으로 안전하게 바꿀 수 있는 파일 인자 목록인지 판정"""
    files: list[str] = []
    for arg in args:
        if arg.startswith(("-", "+", "/")) or arg.startswith(REDIRECTION_PREFIXES):
            return False
        files.append(arg)
    return bool(files)


def _rewrite_pager_segments(command: str) -> tuple[str, str] | None:
    """원본 토큰 위치를 유지하며 페이저 명령을 비대화형 명령으로 변환

    - 변환 불가 또는 페이저 없음 시 `None` 반환
    """
    tokens = _scan_span_tokens(command)
    if tokens is None:
        return None
    segments = _split_span_segments(tokens)
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []

    for leading_sep, seg in segments:
        if not seg:
            continue
        words = [tok.value for tok in seg]
        if _check_git_pager(words) is not None:
            idx = 0
            while idx < len(seg) and RE_ENV_ASSIGNMENT.match(seg[idx].value):
                idx += 1
            if idx < len(seg) and _command_name(seg[idx].value) == "git":
                edits.append((seg[idx].end, seg[idx].end, " --no-pager"))
                notes.append(GIT_PAGER_MESSAGE)
            continue

        _, rest = _strip_env_prefix(words)
        if not rest or _command_name(rest[0]) not in INTERACTIVE_PAGERS:
            continue
        pager_idx = len(seg) - len(rest)
        pager_tok = seg[pager_idx]
        if leading_sep is not None and leading_sep.value == "|":
            # - 파이프라인에 걸린 `| more`/`| less`는 파이프 구간째 제거해 출력을 바로 통과
            if all(arg.startswith(("-", "+", "/")) for arg in rest[1:]):
                edits.append((leading_sep.start, seg[-1].end, ""))
                notes.append("파이프 페이저(`| more`/`| less`)는 출력 없이 멈춥니다.")
            continue

        if _is_plain_pager_cat_args(rest[1:]):
            edits.append((pager_tok.start, pager_tok.end, "cat"))
            notes.append(INTERACTIVE_PAGER_MESSAGE)

    if not edits:
        return None

    updated = command
    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    updated = re.sub(r" {2,}", " ", updated).strip()
    return updated, notes[0]


def evaluate_bash_command(command: str) -> HookDecision:
    """셸 명령을 평가해 허용, 페이저 대체, 차단 중 하나의 판정 반환"""
    hard_reason = _find_hard_block(command)
    if hard_reason is not None:
        return HookDecision("deny", hard_reason)

    rewritten = _rewrite_pager_segments(command)
    if rewritten is not None:
        new_command, note = rewritten
        # - 대체 결과에 또 다른 페이저나 위반이 남으면 대체하지 않고 차단
        if check_bash_command(new_command) is None:
            return HookDecision("rewrite", note, new_command)

    for words in _split_segments(command):
        pager_reason = _check_git_pager(words) or _check_shell_pager(words)
        if pager_reason is not None:
            return HookDecision("deny", pager_reason)

    return HookDecision("allow")


def check_bash_command(command: str) -> str | None:
    """셸 명령의 `AGENTS.md` 규칙 위반 사유 반환, 위반이 없으면 `None`"""
    hard_reason = _find_hard_block(command)
    if hard_reason is not None:
        return hard_reason
    for words in _split_segments(command):
        reason = _check_git_pager(words) or _check_shell_pager(words)
        if reason is not None:
            return reason
    return None


def is_read_only_command(command: str) -> bool:
    """모든 단순 명령이 읽기 전용(`git log`/`diff`/`show`/`status`, `cat`, `head` 등)인지 판정"""
    segments = [words for words in _split_segments(command) if words]
    if not segments:
        return False
    for words in segments:
        _, rest = _strip_env_prefix(words)
        if not rest:
            return False
        parsed = _parse_git(rest)
        if parsed is not None:
            _, _, subcommand, args = parsed
            if subcommand not in READ_ONLY_GIT_SUBCOMMANDS or any(arg.startswith("--output") for arg in args):
                return False
        elif _command_name(rest[0]) not in READ_ONLY_COMMANDS:
            return False
    return True


def with_rewrite_hint(decision: HookDecision) -> str:
    """차단 사유에 대체 명령이 있으면 다시 실행할 명령을 덧붙인 안내문"""
    if decision.action == "rewrite":
        return f"{decision.reason} 다음 명령으로 다시 실행해 주세요: {decision.rewritten_command}"
    return decision.reason


def _extract_cline_commands(payload: dict[str, Any]) -> list[str]:
    """Cline `PreToolUse` 페이로드에서 실행할 셸 명령 목록 추출"""
    tool_call = _as_dict(payload.get("tool_call"))
    pre_tool_use = _as_dict(payload.get("preToolUse"))
    tool_name = str(tool_call.get("name") or pre_tool_use.get("toolName") or "").lower()
    if tool_name and tool_name not in CLINE_SHELL_TOOLS:
        return []

    params = _as_dict(pre_tool_use.get("parameters")) or _as_dict(tool_call.get("arguments"))
    commands: list[str] = []
    for key in ("command", "cmd", "CommandLine"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            commands.append(val)
    raw_list = params.get("commands")
    if isinstance(raw_list, list):
        for item in cast("list[object]", raw_list):
            if isinstance(item, str) and item.strip():
                commands.append(item)
            elif isinstance(item, dict):
                cmd_val = cast("dict[str, Any]", item).get("command")
                if isinstance(cmd_val, str) and cmd_val.strip():
                    commands.append(cmd_val)
    return commands


def check_cline_payload(payload: dict[str, Any]) -> str | None:
    """Cline `PreToolUse` 페이로드 검사, 차단 시 대체 명령 안내를 포함한 사유 반환"""
    for command in _extract_cline_commands(payload):
        decision = evaluate_bash_command(command)
        if decision.action != "allow":
            return with_rewrite_hint(decision)
    return None


def evaluate_agy_payload(payload: dict[str, Any]) -> HookDecision:
    """Antigravity `PreToolUse` 페이로드 판정"""
    tool_call = _as_dict(payload.get("toolCall"))
    if str(tool_call.get("name") or "") != "run_command":
        return HookDecision("allow")
    args = _as_dict(tool_call.get("args"))
    command = str(args.get("CommandLine") or "")
    return evaluate_bash_command(command) if command else HookDecision("allow")


def main(argv: list[str]) -> int:
    """훅 모드별 검사 실행 및 에이전트 규약에 맞는 출력과 종료 코드 반환"""
    mode = argv[1] if len(argv) > 1 else ""
    payload = _load_payload()

    if mode == "pre-bash":
        tool_input = _as_dict(payload.get("tool_input"))
        decision = evaluate_bash_command(str(tool_input.get("command", "")))
        if decision.action == "allow":
            return 0
        if decision.action == "rewrite" and is_read_only_command(decision.rewritten_command):
            # - `updatedInput`은 허용 판정과 함께여야 반영되고, 허용은 자동 모드 검토를 건너뛰므로 읽기 전용 명령에만 씀
            updated_input = {**tool_input, "command": decision.rewritten_command}
            hook_output = {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": decision.reason,
                "updatedInput": updated_input,
            }
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            print(json.dumps({"hookSpecificOutput": hook_output}, ensure_ascii=False))
            return 0
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        print(with_rewrite_hint(decision), file=sys.stderr)
        return BLOCK_EXIT_CODE

    if mode == "pre-agy":
        decision = evaluate_agy_payload(payload)
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        if decision.action == "allow":
            print(json.dumps({"decision": "allow"}, ensure_ascii=False))
        elif decision.action == "rewrite":
            print(
                json.dumps(
                    {
                        "decision": "allow",
                        "reason": decision.reason,
                        "overwrite": {"CommandLine": decision.rewritten_command},
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps({"decision": "deny", "reason": decision.reason}, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
