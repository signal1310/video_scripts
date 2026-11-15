---
name: sync
description: >-
  Use this skill when adding, deleting, or renaming modules under src/ or skills under
  .agents/skills, or changing GUI shortcuts, button labels, or domain terms. Synchronizes
  package exports, ARCHITECTURE.md, README.md, the default notebook template, and IDE settings.
---

# 모듈, 문서 동기화 (`sync`)

1. export: 해당 패키지 `__init__.py`의 import와 `__all__` 갱신
2. 문서: `ARCHITECTURE.md` 모듈 표와 `README.md` 구조, 기능 설명을 코드에 맞추고, 단축키, 버튼 문구, 열 수 변경도 두 문서에 반영
3. 스킬: 스킬을 추가, 삭제, 개명했다면 `README.md` 스킬 표와 `ARCHITECTURE.md` 스킬 목록을 갱신하고 `python scripts/sync_skills.py --write`로 사본 재생성
4. 노트북 템플릿: 공개 API가 바뀌면 `src/core/app_state.py`의 기본 노트북 템플릿과 `tests/test_presets_and_notebooks.py` 계약 확인
5. IDE 설정: `pyproject.toml` 규칙을 바꿨다면 `.vscode/settings.json`을 동기화
6. 검증: `tests.test_metadata_sync` 실행 후 `check.ps1`로 마무리

```powershell
.venv\Scripts\python.exe -m unittest tests.test_metadata_sync -b
```
