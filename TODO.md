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

* **(선택) 내장 FFmpeg 원클릭 인코딩**: 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`) 기준 백그라운드 직접 인코딩
* **(선택) 샘플 인코딩 기반 예측, 보정, 결과 확인** (내장 FFmpeg 인코딩이 선행 조건)
  * 공통 기반: 영상 일부 구간(예: 10~20초)을 실제 프리셋으로 인코딩하고 출력 크기와 비트레이트를 측정하는 엔진
  * **정밀 예측 옵션**: 재인코딩 후보 영상만 샘플 인코딩해 실제 출력 비트레이트로 예상 용량을 계산. 비트레이트 모델이 반영하지 못하는 내용 복잡도(애니메이션, 화면 녹화, 필름 그레인, 스포츠 등) 차이를 보정
  * **키프레임 증가율 보정**: 같은 구간을 원본 키프레임 간격과 1초 간격으로 각각 인코딩하고 크기 차이로 `keyframe_1s_increase_rate`(기본 6%)를 실측 보정. 같은 방식으로 기준 비트레이트(`base_target_kbps`)를 인코더별(NVENC CQ, x265 CRF)로 보정하는 것도 검토
  * **인코딩 결과 확인 단계**: 결과가 원본보다 크거나 절약이 기준보다 작으면 원본을 유지하고, 실제 결과를 예측값과 비교해 표시. 외부 인코더(샤나인코더) 출력은 분류 폴더에서 원본과 짝지어 비교하는 방식 검토

## 3. 실행 성능

* **(예정) 대량(10,000개) 분석의 파일 목록 수집, 지문 계산 오버헤드 제거**: ffprobe를 스텁으로 바꾼 합성 파일 10,000개 측정 기준, 웜 분석 약 1.7초와 콜드 분석 약 8.9초(프로파일 중)의 대부분이 아래 세 곳
  * 목록 중복 수집: 시작 복원(`resolve_restore_keyframe_mode`), 대상 수 계산(`count_pending_files`), 분석(`probe_directory_with_store`)이 각각 전체 목록을 다시 읽음. 한 번 수집한 목록을 공유
  * 지문 순차 계산: `_store_fresh`가 병렬 프로빙이 끝난 뒤 메인 스레드에서 파일을 하나씩 열어 지문을 계산(10,000개 약 5초). ffprobe 워커 안에서 병렬로 계산하되 취소 흐름과 테스트를 함께 확인
  * 제외한 가설: 취소 가능 pool의 `wait` 루프, 분석 DB 조회와 쓰기는 병목이 아님(측정 완료)

* **(보류) `PyAV`(`av`) 인프로세스 연동**: 외부 `ffprobe.exe` 스폰 비용(파일당 약 20ms) 제거 검토. 키프레임 측정이 메타데이터와 단일 ffprobe 호출로 통합되어 파일당 호출이 1회가 되었으므로 우선순위가 낮음

## 4. CUI / CLI 사용성 (보류)

* **(보류) 터미널 가로 폭(80~120칸) 대응 컴팩트 뷰** ([`cui_printer.py`](src/cui/cui_printer.py))
  * `view: Literal["compact", "full"]` 프리셋: `compact`에서 `너비`, `높이`를 `해상도`로 통합하고 보조 열(`회전`, `FPS`, `길이`, `예상 용량`)과 구분선 생략 검토
  * `shutil.get_terminal_size()` 기반 남은 폭 계산으로 파일명 표시 너비 자동 조절(상한 `config.cui.filename_maxlen`)

## 5. 표준화 잔여

* **(추후) 다국어(i18n) 대비 카탈로그**
  * [`models.py`](src/core/models.py)(`QualityZone`, `SmartAction`, `FootageStrategy`), [`file_manager.py`](src/core/file_manager.py) 경고 등에서 한국어 직결을 제거하고 식별자/코드와 파라미터를 반환
  * GUI(`PyQt6`), CUI, Jupyter 겸용 순수 파이썬 `gettext(.po/.mo)` 또는 `locales/*.json` + `t(key, **params)`로 [`messages.py`](src/core/messages.py) 확장
  * `unittest`에서 기준 언어(`ko`)와 추가 언어팩 간 키, 플레이스홀더(`{count}`, `{kbps}` 등) 일치 검증
  * 도입 시 메시지 키 네이밍을 `gui.label.*`, `cui.header.*`(짧은 명사구)와 `tooltip.*`, `desc.*`, `error.*`, `cui.report.*`(존댓말 문장)로 분리

## 6. 구조 개편 (모듈 크기)

> `scripts/verify_module_size.py`의 소프트 상한(500줄) 초과 보고 중 책임 혼재가 있어 큰 개편이 필요한 모듈입니다. 하드 상한은 1000줄입니다.

* **(예정) [`main_window.py`](src/gui/main_window.py) (995줄, 하드 상한까지 5줄)**: 기능을 더하기 전에 먼저 분리. 미리 보기 배선과 설정 반영 핸들러, 표 상태 동기화 연결을 별도 컨트롤러로 이동 검토
* **(예정) [`video_table_view.py`](src/gui/widgets/video_table_view.py) (779줄)**: 열 폭 계산, 적용, 이름 열 맞춤, 맨 위 행 복원, 선택과 행 동작이 한 클래스에 혼재. 열 폭 관리자와 스크롤 위치 복원 도우미로 분리 검토(테스트가 `_width_timer` 등 내부 속성을 참조하므로 함께 정리)
* **(예정) [`session_controller.py`](src/gui/session_controller.py) (679줄)**: 초기 상태 로드, 저장 지연, 직접 지정 DB 저장, 미리 보기 음량, 재평가 위임이 `SessionCoordinator`에 혼재
* **(선택) [`settings_dialog.py`](src/gui/dialogs/settings_dialog.py) (741줄), [`header_bar.py`](src/gui/widgets/header_bar.py) (570줄), [`table_columns.py`](src/gui/table_columns.py) (533줄), [`cui_printer.py`](src/cui/cui_printer.py) (604줄), [`file_manager.py`](src/core/file_manager.py) (569줄), [`help_concepts_page.py`](src/gui/dialogs/help_concepts_page.py) (536줄)**: 소프트 상한 초과 보고, 책임 혼재 여부를 검토해 분리 여부 결정
* **(보류) [`glossary_data.py`](src/gui/dialogs/glossary_data.py)**: 화이트리스트 기록(530줄) 대비 20% 넘게 커진 643줄. 기록 줄 수 갱신은 사용자 승인으로만 수행
