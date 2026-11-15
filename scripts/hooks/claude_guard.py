"""Claude Code 훅 가드 모듈

- `pre-bash`: 금지된 Git 명령(훅 우회, 강제 푸시, 비대상 파일 스테이징) 차단
- `post-edit`: 수정된 파이썬 파일에 대한 Ruff 즉시 검사
- 종료 코드 2와 stderr 출력으로 위반 사유를 에이전트에 전달
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

BLOCK_EXIT_CODE = 2
RUFF_TIMEOUT_SECONDS = 60
REPO_ROOT = Path(__file__).resolve().parents[2]
RUFF_CANDIDATES: tuple[Path, ...] = (
    REPO_ROOT / ".venv" / "Scripts" / "ruff.exe",
    REPO_ROOT / ".venv" / "bin" / "ruff",
)

RE_NO_VERIFY = re.compile(r"(^|\s)--no-verify(\s|$)")
RE_FORCE_PUSH = re.compile(r"\bgit\s+push\b[^;&|]*?(\s--force\b|\s-f\b)")
RE_GIT_ADD = re.compile(r"\bgit\s+add\b([^;&|]*)")
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


def check_bash_command(command: str) -> str | None:
    """Bash 명령의 규칙 위반 사유 반환, 위반이 없으면 `None`"""
    if RE_NO_VERIFY.search(command):
        return "`--no-verify`로 pre-commit 훅을 우회할 수 없습니다."
    if RE_FORCE_PUSH.search(command):
        return "강제 푸시(`--force`/`-f`)는 금지되어 있습니다."
    for match in RE_GIT_ADD.finditer(command):
        for token in match.group(1).split():
            normalized = token.strip("\"'").replace("\\", "/")
            if any(p.search(normalized) for p in FORBIDDEN_ADD_PATTERNS):
                return f"비대상 파일 스테이징 금지: {normalized}"
    return None


def _find_ruff() -> Path | None:
    """저장소 가상환경의 Ruff 실행 파일 탐색"""
    return next((p for p in RUFF_CANDIDATES if p.is_file()), None)


def check_python_file(file_path: str) -> str | None:
    """저장소 안 파이썬 파일의 Ruff 위반 출력 반환, 위반이 없거나 대상이 아니면 `None`

    - `resolve()`로 드라이브 문자 대소문자를 정규화해야 Ruff 예외 규칙(`per-file-ignores`) 매칭이 맞음
    - 저장소 밖 파일은 저장소 규칙 대상이 아니므로 제외
    """
    path = Path(file_path).resolve()
    if path.suffix != ".py" or not path.is_file() or not path.is_relative_to(REPO_ROOT):
        return None
    ruff = _find_ruff()
    if ruff is None:
        return None
    result = subprocess.run(
        [str(ruff), "check", "--output-format", "concise", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=RUFF_TIMEOUT_SECONDS,
        check=False,
    )
    return (result.stdout + result.stderr).strip() if result.returncode != 0 else None


def main(argv: list[str]) -> int:
    """훅 모드별 검사 실행 및 종료 코드 반환"""
    mode = argv[1] if len(argv) > 1 else ""
    tool_input = _tool_input(_load_payload())
    message: str | None = None
    if mode == "pre-bash":
        message = check_bash_command(str(tool_input.get("command", "")))
    elif mode == "post-edit":
        message = check_python_file(str(tool_input.get("file_path", "")))
    if message is None:
        return 0
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(message, file=sys.stderr)
    return BLOCK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
