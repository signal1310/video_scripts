"""Claude Code 훅 가드 모듈

- `pre-bash`: Bash, PowerShell 명령에서 금지된 Git 명령(훅 우회, 강제 푸시, 비대상 파일 스테이징, 강제 워크트리 삭제) 차단
- 종료 코드 2와 stderr 출력으로 위반 사유를 에이전트에 전달
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any, cast

BLOCK_EXIT_CODE = 2

PUNCTUATION_CHARS = "();<>|&"
RE_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
RE_SHORT_FLAGS = re.compile(r"-[A-Za-z]+")
# - 역슬래시 경로가 셸 이스케이프로 훼손되어 금지 패턴을 놓치지 않도록 `/`로 바꿈, `\"`는 따옴표 이스케이프로 유지
BACKSLASH_NOT_BEFORE_QUOTE = re.compile(r'\\(?!")')
GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({"-C", "-c"})
NO_VERIFY_MESSAGE = "`--no-verify`로 pre-commit 훅을 우회할 수 없습니다."
HOOKS_PATH_MESSAGE = "`core.hooksPath` 재지정으로 pre-commit 훅을 우회할 수 없습니다."
FORCE_PUSH_MESSAGE = "강제 푸시(`--force`/`-f`/`--force-with-lease`)는 금지되어 있습니다."
WORKTREE_FORCE_MESSAGE = (
    "`git worktree remove --force`(`-f`)는 사용할 수 없습니다. "
    "정션이나 심볼릭 링크를 먼저 `rmdir`로 끊은 뒤 `--force` 없이 지워 주세요."
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


def _load_payload() -> dict[str, Any]:
    """표준 입력 JSON 페이로드 파싱"""
    try:
        raw = cast("object", json.load(sys.stdin))
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """페이로드의 `tool_input` 매핑 반환"""
    value = payload.get("tool_input")
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _is_separator(word: str) -> bool:
    """명령 구분 토큰(`;`, `&&`, `|`, 괄호, 리다이렉션 등) 여부"""
    return all(char in PUNCTUATION_CHARS for char in word)


def _split_segments(command: str) -> list[list[str]]:
    """Bash 명령을 단순 명령 단위의 단어 목록으로 분할

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


def _parse_git(words: list[str]) -> tuple[list[str], str, list[str]] | None:
    """`git` 호출을 (전역 옵션, 서브커맨드, 인자)로 분해, `git` 호출이 아니면 `None`

    - 선행 환경 변수 대입(`VAR=x git ...`)과 `-C`/`-c` 값을 건너뛰어야 서브커맨드를 식별 가능
    """
    start = 0
    while start < len(words) and RE_ENV_ASSIGNMENT.match(words[start]):
        start += 1
    rest = words[start:]
    if not rest or rest[0] != "git":
        return None
    index = 1
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in GIT_GLOBAL_OPTIONS_WITH_VALUE else 1
    if index >= len(rest):
        return None
    return rest[1:index], rest[index], rest[index + 1 :]


def _has_force_flag(args: list[str]) -> bool:
    """강제 옵션(`--force`, `-f`, `--force-with-lease`)이 있는지 판정, `push`와 `worktree remove`가 공용"""
    for arg in args:
        if arg.startswith("--force"):
            return True
        if RE_SHORT_FLAGS.fullmatch(arg) and "f" in arg:
            return True
    return False


def _check_add_paths(args: list[str]) -> str | None:
    """`add` 인자 중 비대상 파일 패턴이 있으면 사유 반환"""
    for arg in args:
        if arg.startswith("-"):
            continue
        normalized = arg.replace("\\", "/")
        if any(p.search(normalized) for p in FORBIDDEN_ADD_PATTERNS):
            return f"비대상 파일 스테이징 금지: {normalized}"
    return None


def _check_git_words(words: list[str]) -> str | None:
    """단순 명령 하나의 git 규칙 위반 사유 반환"""
    parsed = _parse_git(words)
    if parsed is None:
        return None
    global_options, subcommand, args = parsed
    if any("core.hooksPath" in option for option in global_options):
        return HOOKS_PATH_MESSAGE
    if subcommand == "commit" and ("--no-verify" in args or "-n" in args):
        return NO_VERIFY_MESSAGE
    if subcommand == "push":
        if "--no-verify" in args:
            return NO_VERIFY_MESSAGE
        if _has_force_flag(args):
            return FORCE_PUSH_MESSAGE
    if subcommand == "worktree" and args[:1] == ["remove"] and _has_force_flag(args[1:]):
        return WORKTREE_FORCE_MESSAGE
    if subcommand == "add":
        return _check_add_paths(args)
    return None


def check_bash_command(command: str) -> str | None:
    """Bash 명령의 규칙 위반 사유 반환, 위반이 없으면 `None`"""
    for words in _split_segments(command):
        reason = _check_git_words(words)
        if reason is not None:
            return reason
    return None


def main(argv: list[str]) -> int:
    """훅 모드별 검사 실행 및 종료 코드 반환"""
    mode = argv[1] if len(argv) > 1 else ""
    tool_input = _tool_input(_load_payload())
    message: str | None = None
    if mode == "pre-bash":
        message = check_bash_command(str(tool_input.get("command", "")))
    if message is None:
        return 0
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(message, file=sys.stderr)
    return BLOCK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
