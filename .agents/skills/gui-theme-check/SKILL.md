---
name: gui-theme-check
description: >-
  Use this skill when modifying PyQt6 GUI widgets, dialogs, styles, icons, or
  themes ("GUI 점검", "테마 점검"). Verifies offscreen Qt tests, dark/light
  contrast adaptation, real backend wiring, and UI tone consistency.
---

# GUI · 테마 집중 점검 (`gui-theme-check`)

## 점검 항목

1. **테마 적응·고대비 확인**
   - `src/gui/styles/palettes.py`, `stylesheet.py`, `theme.py` 변경 시 `ThemeAwareMixin` 중복 호출 방지 및 다크/라이트/시스템 모드 전환을 점검한다.
   - `src/gui/styles/icons.py` 변경 시 임포트 시점 소스 트리 디스크 쓰기 없이 캐시 지연 생성 원칙이 유지되는지 확인한다.
2. **실동작 · 문체 점검**
   - 백엔드 미연결 장식 컨트롤이나 작업 전 근거 없는 더미 상태 문구(`"준비 완료"` 등)가 없는지 확인한다.
   - 레이블·버튼·헤더는 간결한 명사구, 툴팁·설정 설명·대화상자는 일관된 존댓말인지 점검한다.
3. **단축키 · 문서 동기화**
   - `src/gui/window_layout.py` 단축키나 `src/gui/widgets/footer_bar.py` 주요 버튼 문구, `VideoTableModel.HEADERS` 열 수를 바꿨다면 `README.md`·`ARCHITECTURE.md`를 함께 갱신한다.
4. **오프스크린 Qt 테스트 실행**
   - `QT_QPA_PLATFORM=offscreen` 환경에서 GUI 스위트를 집중 실행한 뒤 전체 게이트로 마무리한다:
     - `.venv\Scripts\python.exe -m unittest tests.test_gui_models tests.test_gui_settings tests.test_gui_tasks tests.test_gui_theme tests.test_gui_widgets tests.test_gui_window -v`
     - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1`
