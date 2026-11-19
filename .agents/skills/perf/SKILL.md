---
name: perf
description: >-
  Use this skill when implementing or evaluating performance optimizations
  ("성능 측정", "벤치마크", "최적화 비교"). Sets up isolated temporary
  media fixtures, benchmarks before/after safely, and leaves no artifacts.
---

# 격리 성능 벤치마크 (`perf`)

1. 계약 확인: 대상 모듈과 대응하는 `tests/` 하위 폴더 테스트의 기대값(있으면 `TODO.md` 설계 항목)을 먼저 읽음
2. 픽스처: 실제 미디어, `data/` 대신 `TemporaryDirectory` 안의 합성 영상이나 마이크로 벤치마크 객체 사용. 워커 수, 경로, 디스크 사양 하드코딩 금지
3. 측정: 워밍업 후 `time.perf_counter`로 반복 측정(중앙값, 총 시간)하고 전/후 결과가 같은지 검증. 프로세스/스레드 풀이나 `ffprobe` 변경 시 `InterruptedError` 취소와 `_terminate_and_reap` 회수 회귀도 확인
4. 정리: 임시 스크립트, 산출물을 남기지 않고 `check.ps1` 통과 확인
