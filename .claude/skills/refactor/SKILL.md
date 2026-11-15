---
name: refactor
description: >-
  Use this skill for both pre-commit cleanup of recent changes ("커밋 전 정리",
  "코드 정리") and broader structural refactoring, module splitting, deduplication,
  or schema normalization ("리팩터링", "모듈 분리", "중복 제거", "정규화", "refactor").
  Separates narrow diff-scoped tidying from repo-wide restructuring.
---

# 2트랙 리팩터링 (`refactor`)

불변식은 `AGENTS.md` 1절, 2절을 따르고, 이 스킬은 탐색 범위와 순서만 정한다. 모듈을 추가, 삭제, 개명했다면 [`sync`](../sync/SKILL.md)를 이어서 수행한다.

## 트랙 A: 커밋 전 정리 (diff 범위 고정)

대상: "커밋 전 정리", "방금 한 거 리팩터링" 같은 직전 변경분 정리

- 범위: 미커밋 변경이 있으면 `git diff HEAD`, 깨끗하면 이 대화의 직전 커밋 묶음, 맥락이 없으면 `git log -n 10 --stat --oneline`으로 후보 범위를 정해 한 줄로 확인받은 뒤 진행
- 범위 내 파일과 직접 호출부만 읽고 무관한 모듈은 건드리지 않음
- 중복 추출, 매직 넘버 상수화, 장대 함수 분할, 죽은 분기 제거, 독스트링 정돈에 집중
- 동작 보존: 출력, 부작용, 예외 계약은 바꾸지 않으며, 동작 변경이 필요하면 별도로 제안
- 명료함 우선: 중첩 삼항식과 과도한 한 줄 압축을 피하고, 서로 다른 책임을 합치거나 유용한 추상화를 없애지 않음
- 중첩 완화: 깊은 중첩은 조기 반환, 가드 절, 헬퍼 분리로 낮추고, 다중 조건은 `if`/`elif` 체인이나 매핑으로 표현
- 보고: 이해에 영향을 주는 변경만 요약하고 단순 포맷, 이름 정돈은 생략
- 주석: 자명한 설명만 제거하고 Why 주석은 유지

## 트랙 B: 구조 개편 (경량 스캔 후 범위 합의)

대상: "비대 모듈 분리", "정규화" 같은 범위가 열린 요청

- `src/` 전체를 읽지 않고 줄 수/심볼 검색으로 후보 1~2개와 순서를 먼저 정한 뒤 대상만 열기. 비대 모듈 후보는 `scripts/module_size_baseline.json`의 동결 파일에서 고름
- `core`, `service`, `gui`/`cui`, `tests`, `docs` 순으로 단계 분할
- `data/config.json`, `QHeaderView.restoreState()`처럼 디스크에 남은 과거 상태가 새 정책을 덮어쓰지 않는지 복원 경계 점검
- 단계마다 대상 `unittest -b`로 회귀 확인
- 분리, 축소 후 `python scripts/verify_module_size.py --write`로 기준선을 낮춤

## 마무리

`check.ps1` 통과 후 [`review`](../review/SKILL.md) 또는 [`commit`](../commit/SKILL.md)으로 이어간다.
