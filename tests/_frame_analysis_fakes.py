"""화질 분석 실행기 대역과 명령 해석 도우미(FFmpeg 없이 측정 흐름 검증)"""

import json
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from src.core.resolution_tiers import RESOLUTION_TIERS, calc_scaled_dimensions

_SCALE_RE: re.Pattern[str] = re.compile(r"scale=(\d+):(\d+)")
_DISTORTED_RE: re.Pattern[str] = re.compile(
    r"\[d(\d+)\]scale=(\d+):(\d+):flags=\w+,scale=(\d+):(\d+):flags=\w+"
)
_STATS_RE: re.Pattern[str] = re.compile(r"stats_file=([^\[\];]+)")
_VMAF_LOG_RE: re.Pattern[str] = re.compile(r"log_path=([^:]+):log_fmt=json")
_BLACKFRAME_RE: re.Pattern[str] = re.compile(r"pblack:(\d+)")


def tier_of_sizes(out_width: int, out_height: int, active_width: int, active_height: int) -> str:
    """축소 크기와 활성 화면 크기로 규격 키를 찾음. 알 수 없으면 빈 문자열"""
    for tier in RESOLUTION_TIERS:
        if calc_scaled_dimensions(active_width, active_height, tier) == (out_width, out_height):
            return tier.key
    return ""


def tier_of_chain(chain: str) -> str:
    """필터 체인의 첫 축소 크기와 활성 화면 크기로 규격 키를 찾음. 알 수 없으면 빈 문자열"""
    matches = _SCALE_RE.findall(chain)
    if len(matches) < 2:
        return ""
    return tier_of_sizes(int(matches[0][0]), int(matches[0][1]), int(matches[1][0]), int(matches[1][1]))


def metric_of_chain(chain: str) -> str:
    """필터 체인이 재는 지표 이름 반환(`ssim`, `vmaf`, 모르면 빈 문자열)"""
    if "libvmaf" in chain:
        return "vmaf"
    if "ssim=" in chain:
        return "ssim"
    return ""


def chain_tier_logs(chain: str) -> dict[str, str]:
    """다중 규격 SSIM 필터 체인에서 규격 키별 stats 파일 이름"""
    stats_names = [name.strip() for name in _STATS_RE.findall(chain)]
    tiers: dict[str, str] = {}
    for index, out_width, out_height, active_width, active_height in _DISTORTED_RE.findall(chain):
        order = int(index)
        if order >= len(stats_names):
            continue
        key = tier_of_sizes(int(out_width), int(out_height), int(active_width), int(active_height))
        if key:
            tiers[key] = stats_names[order]
    return tiers


class FakeStderrRunner:
    """명령 종류별 표준 오류와 지표 로그를 만드는 실행기

    - `black_ratio`: CLI 스캔 지점을 검은 화면으로 볼 비율
    - `boxes`: CLI 스캔이 돌려줄 cropdetect 사각형 목록
    - `ssim_by_tier`, `vmaf_by_tier`: 규격 키별 지표 값, 없으면 통과 값
    - `on_call`: 명령 실행 직전에 호출(취소 신호 주입 등)
    """

    def __init__(
        self,
        *,
        black_ratio: float = 0.0,
        black_returncode: int = 0,
        boxes: Sequence[tuple[int, int, int, int]] = (),
        ssim_by_tier: dict[str, tuple[float, float]] | None = None,
        vmaf_by_tier: dict[str, float] | None = None,
        fail_metric: bool = False,
        on_call: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.black_ratio: float = black_ratio
        self.black_returncode: int = black_returncode
        self.boxes: tuple[tuple[int, int, int, int], ...] = tuple(boxes)
        self.ssim_by_tier: dict[str, tuple[float, float]] = ssim_by_tier or {}
        self.vmaf_by_tier: dict[str, float] = vmaf_by_tier or {}
        self.fail_metric: bool = fail_metric
        self.on_call: Callable[[list[str]], None] | None = on_call
        self.commands: list[list[str]] = []
        self._lock = threading.Lock()

    def count_commands(self, marker: str) -> int:
        """지정 문자열이 든 명령 개수"""
        with self._lock:
            return sum(1 for args in self.commands if any(marker in token for token in args))

    def tiers_measured(self, metric: str) -> list[str]:
        """지표별로 측정한 규격 키 목록(중복 포함)"""
        found: list[str] = []
        with self._lock:
            commands = list(self.commands)
        for args in commands:
            if "-filter_complex" not in args:
                continue
            chain = args[args.index("-filter_complex") + 1]
            if metric_of_chain(chain) != metric:
                continue
            if metric == "ssim":
                found.extend(chain_tier_logs(chain))
            else:
                found.append(tier_of_chain(chain))
        return found

    def __call__(self, args: list[str], work_dir: str) -> tuple[int, str]:
        """명령을 해석해 표준 오류와 지표 로그를 반환"""
        with self._lock:
            self.commands.append(args)
        if self.on_call is not None:
            self.on_call(args)
        if any("blackframe" in token for token in args):
            if self.black_returncode != 0:
                return (self.black_returncode, "")
            black = 100 if self.black_ratio >= 0.5 else 0
            lines = f"[Parsed_blackframe_0 @ 0x1] frame:0 pblack:{black} pts:0\n"
            lines += "".join(
                f"[Parsed_cropdetect_0 @ 0x1] crop={width}:{height}:{left}:{top}\n"
                for width, height, left, top in self.boxes
            )
            return (0, lines)
        if "-filter_complex" in args:
            return self._metric(args, work_dir)
        return (0, "")

    def _metric(self, args: list[str], work_dir: str) -> tuple[int, str]:
        chain = args[args.index("-filter_complex") + 1]
        if self.fail_metric:
            return (1, "")
        if "libvmaf" in chain:
            tier = tier_of_chain(chain)
            match = _VMAF_LOG_RE.search(chain)
            if not tier or match is None:
                return (1, "")
            score = self.vmaf_by_tier.get(tier, 99.0)
            payload = {"pooled_metrics": {"vmaf": {"mean": score}}}
            Path(work_dir, match.group(1)).write_text(json.dumps(payload), encoding="utf-8")
            return (0, "")
        logs = chain_tier_logs(chain)
        if not logs:
            return (1, "")
        for tier, name in logs.items():
            score, db = self.ssim_by_tier.get(tier, (0.9995, 30.0))
            text = f"n:1 Y:1 U:1 V:1 All:{score:.5f} ({db:.2f})\n"
            Path(work_dir, name).write_text(text, encoding="utf-8")
        return (0, "")
