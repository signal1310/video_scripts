# 소스, 테스트 구조 재편 계획

> 상태(2026-11-19): 완료(R0~R12, 마지막 커밋 `6ada043`). 3단계 커밋 4b 전에 수행
>
> 대상 독자: 이 저장소에서 재편을 구현하는 에이전트. 이 문서만으로 커밋 순서, 옮길 파일, 검증 기준을 정할 수 있도록 작성
>
> 우선순위: `AGENTS.md`, `CLAUDE.md`가 우선. 이 문서와 실제 코드가 어긋나면 구현을 멈추고 보고

---

## 1. 목적과 원칙

- 목적: `src/core` 46개, `src/gui` 95개, `tests` 112개가 평탄하게 놓여 기능 영역이 보이지 않는 문제를 해소. 4b(결정 28), 4단계, 커밋 5가 encode, analysis, gui 영역에 파일을 더하기 전에 정리
- **동작 변화 없음**: 파일 이동, import 경로 변경, 문서 링크 갱신만 함. 로직, 이름, 주석 내용을 고치지 않음(이동으로 낡게 된 경로 언급만 고침)
- **한 단계만**: `src/<계층>/<영역>/`까지. 두 단계 하위 패키지는 만들지 않음
- **소유 영역 우선**(R12에서 보정): 모듈은 책임을 가진 영역 패키지에 두고 다른 영역이 import해도 됨. 영역 패키지 사이에 순환이 생기거나 계층 바로 아래의 기반 모듈이 쓰는 경우에만 계층 바로 아래(GUI 범용 위젯은 `gui/widgets/`)에 둠. 처음 규칙(둘 이상 영역이 쓰면 계층 바로 아래)은 영역끼리 서로 쓰지 않는 core에는 맞았으나, 영역끼리 import하는 것이 정상인 gui에서 영역 핵심 모듈을 밖으로 밀어냄
- **파일 이름 유지**: `core/encode/encode_plan.py`처럼 이름이 겹쳐도 바꾸지 않음(검색, Git 이력 추적 유지)
- **테스트는 src를 따름**: 모듈의 테스트는 그 모듈 패키지에 대응하는 `tests/` 하위 폴더에 둠. 공용 도우미(`_sandbox.py`, `_factories.py`, `_gui_helpers.py`, `_gui_window_base.py`, `_gui_settings_base.py`)는 `tests/` 바로 아래에 둠
- 계층 방향(`gui`, `cui` -> `service` -> `core`)은 바뀌지 않음

## 2. 목표 구조

### 2.1 `src/core`

| 패키지 | 모듈 |
| :--- | :--- |
| `core/encode/` | `encode_concurrency`, `encode_gate`, `encode_paths`, `encode_plan`, `encode_process`, `encode_progress`, `encode_state`, `encode_tag`, `encoder_specs`, `ffmpeg_command`, `ffmpeg_capability`, `stream_layout`, `power_state` |
| `core/analysis/` | `prober`, `probe_parser`, `probe_version_rules`, `storage_probe`, `system_probe`, `file_fingerprint`, `file_manager`, `quality_metrics` |
| `core/evaluation/` | `evaluator`, `quality_target`, `classification_policy`, `smart_action`, `size_estimator`, `downscale_resolver`, `resolution_tiers`, `exception_rules` |
| `core/settings/` | `config`, `app_state`, `state_codec`, `ui_state`, `shortcuts`, `preview_info` |
| `core/` 공용 | `models`, `messages`, `exceptions`, `formatting`, `paths`, `workspace_paths`, `logging_setup`, `eta`, `sorting`, `undo_history`, `__init__` |

### 2.2 `src/service`

| 패키지 | 모듈 |
| :--- | :--- |
| `service/encode/` | `encode_job`, `encode_service`, `encode_placement`, `encode_resume`, `encode_history`, `encode_events`, `encode_ports` |
| `service/` 그대로 | `analysis_probe`, `analysis_store`, `analysis_timing`, `analyzed_dirs`, `cache`, `classifier_service`, `session`, `__init__` |

### 2.3 `src/gui`

| 패키지 | 모듈(현재 위치) |
| :--- | :--- |
| `gui/table/` | 최상위: `table_model`, `table_columns`, `table_column_styles`, `table_tooltips`, `table_state_sync`, `proxy_model`, `delegates`, `column_filters`, `column_history`, `column_layout_state`, `column_sizing`, `numeric_buckets`. `widgets/`: `video_table_view`, `filter_header_view`, `column_filter_popover`, `filter_checklist_widget`, `table_context_menu`, `table_actions`, `table_drag`, `table_empty_overlay`, `table_overlay_layout` |
| `gui/menus/` | `menu_base`, `menu_bar`, `menu_callbacks`, `menu_file`, `menu_edit`, `menu_view`, `menu_selection`, `menu_analysis`, `dynamic_menus`, `manual_target_menu` |
| `gui/preview/` | 최상위: `preview_controller`, `preview_info_items`, `shared_file_handle`. `widgets/`: `video_preview_window`, `video_preview_controls`, `video_preview_overlay`, `preview_settings_panel`, `preview_info_presenter`, `quicklook_launcher` |
| `gui/controllers/` | `session_controller`, `restore_flow`, `restore_resolver`, `external_resync`, `report_evaluator`, `task_runner`, `worker`, `manual_target_controller`, `shortcuts_controller`, `dialog_coordinator` |
| `gui/settings/` | `dialogs/`: `settings_dialog`, `settings_sections`, `settings_advanced_cards`, `setting_row`, `setting_controls`, `settings_form_fields`, `settings_field_bindings`, `criteria_tuner_panel`, `tuner_slider_row`, `shortcuts_panel`, `shortcut_row`. `widgets/`: `settings_group_card`, `collapsible_section` |
| `gui/help/` | `dialogs/`: `help_dialog`, `help_blocks`, `help_guide_pages`, `help_concepts_page`, `glossary_view`, `glossary_data` |
| `gui/dialogs/` 남김 | `log_dialog`, `export_panel`, `exception_rules_dialog`, 최상위 `exporters`(사용처가 `export_panel`뿐) |
| `gui/widgets/` 남김(범용) | `custom_spinbox`, `floating_popup`, `edge_fade_overlay`, `header_bar`, `footer_bar`, `filter_bar`, `bento_dashboard`, `analyze_mode_popup`, `progress_fill_bar`, `collapsible_top_area`, `folder_path_button` |
| `gui/` 최상위 남김 | `__init__`, `__main__`, `main_window`, `theme`, `constants`, `window_layout`, `footer_summary`, `logging_bridge`, `log_file` |
| `gui/encode/` | 커밋 5에서 신설(`encode_controller`, `encode_worker`, 진행 창, 그래프). 이번 재편에서는 만들지 않음 |
| `gui/styles/`, `gui/assets/` | 그대로 |

- 배치 규칙: 위 표에 없거나 표와 실제 사용처가 어긋나는 모듈은 "한 기능 패키지에서만 쓰이면 그 패키지, 둘 이상이면 `widgets/` 또는 `gui/` 최상위"로 정하고 완료 보고에 적음
- 표 작성 근거: 2026-11-19 import 조사(`settings_group_card`, `collapsible_section`은 설정 화면만, `custom_spinbox`, `floating_popup`, `edge_fade_overlay`는 여러 영역, `exporters`는 `export_panel`만 사용)
- `src/cui`는 그대로(5개)

### 2.4 `tests`

| 폴더 | 대상 |
| :--- | :--- |
| `tests/core/encode/`, `tests/core/analysis/`, `tests/core/evaluation/`, `tests/core/settings/`, `tests/core/` | 대응하는 core 모듈의 테스트 |
| `tests/service/encode/`, `tests/service/` | 대응하는 service 모듈의 테스트 |
| `tests/gui/table/`, `tests/gui/menus/`, `tests/gui/preview/`, `tests/gui/controllers/`, `tests/gui/settings/`, `tests/gui/help/`, `tests/gui/` | 대응하는 gui 모듈의 테스트. 여러 영역을 함께 쓰는 창 단위 테스트(`test_gui_window_*`, `test_gui_widgets` 등)는 `tests/gui/` |
| `tests/cui/` | cui 테스트 |
| `tests/tools/` | `scripts/`와 저장소 메타 테스트(`test_check_runner`, `test_check_verified`, `test_hygiene`, `test_module_size`, `test_sync_skills`, `test_claude_guard`, `test_metadata_sync`, `test_presets_and_notebooks` 등) |
| `tests/` 바로 아래 | `__init__.py`와 공용 도우미만 |

- 테스트 하나가 여러 모듈을 다루면 주된 대상 모듈의 패키지에 둠. 판단이 갈린 파일은 완료 보고에 적음
- 하위 폴더마다 `__init__.py`를 둠(`unittest`의 점 경로 로딩에 필요)

## 3. 커밋 순서

각 커밋은 옮기기만 하며, 커밋마다 `check.ps1` 통과. 커밋 사이에 테스트 수가 같아야 함(현재 1335개)

| 순서 | 커밋 | 내용 |
| :--- | :--- | :--- |
| R0 | `chore:` 기반 | (1) `scripts/check_runner.py`가 `tests/` 하위 폴더까지 테스트 모듈을 찾고 점 경로 이름(`tests.core.encode.test_encode_plan`)으로 샤드를 만듦. 모듈별 시간 기록 키도 점 경로(기존 기록은 없는 모듈로 취급되어 크기 추정으로 대체됨). 테스트 추가 (2) 저장소 루트를 `__file__` 기준으로 계산하는 테스트 9곳을 공용 도우미 하나로 바꿈(하위 폴더로 옮겨도 깨지지 않게) (3) AGENTS.md 1절, ARCHITECTURE.md 원칙 문구(4절) |
| R1 | `refactor:` core/encode | 모듈 13개와 그 테스트 이동 |
| R2 | `refactor:` core/analysis | 모듈 8개와 그 테스트 이동 |
| R3 | `refactor:` core/evaluation | 모듈 8개와 그 테스트 이동 |
| R4 | `refactor:` core/settings | 모듈 6개와 그 테스트 이동, 남은 core 테스트를 `tests/core/`로 |
| R5 | `refactor:` service | `service/encode/` 7개와 그 테스트, 남은 service 테스트를 `tests/service/`로 |
| R6 | `refactor:` gui/table | 21개와 그 테스트 |
| R7 | `refactor:` gui/menus, gui/preview | 두 패키지와 그 테스트(커밋을 나눠도 됨) |
| R8 | `refactor:` gui/controllers | 10개와 그 테스트 |
| R9 | `refactor:` gui/settings, gui/help, dialogs 정리 | `dialogs/`에서 펼침, 설정 전용 위젯 이동, `exporters` 이동 |
| R10 | `refactor:` 남은 테스트 | `tests/gui/`, `tests/cui/`, `tests/tools/`. 완료 뒤 `tests/` 바로 아래에는 도우미만 남음 |
| R11 | `refactor:` 평탄화 | 두 단계 폴더 5개를 영역 폴더 한 단계로(추가된 단계) |
| R12 | `refactor:` 배치 규칙 보정 | `gui/table_model`, `gui/table_columns`, `gui/widgets/video_table_view` -> `gui/table/`, `gui/manual_target_menu` -> `gui/menus/`, `core/undo_history` -> `core/settings/`. 테스트도 따라 옮김. AGENTS.md 1절, ARCHITECTURE.md 원칙 2 문구 교체 |

- 순서 바꾸기, 합치기, 나누기는 구현 에이전트 판단으로 가능(보고에 적음). 단 R0이 가장 먼저

## 4. 이동할 때마다 함께 고칠 것

1. 이동은 `git mv`로 함(이력 추적)
2. import 경로: 모든 import가 절대 경로(`src.core.x`)이므로 스크립트로 일괄 변환. **문자열 경로도 변환**: 테스트의 `mock.patch("src.core....")` 등 약 191곳. 변환 누락은 BasedPyright가 못 잡고 테스트 실패로만 드러남
3. 지연 import 문자열: `src/__init__.py`(노트북 템플릿의 `from src import *`, 모듈 이름 문자열로 지연 import), `src/__main__.py`(`src.gui`, `src.cui.cli`)
4. 패키지 `__init__.py`: 새 하위 패키지마다 빈 `__init__.py`(또는 기존 패키지의 재수출 규칙을 따름). 기존 `src/core/__init__.py`, `src/gui/widgets/__init__.py`, `src/gui/dialogs/__init__.py`의 재수출 목록 갱신. 옛 경로를 위한 호환 재수출은 만들지 않음(AGENTS.md 1절 레거시)
5. 문서: `ARCHITECTURE.md` 모듈 표와 링크(`tests.test_metadata_sync`가 양방향 검사), `README.md`, 스킬 원본(`.agents/skills/`)의 경로 언급 후 `python scripts/sync_skills.py --write`, `TERMINOLOGY.md` 13절 명명 규칙과 새 패키지 이름이 맞는지 확인
6. 테스트 이름 참조: `tests.test_metadata_sync`처럼 점 경로로 테스트를 부르는 곳(AGENTS.md 7절, README, sync 스킬, `scripts/hooks/pre-commit`)을 새 경로로 고침
7. `scripts/`의 src import(측정 스크립트 등)

## 5. 검증

- 커밋마다 `check.ps1` 통과(오류, 경고, 스킵 0)
- 커밋마다 테스트 수와 테스트 이름 목록(모듈 경로 제외한 클래스와 메서드 이름)이 직전 커밋과 같음
- `git grep`으로 옛 경로 문자열 잔존 0(`src.core.<옮긴 모듈>`, `src/core/<옮긴 모듈>.py`, `tests.test_<옮긴 테스트>`). `IMPLEMENTATION_PLAN.md`와 이 문서는 검토 에이전트가 고치므로 제외
- 앱 실행 확인: 마지막 커밋 뒤 `python -m src`로 GUI가 뜨고, CUI(`python -m src.cui --help`)가 도움말을 출력함(사용자 데이터를 바꾸지 않는 범위)
- `pre-commit` 훅이 새 테스트 경로로 동작함

## 6. 문서 규칙 변경(승인 문구)

- AGENTS.md 1절의 "디렉터리: 기존 깊이 유지, 분할은 수평 `.py` 모듈 분리 우선"을 아래로 교체
  - 디렉터리: `src/<계층>/` 아래는 기능 영역별 하위 패키지 한 단계까지 두고, 여러 영역이 쓰는 모듈은 계층 바로 아래에 둠. 영역 목록과 배치는 `ARCHITECTURE.md`가 기준이며, 새 모듈은 영역에 맞는 패키지에 추가. `tests/`는 `src/` 구조를 따르고 공용 도우미는 `tests/` 바로 아래에 둠
- ARCHITECTURE.md의 "평탄한 디렉터리, 수평 모듈 분리" 원칙은 위 규칙에 맞춰 고치고, 영역 목록(2절 표 요약)을 넣음

## 7. 하지 않는 것

- 파일 이름 변경, 클래스와 함수 이름 변경, 로직 수정, 모듈 분리나 병합(소프트 상한 초과 파일 포함)
- 옛 경로 호환 재수출
- `IMPLEMENTATION_PLAN.md`와 이 문서 수정(검토 에이전트 담당, 스테이징 금지)
- `data/`, `_ref/`, 사용자 미디어 폴더 접근

## 8. 진행 기록

- 결과: `77936b6`(R0) `de7a41f`(R1 core/encode) `04f6220`(R2 core/analysis) `93b4e5a`(R3 core/evaluation) `51abb8a`(fix 작업 트리 지문) `8b2e370`(R4 core/settings) `812a14a`(R5 service/encode) `864fb10`(R6 gui/table) `d19c655`(R7 gui/menus, gui/preview) `f1252a3`(R8 gui/controllers) `07d39b2`(R9 gui/settings, gui/help) `9a2b881`(R10 tests 나머지와 ARCHITECTURE 영역 목록) `5204d70`(R11 하위 폴더 평탄화) `6ada043`(R12 배치 규칙 보정). R11 검증 트리 `0c49f0d49a`, 테스트 1337개(재편 중 1336 -> 1337은 fix의 재현 테스트), 이름 목록은 모듈 경로를 제외하고 R0 기준과 같음, 옛 경로 `git grep` 0건
- 계획 대비 편차(승인): `core/` 바로 아래에 `config`, `encode_tag`, `encoder_specs`, `probe_parser`, `quality_target`, `smart_action`, `resolution_tiers`(둘 이상 영역이 사용). `quality_metrics`는 `core/evaluation`. `gui/` 바로 아래에 `table_model`, `table_columns`, `manual_target_menu`, `gui/widgets/`에 `video_table_view`(여러 영역이 사용). 컨트롤러의 import는 배치 판단에서 제외. R0는 계획의 테스트 9곳이 실제 7곳
- 계획 결함: 2.3절 표의 `widgets/`, `dialogs/` 표기가 모호해 R6~R9가 두 단계 폴더(`gui/table/widgets` 등 5개)를 만들었고, R11에서 한 단계로 평탄화
- 부수 수정: `51abb8a` 검사 실행기의 작업 트리 지문이 실제 인덱스 복사본 때문에 같은 크기, 같은 초의 수정을 놓침(병렬 부하 재현 4.2%). 임시 인덱스를 `read-tree HEAD`로 만들어 해결, 소요 0.149초에서 0.348초
- R12(배치 규칙 보정): `table_model`, `table_columns`, `video_table_view`를 `gui/table`, `manual_target_menu`를 `gui/menus`, `undo_history`를 `core/settings`로 옮기고 AGENTS.md 1절, ARCHITECTURE.md 원칙 2를 소유 영역 우선 규칙으로 교체. `gui/widgets/__init__.py`의 표 모듈 재수출 4개 제거. 테스트는 `test_undo_history`만 이동(나머지는 창, 위젯, 미리 보기 단위 테스트라 유지). 순환 확인: gui 영역 사이 순환 없음(table -> menus 단방향, widgets -> table 없음), core 영역끼리 import 없음. 테스트 1337개, 커밋 트리 `3dfadd0117`(검증 트리 `b5ac9792d2`는 커밋 전 계획 문서 변경 포함)
- 남은 구조 흠(범위 밖): `core/messages.py`가 `core/analysis/storage_probe.py`의 저장 장치 타입을 참조해 core 직속과 analysis가 서로 의존. 코드 수정이 필요해 그 부분을 고칠 때 정리
- 검증 트리와 커밋 트리: 커밋하지 않은 계획 문서가 작업 트리에 있으면 `check.ps1`의 검증 트리가 커밋 트리와 달라짐. 구현 보고에는 두 해시를 나란히 적음
- 이월: `config.py`(712줄, 기록 543줄)와 `stylesheet.py`(981줄, 기록 792줄)의 화이트리스트 기록 초과는 재편과 별개로 사용자 결정 대기
