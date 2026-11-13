# 프로젝트 작업 규칙 (`video_scripts`)

이 문서는 새 대화 세션이 시작될 때마다 자동으로 적용되는 워크스페이스 규칙입니다.

## 1. 아키텍처 및 리팩터링 핵심 원칙
- **3계층 책임 분리**: `src/core`(도메인 모델·프로빙·평가·설정·파일 IO), `src/service`(오케스트레이션·캐시), `src/cui` / `src/gui`(표시·UI) 경계를 유지하고 순환 참조를 금지한다.
- **중복 금지(DRY / Single Source of Truth)**: 동일 로직, 코덱 가중치 그룹, 설정 범위(`THRESHOLD_BOUNDS`), 공용 UI 상수·유틸리티(`src/gui/constants.py`, `src/gui/exporters.py`)를 여러 모듈에 중복 구현하지 않고 단일 원천에서 재사용한다.
- **매직 넘버 금지**: 의미가 있는 숫자(임계값, 배율, 타이머, 창 크기, 포트/크기 제한 등)는 반드시 명명된 상수로 추출하고, 동일 수치를 변수 없이 반복 입력하지 않는다.
- **SOLID 및 캡슐화**: 높은 응집도와 낮은 결합도를 유지한다. `src/` 내 타 모듈·GUI가 `VideoClassifier` 등 클래스의 `_private` 내부 속성을 직접 참조하지 않고 public 메서드/프로퍼티 경계로 통신한다.
- **God 메서드·클래스·파일 경계**: 하나의 메서드·클래스·모듈에 서로 다른 책임(UI 조립, 상태·이벤트 제어, 비즈니스 로직, IO 등)이 누적되어 비대해지는 것을 경계한다. 선언적 데이터·스타일시트처럼 단일 변경 축에 묶인 경우를 제외하고, 400~500줄 이상으로 커지며 서로 다른 책임이 섞이기 시작하면 단일 책임 단위의 작은 함수·클래스·모듈로 분리한다.
- **평탄한 디렉터리 및 수평 모듈 분리**: Java 스타일처럼 클래스·기능마다 다층 하위 디렉터리를 깊게 파는 과분할(보일러플레이트 re-export 양산 및 탐색 피로 유발)을 지양한다. 현재 계층 깊이(`src/core`, `src/service`, `src/cui`, `src/gui/{dialogs,styles,widgets}`)를 유지하며, 모듈 분리가 필요할 때는 새 하위 폴더 생성보다 기존 계층 디렉터리 내 수평 `.py` 모듈 분리를 우선한다.
- **레거시·미사용 코드 정리**: 더 이상 호출되지 않는 함수, 죽은 분기, 불필요한 호환 레이어가 발견되면 함께 정리한다.

## 2. 런타임 안전성 및 컴파일 수준 정적 검증
- **엄격 정적 분석 준수**: [pyproject.toml](pyproject.toml)의 **Ruff** + **BasedPyright(`strict`)** 규칙에서 `0 errors, 0 warnings`를 유지한다.
- **방어적 타입·예외 작성**:
  - 빈 컬렉션 초기화 시 타입을 명시한다(`lines: list[str] = []`, `tokens: set[str] = set()` 등).
  - 모든 함수·메서드의 파라미터/반환 타입을 명시하고, `Optional`(`| None`) 값은 사용 전 반드시 `None` 여부를 검증한다.
  - 빈 `except: pass`로 예외를 묵살하지 않으며(`S110`/`S112`), 예외 재발생 시 `raise ... from e` 체이닝을 유지하고 필요 시 `logging`으로 기록한다.
- **멀티프로세싱·GUI 스레드 안전성**: [src/core/prober.py](src/core/prober.py)의 `ProcessPoolExecutor` 워커(`_worker_*`)는 Windows `spawn` 호환을 위해 모듈 최상위 함수와 피클 가능한 인자만 사용한다(람다·클로저·Qt 객체 전달 금지). `QThread` 워커([src/gui/worker.py](src/gui/worker.py)) 수정 시 `InterruptedError` 기반 취소·종료 대기(`wait`/`terminate`) 및 `QT_QPA_PLATFORM=offscreen` 테스트 호환을 유지한다.
- **VS Code Pylance 정합성**: [pyproject.toml](pyproject.toml)에 `[tool.pyright]`와 `[tool.basedpyright]`를 동시에 두지 않는다(BasedPyright 파싱 충돌 방지). 정적 분석 규칙을 수정할 때는 [.vscode/settings.json](.vscode/settings.json)의 Pylance 설정도 함께 동기화하여 탐색기에 빨간 오류 표시가 남지 않도록 한다.

## 3. GUI / UX 및 테스트 격리·사실 기반 보고
- **실제 동작과 연결된 UI**: 백엔드/서비스와 연결되지 않은 장식용(껍데기) 컨트롤이나 근거 없는 초기 더미 상태 문구(예: 작업 전 `"준비 완료"` 표시)를 두지 않는다.
- **UI 문체 분리**: 버튼·열 헤더·배지 등 즉시 읽는 UI 레이블과 CUI 표 헤더는 간결한 명사구를 사용하고, 툴팁·설정 설명·오류 안내·확인 대화상자 등 사용자 설명 문구는 일관된 존댓말로 작성한다.
- **테스트 동반 및 환경 격리**: 핵심 로직·GUI 동작 변경 시 `tests/` 단위 테스트(`tests/test_gui_*.py` 등)를 함께 반영한다. 모든 테스트는 `TemporaryDirectory`와 `get_env_path`·`get_rootdir_path` 모킹으로 격리하며, 실제 `.env`, `.rootdir`, `shana_presets/`, 사용자 미디어 폴더를 절대 수정하지 않는다.
- **사실 기반 보고**: 실행·관찰한 파일 경로, 심볼, 테스트·도구 출력 결과만 사실로 보고하며 미확인 내용을 추정 보고하지 않는다.

## 4. 주석 및 코드 문서화 원칙
- **코드 자명성 우선**: 주석을 길게 달아야 이해되는 코드는 주석으로 보완하기 전에 함수 분리·명확한 네이밍·상수 추출로 코드 자체를 먼저 읽기 쉽게 만든다.
- **객관적·절제된 주석**: 불필요한 수식어, 미사여구, 자화자찬을 금지하고 간결하며 객관적인 기술 설명만 남긴다.
- **변경 이력·요구사항 언급 엄금**: `"이 부분이 ~로 변경됨"`, `"채팅/사용자 요청 구현"` 등 작업 과정·수정 내역·대화 요구사항 반영 여부를 적는 주석을 절대 작성하지 않는다(변경 이력은 Git에 위임).
- **장황한 코드 해설 금지 ("Why > What")**: 코드 흐름을 그대로 번역하듯 구구절절 설명하지 않고, 트레이드오프·호출 순서 의존성·외부 라이브러리/OS 제약 및 우회 사유·불변식·예외 사유처럼 코드만으로 드러나지 않는 맥락("왜")만 핵심적으로 적는다.
- **한국어 개조식 독스트링·주석 표준 (PEP 257 / Google 스타일)**: 모든 모듈·클래스·공개 함수/메서드 독스트링(`"""..."""`)과 인라인 주석(`# ...`)은 존댓말(`~합니다`/`~입니다`)이나 `- ` 불릿 목록, 문장 끝 마침표(`.`) 없이 **간결한 한국어 개조식(`...모듈`, `...반환`, `...방지`)**으로 작성한다. 한 줄 요약은 `"""요약"""` 형식으로, 세부 계약이 필요한 경우에만 `"""요약\n\n세부 계약"""` 형식으로 작성하며 구현 세부 수치 나열을 지양한다([pyproject.toml](pyproject.toml) Ruff `D` 규칙 준수). 코드 변경으로 실제 동작과 어긋난 낡은 주석은 방치하지 말고 반드시 함께 수정·제거한다.

## 5. 도메인 불변식 및 설정·프리셋 계약
- **비파괴 파일 분류 원칙**: 실제 파일 이동 시 기존 대상 파일을 덮어쓰지 않으며, `pseudo_classify_mode`에서는 디스크 파일을 이동하지 않고 메모리 상태만 갱신한다.
- **설정 키·프리셋·노트북 계약 동기화**: `.env` 설정 키 추가·변경 시 `DEFAULT_CONFIGS`·`ClassifierConfig.from_env()`·`to_env_dict()`([src/core/config.py](src/core/config.py)), `.env_example`, [settings_dialog.py](src/gui/dialogs/settings_dialog.py), [test_config.py](tests/test_config.py)를 함께 동기화한다. 분류 폴더명(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`), `shana_presets/` 프리셋 매핑, 노트북 진입점([classify_example.ipynb](classify_example.ipynb)의 `from src import *`)은 [test_presets_and_notebooks.py](tests/test_presets_and_notebooks.py)와 함께 계약을 유지한다.

## 6. 저장소 위생 및 로컬 환경 정보 유출 방지
- **비대상 파일 커밋 금지**: `.env`, `.env.*`, `.rootdir`, `.rootdir.*`, `.venv/`, `.ruff_cache/`, `.ipynb_checkpoints/`, `classify.ipynb`, `*.sqlite3*`, `.video_cache*`, `*.csv`, 임시 산출물은 절대 스테이징/커밋하지 않는다.
- **로컬 정보·환경 유추 수치 금지**: 코드·설정·노트북·프리셋·문서에 개인 사용자 홈 절대 경로(Windows `Users`·macOS `Users`·Linux `home` 하위 경로), 임의 드라이브 및 UNC 네트워크 절대 경로, 사설 IP, 로컬 호스트명, 특정 PC 사양에서 유추 가능한 값이나 `max_workers` 정수 하드코딩을 남기지 않는다.
- **포터블 설정 유지**: IDE 설정([.vscode/settings.json](.vscode/settings.json))과 스크립트(`scripts/`)는 `${workspaceFolder}` 또는 스크립트 기준 상대 경로만 사용한다.

## 7. 작업 범위·Git 안전 및 필수 검증 게이트
- **작업 범위·Git 안전**: 요청받지 않은 `README.md`·`TODO.md`나 무관한 모듈을 임의로 대량 수정하지 않는다. 강제 푸시(`git push --force`), 커밋 히스토리 덮어쓰기, `.git/hooks/pre-commit` 우회(`--no-verify`)는 금지한다.
- **작업 완료 및 커밋 전 필수 검증**: 코드를 수정하거나 커밋하기 전에는 **항상** 통합 검증 스크립트를 실행해 전체 통과를 확인한다:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1
```

- 위 스크립트는 **Ruff 정적 린트 → BasedPyright strict 타입 검사 → 저장소 위생·로컬 정보 유출 검사(`scripts/verify_hygiene.py`) → 전체 `unittest` 테스트**를 순서대로 실행한다.
- 커밋 시 `.git/hooks/pre-commit` 훅(Ruff + BasedPyright + `--staged` 위생 검사)이 실패하면 우회하지 말고 원인을 수정한 뒤 커밋한다.
