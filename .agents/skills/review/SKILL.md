---
name: review
description: >-
  Use this skill when the user asks to review, inspect, or audit working tree
  changes ("리뷰", "검수", "코드 확인") before committing. Checks the git diff
  against AGENTS.md rules and reports findings only.
---

# 변경분 리뷰 (`review`)

`AGENTS.md` 규칙 준수만 점검하며 수정하지 않고 보고한다. 정확성 버그 탐색은 `/code-review` 범위다.

1. 대상: `git status -s`와 `git diff`(필요 시 `--cached`)로 변경 파일, 라인 확인
2. 대조: 변경분을 `AGENTS.md` 각 절과 비교
   - 1절 계층 경계, SSOT, 매직 넘버, `_private` 접근, 책임 혼재, 죽은 분기(줄 수는 `verify_module_size.py`가 검사하므로 소프트 상한 초과 보고를 확인하고 책임 혼재 여부만 판단)
   - 2절 타입 명시, 빈 `except`, 워커 피클 가능성, 스레드 취소 경로
   - 3절, 4절 UI 문체, 개조식 독스트링, 이력 언급
   - 3절, 6절 테스트 격리, 로컬 경로, 사설 IP, `max_workers` 하드코딩
   - 5절 설정, 모듈, 단축키 변경에 대응하는 문서, 테스트 갱신
3. 선별: 지적마다 위반 조문과 라인을 인용하고, 코드를 다시 읽어 재검증. 다음은 제외
   - 이번 diff 이전부터 있던 문제
   - Ruff, BasedPyright, `verify_module_size.py`가 잡는 항목
   - 추측성 문제와 취향 수준 제안
   - `noqa` 등으로 의도적으로 억제된 항목
4. 보고: `check.ps1` 결과를 확인하고, 심각도, 파일, 라인 순으로 간결히 보고. 지적이 없으면 점검한 절 번호와 함께 "지적 없음"으로 보고
