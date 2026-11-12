# TODO: 향후 고도화 및 최적화 로드맵 ( Roadmap & Technical Spec )

> **문서 목적**: 퀄리티(`CQ`) 기반 비디오 분류 아키텍처의 **① 실행 성능 극대화**, **② 영구 데이터베이스(캐시) 구축**, **③ GUI 도입 준비**, **④ 코덱 가중치(`C_codec`) 고도화 판별 시나리오**를 상세히 기록한 기술 설계 문서.

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
  * 현재 `ffmpeg.probe(filepath)`는 컨테이너의 모든 섹션과 챕터, 불필요한 태그까지 전부 JSON으로 직렬화.
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

## 3. GUI 도입을 위한 준비 (GUI Readiness & Frontend Architecture)

### 3.1. 서비스 계층(`VideoClassifierService`) 비동기·이벤트 고도화
* **취소 토큰(`CancellationToken` / `threading.Event`) 지원**:
  * 대량 폴더 스캔 또는 파일 이동 중 사용자가 GUI에서 `[작업 중지]`를 클릭했을 때 진행 중인 워커 풀을 안전하게 즉시 종료하는 인터럽트 메커니즘 추가.
* **구조화된 이벤트 콜백 확장**:
  * 현재의 `on_progress(current, total, filename)`와 `on_log(msg)` 외에, 단계별 상태(`SCANNING_META`, `PROBING_KEYFRAMES`, `EVALUATING`, `MOVING_FILES`, `COMPLETED`, `CANCELLED`)를 전달하는 상태 이벤트 콜백 정의.
* **배치(Batch) 다중 폴더 큐 지원**:
  * 단일 `.rootdir`뿐만 아니라 여러 개의 작업 폴더를 큐에 등록해 순차적으로 분석·분류할 수 있는 배치 러너 인터페이스 마련.

### 3.2. GUI 주요 화면 및 인터랙션 설계 (PySide6 / PyQt6 또는 Web UI)
1. **상단 컨트롤 바**:
   * 폴더 선택 / 드래그 앤 드롭 영역, `[빠른 스캔]`, `[정밀 분석(키프레임 포함)]`, `[모의 분류(가분류)]`, `[실제 폴더 분류]`, `[원복(Unclassify)]` 원클릭 액션 버튼
2. **실시간 파라미터 시뮬레이션 슬라이더**:
   * `과화질 경계 배수 (기본 1.20x)`, `저화질 경계 배수 (기본 0.80x)`, `키프레임 허용 상한 (기본 2.2s)`, `소형 영상 기준 (기본 30MB)`을 슬라이더로 조절하면 **`ffprobe` 재실행 없이 즉시 테이블의 처방 결과와 예상 절감 용량(GB) 게이지가 실시간으로 재계산**되어 표시됨.
3. **인터랙티브 비디오 테이블**:
   * 컬럼 헤더 클릭 다중 정렬, 화질 등급별 색상 배지(`과화질` 빨강, `적정화질` 초록, `저화질` 노랑), 체크박스 클릭으로 즉석에서 예외 파일(`keep_origin`) 지정 기능.

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
