# TODO: 남은 과제 로드맵

> 완료한 항목은 삭제하고 남은 과제만 기록합니다. 상태 표기는 다음과 같습니다.
> * **(예정)**: 착수 대상으로 방향이 정해진 과제
> * **(선택)**: 필요성을 보고 도입 여부를 결정할 과제
> * **(보류)**: 선행 조건이나 우선순위 때문에 대기 중인 과제
> * **(추후)**: 별도 범위로 나중에 진행할 과제

## 1. 판정 로직: 코덱 가중치(`C_codec`) 고도화

[`config.py`](src/core/config.py)의 코덱 가중치를 [`VideoEvaluator`](src/core/evaluator.py)에 확장 적용할 6가지 시나리오입니다. 시나리오 A만 일부 반영되었고, B~F는 평가기에 구현되어 있지 않습니다.

### 1.1. 시나리오 A 잔여: 체감 화질 vs 코덱 전환 절감 2원화 (예정)
$$r_{\text{quality}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{src}}}, \quad r_{\text{saving}} = r_{\text{quality}} \times \frac{C_{\text{src}}}{C_{\text{target}}} = \frac{B_{\text{curr}}}{B_{\text{opt, h264}}(W, H, \text{fps}) \times C_{\text{target}}}$$
* 반영됨: 저화질 판정은 $r_{\text{quality}}$에 해당하는 원본 코덱 기준 `비트레이트 비율`, 과화질 판정과 예상 용량은 $r_{\text{saving}}$에 해당하는 목표 인코딩 코덱 기준 `출력 BR 비율`
* 남음: $r_{\text{quality}} \le 1.20$이어도 구형 코덱 이득($C_{\text{src}}/C_{\text{target}} \ge 1.25$)과 적정 이상 화질($r_{\text{quality}} \ge 0.80$)을 충족하면 `ENCODE_CODEC_UPGRADE`로 분류. 해당 분류는 [`SmartAction`](src/core/models.py)에 아직 없음

### 1.2. 나머지 시나리오 (선택)
* **시나리오 B — 고효율 코덱(`AV1`, `HEVC`, `VP9`) 세대 손실 방어**: $C_{\text{src}} \le C_{\text{target}}$이면 `over_bitrate_ratio`(1.20)에 히스테리시스 마진($+0.15 \sim +0.20$)을 가산해, 최소 25~30% 절감이 보장될 때만 재인코딩
* **시나리오 C — 구형 코덱 키프레임 재인코딩 용량 예측($\Delta S$) 정밀화**: $C_{\text{src}} \ge 1.25$인 영상을 최신 코덱($C_{\text{target}} = 0.80 \sim 1.00$)으로 변환할 때 압축 향상분이 I-프레임 오버헤드($+6\%$)를 상쇄하는 효과 반영
  $$S_{\text{after}} = S_{\text{orig}} \times \left(\frac{C_{\text{target}}}{C_{\text{src}}}\right) \times \left(1 + \text{overhead}_{\text{kf}}\right)$$
* **시나리오 D — 블록 노이즈 증폭 방어 라우팅**: $C_{\text{src}} \ge 1.35$(`mpeg2video`, `mpeg4`, `flv1` 등)이고 $r_{\text{quality}} < 0.90$이면 `_2_저화질_키프레임교정`으로 분류
* **시나리오 E — Intra-Frame 전용(`mjpeg`, `prores`, `dnxhd`, $C_{\text{codec}} = 2.50$) 최우선 감축**: 키프레임이 정상(`0.033s`)이어도 $C_{\text{src}} \ge 2.0$이면 포착해 60% 이상 용량 감축
* **시나리오 F — 모바일/VR 탐색 호환성 강제 변환**: HW 디코딩 미지원 레거시 코덱($C_{\text{src}} \ge 1.25$, `wmv3`, `vc1`, `rv40`, `theora`, `flv1` 등)을 표준 코덱과 1초 키프레임으로 현대화하는 옵션

## 2. GUI 2단계 과제 (`src/gui/`)

### 2.1. 레이아웃, 상호작용
* **(예정) 테이블 상단 영역 리본형 접기, 펼치기** ([`window_layout.py`](src/gui/window_layout.py), [`main_window.py`](src/gui/main_window.py))
  * 현황: 테이블 위쪽(`HeaderBar`, `BentoDashboard`, `FilterTuningBar`)이 항상 고정 높이를 차지해 좁은 화면에서 행 가시 영역이 제한됨
  * 방향: 상단 영역 전체를 한 번에 접고 펼치는 토글 버튼과 단축키를 추가하고, 접힘 상태를 `UiLayoutState`에 저장
* **(선택) 테이블 그룹 접기(아코디언, 트리 뷰)**: 표준 FPS, 해상도 등 같은 기준 단위로 소제목 행을 묶어 펼치기, 접기를 지원할지 검토
* **(보류) 선택한 파일만 분류 이동, 제외 후 이동**: 부분 이동은 별도 범위로 검토

### 2.2. 작업 흐름 확장
* **(예정) 다중 폴더 배치 큐**: 복수 폴더 드래그 앤 드롭 등록과 순차 분석, 분류
  * 현황: 드롭 처리([`window_layout.py`](src/gui/window_layout.py) `handle_drop_event`)는 폴더 1개만 받아 단일 작업 폴더로 전환

## 3. 인코딩 연동

* **(선택) 내장 FFmpeg 원클릭 인코딩**: 프리셋(`_1_표준인코딩_CQ22`, `_2_저화질_키프레임교정`) 기준 백그라운드 직접 인코딩
* **(선택) 샘플 인코딩 기반 예측, 보정, 결과 확인** (내장 FFmpeg 인코딩이 선행 조건)
  * 공통 기반: 영상 일부 구간(예: 10~20초)을 실제 프리셋으로 인코딩하고 출력 크기와 비트레이트를 측정하는 엔진
  * **정밀 예측 옵션**: 재인코딩 후보 영상만 샘플 인코딩해 실제 출력 비트레이트로 예상 용량을 계산. 비트레이트 모델이 반영하지 못하는 내용 복잡도(애니메이션, 화면 녹화, 필름 그레인, 스포츠 등) 차이를 보정
  * **키프레임 증가율 보정**: 같은 구간을 원본 키프레임 간격과 1초 간격으로 각각 인코딩하고 크기 차이로 `keyframe_1s_increase_rate`(기본 6%)를 실측 보정. 같은 방식으로 기준 비트레이트(`base_target_kbps`)를 인코더별(NVENC CQ, x265 CRF)로 보정하는 것도 검토
  * **인코딩 결과 확인 단계**: 결과가 원본보다 크거나 절약이 기준보다 작으면 원본을 유지하고, 실제 결과를 예측값과 비교해 표시. 외부 인코더(샤나인코더) 출력은 분류 폴더에서 원본과 짝지어 비교하는 방식 검토

## 4. 실행 성능

* **(보류) `PyAV`(`av`) 인프로세스 연동**: 외부 `ffprobe.exe` 스폰 비용(파일당 약 20ms) 제거 검토. 키프레임 측정이 메타데이터와 단일 ffprobe 호출로 통합되어 파일당 호출이 1회가 되었으므로 우선순위가 낮음

## 5. CUI / CLI 사용성 (보류)

* **(보류) 터미널 가로 폭(80~120칸) 대응 컴팩트 뷰** ([`cui_printer.py`](src/cui/cui_printer.py))
  * `view: Literal["compact", "full"]` 프리셋: `compact`에서 `너비`, `높이`를 `해상도`로 통합하고 보조 열(`회전`, `FPS`, `길이`, `예상 용량`)과 구분선 생략 검토
  * `shutil.get_terminal_size()` 기반 남은 폭 계산으로 파일명 표시 너비 자동 조절(상한 `config.cui.filename_maxlen`)

## 6. 표준화 잔여

* **(추후) 다국어(i18n) 대비 카탈로그**
  * [`models.py`](src/core/models.py)(`QualityZone`, `SmartAction`, `FootageStrategy`), [`file_manager.py`](src/core/file_manager.py) 경고 등에서 한국어 직결을 제거하고 식별자/코드와 파라미터를 반환
  * GUI(`PyQt6`), CUI, Jupyter 겸용 순수 파이썬 `gettext(.po/.mo)` 또는 `locales/*.json` + `t(key, **params)`로 [`messages.py`](src/core/messages.py) 확장
  * `unittest`에서 기준 언어(`ko`)와 추가 언어팩 간 키, 플레이스홀더(`{count}`, `{kbps}` 등) 일치 검증
  * 도입 시 메시지 키 네이밍을 `gui.label.*`, `cui.header.*`(짧은 명사구)와 `tooltip.*`, `desc.*`, `error.*`, `cui.report.*`(존댓말 문장)로 분리
* **(추후) 선택적 `RotatingFileHandler`**: 표준 `logging` 일원화([`logging_setup.py`](src/core/logging_setup.py)) 위에 파일 로그 핸들러 추가
