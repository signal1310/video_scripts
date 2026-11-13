# 1. Video Scripts

비디오 아카이빙을 위해 **임의 해상도(360p ~ 1080p, 2K, 4K+) 및 코덱별 픽셀 밀도 기반 화질 분석**, **1초 키프레임 탐색성 보장**, **소형 영상(Footage) 분포 분석 기반 누적 최적화(Cumulative Optimization) 판별**을 수행하는 Python 프레임워크입니다.

## 1.1. 첫 세팅 및 사용
- **의존성 설치** *(외부 `ffmpeg`/`ffprobe` 실행 파일이 시스템 `PATH`에 등록되어 있어야 합니다)*:
  - GUI / CUI 기본 실행: `pip install -r requirements.txt`
  - Jupyter 노트북 실행 포함: `pip install -r requirements-notebook.txt`
  - 개발·정적 검증 도구 포함: `pip install -r requirements-dev.txt`
- 첫 실행 시 실행 폴더에 `.rootdir`과 `data/config.json`이 기본값으로 자동 생성됩니다 ([`.rootdir_example`](.rootdir_example) 참고, 값 변경 시 재시작 없이 반영)
- **데스크톱 GUI 실행**: `python gui_main.py` (또는 `python -m src.gui`)를 실행하여 대시보드, 필터링 테이블, 모의 분류 및 실제 이동/복원 기능을 사용할 수 있습니다.
- **주피터 노트북(CUI) 모드 실행**: `classify.ipynb`([`classify_example.ipynb`](classify_example.ipynb) 참고)에서 셀을 실행하여 분석, 모의 분류(`pseudo_classify_mode`) 및 실제 폴더 이동을 수행할 수 있습니다.

---

# 2. 프로젝트 디렉터리 구조
콘솔(CUI) 출력과 데스크톱 GUI가 동일한 비즈니스 엔진(`VideoClassifier`)을 공유하도록 구성되어 있습니다. 각 모듈의 책임, 의존성 방향 및 실행 흐름에 대한 상세 설명은 [ARCHITECTURE.md](ARCHITECTURE.md)를 참고하세요.

- `src/core/`: 도메인 모델(`models.py`), 설정(`config.py`), 통합 상태 영속화(`app_state.py`) 및 JSONC 스키마 코덱(`state_codec.py`), 도메인 예외(`exceptions.py`), 표준 로깅 설정(`logging_setup.py`), 공용 메시지·툴팁(`messages.py`), `ffprobe` 실행·병렬 프로빙(`prober.py`) 및 JSON 스트림 파싱(`probe_parser.py`), 화질·소형 영상 분포 평가(`evaluator.py`), 예외 조건식 AST 파서(`exception_rules.py`), 파일 IO·배치 이동/롤백(`file_manager.py`), 자연 정렬(`sorting.py`)
- `src/service/`: 인메모리 캐시(`cache.py`) 및 경로별 공유 캐시·분류 트랜잭션을 총괄하는 서비스(`classifier_service.py`)
- `src/cui/`: 콘솔/Jupyter용 테이블 포매터(`cui_printer.py`) 및 다중 조건 정렬 헬퍼(`sorting.py`)
- `src/gui/`: PyQt6 기반 데스크톱 GUI
  - 창 조립·흐름 제어: `main_window.py`, `window_layout.py`, `session_controller.py`, `task_runner.py`, `dialog_coordinator.py`, `worker.py`, `logging_bridge.py`
  - 모델·렌더링·유틸리티: `table_model.py`, `proxy_model.py`, `delegates.py`, `exporters.py`, `constants.py`
  - `widgets/`: UI 위젯(`header_bar.py`, `bento_dashboard.py`, `filter_bar.py`, `video_table_view.py`, `footer_bar.py`, `custom_spinbox.py`, `collapsible_section.py`, `settings_group_card.py`)
  - `dialogs/`: 환경설정·로그 대화상자(`settings_dialog.py`, `settings_sections.py`, `setting_row.py`, `log_dialog.py`)
  - `styles/` & `theme.py`: 테마 감지(`theme.py`), 팔레트(`palettes.py`), QSS 빌더(`stylesheet.py`), SVG 아이콘(`icons.py`)
- `src/classifier.py` & `src/`: 최상위 `VideoClassifier` 통합 엔트리포인트 (`from src import VideoClassifier` 지원)
- `scripts/` & `tests/`: 정적 분석·위생 검사 통합 게이트([`check.ps1`](scripts/check.ps1), [`verify_hygiene.py`](scripts/verify_hygiene.py)) 및 `unittest` 스위트
- `.agents/skills/`: 반복 작업용 프로젝트 에이전트 스킬(`add-setting`, `gui-theme-check`, `perf-benchmark`, `self-review`, `smart-commit`)

---

# 3. 퀄리티(CQ) 기반 통합 분류 기준 및 폴더 설명

원본 해상도와 화면 비율을 100% 그대로 유지하며, 모든 영상은 해상도·FPS·코덱별 고유 최적 비트레이트($B_{\text{opt}}$) 대비 현재 비트레이트 비율($r = B_{\text{curr}} / B_{\text{opt}}$)과 키프레임 간격($\le 2.2$초)을 기준으로 분류됩니다:

1. **`_1_표준인코딩_CQ22`**:
   - **과화질 영상 ($r > 1.20$, 720p H.264 기준 `1,800 kbps` 초과)**: 원본 해상도와 화면 비율을 보존하면서 `CQ 22 + 1초 키프레임`으로 인코딩하여 화질 손실 없이 용량을 대폭 감축합니다.
   - **적정화질($0.80 \le r \le 1.20$) + 키프레임 불량($> 2.2$초)**: 화질을 깎지 않고 `CQ 22 + 1초 키프레임`을 그대로 적용하며, 1초 키프레임화에 따른 자연스러운 용량 증가분을 수용합니다.
2. **`_2_저화질_키프레임교정`**:
   - **저화질($r < 0.80$) + 키프레임 불량($> 2.2$초)**: 이미 `CQ 22`보다 낮게 압축된 저화질 영상이므로, 압축 노이즈 복원으로 인한 가짜 용량 폭증만 방어하고 원본 화질을 유지한 채 키프레임만 `1초`로 교정합니다.
3. **분류되지 않고 루트에 남는 영상 (원본 보존 / 소장 확정)**:
   - 키프레임이 정상($\le 2.2$초)이면서 적정화질 또는 저화질인 영상
   - 전체 라이브러리 대비 절감 기여도가 미미한 소형 영상(`< 30MB`, `IGNORE_SMALL` 대용량 선별 집중 모드 시 자동 보존)

---

# 4. 샤나인코더(ShanaEncoder) 프리셋 연계 (`shana_presets/`)

분류 완료 후 샤나인코더의 프리셋 폴더에 복사하여 대상 폴더별로 1:1 일괄 인코딩을 수행할 수 있습니다.
모든 프리셋은 **원본 해상도 및 화면 비율을 100% 보존**하며, **1초 키프레임 탐색성 확보** 및 **오디오 무손실 복사(`-c:a copy`)**가 기본 적용되어 있습니다:

- **`_1_표준인코딩_CQ22 (NVENC).xml` / `(CPU_x265).xml`**:
  - `_1_표준인코딩_CQ22` 폴더의 영상들을 일괄 인코딩할 때 선택합니다.
  - 고화질 유지(`CQ 22`) 및 1초 키프레임을 적용합니다.
- **`_2_저화질_키프레임교정 (NVENC).xml` / `(CPU_x265).xml`**:
  - `_2_저화질_키프레임교정` 폴더의 영상들을 일괄 인코딩할 때 선택합니다.
  - 저화질 영상의 압축 노이즈로 인한 가짜 용량 뻥튀기를 방지(`CQ 26`)하면서 1초 키프레임만 교정합니다.

---

# 5. 데스크톱 GUI 사용 가이드

1. **폴더 선택 및 스캔 (`HeaderBar`)**
   - 상단 폴더 선택 버튼 또는 **폴더 드래그 앤 드롭**으로 작업 폴더를 지정합니다. (지원 확장자: `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.wmv`, `.flv`, `.ts`, `.m2ts`, `.mts`, `.m4v`, `.3gp`, `.mpg`, `.mpeg`, `.ogv`, `.vob`, `.asf`, `.rmvb`, `.divx`)
   - 스캔 분할 버튼에서 **빠른 스캔**(해상도·비트레이트 고속 조회) 또는 **정밀 분석**(키프레임 간격 실측 포함)을 선택해 실행합니다.
   - 진행 중에는 **분석 중단** 버튼 또는 `Esc` 키로 실행 중인 `ffprobe` 작업을 즉시 중단할 수 있습니다.
2. **인메모리 실시간 튜닝 및 필터링 (`FilterTuningBar`)**
   - `과화질 배수`, `KF 상한`, `소형 기준`, `예외 규칙`을 변경하면 `ffprobe` 재실행 없이 캐시된 메타데이터로 대시보드(`BentoDashboard`)와 테이블을 즉시 재평가합니다.
   - `화질 등급`(전체 / 과화질 / 적정화질 / 저화질), `표시 대상`(전체 / 인코딩 대상 / 원본 보존 / 키프레임 불량), 파일명·코덱 검색으로 결과를 좁혀볼 수 있습니다.
3. **테이블 탐색 및 우클릭 액션 (`VideoTableView`)**
   - 행 **더블클릭** 또는 `Enter` 키로 기본 미디어 플레이어에서 해당 영상을 바로 재생할 수 있습니다.
   - 행 우클릭 메뉴에서 재생, 탐색기에서 위치 열기, 파일명 복사, **선택 항목 예외 키워드 추가/제거**를 지원합니다.
   - 열 헤더 우클릭 메뉴에서 컬럼 표시/숨김 및 기본 열 너비 복원을 할 수 있으며, 창 크기·컬럼 배치는 자동 저장됩니다.
4. **환경설정·실제 이동 및 복원 (`SettingsDialog`, `FooterBar`)**
   - 환경설정(`Ctrl+,`)에서는 테마(시스템 적응형 / 다크 / 라이트), 작업 루트·타겟 폴더명 및 `data/config.json`에 저장되는 22개 세부 기준값을 UI에서 직접 검증·저장할 수 있습니다.
   - **폴더 분류 이동** 실행 시 기존 대상 파일과 이름이 충돌하면 덮어쓰지 않고 건너뛰며, 중간 I/O 오류 시 이전 이동 파일을 루트로 자동 롤백합니다. **분류 복원**은 하위 폴더의 파일을 루트로 복구하고 빈 폴더를 정리합니다.

### 주요 단축키
| 단축키 | 동작 |
| --- | --- |
| `F5` | 현재 선택된 스캔 모드(빠른 스캔 / 정밀 분석) 실행 |
| `Esc` | 실행 중인 스캔·분류 작업 즉시 중단 |
| `Ctrl+F` | 검색 입력창 포커스 |
| `Ctrl+,` | 환경설정 다이얼로그 열기 |
| `Ctrl+L` | 작업 로그 뷰어 열기 |
| `Ctrl+E` | 현재 필터·정렬된 테이블을 CSV로 내보내기 |

---

# 6. 예외 규칙 문법 (키워드 · AST 안전 조건식)

GUI의 **예외 규칙** 입력란에는 쉼표(`,`) 또는 세미콜론(`;`)으로 구분해 여러 규칙을 동시에 등록할 수 있습니다. 일치하는 영상은 화질·키프레임 판정과 무관하게 인코딩에서 제외되어 원본 보존 처리됩니다.

1. **파일명 키워드**: 일반 문자열을 입력하면 대소문자 구분 없이 `v.filename` 부분 일치로 검사합니다.
   - 예: `sample, 무손실, keep_origin`
2. **안전 조건식 (`v.*` 또는 `lambda v: ...`)**: `eval()`/`compile()`을 쓰지 않고 화이트리스트 기반 AST 해석기로 평가합니다.
   - 예: `v.duration < 10, v.fps >= 60`
   - 예: `v.codec == 'hevc' and v.width >= 3840`
   - 예: `lambda v: v.vid_size_MB < 50 or v.filename.startswith('raw_')`
   - **사용 가능한 `VideoProps` 속성 (`v.*`)**: `filename`, `width`, `height`, `rotate_type`, `fps`, `vid_kbps`, `aud_kbps`, `vid_size_MB`, `duration`, `codec`, `keyframe_interval`, `moved_dirname`, `aspect_ratio`
   - **허용 함수 · 메서드**: 내장 함수 `abs`, `min`, `max`, `round`, `len` / 문자열 메서드 `lower`, `upper`, `strip`, `lstrip`, `rstrip`, `startswith`, `endswith`, `isdigit`, `isnumeric`

*(노트북/Python API에서는 `classifier.add_exception_rule(lambda v: ...)`로 일반 Python 조건 함수를 등록할 수 있습니다.)*

---

# 7. 검증 및 테스트

코드 또는 설정 수정 후 아래 통합 검증 스크립트로 **Ruff 린트 → BasedPyright(`strict`) → 저장소 위생·로컬 정보 유출 검사 → 전체 `unittest` 스위트**를 일괄 확인할 수 있습니다:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1
```

새로 복제한 환경에서 커밋 시점 자동 검증(Ruff·BasedPyright·위생·메타 동기화)을 활성화하려면 저장소 루트에서 1회 실행하세요:

```powershell
git config core.hooksPath scripts/hooks
```

## 7.1. 프로젝트 에이전트 스킬 (`.agents/skills/`)

저장소에 포함된 에이전트 스킬 목록입니다(Antigravity가 `.agents/skills/*/SKILL.md`를 자동 인식합니다):

| 스킬 | 호출 상황 | 주요 역할 |
| --- | --- | --- |
| `add-setting` | 계층 설정 데이터클래스 필드 · `ClassifierConfig` 항목 추가/변경 | 코어 설정 메타데이터·`data/config.json` 직렬화·GUI 설정·테스트·문서 수치 동기화 |
| `gui-theme-check` | PyQt6 GUI·위젯·스타일·테마 수정 (`"GUI 점검"`, `"테마 점검"`) | 다크/라이트 적응, 실동작 제어, 문체, offscreen Qt 테스트 점검 |
| `perf-benchmark` | `TODO.md` 성능 최적화 구현·비교 (`"성능 측정"`, `"벤치마크"`, `"최적화 비교"`) | 격리 임시 픽스처 기반 전/후 속도·회귀 검증 |
| `self-review` | 커밋 전 diff 감수 (`"리뷰"`, `"검수"`, `"코드 확인"`) | 3계층 경계·SSOT·개조식 주석·테스트 격리·저장소 위생 점검 |
| `smart-commit` | 논리 단위 커밋 (`"커밋"`, `"commit"`) | 관심사별 분할 스테이징, 위생·`check.ps1` 검증 및 훅 준수 커밋 |

