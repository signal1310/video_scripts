# TODO: 고도화 및 최적화 로드맵

> **목적**: ① 실행 성능 극대화 ② 영구 캐시 DB ③ GUI 2단계 확장 ④ 코덱 가중치(`C_codec`) 고도화 판별 ⑤ 로깅·예외·i18n·문서화 표준화 설계 스펙

## 1. 실행 성능 최적화

### 1.1. 파이썬 오버헤드 제거
* **문자열 정제·말줄임 벡터화 ([`src/cui/cui_printer.py`](src/cui/cui_printer.py))**:
  * 현황: `sanitize_text()`·`truncate_text()`가 `for ch in text:` 루프마다 `unicodedata` 호출
  * 개선: `text.isascii()` 고속 패스 + `re.sub`/`str.translate` 테이블 적용
* **`@dataclass(slots=True)` 도입 ([`src/core/models.py`](src/core/models.py))**:
  * `VideoProps`, `VideoEvaluation`에 `slots=True` 적용해 `__dict__` 제거·메모리 절감·접근 속도 향상(~25~35%)
* **파일시스템 스캔 최적화 ([`src/core/file_manager.py`](src/core/file_manager.py))**:
  * 현황: `os.listdir()` 후 `isfile()`·`stat()` 중복 시스템 콜
  * 개선: `os.scandir(target_root_dir)`로 Windows `FindFirstFile`/`FindNextFile` 1회 호출로 `is_file()`·`st_size`·`st_mtime_ns` 일괄 획득

### 1.2. FFmpeg / FFprobe 호출 효율화
* **탐색 필드 제한 (`-show_entries`)**:
  * 현황: [`run_ffprobe()`](src/core/prober.py)가 `-show_format -show_streams` 전체 JSON 직렬화
  * 개선: 필수 필드만 지정(`stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,duration,disposition,side_data_list:stream_tags=rotate:format=bit_rate,duration,size`)해 파이프 전송·파싱 단축
* **병렬 모델·`chunksize` 튜닝 ([`src/core/prober.py`](src/core/prober.py))**:
  * `ffprobe`는 외부 바이너리 I/O라 GIL 영향이 작음 → Windows `spawn` 비용 큰 `ProcessPoolExecutor`와 `ThreadPoolExecutor`(스폰 0ms) 실측 비교 후 전환 또는 `chunksize` 최적화

### 1.3. 키프레임 탐지 가속
* **현황 병목**: [`extract_keyframe_interval()`](src/core/prober.py)가 `-skip_frame nokey -show_entries frame=pts_time` 디코더 경로로 `2.5s → 5.5s → 10.0s` 최대 3회 재호출
* **방안 1 — 패킷 플래그(`-show_packets`) 단일 패스 (5~10배 가속)**:
  ```text
  ffprobe -select_streams v:0 -read_intervals %+10.5 -show_packets -show_entries packet=pts_time,flags -of json
  ```
  * 컨테이너 인덱스(MP4 `stss`, MKV `Cues`)에서 `flags`가 `"K"` 시작인 `pts_time`만 추출 → 디코딩 0%, 1회 호출(1~3ms)로 0~10초 키프레임 간격 획득
* **방안 2 — `PyAV`(`av`) 인프로세스 연동**: 외부 `ffprobe.exe` 스폰 비용(파일당 ~20ms) 제거 검토

## 2. 영구 캐시 DB (`sqlite3`)

* **배경·목표**: 인메모리 [`TableCache`](src/service/cache.py)를 영구 저장해 재시작 후에도 0.01초 내 로드
* **설계**:
  * 엔진·경로: 내장 `sqlite3`(`WAL` 모드), 프로젝트 루트/지정 경로 `.video_cache.sqlite3`([`.gitignore`](.gitignore)에 `*.sqlite3*`, `.video_cache*` 등록 필수)
  * 스키마 (`video_metadata_cache`): `file_hash_key` (`TEXT PRIMARY KEY`), `filename` (`TEXT`), `filesize_bytes` (`INTEGER`), `mtime_ns` (`INTEGER`), `width`, `height`, `rotate_type`, `fps`, `vid_kbps`, `aud_kbps`, `vid_size_mb`, `duration`, `codec`, `keyframe_interval` (`REAL NULL` — 키프레임 후속 분석 시 해당 컬럼만 `UPDATE`)
  * 2단 캐시 히트: ① `(filename, filesize_bytes, mtime_ns)` 일치 즉시 로드 → ② 이름 변경/이동 시 앞 64KB + 뒤 64KB + 크기 기반 `blake2b` 해시 일치 재사용

## 3. GUI 도입 현황 및 2단계 과제

### 3.1. 1단계 완료 (`src/gui/`, `gui_main.py`)
* [x] 다크/라이트 테마([`theme.py`](src/gui/theme.py)): 시맨틱 색상·QSS 스타일시트
* [x] KPI 대시보드([`bento_dashboard.py`](src/gui/widgets/bento_dashboard.py)): 수량·현재/예상 용량·순수 절감(Net)·소형 영상 전략 카드
* [x] 인메모리 실시간 튜닝·필터: 임계치·예외 규칙 변경 시 `ffprobe` 없이 즉시 재계산
* [x] 가상화 테이블·델리게이트([`table_model.py`](src/gui/table_model.py), [`proxy_model.py`](src/gui/proxy_model.py), [`delegates.py`](src/gui/delegates.py)): `WindowsNameOrder` 정렬·Pill 뱃지·우클릭 메뉴
* [x] 비동기 워커·취소([`worker.py`](src/gui/worker.py)): `QThread` 래핑 및 `InterruptedError` 즉시 중단

### 3.2. 2단계 확장 및 UX 고도화
* [ ] **통합 상태·설정 파일 도입 및 전체 설정 실시간 정합성(SSOT) 보장**:
  * 필터바([`filter_bar.py`](src/gui/widgets/filter_bar.py)) · 인메모리 `ClassifierConfig` · `.env` / 통합 설정 파일 · 환경설정 창([`settings_dialog.py`](src/gui/dialogs/settings_dialog.py)) · CUI(노트북) 조작 간 모든 상세 설정·임계값·예외 규칙의 양방향 실시간 정합성 보장(`self._classifier is None` 상태 포함, 임의 경로의 부분 저장/덮어쓰기 불일치 제거)
  * 최근 폴더·예외 규칙 등 GUI 현재 상태를 통합 저장하는 별도 설정/상태 파일 도입 (`기본값 복원` 포함)
  * 단, CUI/노트북 편의성을 위해 `.rootdir` 파일은 앞으로도 유지하며 **`.rootdir`에 기록된 작업 폴더 경로가 통합 설정 파일보다 우선 적용**되도록 계층화
* [ ] **전체 기능 메뉴바(`QMenuBar`) 도입**:
  * 파일·편집·보기·분석·분류·도움말 메뉴로 전체 기능 맵과 단축키 카탈로그 노출(`강제 재스캔`, `최근 작업 폴더`, `테마/열 초기화`, `선택 행 액션` 등 수용 및 [`stylesheet.py`](src/gui/styles/stylesheet.py) `QMenuBar` 다크/라이트 스타일 적용)
* [x] **[버그] 창 제목 표시줄(최소화·최대화·닫기) 테마 연동 수정**:
  * 다크/라이트 테마 전환 시 [`MainWindow`](src/gui/main_window.py)·[`SettingsDialog`](src/gui/dialogs/settings_dialog.py)·[`LogDialog`](src/gui/dialogs/log_dialog.py) 등 네이티브 창 제목 표시줄이 시스템 기본색에 고정되지 않도록 Windows DWM(`DWMWA_USE_IMMERSIVE_DARK_MODE`) 속성 연동 ([`theme.py`](src/gui/theme.py) `apply_window_title_bar_theme` 완료)
* [ ] **여러 줄 예외 규칙 편집기**:
  * 현재 단일 텍스트 입력([`edit_exceptions`](src/gui/widgets/filter_bar.py)) 대신, Windows 환경변수 편집기처럼 규칙 항목을 여러 줄(행 리스트) 단위로 추가·수정·삭제·토글할 수 있는 UI로 개편
* [ ] **테이블 선택 행 기반 후속 액션 확장**:
  * 우클릭 메뉴([`video_table_view.py`](src/gui/widgets/video_table_view.py))에 전체 절대 경로 복사, 선택한 파일만 분류 이동 / 제외 후 이동, 다중 선택 재생 등 선택 행 단위 작업 추가
* [ ] **테이블 열 데이터 타입 기반 동적 필터링 (Notion DB / Excel 스타일)**:
  * 현재 고정 드롭다운(`표시 대상:` [`filter_bar.py`](src/gui/widgets/filter_bar.py))을 프로토타입에서 발전시켜, [`VideoTableModel`](src/gui/table_model.py) 각 열에 데이터 타입 스키마(열거형·범주·수치·문자열)를 정의하고 [`VideoSortFilterProxyModel`](src/gui/proxy_model.py)과 연동
  * 열거형/범주형 열(`분류 상태`, `코덱`, `화질 등급`, `인코딩 처방`, 예정 분류 폴더 `_1_`·`_2_` 등)에 대해 열 헤더 필터 버튼/팝오버로 복수 값을 체크·선택해 즉시 필터링하는 UI 도입
* [ ] **CUI·GUI 공용 비파괴 미리보기 통합 및 불필요 디스크 I/O 제거**:
  * [`VideoProps.moved_dirname`](src/core/models.py)의 이중 의미(실제 이동 완료 폴더 vs 모의 분류 예정 폴더)를 분리하여, `moved_dirname`은 실제 물리 위치만 보관하고 분류 예정 미리보기는 [`VideoEvaluation.target_dirname`](src/core/models.py)·[`SummaryReport`](src/core/models.py) 단일 경로로 일원화
  * [`CUIPrinter`](src/cui/cui_printer.py)·[`VideoTableModel`](src/gui/table_model.py) 등이 실이동/모의 구분을 위해 반복 호출하던 `file_exists_in()` 같은 불필요한 디스크 조회 제거 (기존 스캔 캐시·시그니처만으로 판별)
  * GUI 내 미사용 `TASK_CLASSIFY_PSEUDO`·`TASK_UNCLASSIFY_PSEUDO`([`worker.py`](src/gui/worker.py), [`task_runner.py`](src/gui/task_runner.py)) 레거시 분기 정리 및 CUI `print()`/드라이런 미리보기 통합
* [ ] **다중 폴더 배치 큐**: 복수 폴더 드래그 앤 드롭 등록 및 순차 분석/분류 *(단일 폴더 전환은 [`main_window.py`](src/gui/main_window.py) 완료)*
* [ ] **내장 FFmpeg 원클릭 인코딩(선택)**: 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`) 기준 백그라운드 직접 인코딩

## 4. 코덱 가중치(`C_codec`) 고도화 판별 시나리오

[`config.py`](src/core/config.py) `DEFAULT_CODEC_WEIGHTS`를 [`VideoEvaluator`](src/core/evaluator.py)에 확장 적용할 6가지 시나리오:

* **시나리오 A — 체감 화질($r_{\text{quality}}$) vs 코덱 전환 절감($r_{\text{saving}}$) 2원화**:
  $$r_{\text{quality}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{src}}}, \quad r_{\text{saving}} = r_{\text{quality}} \times \frac{C_{\text{src}}}{C_{\text{target}}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{target}}}$$
  $r_{\text{quality}} \le 1.20$라도 구형 코덱 이득($C_{\text{src}}/C_{\text{target}} \ge 1.25$)과 적정 이상 화질($r_{\text{quality}} \ge 0.80$) 충족 시 `ENCODE_CODEC_UPGRADE` 분류
* **시나리오 B — 고효율 코덱(`AV1`, `HEVC`, `VP9`) 세대 손실 방어**: $C_{\text{src}} \le C_{\text{target}}$인 경우 `over_bitrate_ratio`(1.20)에 히스테리시스 마진($+0.15 \sim +0.20$) 가산해 최소 25~30% 절감 보장 시만 재인코딩
* **시나리오 C — 구형 코덱 키프레임 교정 용량 예측($\Delta S$) 정밀화**: $C_{\text{src}} \ge 1.25$ → 최신 코덱($C_{\text{target}} = 0.80 \sim 1.00$) 변환 시 압축 향상분이 I-프레임 오버헤드($+6\%$) 상쇄:
  $$S_{\text{after}} = S_{\text{orig}} \times \left(\frac{C_{\text{target}}}{C_{\text{src}}}\right) \times \left(1 + \text{overhead}_{\text{kf}}\right)$$
* **시나리오 D — 블록 노이즈 증폭 방어 라우팅**: $C_{\text{src}} \ge 1.35$(`mpeg2video`, `mpeg4`, `flv1` 등) & $r_{\text{quality}} < 0.90$ → `_2_저화질_키프레임교정`
* **시나리오 E — Intra-Frame 전용(`mjpeg`, `prores`, `dnxhd`, $C_{\text{codec}} = 2.50$) 최우선 감축**: 키프레임 정상(`0.033s`)이라도 $C_{\text{src}} \ge 2.0$ 포착해 60%+ 용량 감축
* **시나리오 F — 모바일/VR 탐색 호환성 강제 변환**: HW 디코딩 미지원 레거시 코덱($C_{\text{src}} \ge 1.25$, `wmv3`, `vc1`, `rv40`, `theora`, `flv1` 등) 표준 코덱+1초 키프레임 현대화 옵션

## 5. 로깅 · 예외 · 다국어(i18n) · 문체/독스트링 표준

### 5.1. 표준 `logging` 일원화
* **방침**: 외부 패키지(`loguru`, `structlog`) 없이 표준 `logging` 단일화
* [x] `on_log`·수동 태그 제거 및 [`logging_setup.py`](src/core/logging_setup.py) `logger.info/warning/error/exception(...)` 통합
* [x] 진입점별 핸들러 분리: CUI `_DynamicStdoutHandler`(`ensure_console_logging`), GUI [`GuiLogBridge`](src/gui/logging_bridge.py) → [`LogDialog`](src/gui/dialogs/log_dialog.py) *(선택적 `RotatingFileHandler` 추후 가능)*
* [x] 출력 경계 유지: [`cui_printer.py`](src/cui/cui_printer.py) 표·`\r` 진행률(`on_progress`)은 UI 출력 유지, `spawn` 자식 프로세스는 메인 프로세스로 결과/예외 전파 후 로깅

### 5.2. 도메인 예외 · 경계 핸들러
* [x] [`src/core/exceptions.py`](src/core/exceptions.py): 표준 예외(`FileNotFoundError`, `ValueError`, `RuntimeError`, `TimeoutError`, `InterruptedError`, `AttributeError`, `TypeError`) 상속 + `ErrorCode` Enum 정의·전파
* [x] 경계 핸들러: GUI([`ClassifierWorker`](src/gui/worker.py), [`TaskRunner.handle_failed`](src/gui/task_runner.py), [`DialogCoordinator`](src/gui/dialog_coordinator.py))·CUI에서 `format_user_error()`로 `ErrorCode` 기반 안내·힌트 매핑 및 상세 스택 `logging` 기록

### 5.3. 다국어(i18n) 대비 카탈로그 *(추후 진행)*
* [ ] [`models.py`](src/core/models.py)(`QualityZone`, `SmartAction`, `FootageStrategy`)·[`file_manager.py`](src/core/file_manager.py) 경고 등에서 한국어 직결 제거, 식별자/코드+파라미터 반환
* [ ] GUI(`PyQt6`)·CUI·Jupyter 겸용 순수 파이썬 `gettext(.po/.mo)` 또는 `locales/*.json` + `t(key, **params)`로 [`src/core/messages.py`](src/core/messages.py) 확장
* [ ] `unittest`에서 기준 언어(`ko`)와 추가 언어팩 간 키·플레이스홀더(`{count}`, `{kbps}` 등) 일치 검증

### 5.4. 문체(Tone & Voice) · 분량 기준
* [x] **GUI**: 버튼·열 헤더·배지는 간결한 명사구, 툴팁·설정 설명·대화상자는 일관된 존댓말
* [x] **CUI**: 테이블 헤더·진행 표시는 컴팩트 축약형([`src/cui/sorting.py`](src/cui/sorting.py)), 리포트 설명·안내·경고·오류는 존댓말 통일
* [ ] `gui.label.*`, `cui.header.*` vs `tooltip.*`, `desc.*`, `error.*`, `cui.report.*` 키 네이밍 분리 *(5.3 도입 시 전환)*

### 5.5. 독스트링(`"""..."""`) 통일 · 린트 자동화
* [x] PEP 257 / Google 기반 한국어 개조식 독스트링·주석 표준 확정 및 [`AGENTS.md`](AGENTS.md) 동기화
* [x] 구현 수치 나열 제거, 역할·계약(입력·반환·예외) 중심 경량화
* [x] [`pyproject.toml`](pyproject.toml) Ruff `D`(`convention = "google"`) 활성화로 서식 자동 검증
