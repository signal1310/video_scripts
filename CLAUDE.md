# Claude Code 진입점

@AGENTS.md

## 훅 (`.claude/settings.json`)
- `PreToolUse(Bash)`: `--no-verify`, 강제 푸시, 비대상 파일(`data/`, `.env*`, `*.csv` 등) 스테이징 차단
- 구현: `scripts/hooks/claude_guard.py`

## 응답 언어
- 사용자에게 보이는 답변은 항상 한국어로 작성
