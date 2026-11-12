# 1. Video Scripts

비디오 아카이빙을 위해 **임의 해상도(360p ~ 1080p, 2K, 4K+) 및 코덱별 픽셀 밀도 기반 화질 분석**, **1초 키프레임 탐색성 보장**, **소형 영상(Footage) 분포 분석 기반 누적 최적화(Cumulative Optimization) 판별**을 수행하는 Python 프레임워크입니다.

## 1.1. 첫 세팅 및 사용
- `.env_example`을 `.env`로 변경 (커널 재시작 없이 즉시 반영됩니다)
- `.rootdir_example`을 `.rootdir`로 변경 후 작업 폴더 경로 지정 (즉시 반영됩니다)
- **데스크톱 GUI 실행**: `python gui_main.py` (또는 `python -m src.gui`)를 실행하여 대시보드, 필터링 테이블, 가분류 및 실제 이동/원복 기능을 사용할 수 있습니다.
- **주피터 노트북(CUI) 모드 실행**: `classify.ipynb`에서 셀을 실행하여 분석, 모의 분류(`pseudo_classify_mode`) 및 실제 폴더 이동을 수행할 수 있습니다.

---

# 2. 프로젝트 디렉터리 구조
콘솔(CUI) 출력과 데스크톱 GUI가 동일한 비즈니스 엔진(`VideoClassifier`)을 공유하도록 구성되어 있습니다:
- `src/core/`: 도메인 모델(`models.py`), 설정(`config.py`), `ffprobe` 메타데이터 추출기(`prober.py`), 화질 및 소형 영상 분포 평가 엔진(`evaluator.py`), 파일 매니저(`file_manager.py`)
- `src/service/`: 인메모리 캐시(`cache.py`) 및 캐시 동기화·분류 처리를 총괄하는 서비스(`classifier_service.py`)
- `src/cui/`: 콘솔/Jupyter용 테이블 포매터(`cui_printer.py`) 및 다중 조건 정렬 헬퍼(`sorting.py`) (하위 호환을 위한 `src/ui/` 재노출 지원)
- `src/gui/`: PyQt6 기반 데스크톱 GUI
  - `widgets/`: UI 위젯(`header_bar.py`, `bento_dashboard.py`, `filter_bar.py`, `video_table_view.py`, `footer_bar.py`, `custom_spinbox.py`)
  - `dialogs/`: 환경설정 다이얼로그 및 설정 행 위젯(`settings_dialog.py`, `setting_row.py`)
  - `styles/`: 테마 팔레트(`palettes.py`), QSS 빌더(`stylesheet.py`), SVG 아이콘(`icons.py`)
  - `main_window.py`, `table_model.py`, `proxy_model.py`, `delegates.py`, `worker.py`, `theme.py`
- `src/classifier.py` & `src/`: 최상위 `VideoClassifier` 통합 엔트리포인트 (`from src import VideoClassifier` 지원, 기존 `src.video_classify` 하위 호환 지원)

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
   - 전체 라이브러리 대비 절감 기여도가 미미한 소형 클립(`< 30MB`, `IGNORE_SMALL` 대용량 선별 집중 모드 시 자동 보존)

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

