# TODO: 고도화 및 최적화 로드맵

> **목적**: ① 실행 성능 극대화 ② 영구 캐시 DB ③ GUI 2단계 확장 ④ 코덱 가중치(`C_codec`) 고도화 판별 ⑤ 로깅·예외·i18n·문서화 표준화 설계 스펙

## 1. 실행 성능 최적화

### 1.1. 파이썬 오버헤드 제거
* [x] **문자열 정제·말줄임 벡터화 ([`src/cui/cui_printer.py`](src/cui/cui_printer.py))**:
  * 현황: `sanitize_text()`·`truncate_text()`가 `for ch in text:` 루프마다 `unicodedata` 호출
  * 개선: `text.isascii()` 고속 패스 + `ch.isascii()`·슬라이싱 기반 할당 제거 적용
* [x] **`@dataclass(slots=True)` 도입 ([`src/core/models.py`](src/core/models.py))**:
  * `SmallFootageDistribution`, `VideoProps`, `VideoEvaluation`, `SummaryReport`에 `slots=True` 적용해 `__dict__` 제거·메모리 절감·접근 속도 향상
* [x] **파일시스템 스캔 최적화 ([`src/core/file_manager.py`](src/core/file_manager.py))**:
  * 현황: `os.listdir()` 후 `isfile()`·`stat()` 중복 시스템 콜
  * 개선: `os.scandir(target_root_dir)`로 Windows `FindFirstFile`/`FindNextFile` 1회 호출로 `is_file()`·`is_dir()`·`st_size`·`st_mtime_ns` 일괄 획득

### 1.2. FFmpeg / FFprobe 호출 효율화
* [x] **탐색 필드 제한 (`-show_entries`) 및 기측정 키프레임 사전 스킵**:
  * 현황: [`run_ffprobe()`](src/core/prober.py)가 `-show_format -show_streams` 전체 JSON 직렬화 및 [`populate_keyframes()`](src/core/prober.py) 기측정 항목 워커 제출
  * 개선: `FFPROBE_STREAM_FORMAT_ENTRY_SPEC`(`stream=...:stream_disposition=attached_pic:stream_tags=rotate:stream_side_data=rotation:format=bit_rate,duration,size`)로 필수 필드·하위 섹션만 추출하고 기측정 항목은 워커 풀 제출 전 즉시 스킵
* [x] **병렬 모델 `ThreadPoolExecutor` 전환 ([`src/core/prober.py`](src/core/prober.py))**:
  * 실측: 합성 영상 40개 기준 기존 `ProcessPoolExecutor`(spawn) 대비 시간 비율은 `probe_directory` 0.22, 키프레임 포함 0.40, `populate_keyframes` 0.29이며 결과는 세 경우 모두 동일
  * 개선: 워커 프로세스와 `multiprocessing` 제거, 취소는 `threading.Event` 인자로 전달해 ffprobe 폴링 주기 안에 kill, `chunksize` 튜닝은 스레드 풀에 해당 없음

### 1.3. 키프레임 탐지 가속
* **이전 병목**: `-skip_frame nokey` 프레임 디코딩 경로로 `2.5s -> 5.5s -> 10.0s` 최대 3회 재호출
* **방안 1 — 패킷 플래그(`-show_packets`) 단일 패스 (완료)**:
  ```text
  ffprobe -select_streams v:0 -show_packets -show_entries packet=pts_time,dts_time,flags -read_intervals %+2.5 -of json
  ```
  * `flags`에 `K`가 있는 패킷을 `pts_time`(없으면 `dts_time`)으로 정렬해 간격 평균 산출. 디코딩 없이 패킷만 읽으며, 구간 탐색 단계(2.5s, 5.5s, 10.0s)는 유지
  * 실측(실제 영상 828개, 순차 실행): 총 소요 70.4초 -> 62.2초(약 12% 단축). 5~10배 가속은 재현되지 않음
  * 정확도: 합성 영상 32개 전부 정답(구 방식은 HEVC B-프레임 2건 오답). 실제 영상 표본 22개는 전체 프레임 목록에 같은 단계 탐색을 적용한 값과 모두 일치하며, 구 방식과 결과가 다른 2개는 신 방식이 맞음
* **방안 1-b — 메타데이터와 단일 호출 통합 (완료)**: 메타데이터와 패킷을 `-read_intervals %+10.5` 한 번의 ffprobe 호출로 읽고, 단계 창(2.5/5.5/10.0초)은 메모리에서 적용. 단계별 호출 횟수 0~2회 감소
  * 실측(실제 영상 828개, 앱 경로 `probe_directory` 병렬): 메타데이터+키프레임 11.0초(중앙값) -> 4.6초, 키프레임만 추가 채움 7.0초 -> 4.5초
  * 값 검증: 828개 중 827개 전 필드 동일, 나머지 1개(`Bare Encounters`)는 키프레임 간격 0.0000055초 차이이며 임계값 판정 변화 0건. 원인은 2.5초 구간 호출이 2.2초 키프레임 패킷을 누락한 ffprobe 구간 경계 동작
  * 측정 로직 변경 시 `PROBE_VERSION` 올림 필수(`tests/test_probe_measurement_digest.py`가 고정 입력 해시로 강제)
* **방안 2 — `PyAV`(`av`) 인프로세스 연동**: 외부 `ffprobe.exe` 스폰 비용(파일당 ~20ms) 제거 검토

## 2. 영구 캐시 DB (`sqlite3`)

* **배경·목표**: 인메모리 [`TableCache`](src/service/cache.py)를 영구 저장해 재시작 후에도 재측정 없이 재사용
* **구현 (완료)**:
  * 저장소: [`analysis_store.py`](src/service/analysis_store.py) `data/video_cache.sqlite3`(`WAL`, `WITHOUT ROWID`), 스키마는 `VideoProps` 필드에서 생성. DDL 해시를 `user_version`에 기록하고 열 때 `table_info`와 대조해 불일치 시 테이블 재생성(마이그레이션 없음)
  * 키: 기본 키 `(filename, filesize_bytes, mtime_ns)`, 보조 인덱스 `(filesize_bytes, fingerprint)`. `PROBE_VERSION`이 다른 행은 무시
  * 조회: 파일명 IN 조회 -> 같은 크기 후보의 내용 지문(앞 64KB + 뒤 64KB + 크기, `blake2b`, 수정 시각 미사용) 일치 시 이름 변경으로 보고 행 이동 -> 나머지만 프로빙
  * 장애: DB 잠금, 손상, 경로 오류 시 경고 로그 후 캐시 없이 계속. 손상 파일은 재생성
  * 메뉴: 파일 > `분석 캐시 비우기`(건수 확인 후 삭제, 인메모리 캐시 초기화)
  * 실측(실제 영상 828개): 웜 스캔 0.03초, 프로빙 0회 / 콜드 저장 포함 4.98초(저장 없이 4.68초) / 이름 변경 20개 재사용 프로빙 0회 / 10만 행에서 1,000개 이름 조회 9.5ms, 크기 조회 13ms
  * 한계: 가운데만 바뀐 같은 크기 파일은 지문이 같아 이름 변경 재사용 대상이 될 수 있음(앞, 뒤 청크만 읽는 설계상 수용)

## 3. GUI 도입 현황 및 2단계 과제

### 3.1. 1단계 완료 (`src/gui/`, `gui_main.py`)
* [x] 다크/라이트 테마([`theme.py`](src/gui/theme.py)): 시맨틱 색상·QSS 스타일시트
* [x] KPI 대시보드([`bento_dashboard.py`](src/gui/widgets/bento_dashboard.py)): 수량·현재/예상 용량·순수 절감(Net)·소형 영상 전략 카드
* [x] 인메모리 실시간 튜닝·필터: 임계치·예외 규칙 변경 시 `ffprobe` 없이 즉시 재계산
* [x] 가상화 테이블·델리게이트([`table_model.py`](src/gui/table_model.py), [`proxy_model.py`](src/gui/proxy_model.py), [`delegates.py`](src/gui/delegates.py)): `WindowsNameOrder` 정렬·Pill 뱃지·우클릭 메뉴
* [x] 비동기 워커·취소([`worker.py`](src/gui/worker.py)): `QThread` 래핑 및 `InterruptedError` 즉시 중단

### 3.2. 2단계 확장 및 UX 고도화
* [x] **통합 상태·설정 파일 도입 및 전체 설정 실시간 정합성(기준 정의) 보장**:
  * 필터바([`filter_bar.py`](src/gui/widgets/filter_bar.py)) · 인메모리 `ClassifierConfig` · 통합 설정/상태 JSON([`app_state.py`](src/core/app_state.py) `data/config.json`) · 환경 설정 창([`settings_dialog.py`](src/gui/dialogs/settings_dialog.py)) · CUI(노트북) 조작 간 모든 상세 설정·임계값·예외 규칙의 양방향 실시간 정합성 보장(`self._classifier is None` 상태 포함, 임의 경로의 부분 저장/덮어쓰기 불일치 제거)
  * 최근 폴더·예외 규칙·필터·스캔 모드·테마 등 GUI 현재 상태를 통합 저장하는 별도 설정/상태 파일 도입 (`기본값 복원` 버튼 및 `reset_app_state()` 포함)
  * 단, CUI/노트북 편의성을 위해 `.rootdir` 파일은 앞으로도 유지하며 **`.rootdir`에 기록된 작업 폴더 경로가 통합 설정 파일보다 우선 적용**(`resolve_effective_root_dir`)되도록 계층화 (`app_state.py`, `main_window.py`, `classifier.py` 완료)
* [x] **`.rootdir` 경로 파싱 호환성·예외 방어 강화 ([`src/core/config.py`](src/core/config.py))**:
  * `utf-8-sig`(BOM 제거) 읽기, 앞뒤 따옴표(`"..."`/`'...'`) 및 인라인 `#` 주석 정리, `~`·환경변수 확장(`expanduser`/`expandvars`)으로 수동 편집 및 OS별 경로 입력 오류 방지
* [x] **전체 기능 메뉴바(`QMenuBar`) 도입 ([`src/gui/menu_bar.py`](src/gui/menu_bar.py))**:
  * 파일·편집·보기·분석·분류·도움말 메뉴로 전체 기능 맵과 단축키 카탈로그 노출(`강제 재스캔`, `최근 작업 폴더`, `테마/열 초기화`, `선택 행 액션` 등 수용 및 [`stylesheet.py`](src/gui/styles/stylesheet.py) `QMenuBar` 다크/라이트 스타일 적용)
* [x] **[버그] 창 제목 표시줄(최소화·최대화·닫기) 테마 연동 수정**:
  * 다크/라이트 테마 전환 시 [`MainWindow`](src/gui/main_window.py)·[`SettingsDialog`](src/gui/dialogs/settings_dialog.py)·[`LogDialog`](src/gui/dialogs/log_dialog.py) 등 네이티브 창 제목 표시줄이 시스템 기본색에 고정되지 않도록 Windows DWM(`DWMWA_USE_IMMERSIVE_DARK_MODE`) 속성 연동 ([`theme.py`](src/gui/theme.py) `apply_window_title_bar_theme` 완료)
* [x] **여러 줄 예외 규칙 편집기 ([`src/gui/dialogs/exception_rules_dialog.py`](src/gui/dialogs/exception_rules_dialog.py))**:
  * Windows 환경변수 편집기 스타일의 팝업 편집기와 [`FilterTuningBar`](src/gui/widgets/filter_bar.py) `목록 편집...` 버튼 연동, 규칙 항목 추가·수정·순서 변경·삭제·체크박스 활성/비활성 토글 및 [`UiState.disabled_exception_tokens`](src/core/config.py) 영속화 지원
* [ ] **테이블 선택 행 기반 후속 액션 확장**:
  * [x] 우클릭 메뉴([`video_table_view.py`](src/gui/widgets/video_table_view.py))·`분류` 메뉴바에 **전체 절대 경로 클립보드 복사**(`Ctrl+Shift+C`), **다중 선택 영상 일괄 재생**, 파일명 복사, 예외 규칙 추가/제거 통합
  * [ ] *(보류)* 선택한 파일만 분류 이동 / 제외 후 이동 (부분 이동은 추후 별도 범위로 검토)
* [x] **테이블 열 데이터 타입 기반 동적 필터링 (Notion DB / Excel 스타일)**:
  * [`VideoTableModel`](src/gui/table_model.py) 각 열에 데이터 타입 스키마(`ENUM`, `CATEGORY`, `NUMBER`, `TEXT`)를 정의([`column_filters.py`](src/gui/column_filters.py))하고 [`VideoSortFilterProxyModel`](src/gui/proxy_model.py)의 다중 열 AND 필터링과 연동
  * 열 헤더 필터 버튼([`filter_header_view.py`](src/gui/widgets/filter_header_view.py))·헤더 우클릭 메뉴·데이터 타입별 팝오버([`column_filter_popover.py`](src/gui/widgets/column_filter_popover.py)) 및 상단 필터 바 일괄 해제 배지(`FilterTuningBar`)를 통해 열거형·범주형 복수 체크, 수치 범위·미측정 필터, 문자열 조건 즉시 필터링 구현
* [x] **CUI·GUI 공용 비파괴 미리보기 통합 및 불필요 디스크 I/O 제거**:
  * [`VideoProps.moved_dirname`](src/core/models.py)의 이중 의미(실제 완료 폴더 vs 모의 분류 예정 폴더)를 분리하여, `moved_dirname`은 실제 물리 위치만 보관하고 분류 예정 미리보기는 [`VideoEvaluation.target_dirname`](src/core/models.py)·[`SummaryReport`](src/core/models.py) 단일 경로로 일원화
  * [`CUIPrinter`](src/cui/cui_printer.py)·[`VideoTableModel`](src/gui/table_model.py) 등이 실이동/모의 구분을 위해 반복 호출하던 `file_exists_in()` 같은 불필요한 디스크 조회 제거 (기존 스캔 캐시·시그니처만으로 판별)
  * GUI 내 미사용 `TASK_CLASSIFY_PSEUDO`·`TASK_UNCLASSIFY_PSEUDO`([`worker.py`](src/gui/worker.py), [`task_runner.py`](src/gui/task_runner.py)) 레거시 분기 정리 및 CUI `print()`/드라이런 미리보기 통합
* [ ] **테이블 모델 정규화 및 뷰 확장**:
  * **해상도 가로·세로 정규화(확정)**: [`VideoTableModel`](src/gui/table_model.py)의 단일 `해상도`(`W×H`) 열을 가로(`width`)·세로(`height`) 등 정규화된 수치 열로 개편해 정렬·수치 범위 필터·크롭 변종 처리를 일원화
  * **인코딩 처방 정규화 및 상단 필터바 일괄 정리(확정)**: [`SmartAction`](src/core/models.py)에 결합된 조치(인코딩/보존)·사유(과화질/KF 초과/소형/예외)·옵션을 원자 열로 정규화할 때, 열 헤더 필터와 중복되는 [`FilterTuningBar`](src/gui/widgets/filter_bar.py) 1행 `화질 등급`·`표시 대상` 콤보박스를 일괄 제거하고 검색·일괄 해제 배지·표시 개수 중심으로 단순화
  * **테이블 그룹 접기(아코디언·트리 뷰, 선택)**: 표준 FPS·해상도 등 동일 기준 단위로 소제목 헤더 행을 묶어 펼치기/접기 지원 여부 추후 검토
* [ ] **드롭다운·스캔 분할 메뉴 비차단 팝업 개선(선택)**: [`SplitScanButton`](src/gui/widgets/header_bar.py) 등의 `QMenu.exec`(`Popup` 마우스 그랩)를 [`ColumnFilterPopover`](src/gui/widgets/column_filter_popover.py)와 같은 `Tool` + 이벤트 필터 방식으로 전환해 열린 상태에서 배경 호버·1클릭 전환 지원 여부 검토
* [ ] **테이블 상단 영역 리본형 통째로 접기·펼치기 ([`window_layout.py`](src/gui/window_layout.py), [`main_window.py`](src/gui/main_window.py))**:
  * 현황: 테이블 위쪽(`HeaderBar` · `BentoDashboard` · `FilterTuningBar`)이 항상 고정 높이를 차지해 좁은 화면에서 테이블 행 가시 영역이 제한됨
  * 개선: 오피스 리본 접기 버튼처럼 상단 영역 전체를 한 번에 접고 펼치는 토글 버튼(및 단축키)을 추가하고 접힘 상태를 `UiLayoutState`에 저장해 테이블 세로 영역 확보
* [x] **`작업 폴더로 복원` 버튼 실수 클릭 시 영향 분석 및 확인 가드 ([`footer_bar.py`](src/gui/widgets/footer_bar.py), [`task_runner.py`](src/gui/task_runner.py))**:
  * 현황: `run_real_classify(confirm=True)`와 달리 `run_unclassify()`는 확인 대화상자 없이 클릭 즉시 `TASK_UNCLASSIFY_ALL`을 실행하여 작업 루트 하위 폴더의 영상 파일을 루트로 일괄 이동시킴
  * 개선: 실수 클릭 시 대상 폴더·파일 이동 범위(타 일반 하위 폴더 포함 여부) 영향 정리, 실행 전 확인 대화상자(예: 예정 건수 요약 또는 작업 폴더 내 분류 폴더 한정 복원 검토) 등 오클릭 방지 장치 도입 검토
* [ ] **표기 약어 `BR` → `VBR`(Video BitRate) 일괄 수정**:
  * 대상: [`messages.py`](src/core/messages.py)(`ColumnId.BITRATE`·`OPT_BITRATE`·`RATIO`), [`cui_printer.py`](src/cui/cui_printer.py) 약어 범례(`_CUI_HEADER_LEGEND`), [`sorting.py`](src/cui/sorting.py) CUI 정렬 키(기존 `BR` 별칭 호환 검토), [`config.py`](src/core/config.py)·[`settings_sections.py`](src/gui/dialogs/settings_sections.py) (`BR 비율 소수점 자릿수`), 관련 테스트·문서 일괄 정비
* [ ] **다중 폴더 배치 큐**: 복수 폴더 드래그 앤 드롭 등록 및 순차 분석/분류 *(단일 폴더 전환은 [`main_window.py`](src/gui/main_window.py) 완료)*
* [ ] **내장 FFmpeg 원클릭 인코딩(선택)**: 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`) 기준 백그라운드 직접 인코딩
* [ ] **샘플 인코딩 기반 예측, 보정, 결과 확인 (내장 FFmpeg 인코딩이 선행 조건)**:
  * 공통 기반: 영상 일부 구간(예: 10~20초)을 실제 프리셋으로 인코딩하고 출력 크기와 비트레이트를 측정하는 엔진
  * [ ] **정밀 예측 옵션**: 재인코딩 후보 영상만 샘플 인코딩해 실제 출력 비트레이트로 예상 용량을 계산. 비트레이트 모델이 반영하지 못하는 내용 복잡도(애니메이션, 화면 녹화, 필름 그레인, 스포츠 등) 차이를 보정
  * [ ] **키프레임 증가율 보정**: 같은 구간을 원본 키프레임 간격과 1초 간격으로 각각 인코딩하고 크기 차이로 `keyframe_1s_increase_rate`(기본 6%)를 실측 보정. 같은 방식으로 기준 비트레이트(`base_target_kbps`)를 인코더별(NVENC CQ, x265 CRF)로 보정하는 것도 검토
  * [ ] **인코딩 결과 확인 단계**: 결과가 원본보다 크거나 절약이 기준보다 작으면 원본을 유지하고, 실제 결과를 예측값과 비교해 표시. 외부 인코더(샤나인코더) 출력은 분류 폴더에서 원본과 짝지어 비교하는 방식 검토
* [ ] **테이블 헤더 상태 버전 관리 구현 (`v1.0.0` 배포 전 과제)**:
  * 배포 버전 `v1.0.0` 전까지는 개발 중 헤더 스테이트 버전(`_HEADER_STATE_VERSION = "v1"`) 증가나 구버전 하위 호환을 신경 쓰지 않음
  * `v1.0.0` 정식 배포 전에 최종 테이블 열 구성 기준으로 헤더 상태 버전 관리·마이그레이션 정책을 확정 구현 ([`window_layout.py`](src/gui/window_layout.py))

## 4. 코덱 가중치(`C_codec`) 고도화 판별 시나리오

[`config.py`](src/core/config.py) `DEFAULT_CODEC_WEIGHTS`를 [`VideoEvaluator`](src/core/evaluator.py)에 확장 적용할 6가지 시나리오:

* **시나리오 A — 체감 화질($r_{\text{quality}}$) vs 코덱 전환 절감($r_{\text{saving}}$) 2원화**:
  $$r_{\text{quality}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{src}}}, \quad r_{\text{saving}} = r_{\text{quality}} \times \frac{C_{\text{src}}}{C_{\text{target}}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{target}}}$$
  $r_{\text{quality}} \le 1.20$라도 구형 코덱 이득($C_{\text{src}}/C_{\text{target}} \ge 1.25$)과 적정 이상 화질($r_{\text{quality}} \ge 0.80$) 충족 시 `ENCODE_CODEC_UPGRADE` 분류
  * **반영 (2026-11-16)**: 두 비율을 나눠 저화질 판정은 $r_{	ext{quality}}$에 해당하는 원본 코덱 기준 `비트레이트 비율`, 과화질 판정과 예상 용량은 $r_{	ext{saving}}$에 해당하는 목표 인코딩 코덱 기준 `출력 BR 비율`로 적용. `ENCODE_CODEC_UPGRADE` 분류 자체는 아직 없으므로 별도 과제로 남김
* **시나리오 B — 고효율 코덱(`AV1`, `HEVC`, `VP9`) 세대 손실 방어**: $C_{\text{src}} \le C_{\text{target}}$인 경우 `over_bitrate_ratio`(1.20)에 히스테리시스 마진($+0.15 \sim +0.20$) 가산해 최소 25~30% 절감 보장 시만 재인코딩
* **시나리오 C — 구형 코덱 키프레임 재인코딩 용량 예측($\Delta S$) 정밀화**: $C_{\text{src}} \ge 1.25$ → 최신 코덱($C_{\text{target}} = 0.80 \sim 1.00$) 변환 시 압축 향상분이 I-프레임 오버헤드($+6\%$) 상쇄:
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

## 6. CUI / CLI 사용성 개선 *(보류 · 추후 검토)*

* [ ] *(보류)* **터미널 가로 폭(80~120칸) 대응 컴팩트 뷰 ([`src/cui/cui_printer.py`](src/cui/cui_printer.py))**:
  * `view: Literal["compact", "full"]` 프리셋: `compact` 모드에서 `W`+`H` → `해상도` 통합 및 보조 열(`회전`, `FPS`, `길이`, `예상 용량`)·구분선 생략 검토
  * `shutil.get_terminal_size()` 기반 남은 폭 계산으로 파일명 표시 너비 자동 조절(상한 `config.cui.filename_maxlen`)
* [ ] *(보류)* **터미널 CLI 단일 진입점 (`python -m src` / `python -m src.cui`)**:
  * 표준 `argparse` 기반 CUI 진입 모듈 추가(`--limit`, `--sort`, `--summary-only`, `--full`, `--classify`, `--unclassify`, `data/rootdir.txt` 기본 경로 연동)
  * `python -m src` 실행 시 GUI 기본 구동 또는 `--cui` 서브커맨드 분기 검토
