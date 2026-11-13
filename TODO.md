# TODO: 향후 고도화 및 최적화 로드맵 ( Roadmap & Technical Spec )

> **문서 목적**: 퀄리티(`CQ`) 기반 비디오 분류 아키텍처의 **① 실행 성능 극대화**, **② 영구 데이터베이스(캐시) 구축**, **③ GUI 도입 준비**, **④ 코덱 가중치(`C_codec`) 고도화 판별 시나리오**, **⑤ 로깅·예외·다국어(i18n)·문서화 표준화**를 상세히 기록한 기술 설계 문서.

---

## 1. 실행 성능 향상 및 최적화 (Performance & Optimization)

### 1.1. 파이썬 레벨 실행 성능 악화 요인 제거 (Python Overhead Elimination)
* **문자열 정제·말줄임 루프의 C-레벨 벡터화 ([`src/cui/cui_printer.py`](src/cui/cui_printer.py))**:
  * 현재 `sanitize_text()`와 `truncate_text()`는 파이썬 `for ch in text:` 루프를 돌며 글자마다 `unicodedata`를 호출.
  * **개선**: ASCII 고속 패스(`text.isascii()`일 때 슬라이싱 즉시 반환) 및 정규식(`re.sub`) / `str.translate` 매핑 테이블을 적용하여 파이썬 루프 오버헤드를 제거.
* **`@dataclass(slots=True)` 도입 ([`src/core/models.py`](src/core/models.py))**:
  * `VideoProps`, `VideoEvaluation` 데이터클래스에 `slots=True`를 적용하여 인스턴스당 `__dict__` 생성을 없애고 메모리 사용량 절감 및 속성 접근 속도를 약 25~35% 향상.
* **파일시스템 스캔 최적화 ([`src/core/file_manager.py`](src/core/file_manager.py))**:
  * 현재 `os.listdir()` 이후 개별 파일마다 `os.path.isfile()`과 `os.stat()`을 별도 호출하여 시스템 콜이 중복 발생.
  * **개선**: `os.scandir(target_root_dir)`를 사용하면 Windows의 `FindFirstFile`/`FindNextFile` 커널 호출 단 1회만으로 파일 여부(`entry.is_file()`), 파일 크기(`entry.stat().st_size`), 수정 시각(`st_mtime_ns`)을 추가 디스크 I/O 없이 한 번에 가져올 수 있음.

### 1.2. FFmpeg / FFprobe 호출 효율 극대화
* **불필요한 메타데이터 탐색 제한 (`-show_entries` 최적화)**:
  * 현재 [`run_ffprobe()`](src/core/prober.py)는 `subprocess`로 `ffprobe -show_format -show_streams`를 호출해 컨테이너의 모든 섹션과 챕터, 불필요한 태그까지 전부 JSON으로 직렬화.
  * **개선**: 필요한 필드(`stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,duration,disposition,side_data_list:stream_tags=rotate:format=bit_rate,duration,size`)만 지정하여 JSON 파이프 전송량과 파싱 시간을 단축.
* **멀티프로세싱 vs 멀티스레딩 적응형 분기 및 `chunksize` 튜닝 ([`src/core/prober.py`](src/core/prober.py))**:
  * Windows에서 `ProcessPoolExecutor`는 워커 프로세스 생성(Spawn) 비용이 큼.
  * `ffprobe`는 외부 네이티브 바이너리 I/O 작업이므로 GIL(Global Interpreter Lock)의 영향을 거의 받지 않음. 따라서 **`ThreadPoolExecutor`(워커 스폰 비용 0ms)와 `ProcessPoolExecutor`의 실측 벤치마크를 비교**하여 오버헤드가 없는 쪽으로 전환하거나, 대량 작업 시 `chunksize`를 최적 배분.

### 1.3. 키프레임 탐지 성능 대폭 강화 (Keyframe Detection Acceleration)
* **현재 병목 원인**:
  * [`extract_keyframe_interval()`](src/core/prober.py)에서 `-skip_frame nokey -show_entries frame=pts_time` 옵션을 사용하며 `2.5s` $\to$ `5.5s` $\to$ `10.0s`까지 최대 3번 `ffprobe`를 재호출.
  * `-show_frames`는 비디오 디코더를 구동하여 프레임을 해석하므로 무겁고, 키프레임 간격이 긴 영상일수록 프로세스를 최대 3번 띄우게 됨.
* **개선 방안 1 — 디먹서 패킷 플래그(`-show_packets`) 단일 패스 탐색 (5~10배 가속)**:
  * 비디오 디코더를 아예 거치지 않고 컨테이너 헤더의 인덱스(MP4 `stss` 박스, MKV `Cues`)에서 패킷 플래그만 읽는 방식으로 변경:
    ```text
    ffprobe -select_streams v:0 -read_intervals %+10.5 -show_packets -show_entries packet=pts_time,flags -of json
    ```
  * 패킷의 `flags`가 `"K"`(Keyframe)로 시작하는 항목의 `pts_time`만 추출하면, **디코딩 비용 0%**로 **단 1회 호출** 만에 0~10초 구간의 키프레임 간격을 1~3ms 내에 즉시 획득 가능.
* **개선 방안 2 — `PyAV`(`av` 라이브러리) 인프로세스(In-Process) 도입 검토**:
  * `ffprobe.exe` 외부 프로세스를 매번 띄우는 대신 FFmpeg C 라이브러리에 직접 바인딩된 `PyAV`를 사용하면 Windows 프로세스 생성 오버헤드(파일당 ~20ms) 자체를 완전히 제거 가능.

---

## 2. 영구 데이터베이스 구축 (Persistent Metadata & Keyframe DB)

### 2.1. 목적 및 배경
* 현재 [`TableCache`](src/service/cache.py)는 인메모리(In-Memory) 방식이므로 Jupyter 커널이나 프로그램을 재시작하면 수백 개의 영상을 다시 `ffprobe`로 분석해야 함.
* **목표**: 한 번 분석된 영상은 파일이 변경되기 전까지 영구 보존하여, 프로그램 재시작 후에도 **0.01초 만에 즉시 테이블과 리포트를 띄우는 영구 캐시 DB**를 구축.

### 2.2. 데이터베이스 아키텍처 설계
* **엔진**: Python 내장 `sqlite3` (`WAL` 저널 모드 활성화로 동시 읽기/쓰기 성능 확보)
* **DB 파일 경로 및 Git 격리**:
  * 프로젝트 루트(또는 `.env` 지정 경로)에 `.video_cache.sqlite3` 파일로 생성
  * **`.gitignore` 필수 등록**: `*.sqlite3*`, `.video_cache*` 패턴을 [`.gitignore`](.gitignore)에 등록하여 버전 관리에서 영구 제외
* **테이블 스키마 (`video_metadata_cache`)**:
  * `file_hash_key` (`TEXT PRIMARY KEY`): 파일 식별 키 (고속 부분 해시 또는 경로+크기+수정시각 조합)
  * `filename` (`TEXT`), `filesize_bytes` (`INTEGER`), `mtime_ns` (`INTEGER`)
  * `width`, `height`, `rotate_type`, `fps`, `vid_kbps`, `aud_kbps`, `vid_size_mb`, `duration`, `codec`
  * `keyframe_interval` (`REAL NULL`): 최초에 키프레임 미포함 모드로 스캔했더라도, 추후 키프레임 분석 시 해당 컬럼만 `UPDATE`하여 영구 보존
* **고속 변경 감지 및 파일 이동 추적 (Fast Fingerprint)**:
  * 1차 히트 판정: `(filename, filesize_bytes, mtime_ns)`가 일치하면 `ffprobe` 호출 없이 DB에서 즉시 로드.
  * 2차 히트 판정 (파일명 변경 또는 하위 폴더 이동 시): 파일의 앞 64KB + 뒤 64KB + 파일 크기를 조합한 초고속 해시(`blake2b`)가 일치하면, 파일명이 바뀌었더라도 기존 분석 결과를 그대로 재사용!

---

## 3. GUI 도입 현황 및 2단계 확장 과제 (GUI Architecture)

### 3.1. 1단계 구현 완료 사항 (`src/gui/`, `gui_main.py`)
* [x] **다크/라이트 테마(`theme.py`)**: 시맨틱 상태 색상 및 QSS 스타일시트 적용.
* [x] **KPI 대시보드([`bento_dashboard.py`](src/gui/widgets/bento_dashboard.py))**: 전체 수량, 현재/예상 용량, 최종 순수 절감량(Net), 소형 영상 최적화 전략 카드 배치.
* [x] **실시간 파라미터 튜닝 및 검색/필터 바**: 과화질 배수, 키프레임 허용 상한, 소형 영상 기준, 예외 키워드 변경 시 `ffprobe` 재호출 없이 인메모리 즉시 재계산.
* [x] **가상화 테이블 및 뱃지 렌더러([`table_model.py`](src/gui/table_model.py), [`proxy_model.py`](src/gui/proxy_model.py), [`delegates.py`](src/gui/delegates.py))**: `WindowsNameOrder` 자연 정렬, 칼럼별 수치 정렬, 둥근 필(Pill) 뱃지 렌더링, 우클릭 컨텍스트 메뉴 지원.
* [x] **비동기 백그라운드 워커 및 작업 중단([`worker.py`](src/gui/worker.py))**: `VideoClassifier` 래핑 `QThread` 구동 및 `InterruptedError` 기반 즉시 중단 처리.

### 3.2. 향후 2단계 GUI 고도화 과제
* [ ] **다중 폴더 배치(Batch) 큐** *(단일 폴더 드래그 앤 드롭 전환은 [`main_window.py`](src/gui/main_window.py)에 구현됨)*:
  * 탐색기에서 여러 작업 폴더를 드래그 앤 드롭하여 큐에 등록하고 순차적으로 분석·분류하는 배치 러너 인터페이스 마련.
* [ ] **원클릭 내장 FFmpeg 인코딩 실행 (선택 사항)**:
  * 샤나인코더를 별도로 열지 않고 GUI 내부에서 선택된 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`)으로 직접 백그라운드 인코딩을 수행하는 기능.

---

## 4. 코덱 가중치(`C_codec`) 기반 고도화 판별 시나리오

현재 [`src/core/config.py`](src/core/config.py)의 `DEFAULT_CODEC_WEIGHTS`(`C_codec`) 상수값을 활용하여 추후 [`VideoEvaluator`](src/core/evaluator.py)에 도입할 수 있는 6가지 판별 시나리오.

### 시나리오 A: 「체감 화질 지수($r_{\text{quality}}$)」와 「코덱 변환 절감 지수($r_{\text{saving}}$)」의 2원화
* **원본 체감 화질 지수 ($r_{\text{quality}}$)**:
  $$r_{\text{quality}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{src}}}$$
  * 원본의 실제 화질 등급(`과화질` / `적정화질` / `저화질`) 판별용.
* **타겟 코덱 전환 시 기대 비트레이트 배수 ($r_{\text{saving}}$)**:
  $$r_{\text{saving}} = r_{\text{quality}} \times \left(\frac{C_{\text{src}}}{C_{\text{target}}}\right) = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{target}}}$$
  * 설령 $r_{\text{quality}} \le 1.20$이더라도, **구형 코덱($C_{\text{src}} > C_{\text{target}}$)이라서 최신 코덱 변환만으로 얻을 수 있는 압축 효율 이득($\frac{C_{\text{src}}}{C_{\text{target}}} \ge 1.25$)이 크고 $r_{\text{quality}} \ge 0.80$이라면**, 코덱 업그레이드를 통한 용량 감축 대상(`ENCODE_CODEC_UPGRADE`)으로 분류.

### 시나리오 B: 초고효율 코덱(`AV1`, `HEVC`)의 "세대 손실(Generation Loss) 방지" 마진 부여
* $C_{\text{src}} \le C_{\text{target}}$인 고효율 코덱 영상(`AV1`, `HEVC`, `VP9`)에 대해서는 과화질 판별 임계치(`over_bitrate_ratio = 1.20`)에 **세대 손실 방어 마진(Hysteresis, 예: $+0.15 \sim +0.20$)**을 더해, 최소 25~30% 이상의 확실한 용량 감축이 보장될 때만 재인코딩하도록 보호.

### 시나리오 C: 구형 코덱의 "1초 키프레임 교정 시 용량 변화 예측($\Delta S$)" 정밀화
* 원본이 구형 코덱($C_{\text{src}} \ge 1.25$)인 영상은 1초 키프레임 교정을 위해 최신 코덱($C_{\text{target}} = 0.80 \sim 1.00$)으로 재인코딩할 때 **코덱 압축 효율 향상분($\frac{C_{\text{target}}}{C_{\text{src}}}$)이 I-프레임 증가 오버헤드($+6\%$)를 상쇄하고도 남는 점**을 반영:
  $$S_{\text{after}} = S_{\text{orig}} \times \left(\frac{C_{\text{target}}}{C_{\text{src}}}\right) \times \left(1 + \text{overhead}_{\text{kf}}\right)$$

### 시나리오 D: 구형 코덱의 블록 노이즈(깍두기) 증폭 방어 라우팅
* $C_{\text{src}} \ge 1.35$ (`mpeg2video`, `mpeg4`, `flv1` 등)이면서 체감 화질 지수 $r_{\text{quality}} < 0.90$인 구형 코덱 영상은 블록 노이즈 뻥튀기를 막기 위해 `_2_저화질_키프레임교정`으로 라우팅.

### 시나리오 E: Intra-Frame 전용 코덱(`mjpeg`, `prores`, `dnxhd`, $C_{\text{codec}} = 2.50$) 최우선 감축
* 모든 프레임이 I-Frame이라 `keyframe_interval`은 정상(`0.033s`)으로 나오지만 프레임 간 압축이 없어 용량이 비정상적으로 큰 Intra 코덱($C_{\text{src}} \ge 2.0$)을 포착하여 60% 이상 용량을 감축.

### 시나리오 F: 하드웨어 디코더 및 VR/모바일 탐색(Scrubbing) 호환성 강제 변환 모드
* Meta Quest 3, 모바일 등에서 하드웨어 디코딩 가속이 지원되지 않아 탐색 렉을 유발하는 레거시 코덱($C_{\text{src}} \ge 1.25$, `wmv3`, `vc1`, `rv40`, `theora`, `flv1` 등)을 표준 코덱 + 1초 키프레임으로 현대화하는 선택적 옵션을 제공.

---

## 5. 로깅 · 예외 처리 · 다국어(i18n) 및 문체/독스트링 표준화

### 5.1. 표준 `logging` 기반 로깅 일원화
* **방침**: 외부 로깅 라이브러리(`loguru`, `structlog` 등) 추가 대신 파이썬 표준 라이브러리 `logging`으로 일원화.
* [x] **수동 태그·콜백 혼재 해소**: `on_log` 콜백 및 수동 태그 조립을 제거하고 [`logging_setup.py`](src/core/logging_setup.py) 기반 `logger.info/warning/error/exception(...)` 호출로 통합.
* [x] **진입점별 핸들러 분리**: CUI/노트북은 `_DynamicStdoutHandler`(`ensure_console_logging`), GUI는 [`GuiLogBridge`](src/gui/logging_bridge.py) Qt 시그널 브리지([`LogDialog`](src/gui/dialogs/log_dialog.py) 연결)로 출력 라우팅 *(선택적 `RotatingFileHandler`는 필요 시 추가 예정)*.
* [x] **화면 출력과 로그 경계 유지**: [`cui_printer.py`](src/cui/cui_printer.py)의 표/리포트 출력과 `\r` 진행률 bar(`on_progress`)는 UI 출력으로 유지하고, `ProcessPoolExecutor`(`spawn`) 자식 프로세스는 메인 프로세스로 결과/예외를 올려 로깅.

### 5.2. 에러 코드 기반 도메인 예외 및 경계(Boundary) 예외 핸들러
* [x] **표준 예외 계층 유지형 도메인 예외 도입**: [`src/core/exceptions.py`](src/core/exceptions.py)에 표준 예외(`FileNotFoundError`, `ValueError`, `RuntimeError`, `TimeoutError`, `InterruptedError`, `AttributeError`, `TypeError`)를 상속한 도메인 예외와 `ErrorCode` Enum 정의 및 전파.
* [x] **경계(Boundary) 핸들러 통합**: GUI([`ClassifierWorker`](src/gui/worker.py), [`TaskRunner.handle_failed`](src/gui/task_runner.py), [`DialogCoordinator`](src/gui/dialog_coordinator.py)) 및 CUI 경계에서 `format_user_error()`로 `ErrorCode` 기반 사용자 안내와 해결 힌트를 매핑하고, 예기치 않은 예외의 상세 스택은 `logging`에 기록.

### 5.3. 다국어(i18n) 대비 메시지 카탈로그 및 도메인/표시 분리 *(추후 진행)*
* [ ] **도메인 코드와 표시 문자열 분리**: [`models.py`](src/core/models.py)의 `QualityZone`·`SmartAction`·`FootageStrategy`와 [`file_manager.py`](src/core/file_manager.py) 경고 등이 한국어 문자열을 직접 보유하지 않고 안정적 식별자(Enum 멤버/코드 + 파라미터)만 반환하도록 전환.
* [ ] **통합 로케일 카탈로그 도입**: GUI(`PyQt6`)·CUI·Jupyter 노트북을 모두 지원할 수 있도록 순수 파이썬 기반 `gettext(.po/.mo)` 또는 `locales/*.json` + `t(key, **params)` 카탈로그 체계로 [`src/core/messages.py`](src/core/messages.py) 확장.
* [ ] **로케일 무결성 자동 검증**: `unittest`에서 기준 언어(`ko`)와 추가 언어팩 간 키 집합 일치 및 포맷 플레이스홀더(`{count}`, `{kbps}` 등) 일치 여부를 검증.

### 5.4. GUI · CUI 문체(Tone & Voice) 및 분량 기준 정립
* [x] **GUI 텍스트 기준**: 버튼·열 헤더·상태 뱃지 등 즉시 읽는 문자열은 간결한 명사구로 유지하고, 툴팁·설정 설명·대화상자 안내 등 보조 설명은 일관된 존댓말로 상세하고 친절하게 정비.
* [x] **CUI 텍스트 기준**: 테이블 헤더·진행 표시 등 콘솔 폭 제약을 받는 요소는 컴팩트한 축약형([`src/cui/sorting.py`](src/cui/sorting.py))을 유지하되, 리포트 설명·안내·경고·오류 문구는 GUI와 동일하게 친절한 존댓말로 통일.
* [ ] **용도 기반 메시지 키 구분**: `gui.label.*`, `cui.header.*` vs `tooltip.*`, `desc.*`, `error.*`, `cui.report.*` 형태로 키 네이밍을 분리해 용도별 분량·어조가 섞이지 않도록 관리 *(5.3 다국어 도입 시 함께 전환)*.

### 5.5. 클래스·메서드 독스트링(`"""..."""`) 통일 및 린트 자동화
* [x] **단일 독스트링 포맷 확정**: PEP 257 / Google 스타일 기반 한국어 개조식 독스트링·주석 표준을 전 코드베이스에 확정하고 [`AGENTS.md`](AGENTS.md)와 동기화.
* [x] **계약 중심 경량화**: 구현 세부 수치 나열을 제거하고 역할·계약(입력 조건/반환/예외) 중심으로 정비.
* [x] **Ruff `D`(pydocstyle) 점진 적용**: [`pyproject.toml`](pyproject.toml)에 `D` 규칙(`convention = "google"`)을 활성화해 공개 심볼 독스트링·서식 일관성 자동 검증.
