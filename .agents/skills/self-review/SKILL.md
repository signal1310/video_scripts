---
name: self-review
description: >-
  Use this skill when the user asks to review, inspect, or audit working tree
  changes ("리뷰", "검수", "코드 확인") before committing. Checks AGENTS.md
  architecture, style, docstring, test isolation, and hygiene rules on git diff.
---

# 변경분 셀프 리뷰 (`self-review`)

## 절차

1. **대상 파악**
   - `git status -s`와 `git diff`(필요 시 `git diff --cached`)로 수정된 파일과 변경 라인을 전수 확인한다.
2. **아키텍처·코드 품질 점검**
   - **3계층 경계**: `src/core`가 `service`/`cui`/`gui`를 임포트하거나 `src/gui`·`src/cui` 간 순환·직접 의존이 생기지 않았는지 확인한다.
   - **SSOT · 매직 넘버 · 캡슐화**: 중복 로직이나 추출 누락 상수, `src/` 타 모듈의 `_private` 직접 접근 여부를 점검한다.
   - **비대 모듈 · 미사용 코드**: 400~500줄 이상 다중 책임 혼재 여부 및 죽은 분기·레거시를 확인한다.
3. **문체 · 독스트링 · 주석 점검**
   - **독스트링·주석**: 존댓말(`~합니다`), `- ` 불릿, 문장 끝 마침표(`.`), 변경 이력·요구사항 언급이 섞이지 않은 한국어 개조식인지 점검한다.
   - **UI 문체**: 버튼·열 헤더·배지는 명사구, 툴팁·설정 설명·경고·오류는 일관된 존댓말인지 확인한다.
4. **안전성 · 위생 · 테스트 확인**
   - `TemporaryDirectory`와 경로 모킹으로 테스트가 격리되었는지, 실제 `.env`/`.rootdir`/`shana_presets/`를 건드리지 않는지 확인한다.
   - 로컬 경로, 사설 IP, 하드코딩된 워커 수 등 위생 위반 요소 유무를 점검한다.
5. **실측 검증 및 보고**
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1` 실행 결과를 확인한 뒤, 발견 사항(심각도·파일·라인)만 사실 기반으로 간결히 보고한다.
