---
name: test
description: >-
  Use this skill when writing or modifying unit tests under tests/ ("테스트 추가",
  "테스트 작성"). Applies the sandbox isolation pattern so tests never touch real
  config data, presets, registry, or user media folders.
---

# 격리 테스트 작성 (`test`)

격리 규칙은 `AGENTS.md` 3절을 따르고, 여기서는 작성 절차만 다룬다.

1. 격리: `tests/__init__.py`가 임포트 시 `install_test_sandbox()`를 자동 호출하므로 `tests` 패키지 하위로 작성(쓰기 허용 범위는 `tests/_sandbox.py` 참고)
2. 배치: 대상 모듈의 `src` 위치를 따른 `tests/` 하위 폴더(예: `src/core/encode/encode_gate.py`의 테스트는 `tests/core/encode/test_encode_gate.py`)의 같은 주제 기존 파일에 우선 추가하고, 기존 파일이 다중 책임이 될 때만 새 파일 생성
   - 여러 영역을 함께 쓰는 창 단위 GUI 테스트는 `tests/gui/`, `scripts/`와 저장소 메타 테스트는 `tests/tools/`, 공용 도우미는 `tests/` 바로 아래
   - 새 하위 폴더에는 `__init__.py`를 둠(점 경로 로딩과 `check.ps1` 샤드 탐색에 필요)
   - 새 `src` 모듈은 같은 위치의 `test_<모듈>.py`가 있어야 하고, 새 함수는 전체 테스트에서 한 번 이상 실행되어야 함(`check.ps1` 5단계, `AGENTS.md` 3절). 실패하면 예외 파일에 넣지 말고 테스트를 추가
   - 예외 파일에 항목이 있는 모듈을 수정한다면 그 모듈의 항목(대응 테스트, 미실행 함수)을 같은 커밋에서 해소하고 `--write`로 목록에서 지움
   - 타이머로 호출되는 함수는 타이머 만료를 기다리지 말고 함수를 직접 호출하거나 타이머를 직접 발동해 결정적으로 실행(실행 여부 불안정 목록은 임시 보관소이며 새 항목을 넣지 않음)
3. 작성
   - 파일 생성은 `tempfile.TemporaryDirectory` 안에서만 수행하고, 경로 의존 로직은 `get_state_path`, `get_rootdir_path` 등을 `unittest.mock.patch`로 대체
   - GUI 테스트는 모듈 최상단에서 `_APP = QApplication.instance() or QApplication([])`로 앱을 하나만 만들고 `QT_QPA_PLATFORM=offscreen`에서 동작하게 작성
4. 실행: 대상 테스트를 먼저 돌리고 완료 전 `check.ps1`을 1회 실행

```powershell
.venv\Scripts\python.exe -m unittest tests.<폴더>.test_<대상> -b   # 예: tests.core.encode.test_encode_gate

# GUI 스위트 전체
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m unittest discover -s tests/gui -t . -b
```
