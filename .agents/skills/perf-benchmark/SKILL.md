---
name: perf-benchmark
description: >-
  Use this skill when implementing or evaluating performance optimizations from
  TODO.md ("성능 측정", "벤치마크", "최적화 비교"). Sets up isolated temporary
  media fixtures, benchmarks before/after safely, and leaves no artifacts.
---

# 격리 성능 벤치마크 런북 (`perf-benchmark`)

## 절차

1. **대상 계약 확인**
   - 최적화 전 `TODO.md` 해당 설계 항목과 기존 단위 테스트(`tests/test_prober.py`, `tests/test_file_manager.py`, `tests/test_service_cache.py`, `tests/test_e2e_classifier.py`) 계약을 먼저 읽는다.
2. **격리 픽스처 구성**
   - 실제 미디어 폴더나 `.rootdir`를 절대 사용하지 말고 Python `tempfile.TemporaryDirectory` 안에 테스트용 합성 영상(또는 마이크로 벤치마크 대상 객체)을 생성한다.
   - 워커 수나 로컬 경로·디스크 사양 수치를 코드·문서에 하드코딩하지 않는다.
3. **전/후 동일 조건 측정**
   - 워밍업 후 반복 측정(`time.perf_counter`)으로 중앙값·총 소요 시간을 기록하고, I/O·디코딩·결과 일치성(기존 테스트 기대값과 동일 결과 반환)을 검증한다.
   - `ProcessPoolExecutor` / `ThreadPoolExecutor` / `ffprobe` 변경 시 취소(`InterruptedError`) 및 타임아웃 회수(`_terminate_and_reap`) 회귀 여부를 반드시 함께 시험한다.
4. **정리 및 전체 검증**
   - 측정용 임시 스크립트·산출물이 저장소에 남지 않도록 정리한다(필요 시 대화용 scratch 디렉터리만 사용).
   - 최적화 반영 후 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1` 전체 통과를 확인한다.
