# TODO: 남은 과제 로드맵

> 완료한 항목은 삭제하고 남은 과제만 기록합니다. 상태 표기는 다음과 같습니다.
> * **(예정)**: 착수 대상으로 방향이 정해진 과제
> * **(선택)**: 필요성을 보고 도입 여부를 결정할 과제
> * **(보류)**: 선행 조건이나 우선순위 때문에 대기 중인 과제
> * **(추후)**: 별도 범위로 나중에 진행할 과제

## 1. GUI 2단계 과제 (`src/gui/`)

### 1.1. 레이아웃, 상호작용
* **(선택) 테이블 그룹 접기(아코디언, 트리 뷰)**: 표준 FPS, 해상도 등 같은 기준 단위로 소제목 행을 묶어 펼치기, 접기를 지원할지 검토
* **(보류) 선택한 파일만 분류 이동, 제외 후 이동**: 부분 이동은 별도 범위로 검토

### 1.2. 작업 흐름 확장
* **(예정) 다중 폴더 배치 큐**: 복수 폴더 드래그 앤 드롭 등록과 순차 분석, 분류
  * 현황: 드롭 처리([`window_layout.py`](src/gui/window_layout.py) `handle_drop_dirs_event`)는 Shift 또는 Ctrl을 누른 채 드롭하면 작업 폴더를 추가하고, 그 밖에는 단일 작업 폴더로 전환. 순차 분석 큐는 미구현

## 2. 인코딩 연동

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
* **(예정) 네트워크 드라이브 동시 실행 상한 확정**: 이전에는 제한이 없었으나 CPU 기준 절반으로 제한하도록 바뀌었고 측정하지 않음. 측정 후 유지 또는 `None`으로 되돌림을 결정
* **(예정) 저장 장치 상한 정책 실효성 검토**: HDD와 SSD 상한이 모두 `None`이라 대기열 구조는 작동하지만 제한 효과는 없음. 측정 결과로 정책 값을 정하거나 구조를 단순화
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

* **(예정) [`main_window.py`](src/gui/main_window.py) (907줄)**: 기능을 더하기 전에 먼저 분리. 미리 보기 배선과 설정 반영 핸들러, 표 상태 동기화 연결을 별도 컨트롤러로 이동 검토
* **(예정) [`video_table_view.py`](src/gui/table/video_table_view.py) (990줄, 하드 상한까지 10줄)**: 열 폭 계산, 적용, 이름 열 맞춤, 맨 위 행 복원, 선택과 행 동작이 한 클래스에 혼재. 열 폭 관리자와 스크롤 위치 복원 도우미로 분리 검토(테스트가 `_width_timer` 등 내부 속성을 참조하므로 함께 정리)
* **(예정) [`session_controller.py`](src/gui/controllers/session_controller.py) (862줄)**: 초기 상태 로드, 저장 지연, 직접 지정 DB 저장, 미리 보기 음량, 재평가 위임이 `SessionCoordinator`에 혼재
* **(선택) [`settings_dialog.py`](src/gui/settings/settings_dialog.py) (682줄), [`header_bar.py`](src/gui/widgets/header_bar.py) (567줄), [`table_columns.py`](src/gui/table/table_columns.py) (509줄), [`cui_printer.py`](src/cui/cui_printer.py) (584줄), [`help_concepts_page.py`](src/gui/help/help_concepts_page.py) (544줄), [`table_model.py`](src/gui/table/table_model.py) (530줄), [`analysis_store.py`](src/service/analysis_store.py) (652줄), [`state_codec.py`](src/core/settings/state_codec.py) (570줄), [`encode_service.py`](src/service/encode/encode_service.py) (531줄)**: 소프트 상한 초과 보고, 책임 혼재 여부를 검토해 분리 여부 결정
* **(보류) [`glossary_data.py`](src/gui/help/glossary_data.py)**: 화이트리스트 기록(530줄) 대비 609줄(15% 증가, 20% 이내). 기록 줄 수 갱신은 사용자 승인으로만 수행
* **(예정) [`messages.py`](src/core/messages.py)의 영역 역참조**: core 바로 아래 기반 모듈인 `messages.py`가 [`storage_probe.py`](src/core/analysis/storage_probe.py)의 저장 장치 타입(`StorageKind`, `StorageInfo`, `BUS_TYPE_USB`)을 참조해 core 직속과 `core/analysis`가 서로 의존. 저장 장치 문구를 analysis로 옮기거나 타입을 직속으로 옮겨 해소(코드 수정 필요). 그 부분을 수정할 때 함께 정리

## 7. 테스트 대응, 함수 실행 예외 줄이기

> `scripts/test_coverage_exceptions.json`의 예외 항목(기준선 234건: 대응 테스트 없는 모듈 112, 미실행 함수 120, 실행 여부 불안정 함수 2)을 0으로 줄이는 장기 과제입니다. `check.ps1`이 실행할 때마다 진행률을 출력하고, 항목이 있는 모듈을 수정할 때는 그 항목을 같은 커밋에서 해소합니다(`AGENTS.md` 3절).

* **(예정) 비GUI 미실행 함수 29개**: 항목마다 테스트 추가, 호출처 없는 죽은 코드 삭제(`classifier.py`의 `set_*` 7개, `SummaryReport.unmeasured_keyframe_count`, `VideoClassifierService.registry`와 `invalidate_cache` 후보), 외부 프로세스와 OS 호출 기본 구현(`encode_ports`, `power_state`, `EncodeProcess.set_priority`)은 가짜 주입 지점을 통한 테스트 또는 사용자 승인 아래 분류 중에서 결정
* **(예정) 대응 테스트 없는 비GUI 모듈**: `file_fingerprint.py`, `downscale_resolver.py`, `encode_ports.py` 등. 모듈 단위로 `tests/<경로>/test_<모듈>.py` 추가
* **(추후) GUI 모듈과 함수**: 영역 단위(메뉴, 설정, 표, 미리 보기, 컨트롤러)로 나눠 진행. 사용자 동작 경로(메뉴, 단축키, 미리 보기)를 우선
* **(추후) GUI 테스트 임시 폴더 정리 간헐 실패**: `tests.gui.test_gui_window_workflow`가 임시 폴더를 지우다 `NotADirectoryError`로 실패한 적이 있음(4c 검증 중 첫 실행, 재실행 통과, 원인 미조사). 재현되면 원인을 조사
* **(추후) 실행 여부가 실행마다 달라지는 함수 2개**: 타이머로 호출되는 함수라 테스트가 타이머 시점에 기대고 있어 간헐적으로 실행되지 않음. 임시로 불안정 목록(`nondeterministic_functions`)에 두었고, 목표는 테스트가 함수를 직접 호출하거나 타이머를 직접 발동해 결정적으로 만든 뒤 목록에서 지우는 것
  * [`MainWindow._prewarm_settings_dialog`](src/gui/main_window.py): `call_later(self, _SETTINGS_PREWARM_DELAY_MS, ...)`로 예약됨. 창이 일찍 닫히면 실행되지 않음(측정 10회 중 2회 미실행)
  * [`SessionCoordinator.flush_pending_selection`](src/gui/controllers/session_controller.py): 선택 저장 디바운스 단발 타이머(`FILTER_PERSIST_DEBOUNCE_MS`)의 timeout 슬롯. 타이머가 만료되기 전에 테스트가 끝나면 실행되지 않음(측정 중 1회 확인)

## 8. 메타 문서 자동 최신화

> 문서에 적은 숫자와 목록이 코드와 어긋나는 것을 줄이는 과제입니다. 경로, 링크, 점 경로는 이미 `test_metadata_sync`가 검사하므로 여기서는 나머지를 다룹니다.

* **(예정, 권장) 줄 수 숫자를 문서에서 뺌**: 줄 수는 [`verify_module_size.py`](scripts/verify_module_size.py)가 매번 정확히 출력하므로, 6절 목록의 숫자를 빼고 "소프트 상한 초과 보고 참조"로 바꾸면 갱신할 것이 없어짐. 검사를 추가하는 것보다 확실함
* **(예정) 문서 숫자 검사**: 문서에 적은 줄 수나 개수가 실제와 크게 다르면 실패하는 검사를 `test_metadata_sync`에 추가. 숫자를 남기는 곳에만 필요
* **(예정) 규칙 구멍 메우기**: [AGENTS.md](AGENTS.md) 5절과 `sync` 스킬의 문서 목록에 TODO.md를 넣고, AGENTS.md 7절의 "요청하지 않은 TODO.md 수정 금지"에 "관련 모듈을 고칠 때의 동기화는 예외"를 추가

> 보류: 책임 설명 문장과 완료 여부의 자동 판정은 정확도가 낮아 틀렸을 때 신뢰를 떨어뜨리므로 하지 않음. 큰 구조 변경 뒤 정기 점검으로 대신함
