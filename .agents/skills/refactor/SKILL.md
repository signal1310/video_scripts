---
name: refactor
description: >-
  Use this skill for both pre-commit cleanup of recent changes ("커밋 전 정리",
  "코드 정리") and broader structural refactoring, module splitting, deduplication,
  or schema normalization ("리팩터링", "모듈 분리", "중복 제거", "정규화", "refactor").
  Separates narrow diff-scoped tidying from repo-wide restructuring to minimize
  token waste while enforcing AGENTS.md invariants.
---

# 2트랙 안전 리팩터링 (`refactor`)

## 1. 트랙 판별 (탐색 범위 제한)

- **트랙 A — 커밋 전 마무리 정리 (`diff` 범위 고정)**
  - 조건: 방금 작업한 변경분 정리 요청(`"방금 한 거 리팩터링"`, `"커밋 전 정리"`, `"중복·코드 정리"`)
  - **대상 범위 결정 우선순위**:
    1. `git status -s`에 미커밋 변경분이 있으면 `git diff HEAD` 전체를 범위로 잡는다.
    2. 작업 트리가 깨끗할 때 현재 대화에서 만든 직전 커밋 묶음이 있으면 그 묶음(`base..HEAD`, `n`개)을 잡는다.
    3. 새 대화 등 맥락이 없으면 diff 본문을 열지 말고 `git log -n 10 --stat --oneline`으로 제목과 변경 파일명만 훑어 분할 커밋 묶음(`feat:` → `test:` → `docs:` 등 동일 주제·파일군 `n`개) 또는 브랜치 분기점(`main..HEAD`) 경계를 찾는다.
    4. `1개` vs `n개` 경계가 모호하면 본문을 읽기 전에 후보 커밋 범위를 1줄로 확인받고 진행한다.
  - 확정된 범위의 변경 파일과 직접 호출부(1-hop)만 읽어 정리하고 무관한 모듈은 건드리지 않는다.
  - 방금 추가한 코드 내 중복 추출, 매직 넘버 상수화, 400~500줄 초과/장대 함수 분할, 죽은 분기 제거, 한국어 개조식 독스트링 정돈에 집중한다.
- **트랙 B — 전역·구조 개편 (경량 스캔 → 범위 합의 우선)**
  - 조건: 대상이 열려 있는 구조 개편 요청(`"전체 리팩터링"`, `"비대 모듈 분리"`, `"정규화"`, `"구조 개편"`)
  - 처음부터 `src/` 전체 본문을 통째로 읽지 말고 줄 수·심볼 검색(`Select-String`) 등 경량 스캔으로 대상 후보 1~2개와 이행 순서를 먼저 확정한 뒤 대상만 연다.
  - 수정은 `Core → Service → GUI/CUI → Tests → Docs` 의존 순서로 단계별 분할 실행한다.

## 2. 아키텍처 · 안전 불변식 (`AGENTS.md` §1·§2)

- **3계층 단방향 경계**: `src/core` ← `src/service` ← `src/cui` / `src/gui` 방향만 허용하며 순환 참조를 만들지 않는다.
- **평탄한 수평 분리**: 과도한 하위 패키지 중첩 대신 기존 깊이(`src/core`, `src/gui`, `src/gui/widgets`) 안에서 단일 책임 `.py` 모듈로 수평 분리한다.
- **SSOT · 캡슐화 · 레거시**: 중복 로직·상수는 공용 모듈(`src/gui/constants.py`, `src/core/sorting.py`, `src/gui/exporters.py`) 단일 원천으로 통합하고, 타 모듈의 `_private` 직접 접근은 public 메서드·프로퍼티로 전환하며 죽은 분기·미호출 호환 래퍼를 제거한다.
- **병렬·스레드 안전**: `src/core/prober.py` 워커는 Windows `spawn` 호환 최상위 피클 가능 함수로 유지하고, GUI 워커(`src/gui/worker.py`)는 `InterruptedError` 취소 경로를 보존한다.
- **직렬화 하위 방어**: `data/config.json`([`state_codec.py`](../../../src/core/state_codec.py))이나 `QHeaderView.restoreState()`([`window_layout.py`](../../../src/gui/window_layout.py))처럼 디스크에 남은 과거 상태가 새 정책을 덮어쓰지 않도록 복원 경계를 점검한다.

## 3. 동기화 · 검증 게이트

- `src/` 내 모듈 추가·삭제·개명 또는 공개 심볼 이동 시 `src/__init__.py`·`src/core/__init__.py`·`README.md`·`ARCHITECTURE.md`·`classify_example.ipynb`를 함께 동기화한다.
- 트랙 B는 분리 단계마다 대상 `unittest -b`로 회귀를 확인하고, 작업 완료 시 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1` 전체 통과를 확인한다.
- 커밋 직전에는 필요에 따라 [`self-review`](../self-review/SKILL.md) 또는 [`smart-commit`](../smart-commit/SKILL.md)으로 이어간다.
