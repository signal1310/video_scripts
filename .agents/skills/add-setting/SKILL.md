---
name: add-setting
description: >-
  Use this skill when adding, renaming, or removing a DEFAULT_CONFIGS key
  or ClassifierConfig field. Keeps config parsing, data/config.json serialization,
  GUI settings UI, tests, and doc counts synchronized.
---

# 설정 키 추가·변경 런북 (`add-setting`)

## 동기화 체크리스트

1. **코어 설정 단일 원천 (`src/core/config.py`)**
   - `DEFAULT_CONFIGS`에 키·문자열 기본값을 추가/수정한다.
   - `ClassifierConfig` 필드, `from_mapping()`, `to_dict()`를 빠짐없이 동기화한다.
2. **상태 JSON 직렬화 (`src/core/app_state.py`)**
   - `data/config.json` 역직렬화/기본값 복원 시 `ClassifierConfig.from_mapping()` / `to_dict()`와 일관되게 동작하는지 점검한다.
3. **GUI 바인딩**
   - `src/gui/constants.py`(`THRESHOLD_BOUNDS`)에 수치 범위가 필요한 항목인지 확인한다.
   - `src/gui/dialogs/settings_sections.py`, `setting_row.py`(`get_config_value()`), `settings_dialog.py`에 입력 위젯·툴팁(존댓말)·초기화/저장 연결을 반영한다(장식 컨트롤 금지).
   - 실시간 튜닝 바(`src/gui/widgets/filter_bar.py`) 연동 대상이면 동기화 경로를 함께 점검한다.
4. **문서 표기**
   - `README.md` 및 `ARCHITECTURE.md`의 설정 키 개수 표기(`N개`)를 실제 `len(DEFAULT_CONFIGS)`에 맞게 갱신한다.
5. **테스트 및 검증**
   - `tests/test_config.py`, `tests/test_app_state.py` 및 `tests/test_gui_settings.py`에 왕복 저장·기본값·UI 반영 테스트를 추가한다.
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1`로 `test_metadata_sync.py` 포함 전체 통과를 확인한다.
