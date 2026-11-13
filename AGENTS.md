# `video_scripts` 작업 규칙

## 1. 아키텍처 · 리팩터링 원칙
- **3계층 책임 분리**: `src/core`(도메인·프로빙·평가·설정·IO) ← `src/service`(오케스트레이션·캐시) ← `src/cui` / `src/gui`(표시·UI) 단방향 경계 유지, 순환 참조 금지
- **중복 금지(DRY / SSOT)**: 동일 로직·코덱 가중치·`THRESHOLD_BOUNDS`·UI 공용 상수/유틸([constants.py](src/gui/constants.py), [exporters.py](src/gui/exporters.py)) 단일 원천 재사용
- **매직 넘버 금지**: 의미 있는 수치(임계값·배율·타이머·창 크기·포트/크기 제한 등)는 명명 상수로 추출, 리터럴 반복 금지
- **SOLID · 캡슐화**: 높은 응집도·낮은 결합도 유지. `src/` 타 모듈·GUI가 `VideoClassifier` 등 클래스의 `_private` 속성을 직접 참조하지 않고 public 메서드/프로퍼티로 통신
- **God 객체·모듈 방지**: 단일 메서드·클래스·모듈에 이질적 책임(UI·상태·비즈니스·IO) 누적 금지. 선언적 데이터·스타일시트 외 400~500줄 이상 비대화 및 다중 책임 혼재 시 단일 책임 단위로 분리
- **평탄한 디렉터리**: 과도한 하위 패키지 분할 지양. 기존 깊이(`src/core`, `src/service`, `src/cui`, `src/gui/{dialogs,styles,widgets}`) 유지, 분할 시 수평 `.py` 모듈 분리 우선
- **레거시 정리**: 미호출 함수·죽은 분기·불필요 호환 레이어 발견 시 함께 제거

## 2. 정적 검증 · 런타임 안전
- **엄격 정적 분석**: [pyproject.toml](pyproject.toml)의 Ruff + BasedPyright(`strict`) 기준 `0 errors, 0 warnings` 유지
- **방어적 타입·예외**: 빈 컬렉션 타입 명시(`lines: list[str] = []`, `tokens: set[str] = set()`), 전 파라미터·반환 타입 명시, `| None` 역참조 전 `None` 검증. 빈 `except: pass` 금지(`S110`/`S112`), 예외 재발생 시 `raise ... from e` 유지 및 필요 시 `logging` 기록
- **병렬·GUI 스레드 안전**: [prober.py](src/core/prober.py)의 `ProcessPoolExecutor` 워커(`_worker_*`)는 Windows `spawn` 호환을 위해 모듈 최상위 함수·피클 가능 인자만 사용(람다·클로저·Qt 객체 금지). [worker.py](src/gui/worker.py)의 `QThread`는 `InterruptedError` 기반 취소·종료 대기(`wait`/`terminate`) 및 `QT_QPA_PLATFORM=offscreen` 테스트 호환 유지
- **VS Code Pylance 정합성**: [pyproject.toml](pyproject.toml)에 `[tool.pyright]`·`[tool.basedpyright]` 공존 금지(파싱 충돌). 규칙 변경 시 [.vscode/settings.json](.vscode/settings.json) 동기화

## 3. UI/UX · 테스트 격리 · 사실 보고
- **실동작 UI**: 백엔드 미연결 장식 컨트롤 및 근거 없는 초기 더미 문구(예: 작업 전 `"준비 완료"`) 금지
- **UI 문체 분리**: 버튼·열 헤더·배지·CUI 표 헤더는 간결한 명사구, 툴팁·설정 설명·오류 안내·확인 대화상자는 일관된 존댓말 사용
- **테스트 동반·격리**: 핵심 로직·GUI 변경 시 `tests/`(`test_gui_*.py` 등) 반영. `TemporaryDirectory` + `_sandbox.py`·`get_state_path`·`get_rootdir_path` 모킹으로 격리하며 실제 `data/config.json`·`.rootdir`·`shana_presets/`·레지스트리·사용자 미디어 폴더 수정 금지
- **사실 기반 보고**: 직접 확인한 파일 경로·심볼·테스트/도구 출력만 보고, 미확인 내용 추정 금지

## 4. 주석 · 코드 문서화
- **자명성 우선 · 절제**: 긴 주석 전 함수 분리·명확한 네이밍·상수 추출 우선. 미사여구·자화자찬 금지, 객관적 기술 설명만 작성
- **이력·요구사항 언급 엄금**: 수정 경위·채팅/사용자 요청 반영 여부 기재 금지(Git에 위임)
- **Why > What**: 코드 흐름 번역 금지. 트레이드오프·호출 순서 의존성·외부/OS 제약 우회·불변식·예외 사유만 핵심 기술
- **한국어 개조식 표준 (PEP 257 / Google)**: 모듈·클래스·공개 함수/메서드 독스트링(`"""..."""`) 및 인라인 주석(`# ...`)은 존댓말·`- ` 불릿·문장 끝 마침표(`.`) 없이 간결한 개조식(`...모듈`, `...반환`, `...방지`) 작성. 한 줄 요약 기본, 계약 필요 시 `"""요약\n\n세부 계약"""` 사용 및 구현 수치 나열 지양(Ruff `D` 준수). 코드와 어긋난 낡은 주석은 즉시 수정·제거

## 5. 도메인 불변식 · 계약 동기화
- **비파괴 분류**: 실이동 시 기존 대상 파일 덮어쓰기 금지, `pseudo_classify_mode`는 디스크 이동 없이 메모리 상태만 갱신
- **설정·프리셋·노트북 동기화**: 설정 키 추가/변경 시 `DEFAULT_CONFIGS`·`ClassifierConfig.from_mapping()`·`to_dict()`([config.py](src/core/config.py)), `AppState`([app_state.py](src/core/app_state.py)), [settings_dialog.py](src/gui/dialogs/settings_dialog.py), [test_config.py](tests/test_config.py) 동기화. 분류 폴더명(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`), `shana_presets/` 매핑, [classify_example.ipynb](classify_example.ipynb)(`from src import *`)는 [test_presets_and_notebooks.py](tests/test_presets_and_notebooks.py) 계약 유지
- **메타파일·문서·IDE 동기화**: `src/` 모듈 추가/삭제/개명, GUI 버튼/단축키·도메인 용어 변경 시 [ARCHITECTURE.md](ARCHITECTURE.md), [README.md](README.md), [classify_example.ipynb](classify_example.ipynb), [pyproject.toml](pyproject.toml) ↔ [.vscode/settings.json](.vscode/settings.json) 갱신([test_metadata_sync.py](tests/test_metadata_sync.py) 자동 검증)

## 6. 저장소 위생 · 로컬 유출 방지
- **비대상 파일 커밋 금지**: `data/`, `.env`, `.env.*`, `.rootdir`, `.rootdir.*`, `.venv/`, `.ruff_cache/`, `.ipynb_checkpoints/`, `classify.ipynb`, `*.sqlite3*`, `.video_cache*`, `*.csv`, 임시 산출물 스테이징/커밋 금지
- **로컬 정보 유출 금지**: 코드·설정·노트북·프리셋·문서에 홈 절대 경로(`Users`/`home`), 임의 드라이브·UNC 경로, 사설 IP, 로컬 호스트명, PC 사양 유추값, `max_workers` 정수 하드코딩 금지
- **포터블 설정**: [.vscode/settings.json](.vscode/settings.json)·`scripts/`는 `${workspaceFolder}` 또는 스크립트 기준 상대 경로만 사용

## 7. 작업 범위 · Git 안전 · 필수 검증
- **범위·Git 안전**: 미요청 `README.md`·`TODO.md`·무관 모듈 대량 수정 금지. `git push --force`, 커밋 히스토리 덮어쓰기, [pre-commit](scripts/hooks/pre-commit) 우회(`--no-verify`) 금지
- **필수 검증 게이트**:
  - **코드·설정·테스트 변경**: 중간 확인은 대상 `unittest`(`-b`)만 실행하고, 완료·커밋 전 `check.ps1` 1회 실행(Ruff → BasedPyright strict → [verify_hygiene.py](scripts/verify_hygiene.py) → `unittest`; 상세 출력 필요 시 `-VerboseTests`)
  - **문서·스킬만 변경**: [verify_hygiene.py](scripts/verify_hygiene.py) + `tests.test_metadata_sync` 통과 또는 `pre-commit` 훅 검증으로 갈음 가능

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1
```
