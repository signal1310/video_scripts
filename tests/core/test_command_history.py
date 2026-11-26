"""FFmpeg 명령 기록 링 버퍼: 상한, 스냅샷 불변성, 리스너, 스레드 안전, 표시 문자열을 검증"""

import threading
import unittest
from datetime import datetime

from src.core.command_history import (
    COMMAND_HISTORY_LIMIT,
    COMMAND_STAGES,
    STAGE_CAPABILITY,
    STAGE_FRAME_ANALYSIS,
    STAGE_MAIN_ENCODE,
    STAGE_PROBING,
    STAGE_SAMPLE_ENCODE,
    STAGE_VMAF,
    CommandRecord,
    add_listener,
    clear,
    command_scope,
    format_command_line,
    input_path_of,
    latest,
    record_finish,
    record_start,
    remove_listener,
    snapshot,
    target_name_of,
)


class CommandHistoryTestCase(unittest.TestCase):
    """각 테스트가 모듈 전역 버퍼와 리스너를 비운 상태에서 시작하도록 정리"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()


class RecordLifecycleTests(CommandHistoryTestCase):
    """기록 시작, 종료, 스냅샷 값과 불변성 검증"""

    def test_record_start_keeps_running_state_and_finish_fills_exit_code(self) -> None:
        """시작 직후에는 종료 코드와 소요가 비고, 종료를 기록하면 채워지는지 검증"""
        handle = record_start(
            STAGE_MAIN_ENCODE,
            ["ffmpeg", "-i", r"C:\clips\clip.mp4", "out.mp4.part"],
            target=r"C:\clips\clip.mp4",
            source_path=r"C:\clips\clip.mp4",
        )
        running = snapshot()
        self.assertEqual(len(running), 1)
        self.assertIsNone(running[0].exit_code)
        self.assertIsNone(running[0].elapsed_sec)
        self.assertEqual(running[0].stage, STAGE_MAIN_ENCODE)
        self.assertEqual(running[0].target_name, "clip.mp4")
        self.assertEqual(running[0].source_path, r"C:\clips\clip.mp4")
        self.assertIsInstance(running[0].timestamp, datetime)
        self.assertEqual(handle.seq, running[0].seq)
        self.assertEqual(handle.stage, STAGE_MAIN_ENCODE)
        self.assertEqual(handle.target_name, "clip.mp4")
        self.assertEqual(handle.source_path, r"C:\clips\clip.mp4")
        self.assertEqual(
            handle.args, ("ffmpeg", "-i", "C:\\clips\\clip.mp4", "out.mp4.part")
        )

        record_finish(handle, 0, elapsed_sec=12.34)
        finished = snapshot()[0]
        self.assertEqual(finished.exit_code, 0)
        self.assertEqual(finished.source_path, r"C:\clips\clip.mp4")
        self.assertAlmostEqual(finished.elapsed_sec or 0.0, 12.34, places=2)

    def test_record_start_without_source_path_keeps_it_empty(self) -> None:
        """원본 경로를 주지 않은 기록은 빈 문자열로 남는지 검증"""
        handle = record_start(STAGE_CAPABILITY, ["ffmpeg", "-encoders"])
        self.assertEqual(handle.source_path, "")
        self.assertEqual(snapshot()[0].source_path, "")

    def test_record_finish_without_elapsed_uses_measured_time(self) -> None:
        """소요 시간을 주지 않으면 시작부터 잰 값을 0 이상으로 채우는지 검증"""
        handle = record_start(STAGE_PROBING, ["ffprobe", "-v", "error", "a.mkv"], target="a.mkv")
        handle.mark_started()
        record_finish(handle, 1)
        record = snapshot()[0]
        self.assertEqual(record.exit_code, 1)
        elapsed = record.elapsed_sec
        assert elapsed is not None
        self.assertGreaterEqual(elapsed, 0.0)

    def test_snapshot_is_immutable_copy(self) -> None:
        """스냅샷이 불변 튜플이고, 이후 종료 기록이 이전 스냅샷 값을 바꾸지 않는지 검증"""
        handle = record_start(STAGE_FRAME_ANALYSIS, ["ffmpeg", "-i", "a.mp4"], target="a.mp4")
        first = snapshot()
        record_finish(handle, 0)
        self.assertIsInstance(first, tuple)
        self.assertIsInstance(first[0], CommandRecord)
        self.assertIsNone(first[0].exit_code)
        self.assertEqual(snapshot()[0].exit_code, 0)
        with self.assertRaises((AttributeError, TypeError)):
            CommandRecord.__setattr__(first[0], "exit_code", 3)


class CommandScopeTests(CommandHistoryTestCase):
    """구간 감싸기 헬퍼가 정상 종료와 예외 모두에서 종료 기록을 남기는지 검증"""

    def test_scope_records_exit_code_and_elapsed(self) -> None:
        """핸들에 종료 코드를 채우면 종료 기록에 그 값과 소요, 원본 경로가 남는지 검증"""
        with command_scope(
            STAGE_VMAF, ["ffmpeg", "-i", "x.mp4"], target="x.mp4", source_path="C:/work/x.mp4"
        ) as handle:
            handle.exit_code = 0
        record = snapshot()[0]
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.source_path, "C:/work/x.mp4")
        self.assertIsNotNone(record.elapsed_sec)

    def test_scope_finishes_when_cancelled_by_exception(self) -> None:
        """취소처럼 예외로 빠져나가도 실행 중 상태로 남지 않고 종료 기록이 남는지 검증"""
        with (
            self.assertRaises(InterruptedError),
            command_scope(STAGE_SAMPLE_ENCODE, ["ffmpeg", "-i", "y.mp4"], target="y.mp4"),
        ):
            raise InterruptedError("샘플 인코딩이 취소되었습니다")
        record = snapshot()[0]
        self.assertIsNone(record.exit_code)
        self.assertIsNotNone(record.elapsed_sec)


class SnapshotAndClearTests(CommandHistoryTestCase):
    """보관 상한, 지우기, 오래된 기록부터의 정렬 검증"""

    def test_buffer_keeps_only_latest_records(self) -> None:
        """상한을 넘겨 기록하면 가장 오래된 기록부터 밀려나는지 검증"""
        handles = [
            record_start(STAGE_PROBING, ["ffprobe", f"f{index}.mp4"], target=f"f{index}.mp4")
            for index in range(COMMAND_HISTORY_LIMIT + 5)
        ]
        for handle in handles:
            record_finish(handle, 0)
        records = snapshot()
        self.assertEqual(len(records), COMMAND_HISTORY_LIMIT)
        self.assertEqual(records[-1].target_name, f"f{COMMAND_HISTORY_LIMIT + 4}.mp4")
        self.assertEqual(records[0].target_name, "f5.mp4")

    def test_clear_empties_buffer(self) -> None:
        """지우기가 보관한 기록을 모두 없애는지 검증"""
        record_finish(record_start(STAGE_PROBING, ["ffprobe", "a.mp4"], target="a.mp4"), 0)
        clear()
        self.assertEqual(snapshot(), ())

    def test_latest_returns_only_the_newest_record(self) -> None:
        """최신 조회가 마지막 한 건만 돌려주고, 비어 있으면 None인지 검증"""
        self.assertIsNone(latest())
        record_finish(record_start(STAGE_PROBING, ["ffprobe", "a.mp4"], target="a.mp4"), 0)
        second = record_start(STAGE_VMAF, ["ffmpeg", "-i", "b.mp4"], target="b.mp4")
        newest = latest()
        assert newest is not None
        self.assertEqual(newest.seq, second.seq)
        self.assertEqual(newest.target_name, "b.mp4")


class ListenerTests(CommandHistoryTestCase):
    """리스너 등록, 해제, 알림 시점 검증"""

    def test_listener_called_on_start_finish_and_clear(self) -> None:
        """시작, 종료, 지우기마다 리스너가 호출되는지 검증"""
        calls: list[int] = []

        def listener() -> None:
            calls.append(1)

        add_listener(listener)
        self.addCleanup(remove_listener, listener)
        handle = record_start(STAGE_CAPABILITY, ["ffmpeg", "-version"])
        record_finish(handle, 0)
        clear()
        self.assertEqual(len(calls), 3)

    def test_removed_listener_is_not_called(self) -> None:
        """해제한 리스너는 더 이상 호출되지 않고 중복 등록도 한 번만 남는지 검증"""
        calls: list[int] = []

        def listener() -> None:
            calls.append(1)

        add_listener(listener)
        add_listener(listener)
        remove_listener(listener)
        record_finish(record_start(STAGE_PROBING, ["ffprobe", "a.mp4"]), 0)
        self.assertEqual(calls, [])



class ThreadSafetyTests(CommandHistoryTestCase):
    """여러 스레드가 동시에 기록해도 상한 안에서 일관된 스냅샷을 얻는지 검증"""

    def test_concurrent_records_stay_consistent(self) -> None:
        """워커 스레드들이 동시에 시작과 종료를 기록해도 개수와 값이 맞는지 검증"""
        worker_count = 8
        per_worker = 25
        errors: list[BaseException] = []

        def worker(worker_index: int) -> None:
            try:
                for index in range(per_worker):
                    handle = record_start(
                        STAGE_PROBING,
                        ["ffprobe", f"w{worker_index}_{index}.mp4"],
                        target="clip.mp4",
                    )
                    record_finish(handle, 0)
            except BaseException as error:  # pragma: no cover - 실패 시에만 실행
                errors.append(error)

        threads = [
            threading.Thread(target=worker, args=(index,)) for index in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        records = snapshot()
        self.assertEqual(len(records), worker_count * per_worker)
        self.assertTrue(all(record.exit_code == 0 for record in records))
        self.assertEqual(len({record.seq for record in records}), len(records))


class FormattingTests(unittest.TestCase):
    """표시용 명령줄과 대상 파일 이름 계산 검증"""

    def test_format_command_line_quotes_only_when_needed(self) -> None:
        """공백이나 따옴표가 있는 인자만 큰따옴표로 감싸는지 검증"""
        self.assertEqual(
            format_command_line(["ffmpeg", "-i", "C:\\my clips\\a b.mp4", "-c:v", "hevc_nvenc"]),
            'ffmpeg -i "C:\\my clips\\a b.mp4" -c:v hevc_nvenc',
        )
        self.assertEqual(format_command_line([""]), '""')
        self.assertEqual(format_command_line(['a"b']), '"a\\"b"')

    def test_target_name_of_strips_folders(self) -> None:
        """대상 경로에서 파일 이름만 남기고 빈 값은 빈 문자열로 두는지 검증"""
        self.assertEqual(target_name_of("C:\\clips\\clip.mp4"), "clip.mp4")
        self.assertEqual(target_name_of("C:/clips/clip.mp4"), "clip.mp4")
        self.assertEqual(target_name_of("clip.mkv"), "clip.mkv")
        self.assertEqual(target_name_of(""), "")

    def test_input_path_of_finds_first_input(self) -> None:
        """인자 목록에서 첫 입력 경로를 찾고 없으면 빈 문자열을 돌려주는지 검증"""
        self.assertEqual(input_path_of(["ffmpeg", "-i", "a.mp4", "-i", "b.mp4"]), "a.mp4")
        self.assertEqual(input_path_of(["ffmpeg", "-version"]), "")
        self.assertEqual(input_path_of(["ffmpeg", "-i"]), "")


class StageConstantTests(unittest.TestCase):
    """단계 이름 상수와 선택 목록 검증"""

    def test_stage_constants_cover_filter_list(self) -> None:
        """단계 상수가 모두 필터 목록에 들어 있고 중복이 없는지 검증"""
        self.assertEqual(
            COMMAND_STAGES,
            ("화질 분석", "샘플 인코딩", "본 인코딩", "화질 비교", "프로빙", "VMAF 측정", "능력 시험"),
        )
        self.assertEqual(len(set(COMMAND_STAGES)), len(COMMAND_STAGES))


if __name__ == "__main__":
    unittest.main()

