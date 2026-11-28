r"""판별 기준(과화질 배수) 변경 시 표 갱신 경로의 단계별 소요 시간 측정 스크립트

- 합성 평가 결과 5000행으로 설정 변경의 각 단계(재평가, 표 재적재, 정렬 키, 열 필터 정리,
  구간 버킷, 그룹 재구성, 실제 창 반영)를 각각 잼
- 임시 폴더의 작은 가짜 파일만 쓰고 실제 미디어, data 폴더는 읽거나 쓰지 않음(테스트 격리 샌드박스)
- 사용: .venv\\Scripts\\python.exe scripts\\measure_threshold_change.py
"""

import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.models import QualityZone, VideoProps

ROW_COUNT: int = 5000
REPEAT: int = 3
# - 실제 미디어를 건드리지 않도록 존재하지 않는 평가 기준 폴더 이름만 씀
MEASURE_ROOT: str = "measure_work"


def _video(index: int) -> VideoProps:
    return VideoProps(
        filename=f"clip_{index:05d}.mp4",
        width=1920 if index % 3 else 1280,
        height=1080 if index % 3 else 720,
        rotate_type=0,
        fps=30.0 if index % 2 else 25.0,
        vid_kbps=3000 + (index % 40) * 250,
        aud_kbps=128,
        vid_size_MB=100.0 + (index % 17) * 12.5,
        duration=60.0 + (index % 23) * 30.0,
        codec=("h264", "hevc", "av1", "vp9")[index % 4],
        keyframe_interval=0.5 + (index % 7) * 0.5,
    )


def _timed(label: str, fn: Callable[[], Any]) -> tuple[str, float]:
    best = float("inf")
    for _ in range(REPEAT):
        started = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - started)
    return label, best


def _changed_config() -> ClassifierConfig:
    base = ClassifierConfig()
    return replace(base, thresholds=replace(base.thresholds, over_bitrate_ratio=2.35))


def _make_folder() -> str:
    """격리 샌드박스 안에 작은 가짜 파일 5000개를 만들고 폴더를 반환"""
    from tests._sandbox import install_test_sandbox

    folder = tempfile.mkdtemp(dir=install_test_sandbox())
    for index in range(ROW_COUNT):
        with open(os.path.join(folder, _video(index).filename), "wb") as handle:
            handle.write(b"\0" * 64)
    return folder


def measure_table() -> list[tuple[str, float]]:
    """표 모델 단계별 소요 시간: 재적재, 정렬 키, 열 필터 정리, 구간 버킷, 그룹 재구성"""
    from src.core.evaluation.evaluator import VideoEvaluator
    from src.gui.table.column_filter_values import prune_stale_filter_rules
    from src.gui.table.column_filters import ColumnFilterRule
    from src.gui.table.numeric_buckets import build_numeric_buckets
    from src.gui.table.proxy_model import VideoSortFilterProxyModel
    from src.gui.table.table_grouping import VideoGroupProxyModel
    from src.gui.table.table_model import VideoTableModel

    base = ClassifierConfig()
    changed = _changed_config()
    videos = [_video(i) for i in range(ROW_COUNT)]
    previous = VideoEvaluator.evaluate_all(MEASURE_ROOT, videos, base).evaluations
    next_rows = VideoEvaluator.evaluate_all(MEASURE_ROOT, videos, changed).evaluations

    model = VideoTableModel(config=base)
    model.set_data_source(previous, root_dir=MEASURE_ROOT, config=base)
    model.set_sort_stack([(VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder)])
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    group = VideoGroupProxyModel()
    group.setSourceModel(proxy)

    rules = {
        VideoTableModel.COL_RATIO: ColumnFilterRule(selected_values=frozenset({"ratio:1.5_2"})),
        VideoTableModel.COL_SAVED_SIZE: ColumnFilterRule(min_value=5.0),
        VideoTableModel.COL_ZONE: ColumnFilterRule(selected_values=frozenset({QualityZone.OVER.value})),
    }
    kinds = [
        spec.bucket_kind
        for spec in VideoTableModel.COLUMN_SPECS.values()
        if spec.bucket_kind.value != "none"
    ]

    def refilter() -> None:
        proxy.clear_all_column_filters()
        for col, rule in rules.items():
            if rule.is_active(VideoTableModel.get_column_spec(col).data_type):
                proxy.set_column_filter(col, rule)

    results = [
        _timed(
            "re-evaluation",
            lambda: VideoEvaluator.evaluate_all(MEASURE_ROOT, videos, changed),
        ),
        _timed(
            "table data source reload",
            lambda: model.set_data_source(next_rows, root_dir=MEASURE_ROOT, config=changed),
        ),
        _timed(
            "sort keys + order",
            lambda: model.set_sort_stack(
                [
                    (VideoTableModel.COL_NAME, Qt.SortOrder.AscendingOrder),
                    (VideoTableModel.COL_RATIO, Qt.SortOrder.DescendingOrder),
                    (VideoTableModel.COL_SAVED_SIZE, Qt.SortOrder.DescendingOrder),
                ]
            ),
        ),
        _timed("column filter prune", lambda: prune_stale_filter_rules(rules, changed)),
        _timed(
            "numeric bucket rebuild",
            lambda: [build_numeric_buckets(kind, changed) for kind in kinds],
        ),
        _timed("group rebuild", group._rebuild),  # pyright: ignore[reportPrivateUsage]
        _timed("column filter refilter (3 columns)", refilter),
    ]
    return results



def measure_end_to_end(*, facts_match: bool) -> list[tuple[str, float]]:
    """실제 설정 변경 한 번의 소요 시간과 GUI 스레드 파일 상태 확인 횟수

    - facts_match면 폐기 기록의 인코더와 화질 기준값이 지금 평가와 같아 원본 상태 확인까지 가는 경우
    """
    from tests._factories import make_report

    import src.service.encode.encode_previous_discard as previous_discard_mod
    from src.classifier import VideoClassifier
    from src.core.encode.encode_previous_discard import DiscardFact
    from src.core.evaluation.evaluator import VideoEvaluator
    from src.gui.main_window import MainWindow
    from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache

    base = ClassifierConfig()
    changed = _changed_config()
    folder = _make_folder()
    videos = [_video(i) for i in range(ROW_COUNT)]
    rows = VideoEvaluator.evaluate_all(folder, videos, base).evaluations
    quality = rows[0].applied_cq if facts_match else -1.0
    facts: dict[str, DiscardFact] = {}
    for video in videos:
        path = os.path.join(folder, video.filename)
        stat = os.stat(path)
        facts[path] = DiscardFact(stat.st_size, stat.st_mtime_ns, base.encode.encode_codec, quality)
    clf = VideoClassifier(root_dir=folder, config=base)
    DEFAULT_CACHE_REGISTRY.put(folder, TableCache(folder, videos, include_keyframe=True))
    report = make_report(rows, root_dir=folder)
    window = MainWindow(classifier=clf)
    window.show()
    window.apply_report(report)
    window.dialogs.prewarm_settings_dialog(
        window._effective_config(),  # pyright: ignore[reportPrivateUsage]
        folder,
        app_state=window._app_state,  # pyright: ignore[reportPrivateUsage]
    )
    for _ in range(5):
        QApplication.processEvents()

    history = window._encode._history  # pyright: ignore[reportPrivateUsage]
    original_facts = type(history).discarded_facts
    type(history).discarded_facts = lambda self: facts

    real_stat = os.stat
    tally = {"stat": 0.0}

    def counted_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        if threading.current_thread() is threading.main_thread():
            tally["stat"] += 1
        return real_stat(path, *args, **kwargs)

    previous_discard_mod.os.stat = counted_stat
    try:
        sync_times: list[float] = []
        walls: list[float] = []
        for _ in range(REPEAT):
            tally["stat"] = 0.0
            started = time.perf_counter()
            window._on_criteria_changed(  # pyright: ignore[reportPrivateUsage]
                changed.thresholds, changed.savings
            )
            sync_times.append(time.perf_counter() - started)
            for _ in range(6000):
                QApplication.processEvents()
                if not window._evaluator.pending:  # pyright: ignore[reportPrivateUsage]
                    break
                time.sleep(0.005)
            walls.append(time.perf_counter() - started)
    finally:
        previous_discard_mod.os.stat = real_stat
        type(history).discarded_facts = original_facts
    window.close()
    # - 닫은 창과 미리 만든 설정 대화상자를 이벤트 루프에서 정리해 종료 시 네이티브 크래시를 피함
    window.deleteLater()
    QApplication.processEvents()
    return [
        ("change: gui os.stat calls", tally["stat"]),
        ("change: sync part ms (best)", min(sync_times) * 1000.0),
        ("change: end to end ms (best)", min(walls) * 1000.0),
        ("change: end to end ms (worst)", max(walls) * 1000.0),
    ]


def main() -> int:
    """표 단계와 실제 설정 변경의 소요 시간을 재서 출력"""
    app = QApplication.instance() or QApplication([])
    _ = app
    table = measure_table()
    print(f"table steps, rows={ROW_COUNT}, best of {REPEAT}")
    for name, value in table:
        print(f"  {name:<40}{value * 1000:9.1f} ms", flush=True)
    print(f"  {'sum':<40}{sum(v for _n, v in table) * 1000:9.1f} ms", flush=True)

    for facts_match, label in ((True, "discard facts match"), (False, "discard facts differ")):
        print(f"window end to end, {label}")
        for name, value in measure_end_to_end(facts_match=facts_match):
            print(f"  {name:<40}{value:9.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
