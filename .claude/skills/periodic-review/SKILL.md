---
name: periodic-review
description: >-
  Use this skill for the reviewer's periodic integrated code review ("정기 리뷰", "통합 리뷰", "버그 탐색", "미검토 커밋
  검토"), when five merges have piled up since the last review or a sub-stage of the plan has finished
  (REVIEW.md 8절). Reads the accumulated diff area by area for cross-merge bugs, duplication, layer breaks and dead
  code, and hands unaudited commit ranges to a read-only session.
---

# 정기 통합 리뷰 (`periodic-review`)

목적, 주기, 범위 기록, 결과 처리는 `REVIEW.md` 8절이 기준이고, 이 스킬은 읽는 순서와 보고 양식만 담음. 워커 감사(브랜치 하나의 변경과 그 주변)와 달리 병합이 쌓인 뒤에야 보이는 문제를 찾음.

## 1. 범위 정하기

1. 계획서 13.5절 "정기 리뷰 기록"의 마지막 `끝 커밋`을 찾음. 기록에 없는 구간은 미리뷰로 봄
2. `git --no-pager log --oneline <끝 커밋>..main`과 `git --no-pager diff --stat <끝 커밋>..main -- src`로 규모를 봄
3. 감사 없이 들어간 커밋(병합 기록에 감사 run이 없는 것)은 따로 표시함

## 2. 영역별로 읽기

영역(core, service, gui, tests) 순서로 `git --no-pager diff <끝 커밋>..main -- src/<영역>`과 바뀐 모듈의 호출처를 직접 읽음. 검증은 다시 돌리지 않고 병합 기록의 트리 해시와 대조함(`REVIEW.md` 2절 4항).

영역마다 찾을 것:
- 병합 사이의 상호작용(두 브랜치가 같은 상태, 같은 상수, 같은 DB 열을 다르게 씀)
- 중복 로직과 기준 정의 하나 위반(`AGENTS.md` 1절)
- 계층 위반, 모듈 크기 추세(`scripts/verify_module_size.py` 기록과 비교)
- 계획서 결정과 코드의 불일치
- 죽은 코드, 아무것도 단언하지 않는 테스트

## 3. 보고

- 지적마다 번호, 재현 가능한 근거(파일과 줄, 호출 경로, 측정값)를 붙임
- 읽은 범위와 읽지 않은 범위를 적음
- 결과 처리(수정 워커, 사람 확인 게이트, `TODO.md`)는 `REVIEW.md` 8절을 따르고, 끝나면 13.5절의 리뷰 구간을 갱신함

## 4. 미검토 커밋 일괄 검토(범위가 클 때)

감사 없이 들어간 커밋이 많으면 커밋 범위를 나눠 별도 세션에 읽기 전용으로 맡김.

1. 브리프와 보고서는 브리지 `queue/refs/review-unaudited-<날짜>/`에 둠
2. 브리프: 범위 표(묶음, 커밋, 규모), 기준 main 커밋, 금지 사항(워커 시작, 브리지 작업 트리 접근, `check.ps1`과 앱 실행, 코드와 문서 수정, 커밋, 보고서 파일 하나만 작성, python 프로세스는 PID로만 종료)
3. 보고 형식: `[심각도] 파일:줄 (커밋) - 시나리오 - 수정안 [유효|미확인]`. 최신 main에서 이미 고쳐진 것은 뺌. 중단되면 보고서를 덮어쓰지 않고 `재개 기록` 절을 덧붙여 이어감
4. 사용자가 "검토 끝"이라고 하면 `git status`, `git log`, `git stash list`, `git reflog`로 검토 세션이 main을 바꾸지 않았는지 먼저 확인하고, `[유효]` 지적을 코드로 확정해 수정 워커로 보냄
