# Claude Code 진입점

@AGENTS.md

## 훅 (`.claude/settings.json`)
- `PreToolUse(Bash|PowerShell)`: 금지 명령 패턴을 차단하고, 읽기 전용 페이저 명령은 비대화형 명령으로 바꿔 허용
- 구현: `scripts/hooks/command_guard.py`(패턴 목록은 모듈 독스트링, Cline과 Antigravity 전역 훅도 같은 모듈을 씀)

## 응답 언어
- 사용자에게 보이는 답변은 항상 한국어로 작성
