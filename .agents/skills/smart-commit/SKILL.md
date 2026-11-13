---
name: smart-commit
description: >-
  Use this skill when the user says "커밋", "commit", or asks to commit changes.
  Inspects working tree changes, splits them into cohesive logical commits by concern,
  verifies hygiene and checks, and commits cleanly without bypassing hooks.
---

# 스마트 분할 커밋 (`smart-commit`)

## 절차

1. **현황·스타일 파악**
   - `git status -s`, `git diff`, `git log -n 5 --oneline`을 확인해 변경 범위와 기존 커밋 제목 형식(`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:` + 간결한 한국어 요약)을 파악한다.
2. **논리 단위 분할 판단**
   - 단일 성격의 변경이면 **1개 커밋**으로 묶는다.
   - 서로 다른 관심사(예: `src/core`·`src/service` ↔ `src/gui`·`src/cui` ↔ `tests/` ↔ `docs`·`.agents/`·설정)가 섞여 있으면 의존·맥락 순서대로 **여러 커밋**으로 나눈다.
3. **사전 검증 및 위생 확인**
   - `.env`, `.env.*`, `.rootdir`, `.rootdir.*`, 캐시(`.video_cache*`, `*.sqlite3*`, `.ruff_cache/`), `classify.ipynb`, `*.csv`, 임시 산출물은 절대 스테이징하지 않는다.
   - 직전 검증 이후 코드·설정·문서가 변경되었다면 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1` 통과를 먼저 확인한다.
4. **커밋 실행 및 확인**
   - 각 커밋 단위에 해당하는 파일만 `git add <paths>`로 스테이징한 뒤 `git commit -m "<type>: <요약>"`을 실행한다(`--no-verify` 금지, `.git/` 쓰기 시 Windows 샌드박스 예외 사용).
   - 모든 커밋 완료 후 `git status -s`로 잔여 변경이 없음을 확인하고, 커밋 해시·메시지·포함 파일을 간결히 보고한다.
