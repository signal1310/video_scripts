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
2. 배치: 같은 주제의 기존 `tests/test_*.py`에 우선 추가하고(GUI는 `test_gui_*.py`), 기존 파일이 다중 책임이 될 때만 새 파일 생성
3. 작성
   - 파일 생성은 `tempfile.TemporaryDirectory` 안에서만 수행하고, 경로 의존 로직은 `get_state_path`, `get_rootdir_path` 등을 `unittest.mock.patch`로 대체
   - GUI 테스트는 모듈 최상단에서 `_APP = QApplication.instance() or QApplication([])`로 앱을 하나만 만들고 `QT_QPA_PLATFORM=offscreen`에서 동작하게 작성
4. 실행: 대상 테스트를 먼저 돌리고 완료 전 `check.ps1`을 1회 실행

```powershell
.venv\Scripts\python.exe -m unittest tests.test_<대상> -b

# GUI 스위트 전체
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m unittest discover -s tests -t . -p "test_gui_*.py" -b
```
