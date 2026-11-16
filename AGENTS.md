# `video_scripts` 작업 규칙

## 1. 아키텍처
- 계층: `src/cui`와 `src/gui`는 `src/service`를, `src/service`는 `src/core`를 의존하는 단방향이며 순환 참조 금지
- 중복, 기준 정의: 동일 로직, 코덱 가중치, `THRESHOLD_BOUNDS`, GUI 공용 상수/유틸은 기준 정의 하나를 재사용
- 매직 넘버: 의미 있는 수치(임계값, 배율, 타이머, 크기 제한 등)는 명명 상수로 추출
- 캡슐화: 타 모듈이 클래스의 `_private` 속성을 직접 참조하지 않고 public 메서드, 프로퍼티 사용
- 비대화 방지: 한 메서드, 클래스, 모듈에 이질적 책임(UI, 상태, 비즈니스, IO)을 쌓지 않음
  - 모듈이 500줄을 넘으면 책임 혼재 여부를 검토해 혼재 시 분리
  - 1000줄 초과는 불가이며 `scripts/verify_module_size.py`가 검사. 줄 수 자체보다 책임 혼재를 기준으로 판단
  - 예외 파일은 `scripts/module_size_exceptions.json` 한 곳에서 관리
    - `frozen`: 기존 초과 파일. 줄 수 증가가 불가하고, 분리, 축소 후 `--write`로 상한만 낮춤
    - `exempt`: 화이트리스트. 선언 데이터, 단일 책임 스키마처럼 분리가 오히려 해로운 파일. 분류, 사유, 검토 당시 줄 수를 기록하며, 기록보다 20% 넘게 커지면 재검토
  - 화이트리스트 등록과 기록 줄 수 갱신은 사용자 승인으로만 수행. 스크립트와 에이전트는 항목을 자동 추가하지 않음
- 디렉터리: 기존 깊이 유지, 분할은 수평 `.py` 모듈 분리 우선
- 레거시: 작업 범위 안의 미호출 함수, 죽은 분기, 불필요 호환 레이어는 발견 즉시 제거하고, 범위 밖은 7절 최소 변경에 따라 보고만 함

## 2. 정적 검증, 런타임 안전
- Ruff + BasedPyright(`strict`) `0 errors, 0 warnings` 유지
- 타입, 예외: 전 파라미터, 반환 타입 명시, 빈 컬렉션 타입 명시(`lines: list[str] = []`), `| None` 역참조 전 검증, 빈 `except` 금지, 재발생은 `raise ... from e`
- 병렬: `src/core/prober.py`는 `ThreadPoolExecutor`를 사용하고 취소는 `threading.Event`를 워커 인자로 전달해 실행 중인 ffprobe를 kill하며, 워커에서 Qt 객체 접근 금지. `src/gui/worker.py`의 `QThread`는 `InterruptedError` 취소, 종료 대기와 `QT_QPA_PLATFORM=offscreen` 테스트 호환 유지
- Pylance: `pyproject.toml`에 `[tool.pyright]`, `[tool.basedpyright]` 공존 금지, 규칙 변경 시 `.vscode/settings.json` 동기화

## 3. UI, 테스트, 보고
- UI: 백엔드 미연결 장식 컨트롤과 근거 없는 초기 더미 문구(예: 작업 전 `"준비 완료"`) 금지
- 문체: 버튼, 열 헤더, 배지, CUI 표 헤더는 간결한 명사구, 툴팁, 설정 설명, 오류 안내, 확인 대화상자는 존댓말
- 용어: 사용자 노출 문구(열, 값, 메뉴, 설정, 툴팁, 문서)는 `TERMINOLOGY.md`의 표준어와 폭별 표기(좁은 곳 약어, 넉넉한 곳 풀네임)를 따르고, 새 용어는 자연스러운 한국어 후보와 추천안을 사용자에게 제시해 승인받은 뒤 그 문서의 표에 먼저 추가. 코드 식별자 명명은 `TERMINOLOGY.md` 13절
- 테마: `src/gui/theme.py`, `src/gui/styles/` 변경 시 다크/라이트/시스템 전환과 양쪽 모드의 대비를 확인하고, 색상은 팔레트 토큰만 사용(하드코딩 금지). 아이콘은 임포트 시점 디스크 쓰기 없이 지연 생성
- 테스트: 핵심 로직, GUI 변경 시 `tests/`에 반영. `TemporaryDirectory`와 `_sandbox.py`, 경로 모킹으로 격리하고 실제 `data/*`, `shana_presets/`, 레지스트리, 사용자 미디어 폴더는 수정 금지
- 보고: 직접 확인한 경로, 심볼, 도구 출력만 보고하고 미확인 내용은 추정하지 않음

## 4. 주석, 문서화
- 주석 전에 함수 분리, 네이밍, 상수 추출을 우선하고, 객관적 기술만 작성
- 수정 경위, 요청 반영 여부는 쓰지 않음(Git에 위임)
- 코드 흐름 번역 대신 Why(트레이드오프, 호출 순서, OS 제약, 불변식, 예외 사유)만 기술
- 독스트링, 인라인 주석: 한국어 개조식(존댓말, `- ` 불릿, 문장 끝 마침표 없음), 한 줄 요약 기본, 구현 수치 나열 지양(Ruff `D`)
- 코드와 어긋난 낡은 주석은 즉시 수정, 제거
- 문자: 메타 문서(`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CLAUDE.md`, 스킬), 주석, 커밋 메시지에는 한글을 제외한 ASCII 밖 문자(가운뎃점, 화살표, 절 기호, 줄표 등)를 쓰지 않음(나열은 쉼표, 구분은 `/`, 방향은 `->`, 절 참조는 `N절`). 프로그램 코드와 UI 문자열에서도 가운뎃점을 쓰지 않음(나열은 쉼표, 짝을 이루는 이름은 `/`)

## 5. 도메인 불변식, 동기화
- 비파괴 분류: 실이동 시 기존 대상 파일 덮어쓰기 금지, `pseudo_classify_mode`는 디스크 이동 없이 메모리 상태만 갱신
- 설정 키 변경: `src/core/config.py`의 섹션 데이터클래스 기본값, `ClassifierConfig.from_dict()`, `to_dict()`, `AppState`, 설정 대화상자, `tests/test_config.py`를 함께 갱신
- 계약 유지: 분류 폴더명(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`), `shana_presets/` 매핑, 기본 노트북 템플릿(`from src import *`)은 `tests/test_presets_and_notebooks.py` 기준
- 단일 프리셋 불변식: 한 분류 폴더에 속한 모든 영상은 그 폴더에 매핑된 단일 프리셋으로 재인코딩 가능해야 함. 판정 사유가 달라도 같은 폴더에 두려면 해당 프리셋이 그 영상 전체에 적합해야 하며, 폴더를 늘릴 때는 새 프리셋 XML과 1:1 매핑을 함께 추가
- 위 불변식은 `tests/test_presets_and_notebooks.py`가 검증하지 않으므로, 매핑 변경 시 폴더와 프리셋의 대응을 직접 확인
- 문서, IDE: `src/` 모듈 추가, 삭제, 개명, GUI 버튼, 단축키, 용어 변경 시 `ARCHITECTURE.md`, `README.md`, 기본 노트북 템플릿, `pyproject.toml`과 `.vscode/settings.json`을 갱신(`tests/test_metadata_sync.py`가 검증)

## 6. 저장소 위생
- 커밋 금지: `data/`, `.env`, `.env.*`, `.rootdir`, `.rootdir.*`, `.venv/`, `.ruff_cache/`, `.ipynb_checkpoints/`, `classify.ipynb`, `*.sqlite3*`, `.video_cache*`, `*.csv`, 임시 산출물
- 로컬 정보 유출 금지: 코드, 설정, 노트북, 프리셋, 문서에 홈 절대 경로(`Users`/`home`), 임의 드라이브, UNC 경로, 사설 IP, 로컬 호스트명, PC 사양 유추값, `max_workers` 정수 하드코딩 금지
- 포터블 설정: `.vscode/settings.json`, `scripts/`는 `${workspaceFolder}` 또는 스크립트 기준 상대 경로만 사용

## 7. 작업 원칙, 검증
- 가정: 요구가 모호하거나 해석이 갈리면 가정을 명시하고 선택지를 제시하며, 더 단순한 방법이 있으면 먼저 제안
- 단순성: 요청하지 않은 기능, 설정, 유연성과 단일 용도 추상화, 발생할 수 없는 경로의 방어 코드는 추가하지 않음(`| None` 등 타입이 허용하는 경우의 검증은 2절 유지)
- 최소 변경: 변경한 모든 줄은 요청에 닿아야 하며 인접 코드, 주석, 포맷은 기존 스타일대로 둠. 내 변경으로 생긴 미사용 import, 변수, 함수는 제거하고, 무관한 기존 죽은 코드는 보고만 함. 요청하지 않은 `README.md`, `TODO.md`, 무관 모듈의 대량 수정 금지
- 목표 기반: 작업을 검증 가능한 목표로 바꿈(버그 수정은 재현 테스트 작성 후 통과, 리팩터링은 전후 테스트 통과). 여러 단계면 단계마다 검증 방법을 적은 계획을 먼저 제시
- Git 안전: `git push --force`, 히스토리 덮어쓰기, `--no-verify` 금지
- 코드, 설정, 테스트 변경: 중간에는 대상 `unittest -b`만 실행하고, 완료, 커밋 전 `check.ps1`을 1회 실행(Ruff, BasedPyright, `verify_hygiene.py`, `unittest` 병렬, 상세 출력은 `-VerboseTests`). 오류, 경고, 스킵 없이 통과하면 검증 결과 블록은 생략하고, 실패, 경고, 스킵, 미실행이 있을 때만 상세 보고
- 문서, 스킬만 변경: `verify_hygiene.py`와 `tests.test_metadata_sync` 통과 또는 `pre-commit` 훅으로 갈음
- 스킬: 원본은 `.agents/skills/<name>/SKILL.md`이며 `.claude/skills/`는 사본이므로 직접 수정하지 않고, 원본 수정 후 `python scripts/sync_skills.py --write`로 재생성(불일치는 `tests.test_metadata_sync`가 검출). 스킬을 추가, 삭제, 개명하면 `README.md` 스킬 표와 `ARCHITECTURE.md` 스킬 목록도 갱신

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1
```
