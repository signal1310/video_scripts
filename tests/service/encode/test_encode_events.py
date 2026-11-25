"""인코딩 진행 이벤트의 본 인코딩 단계 이벤트와 진행 중계 검증"""

import unittest

from src.service.encode.encode_events import (
    EVENT_PHASE,
    EVENT_PROGRESS,
    PHASE_MAIN,
    EncodeEvent,
    ProgressRelay,
    main_phase_event,
)


class EncodeEventsTests(unittest.TestCase):
    """본 인코딩 단계 이벤트와 0.5초 주기 진행 이벤트 검증"""

    def test_main_phase_event_clears_sample_badge_with_fixed_quality(self) -> None:
        event = main_phase_event(3, "C:/work/a.mp4", 24.0)
        self.assertEqual((event.kind, event.batch_id, event.source_path), (EVENT_PHASE, 3, "C:/work/a.mp4"))
        assert event.phase is not None
        self.assertEqual(event.phase.stage, PHASE_MAIN)
        self.assertEqual(event.phase.baseline_quality, 24.0)
        self.assertEqual(event.phase.current_quality, 24.0)

    def test_progress_relay_emits_progress_for_a_complete_block(self) -> None:
        events: list[EncodeEvent] = []
        relay = ProgressRelay(
            emit=events.append,
            batch_id=1,
            source_path="C:/work/a.mp4",
            duration_sec=10.0,
            elapsed=lambda: 2.0,
            clock=lambda: 0.0,
        )
        self.assertIsNone(relay.feed("no separator"))
        for line in (
            "frame=30",
            "fps=30",
            "total_size=1000",
            "out_time=00:00:02.000000",
            "speed=1.0x",
            "progress=continue",
        ):
            relay.feed(line)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, EVENT_PROGRESS)
        self.assertEqual(event.elapsed_sec, 2.0)


if __name__ == "__main__":
    unittest.main()
