---
name: commit
description: >-
  Use this skill when the user says "커밋", "commit", or asks to commit changes.
  Inspects working tree changes, splits them into cohesive logical commits by concern,
  verifies hygiene and checks, and commits cleanly without bypassing hooks.
---

# 분할 커밋 (`commit`)

1. 현황: `git status -s`, `git diff`, `git log -n 5 --oneline`으로 변경 범위와 제목 형식(`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:` + 간결한 한국어 요약) 파악
2. 분할: 단일 성격이면 1개 커밋, 관심사(core, service / gui, cui / tests / docs, `.agents/`, `scripts/`, 설정)가 섞이면 의존 순서대로 분리. 스킬 원본을 바꿨다면 `.claude/skills` 사본과 README, ARCHITECTURE의 스킬 목록을 같은 커밋에 포함
3. 검증: `AGENTS.md` 6절 비대상 파일은 스테이징하지 않음. `check.ps1`을 실행해 통과를 확인(같은 상태를 이미 통과했으면 즉시 끝남)(문서, 스킬만 변경 시 `pre-commit` 훅으로 갈음)
4. 실행: 커밋 메시지도 `AGENTS.md` 4절 문자 규칙(한글 외 ASCII 밖 문자 금지)을 따르며, 단위별로 대상 파일 스테이징과 `git commit -m "<type>: <요약>"`을 한 번에 실행(`--no-verify` 금지). 완료 후 `git status -s`로 잔여 변경이 없는지 확인하고 해시, 메시지, 파일을 간결히 보고
