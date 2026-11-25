# TODO: 남은 과제 로드맵

> 완료한 항목은 삭제하고 남은 과제만 기록합니다. 상태 표기는 다음과 같습니다.
> * **(예정)**: 착수 대상으로 방향이 정해진 과제
> * **(선택)**: 필요성을 보고 도입 여부를 결정할 과제
> * **(보류)**: 선행 조건이나 우선순위 때문에 대기 중인 과제
> * **(추후)**: 별도 범위로 나중에 진행할 과제

## 1. GUI 2단계 과제 (`src/gui/`)

### 1.1. 레이아웃, 상호작용
* **(선택) 커스텀 제목 표시줄(Windows 11 네이티브 캡션 동치)**: 설정 창 등에 항상 위에 표시(`pin`), 도움말(`?`) 버튼을 창 제목 띠(`X` 버튼 줄)에 배치하기 위해 `FramelessWindowHint` + Win32 `WM_NCCALCSIZE` / `WM_NCHITTEST` 기반 커스텀 제목 표시줄 도입 검토. Windows 11 표준 캡션 버튼 규격(`46x32` DIP, `Segoe Fluent Icons` 글리프, 최대화 버튼 `HTMAXBUTTON`으로 Snap Layout 유지), 8방향 리사이즈, 고DPI 다중 모니터 전환, 앱 전체 창 간 일관성을 함께 검증해야 함
* **(선택) 테이블 그룹 접기(아코디언, 트리 뷰)**: 표준 FPS, 해상도 등 같은 기준 단위로 소제목 행을 묶어 펼치기, 접기를 지원할지 검토
* **(보류) 선택한 파일만 분류 이동, 제외 후 이동**: 부분 이동은 별도 범위로 검토
* **(예정) 재인코딩 진행 창 남은 시간 추정**: 전체 남은 시간이 실제와 맞지 않고 계속 늘어남. 확인된 현상(2026-11-20 테스트 인코딩): 한 영상의 남은 시간이 14분일 때 다른 인코딩 중 영상의 남은 시간이 20초 안팎인데 전체 남은 시간은 3초씩 늘고, 이후 3초마다 1초씩 늘어남
  * 현황: 전체 남은 시간은 `overall_remaining_sec`(경과 시간 x 남은 개수 / 완료 개수 + 진행 중 비율 합)으로 계산. 진행 중 비율을 넣어 완료 전에도 값이 나오게 했으나, 영상 하나의 남은 시간(`remaining_sec`)과 맞지 않음
  * 검토: 항목마다 남은 시간을 따로 추정해 합산하거나, 동시 실행 수를 고려한 처리량으로 계산하고, 완료가 없을 때와 경과 시간이 늘 때 값이 튀지 않도록 평활화. 테스트는 여러 영상과 동시 실행 조건의 시각 값으로 고정
  * 2026-11-20 수정(`d3bc857`): 평균 속도와 전체 남은 시간이 이번 실행에서 인코딩한 영상만 셈. 건너뜀과 이전 실행에서 끝난 영상은 뺌. 1분 영상 하나가 30초까지 1.25배로 돌 때 평균 1.25배와 남은 시간 05:00으로 고정하는 테스트를 추가. 실제 화면에서 남은 시간이 안정적인지는 아직 확인하지 않음

### 1.2. 작업 흐름 확장
* **(보류) 같은 작업 폴더의 오래된 열린 배치 정리**: 중지 뒤 다시 시작할 때마다 이전 배치가 열린 채 남음. 이어서 재인코딩 확인은 같은 폴더의 가장 최근 배치만 묻고(2026-11-20 사용자 결정), 나머지는 열린 채 쌓임. 사용자는 지금처럼 두기를 선택했고, 같은 폴더의 열린 배치를 한꺼번에 닫는 기능은 미구현
* **(예정) 다중 폴더 배치 큐**: 복수 폴더 드래그 앤 드롭 등록과 순차 분석, 분류
  * 현황: 드롭 처리([`window_layout.py`](src/gui/window_layout.py) `handle_drop_dirs_event`)는 Shift 또는 Ctrl을 누른 채 드롭하면 작업 폴더를 추가하고, 그 밖에는 단일 작업 폴더로 전환. 순차 분석 큐는 미구현

## 2. 인코딩 연동

* **(예정) 평가 로직: 결과 크기 초과와 조기 중단 예측**: 평가기의 예상 절약(`expected_delta_mb`)이 일부 영상에서 크게 빗나감. 2026-11-20 배치 14: video14(MKV, 다중 트랙, ASS 글꼴)와 video15(MP4, 표지, 챕터)는 예상 결과 22 MB 안팎이었으나 실제 결과는 원본(75.1 MB, 51.3 MB)보다 컸고, video2, 3, 4, 6은 조기 중단 추정이 원본보다 31~98% 컸음
  * 검토: 크기 초과 파일의 공통 특성(컨테이너, 트랙 수, 자막과 글꼴 첨부, 키프레임 간격)을 분석해 평가 보정 규칙을 정하고, 결과 크기 초과 건을 기록(`encode_item.output_size`)으로 모아 예측 오차를 측정
  * 현황: 게이트 폐기 항목은 결과 크기를 기록하고(새 실행부터), 재인코딩 목록의 폐기 행이 예상과 측정 크기를 보여 줌
  * 2026-11-20 키프레임 우선 게이트(`ecc047d`, 결정 10 개정): 키프레임 초과 영상은 크기로 폐기하지 않음. 위 배치 14의 video14와 video15도 이제 원본보다 커도 배치됨. 예상 절약 빗나감과 조기 중단 예측 문제는 그대로 남음
* **(보류) 앱 표시 이름 확정과 재인코딩 태그 이름 기본값 재논의**: 현재 알파 버전이라 앱 이름이 확정되지 않음(창 제목 상수 `APP_WINDOW_TITLE`은 옛 이름 "Video Classifier", README는 "Video Scripts", 패키지는 `video-scripts`). 재인코딩 태그 이름 기본값은 임시로 `re_encoded`(`IMPLEMENTATION_PLAN.md` 결정 28). 앱 이름을 확정하면 프로그램 고유 이름을 넣은 기본값(예: `<앱이름>_re_encoded`)으로 바꿀지 논의
  * 바꿀 때 주의: 이전 기본값 `re_encoded`를 태그 이름 이력에 남겨 기존 결과 파일이 계속 "이미 재인코딩"으로 판정되게 함
* **(선택) 내장 FFmpeg 원클릭 인코딩**: 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`) 기준 백그라운드 직접 인코딩
* **(선택) 샘플 인코딩 기반 예측, 보정, 결과 확인** (내장 FFmpeg 인코딩이 선행 조건. 예측과 보정은 `IMPLEMENTATION_PLAN.md` 결정 29 B, C로 4단계에서 진행)
  * 공통 기반: 영상 일부 구간(예: 10~20초)을 실제 프리셋으로 인코딩하고 출력 크기와 비트레이트를 측정하는 엔진
  * **정밀 예측 옵션**: 재인코딩 후보 영상만 샘플 인코딩해 실제 출력 비트레이트로 예상 용량을 계산. 비트레이트 모델이 반영하지 못하는 내용 복잡도(애니메이션, 화면 녹화, 필름 그레인, 스포츠 등) 차이를 보정
  * **키프레임 증가율 보정**: 같은 구간을 원본 키프레임 간격과 1초 간격으로 각각 인코딩하고 크기 차이로 `keyframe_1s_increase_rate`(기본 6%)를 실측 보정. 같은 방식으로 기준 비트레이트(`base_target_kbps`)를 인코더별(NVENC CQ, x265 CRF)로 보정하는 것도 검토
  * **인코딩 결과 확인 단계**: 결과가 원본보다 크거나 절약이 기준보다 작으면 원본을 유지하고, 실제 결과를 예측값과 비교해 표시. 외부 인코더(샤나인코더) 출력은 분류 폴더에서 원본과 짝지어 비교하는 방식 검토

## 3. 실행 성능

* **(보류) 파일 목록 중복 수집 제거**: 시작 복원(`resolve_restore_keyframe_mode`), 대상 수 계산(`count_pending_files`), 분석(`probe_directory_with_store`)이 각각 전체 목록을 다시 읽음. 한 번 수집한 목록을 공유하면 되지만, 목록 한 번 조회가 15,000개 기준 약 30ms라 목표 안이므로 체감 지연이 확인될 때 진행
  * 제외한 가설: 취소 가능 pool의 `wait` 루프, 분석 DB 조회와 쓰기는 병목이 아님(측정 완료)

* **(보류) `PyAV`(`av`) 인프로세스 연동**: 외부 `ffprobe.exe` 스폰 비용(파일당 약 20ms) 제거 검토. 키프레임 측정이 메타데이터와 단일 ffprobe 호출로 통합되어 파일당 호출이 1회가 되었으므로 우선순위가 낮음

* **(예정) 외장 HDD 지문 병렬화 측정**: 지문 계산의 기본 작업 수(코어 + 4)가 외장 HDD(USB 브리지, 판별 `UNKNOWN`)에서 순차보다 느린지 확인. 새 폴더의 합성 파일로 순차와 병렬을 비교하고 기존 미디어는 읽지 않음. 느리면 `UNKNOWN` 장치의 지문 작업 수를 1로 제한
  * 측정 안전 규칙(사용자 지시): 사용자 미디어가 든 드라이브이므로 파일 손실 금지. 신규 폴더 하나에서만 쓰고, 삭제는 기록한 파일만, 기존 내용은 열거, 읽기, ffprobe 금지. 드라이브 문자와 장치 정보는 코드, 문서, 커밋에 쓰지 않고 실행 인자로만 전달
* **(예정) 네트워크 드라이브 동시 실행 상한 확정**: 이전에는 제한이 없었으나 CPU 기준 절반으로 제한하도록 바뀌었고 측정하지 않음. 측정 후 유지 또는 `None`으로 되돌림을 결정
* **(예정) 저장 장치 상한 정책 실효성 검토**: HDD와 SSD 상한이 모두 `None`이라 대기열 구조는 작동하지만 제한 효과는 없음. 측정 결과로 정책 값을 정하거나 구조를 단순화
  * 기준 결정(2026-11-17 사용자 승인): HDD 동시 실행 수는 장치 종류별 정책 상수로 두고 값은 실측으로 정함(`max_workers` 정수 하드코딩 금지는 PC 사양 고정 금지로 해석). 판별 실패(`UNKNOWN`)는 기존 기본값 유지. 사용자 직접 지정 설정은 1차에 추가하지 않고 판별 결과를 로그로만 표시. 판별은 Windows만이고 그 외 OS는 `UNKNOWN`
* **(선택) 지문 계산 취소 지원**: `_fingerprints_for`가 배치 전체를 끝까지 실행해 취소 요청 후에도 대기함. 배치 단위로 취소 플래그를 확인
* **(선택) 직접 지정 맵 동기화 테스트**: 설정 적용 뒤 직접 지정 맵이 분석 서비스에 반영되는 경로에 전용 테스트가 없고 코드 읽기로만 확인함
* **(보류) 콜드 캐시 GUI 멈춤 측정**: 목표 검증은 웜 캐시와 빈 파일 기준이며, 재부팅 후 실제 파일 기준 측정은 하지 않음

## 4. CUI / CLI 사용성 (보류)

* **(보류) 터미널 가로 폭(80~120칸) 대응 컴팩트 뷰** ([`cui_printer.py`](src/cui/cui_printer.py))
  * `view: Literal["compact", "full"]` 프리셋: `compact`에서 `너비`, `높이`를 `해상도`로 통합하고 보조 열(`회전`, `FPS`, `길이`, `예상 용량`)과 구분선 생략 검토
  * `shutil.get_terminal_size()` 기반 남은 폭 계산으로 파일명 표시 너비 자동 조절(상한 `config.cui.filename_maxlen`)

## 5. 표준화 잔여

* **(추후) 다국어(i18n) 대비 카탈로그**
  * [`models.py`](src/core/models.py)(`QualityZone`, `SmartAction`, `FootageStrategy`), [`file_manager.py`](src/core/analysis/file_manager.py) 경고 등에서 한국어 직결을 제거하고 식별자/코드와 파라미터를 반환
  * GUI(`PyQt6`), CUI, Jupyter 겸용 순수 파이썬 `gettext(.po/.mo)` 또는 `locales/*.json` + `t(key, **params)`로 [`messages.py`](src/core/messages.py) 확장
  * `unittest`에서 기준 언어(`ko`)와 추가 언어팩 간 키, 플레이스홀더(`{count}`, `{kbps}` 등) 일치 검증
  * 도입 시 메시지 키 네이밍을 `gui.label.*`, `cui.header.*`(짧은 명사구)와 `tooltip.*`, `desc.*`, `error.*`, `cui.report.*`(존댓말 문장)로 분리

## 6. 구조 개편 (모듈 크기)

> `scripts/verify_module_size.py`의 소프트 상한(500줄) 초과 보고 중 책임 혼재가 있어 큰 개편이 필요한 모듈입니다. 하드 상한은 1000줄입니다.

* **(예정) [`main_window.py`](src/gui/main_window.py) (905줄)**: 기능을 더하기 전에 먼저 분리. 미리 보기 배선과 설정 반영 핸들러, 표 상태 동기화 연결을 별도 컨트롤러로 이동 검토
  * 분리 보류 사유(2026-11-16): 설정, 튜너, 도움말 핸들러가 `_session`, `_tasks`, `_app_state`, `dialogs` 등 MainWindow private 속성 10여 개에 의존함. 옮기면 private 접근이 모듈 경계를 넘거나 테스트 참조 15곳(`open_tuner`, `_on_criteria_changed` 등) 수정이 필요함. 재인코딩 작업은 호출만 바꿔 줄을 늘리지 않음
* **(예정) [`window_layout.py`](src/gui/window_layout.py) (646줄)**: 5c-6 중 454줄에서 늘어 필터바 배선, 표 표시, 묶기 연결이 섞임(2026-11-25 정기 리뷰). 표 표시와 묶기 연결을 별도 모듈로 분리하는 안을 5d 착수 전에 검토. 같은 리뷰에서 [`main_window.py`](src/gui/main_window.py)가 893줄에서 902줄로 늘었고 [`data_cleanup_controller.py`](src/gui/controllers/data_cleanup_controller.py)(589줄)는 화면/설정 초기화 메서드 17개와 서비스 연결이 함께 있음
* **(예정) [`video_table_view.py`](src/gui/table/video_table_view.py) (990줄, 하드 상한까지 10줄)**: 열 폭 계산, 적용, 이름 열 맞춤, 맨 위 행 복원, 선택과 행 동작이 한 클래스에 혼재. 열 폭 관리자와 스크롤 위치 복원 도우미로 분리 검토(테스트가 `_width_timer` 등 내부 속성을 참조하므로 함께 정리)
* **(예정) [`session_controller.py`](src/gui/controllers/session_controller.py) (862줄)**: 초기 상태 로드, 저장 지연, 직접 지정 DB 저장, 미리 보기 음량, 재평가 위임이 `SessionCoordinator`에 혼재
* **(선택) [`settings_dialog.py`](src/gui/settings/settings_dialog.py) (682줄), [`header_bar.py`](src/gui/widgets/header_bar.py) (567줄), [`table_columns.py`](src/gui/table/table_columns.py) (509줄), [`cui_printer.py`](src/cui/cui_printer.py) (584줄), [`help_concepts_page.py`](src/gui/help/help_concepts_page.py) (544줄), [`table_model.py`](src/gui/table/table_model.py) (530줄), [`analysis_store.py`](src/service/analysis_store.py) (652줄), [`state_codec.py`](src/core/settings/state_codec.py) (570줄), [`encode_service.py`](src/service/encode/encode_service.py) (531줄)**: 소프트 상한 초과 보고, 책임 혼재 여부를 검토해 분리 여부 결정
* **(보류) [`glossary_data.py`](src/gui/help/glossary_data.py)**: 화이트리스트 기록(530줄) 대비 609줄(15% 증가, 20% 이내). 기록 줄 수 갱신은 사용자 승인으로만 수행
* **(예정) [`messages.py`](src/core/messages.py)의 영역 역참조**: core 바로 아래 기반 모듈인 `messages.py`가 [`storage_probe.py`](src/core/analysis/storage_probe.py)의 저장 장치 타입(`StorageKind`, `StorageInfo`, `BUS_TYPE_USB`)을 참조해 core 직속과 `core/analysis`가 서로 의존. 저장 장치 문구를 analysis로 옮기거나 타입을 직속으로 옮겨 해소(코드 수정 필요). 그 부분을 수정할 때 함께 정리
* **(보류) `build_display_card` 분할**: [`settings_advanced_cards.py`](src/gui/settings/settings_advanced_cards.py)의 선언형 행 명세 위주라 쪼개도 이득이 적음
* **(선택) [`cui_printer.py`](src/cui/cui_printer.py) (583줄)**: 500줄 검토 기준 초과. 텍스트 폭 유틸 분리가 후보
* **(선택) 프로덕션 코드가 쓰지 않는 메서드 정리**: `get_rotate_type`, `register_numeric_bucket_builder`, `log_history`, `effective_headers`는 `src`에서 정의 외 사용이 없고 테스트에서만 씀(2026-11-21 확인). 긴 함수 `build_stylesheet`, `build_cli_parser`는 보고만 하고 손대지 않음
* **(예정) 인메모리 캐시(`CacheRegistry`, `TableCache`) 전면 리팩터링**: 영구 SQLite 캐시([`analysis_store.py`](src/service/analysis_store.py)) 도입 이후 다중 폴더 전역 딕셔너리 캐시의 실효성이 줄고, 재평가 경로의 디스크 I/O 분리가 미완인 구조를 단일 스냅샷 기반으로 전면 개편
  * 현황과 문제점:
    * [`cache.py`](src/service/cache.py)의 `CacheRegistry._caches`는 폴더를 전환할 때마다 상한 없이 메모리에 쌓여 명시적 무효화 전까지 해제되지 않음(GUI는 폴더 전환 시 화면을 비우므로 다중 폴더 캐시 체감 이점이 거의 없음)
    * [`classifier_service.py`](src/service/classifier_service.py)의 `cached_report_job`은 `VideoProps`만 메모리에서 읽고, 매 재평가마다 `_resolve_encoded_evidence_for`(이력 DB 열기, 지문 매칭)와 `_resolve_active_frames_for`(화질 분석 DB 열기, 전체 영상 `os.stat` 반복)를 디스크에서 다시 실행해 설정 슬라이더 조작 시 완전한 0-I/O 재평가가 되지 않음
    * [`task_runner.py`](src/gui/controllers/task_runner.py)가 전역 싱글턴 `DEFAULT_CACHE_REGISTRY.invalidate()`를 직접 호출하는 등 세션 상태와 전역 레지스트리 결합이 남아 있음
  * 리팩터링 목표:
    * 전역 다중 폴더 `CacheRegistry` / `DEFAULT_CACHE_REGISTRY`를 제거(또는 세션 소유 단일 슬롯으로 대체)하고, `ClassifierSession` / `VideoClassifierService`가 **현재 작업 폴더 1개의 통합 스냅샷**(`VideoProps` 목록, `DirSignature`, `legacy_names`, `renamed_pairs` + 화질 분석 결과 맵 + 재인코딩 이력 매칭 결과)만 보유하도록 단순화
    * 폴더 전환 시 이전 폴더의 메모리 스냅샷을 즉시 해제하고, 이전 분석 결과 복원은 영구 `AnalysisStore`(`video_cache.sqlite3`)에 일임
    * 화질 분석 완료, 여백 제외 토글, 재인코딩 완료, 외부 변경 감지가 일어날 때만 스냅샷의 `active_frames` / `encoded_evidence`를 증분 갱신하여 설정 변경, 예외 규칙, 출력 해상도 직접 지정, `Ctrl+Z` 재평가를 완전한 0-I/O 인메모리 연산으로 전환
    * [`external_resync.py`](src/gui/controllers/external_resync.py)의 `prune_missing`, `TaskRunner.clear_analysis_cache`, 노트북 파사드(`src/classifier.py`)와 관련 테스트 일괄 정리

## 7. 테스트 대응, 함수 실행 예외 줄이기

> `scripts/test_coverage_exceptions.json`의 예외 항목(기준선 234건: 대응 테스트 없는 모듈 112, 미실행 함수 120, 실행 여부 불안정 함수 2)을 0으로 줄이는 장기 과제입니다. `check.ps1`이 실행할 때마다 진행률을 출력하고, 항목이 있는 모듈을 수정할 때는 그 항목을 같은 커밋에서 해소합니다(`AGENTS.md` 3절).

* **(예정) 비GUI 미실행 함수 29개**: 항목마다 테스트 추가, 호출처 없는 죽은 코드 삭제(`classifier.py`의 `set_*` 7개, `SummaryReport.unmeasured_keyframe_count`, `VideoClassifierService.registry`와 `invalidate_cache` 후보), 외부 프로세스와 OS 호출 기본 구현(`encode_ports`, `power_state`, `EncodeProcess.set_priority`)은 가짜 주입 지점을 통한 테스트 또는 사용자 승인 아래 분류 중에서 결정
* **(예정) 대응 테스트 없는 비GUI 모듈**: `file_fingerprint.py`, `downscale_resolver.py`, `encode_ports.py` 등. 모듈 단위로 `tests/<경로>/test_<모듈>.py` 추가
* **(추후) GUI 모듈과 함수**: 영역 단위(메뉴, 설정, 표, 미리 보기, 컨트롤러)로 나눠 진행. 사용자 동작 경로(메뉴, 단축키, 미리 보기)를 우선
* **(완료 `d1fc73d`, `7f9cc64`) GUI 테스트 임시 폴더 정리 간헐 실패**: 2차(2026-11-20) 원인은 재평가 워커 스레드(`ReportEvaluator`)가 창 종료 뒤에도 분석 DB 연결을 쥔 것. 창 종료 때 진행 중 재평가를 기다려 해소(수정 전 6회 중 4회 실패, 수정 후 8회 통과, `check.ps1 -Force` 3회 통과). 아래는 1차 해소 기록. `tests.gui.test_gui_window_workflow`(`NotADirectoryError`), `tests.gui.table.test_gui_table`(SQLite 잠금)이 임시 폴더를 지우다 실패. 원인은 창을 닫아도 분석 DB 연결(`AnalysisStore`)이 GC 전까지 열려 있는 것(WinError 32, 2026-11-19 확인). 창 종료 때 연결을 닫아 해소(`check.ps1 -Force` 3회 통과). 재인코딩 이력 연결 닫기는 커밋 5b-2a
* **(추후) `video_table_view.py`(990줄), `table_model.py`(571줄) 분리**: 5b-2b에서 미룸. 선택 기록과 마우스 드래그 처리는 뷰의 비공개 상태에 의존하므로 Protocol 설계 후 분리. `table_model.py`는 상태 열 조회 코드가 후보
* **(추후) 종료 예약 취소 뒤 빈 작업자 대기**: `77b981e`는 예약 중 건너뛴 대기 영상을 그 회차의 모든 항목이 끝난 뒤 다시 넣음. 예약 중 한 작업자가 비었고 다른 작업자의 영상이 길면, 취소 뒤에도 그 영상이 끝날 때까지 빈 작업자가 놀음. 취소 시점에 건너뛴 항목을 바로 다시 넣는 방식 검토. 회차가 끝난 직후 취소하면 배치가 대기 항목을 남기고 끝나는 좁은 구간도 함께 확인
* **(추후) 진행 창 후속(5c 편차, 2026-11-20 결정)**: 디코딩 방식 표시(진행 이벤트에 값 추가 필요), 결과 목록 상태 배지, 순간 최고 비트레이트 수치. 세부 화면 조정: 최소 크기(380x440)에서 카드 수치 잘림, 결과 표 높이 조절 사용감, 그래프 라벨 겹침, 튀는 값이 크면 선이 아래로 눌리는 축 범위, 탭 디자인 세부
* **(추후) 재평가 취소 신호**: 창 종료 때 `ReportEvaluator.shutdown`이 진행 중 재평가를 시간 제한 없이 기다림(`7f9cc64`). 영상이 아주 많은 폴더에서는 창 닫기가 몇 초 멈출 수 있음. 재평가 작업에 취소 신호(`threading.Event`)를 넘겨 창 종료 때 중단하고 연결을 닫는 방식 검토
* **(추후) FFmpeg 능력 확인 캐시**: 재인코딩 시작 조건의 FFmpeg 확인은 실행 파일 경로별로 앱 실행 중 한 번만 함(`EncodeCoordinator`). 경로를 비운 채(PATH) 실행 중 FFmpeg를 설치하면 재시작해야 반영됨. 설정 탭 FFmpeg 상태 새로 고침과 캐시를 맞출지 검토
* **(추후) `check.ps1` 병렬 실행의 1초 타임아웃 간헐 실패**: `tests.tools.test_check_runner`의 `test_fault_timeout_dumps_stack_inside_shard`가 병렬 검사에서 가끔 실패하고 단독 실행은 통과(2026-11-19). 2026-11-20 구현 작업 중 병렬 검증에서 두 번 더 실패했고 단독 실행과 재실행은 통과(테스트 대응 예외 항목에는 추가하지 않음). 원인 미조사
* **(추후) 실행 여부가 실행마다 달라지는 함수 2개**: 타이머로 호출되는 함수라 테스트가 타이머 시점에 기대고 있어 간헐적으로 실행되지 않음. 임시로 불안정 목록(`nondeterministic_functions`)에 두었고, 목표는 테스트가 함수를 직접 호출하거나 타이머를 직접 발동해 결정적으로 만든 뒤 목록에서 지우는 것
  * [`MainWindow._prewarm_settings_dialog`](src/gui/main_window.py): `call_later(self, _SETTINGS_PREWARM_DELAY_MS, ...)`로 예약됨. 창이 일찍 닫히면 실행되지 않음(측정 10회 중 2회 미실행)
  * [`SessionCoordinator.flush_pending_selection`](src/gui/controllers/session_controller.py): 선택 저장 디바운스 단발 타이머(`FILTER_PERSIST_DEBOUNCE_MS`)의 timeout 슬롯. 타이머가 만료되기 전에 테스트가 끝나면 실행되지 않음(측정 중 1회 확인)

## 8. 메타 문서 자동 최신화

> 문서에 적은 숫자와 목록이 코드와 어긋나는 것을 줄이는 과제입니다. 경로, 링크, 점 경로는 이미 `test_metadata_sync`가 검사하므로 여기서는 나머지를 다룹니다.

* **(예정, 권장) 줄 수 숫자를 문서에서 뺌**: 줄 수는 [`verify_module_size.py`](scripts/verify_module_size.py)가 매번 정확히 출력하므로, 6절 목록의 숫자를 빼고 "소프트 상한 초과 보고 참조"로 바꾸면 갱신할 것이 없어짐. 검사를 추가하는 것보다 확실함
* **(예정) 문서 숫자 검사**: 문서에 적은 줄 수나 개수가 실제와 크게 다르면 실패하는 검사를 `test_metadata_sync`에 추가. 숫자를 남기는 곳에만 필요
* **(예정) 규칙 구멍 메우기**: [AGENTS.md](AGENTS.md) 5절과 `sync` 스킬의 문서 목록에 TODO.md를 넣고, AGENTS.md 7절의 "요청하지 않은 TODO.md 수정 금지"에 "관련 모듈을 고칠 때의 동기화는 예외"를 추가

> 보류: 책임 설명 문장과 완료 여부의 자동 판정은 정확도가 낮아 틀렸을 때 신뢰를 떨어뜨리므로 하지 않음. 큰 구조 변경 뒤 정기 점검으로 대신함
