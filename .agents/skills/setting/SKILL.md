---
name: setting
description: >-
  Use this skill when adding, renaming, or removing a hierarchical config dataclass field
  or ClassifierConfig setting. Keeps config metadata, data/config.json serialization,
  GUI settings UI, tests, and doc counts synchronized.
---

# 설정 키 추가, 변경 (`setting`)

1. 코어: `src/core/config.py`의 해당 섹션 데이터클래스에 타입, 기본값, `field(metadata={"comment": ..., "bounds": ...})`를 반영하고, `ClassifierConfig`, `CORE_SETTING_COUNT`, `collect_setting_bounds()`가 새 필드를 포함하는지 확인
2. 직렬화: `state_codec.py`, `app_state.py`의 JSONC 파싱/출력과 `from_dict()`/`to_dict()` 왕복, 기본값 복원 확인
3. GUI: 수치 범위가 `THRESHOLD_BOUNDS`로 자동 수집되는지 확인하고, `src/gui/dialogs/`의 설정 화면에 입력 위젯, 존댓말 툴팁, 초기화, 저장 연결 반영(장식 컨트롤 금지). 실시간 필터 바와 연동되는 항목이면 동기화 경로도 점검
4. 문서: `README.md`, `ARCHITECTURE.md`의 설정 키 개수를 `CORE_SETTING_COUNT`에 맞춤
5. 테스트: `test_config.py`, `test_app_state.py`, `test_gui_settings.py`에 왕복 저장, 기본값, UI 반영을 추가하고 `check.ps1` 통과 확인
