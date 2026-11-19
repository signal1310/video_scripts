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
- 디렉터리: `src/<계층>/` 아래는 기능 영역별 하위 패키지 한 단계까지 두고, 여러 영역이 쓰는 모듈은 계층 바로 아래에 둠. 영역 목록과 배치는 `ARCHITECTURE.md`가 기준이며, 새 모듈은 영역에 맞는 패키지에 추가. `tests/`는 `src/` 구조를 따르고 공용 도우미는 `tests/` 바로 아래에 둠
- 레거시: 작업 범위 안의 미호출 함수, 죽은 분기, 불필요 호환 레이어는 발견 즉시 제거하고, 범위 밖은 7절 최소 변경에 따라 보고만 함

## 2. 정적 검증, 런타임 안전
- Ruff + BasedPyright(`strict`) `0 errors, 0 warnings` 유지
- 타입, 예외: 전 파라미터, 반환 타입 명시, 빈 컬렉션 타입 명시(`lines: list[str] = []`), `| None` 역참조 전 검증, 빈 `except` 금지, 재발생은 `raise ... from e`
- 병렬: `src/core/analysis/prober.py`는 `ThreadPoolExecutor`를 사용하고 취소는 `threading.Event`를 워커 인자로 전달해 실행 중인 ffprobe를 kill하며, 워커에서 Qt 객체 접근 금지. `src/gui/controllers/worker.py`의 `QThread`는 `InterruptedError` 취소, 종료 대기와 `QT_QPA_PLATFORM=offscreen` 테스트 호환 유지
- Pylance: `pyproject.toml`에 `[tool.pyright]`, `[tool.basedpyright]` 공존 금지, 규칙 변경 시 `.vscode/settings.json` 동기화

## 3. UI, 테스트, 보고
- UI: 백엔드 미연결 장식 컨트롤과 근거 없는 초기 더미 문구(예: 작업 전 `"준비 완료"`) 금지
- 문체: 버튼, 열 헤더, 배지, CUI 표 헤더는 간결한 명사구, 툴팁, 설정 설명, 오류 안내, 확인 대화상자는 존댓말
- 버튼 표기: 버튼에는 기능 이름 텍스트를 쓰지 않고 아이콘만 표시하며, 기능 이름은 접근성 이름(간결한 명사구)과 툴팁(존댓말)으로 제공
  - 신규, 변경하는 버튼에 적용하고, 기존 텍스트 버튼은 해당 화면을 수정할 때 전환(일괄 전환은 범위 합의 후 수행)
  - `QMessageBox`, 파일 대화상자의 표준 버튼은 제외
- 용어: 사용자 노출 문구(열, 값, 메뉴, 설정, 툴팁, 문서)는 `TERMINOLOGY.md`의 표준어와 폭별 표기(좁은 곳 약어, 넉넉한 곳 풀네임)를 따르고, 새 용어는 자연스러운 한국어 후보와 추천안을 사용자에게 제시해 승인받은 뒤 그 문서의 표에 먼저 추가. 코드 식별자 명명은 `TERMINOLOGY.md` 13절
- 테마: `src/gui/theme.py`, `src/gui/styles/` 변경 시 다크/라이트/시스템 전환과 양쪽 모드의 대비를 확인하고, 색상은 팔레트 토큰만 사용(하드코딩 금지). 아이콘은 임포트 시점 디스크 쓰기 없이 지연 생성
- 테스트: 핵심 로직, GUI 변경 시 `tests/`에 반영. `TemporaryDirectory`와 `_sandbox.py`, 경로 모킹으로 격리하고 실제 `data/*`, 레지스트리, 사용자 미디어 폴더(로컬 표본 포함)는 수정 금지
- 보고: 직접 확인한 경로, 심볼, 도구 출력만 보고하고 미확인 내용은 추정하지 않음

## 4. 주석, 문서화
- 주석 전에 함수 분리, 네이밍, 상수 추출을 우선하고, 객관적 기술만 작성
- 수정 경위, 요청 반영 여부는 쓰지 않음(Git에 위임)
- 코드 흐름 번역 대신 Why(트레이드오프, 호출 순서, OS 제약, 불변식, 예외 사유)만 기술
- 독스트링, 인라인 주석: 한국어 개조식(존댓말, `- ` 불릿, 문장 끝 마침표 없음), 한 줄 요약 기본, 구현 수치 나열 지양(Ruff `D`)
- 코드와 어긋난 낡은 주석은 즉시 수정, 제거
- 문자: 메타 문서(`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CLAUDE.md`, 스킬), 주석, 커밋 메시지에는 한글을 제외한 ASCII 밖 문자(가운뎃점, 화살표, 절 기호, 줄표 등)를 쓰지 않음(나열은 쉼표, 구분은 `/`, 방향은 `->`, 절 참조는 `N절`). 프로그램 코드와 UI 문자열에서도 가운뎃점을 쓰지 않음(나열은 쉼표, 짝을 이루는 이름은 `/`)

## 5. 도메인 불변식, 동기화
- 비파괴 인코딩: 원본은 사후 게이트 통과 전까지 변경하지 않고, 결과는 `.part` 임시 파일을 거쳐 배치하며, 보관, 배치 시 기존 파일 덮어쓰기 금지. 배치에 실패하면 원본과 결과를 모두 보존
- 재처리 방지: 직접 인코딩 결과에는 처리 표식 태그(`video_scripts_encode`)를 기록하고, 표식이 있는 영상은 영상별 직접 지정 없이 인코딩 대상에 넣지 않음
- 설정 키 변경: `src/core/config.py`의 섹션 데이터클래스 기본값, `ClassifierConfig.from_dict()`, `to_dict()`, `AppState`, 설정 대화상자, `tests/core/test_config.py`를 함께 갱신
- 계약 유지: 기본 노트북 템플릿(`from src import *`)은 노트북 템플릿 테스트 기준
- 문서, IDE: `src/` 모듈 추가, 삭제, 개명, GUI 버튼, 단축키, 용어 변경 시 `ARCHITECTURE.md`, `README.md`, 기본 노트북 템플릿, `pyproject.toml`과 `.vscode/settings.json`을 갱신(`tests/tools/test_metadata_sync.py`가 검증)

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
- 링크 폴더 삭제: 정션, 심볼릭 링크가 든 폴더(임시 `git worktree`에 연결한 `.venv` 등)를 지울 때는 링크를 먼저 `rmdir`로 끊고 원본이 그대로인지 확인한 뒤 지움. 링크가 남은 폴더에 `git worktree remove --force`, `rm -rf`, `Remove-Item -Recurse` 같은 재귀 삭제를 쓰지 않음(링크를 따라가 원본 `.venv`, `data/`를 지울 수 있음)
- 코드, 설정, 테스트 변경: 중간에는 대상 `unittest -b`만 실행하고, 완료, 커밋 전 `check.ps1`을 1회 실행(Ruff, BasedPyright, `verify_hygiene.py`, `unittest` 병렬, 상세 출력은 `-VerboseTests`). 오류, 경고, 스킵 없이 통과하면 검증 결과 블록은 생략하고, 실패, 경고, 스킵, 미실행이 있을 때만 상세 보고
- 검증 기록: `check.ps1`은 통과한 작업 트리 내용(추적 파일과 무시되지 않은 새 파일의 트리 해시)과 `.venv` 환경의 지문을 Git 제외 위치에 기록하고, 같은 상태로 다시 실행하면 "이미 검증됨"만 출력하고 곧바로 끝냄. 검토, 분할 커밋 중에도 `check.ps1`을 그대로 실행하며, 다시 돌려야 할 때(간헐 실패 조사, 반복 측정)만 `-Force`를 씀. 완료 보고에는 `check.ps1`이 출력한 트리 해시를 적음
- Ruff 자동 수정: `check.ps1`이 Ruff 안전 수정을 다른 검사보다 먼저 적용하므로 따로 `ruff check --fix`를 실행하지 않음. `Ruff 자동 수정:` 줄에 나온 파일은 다시 읽은 뒤 편집하고 커밋에 포함
- 문서, 스킬만 변경: `verify_hygiene.py`와 `tests.tools.test_metadata_sync` 통과 또는 `pre-commit` 훅으로 갈음
- 스킬: 원본은 `.agents/skills/<name>/SKILL.md`이며 `.claude/skills/`는 사본이므로 직접 수정하지 않고, 원본 수정 후 `python scripts/sync_skills.py --write`로 재생성(불일치는 `tests.tools.test_metadata_sync`가 검출). 스킬을 추가, 삭제, 개명하면 `README.md` 스킬 표와 `ARCHITECTURE.md` 스킬 목록도 갱신

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1
```
