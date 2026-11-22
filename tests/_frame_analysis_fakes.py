"""화질 분석 실행기 대역과 명령 해석 도우미(FFmpeg 없이 측정 흐름 검증)"""

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from src.core.resolution_tiers import RESOLUTION_TIERS, calc_scaled_dimensions

_SCALE_RE: re.Pattern[str] = re.compile(r"scale=(\d+):(\d+)")


def tier_of_chain(chain: str) -> str:
    """필터 체인의 축소 크기와 활성 화면 크기로 규격 키를 찾음. 알 수 없으면 빈 문자열"""
    matches = _SCALE_RE.findall(chain)
    if len(matches) < 2:
        return ""
    out_width, out_height = int(matches[0][0]), int(matches[0][1])
    active_width, active_height = int(matches[1][0]), int(matches[1][1])
    for tier in RESOLUTION_TIERS:
        if calc_scaled_dimensions(active_width, active_height, tier) == (out_width, out_height):
            return tier.key
    return ""


def metric_of_chain(chain: str) -> str:
    """필터 체인이 재는 지표 이름 반환(`ssim`, `vmaf`, 모르면 빈 문자열)"""
    if "libvmaf" in chain:
        return "vmaf"
    if "ssim=" in chain:
        return "ssim"
    return ""


class FakeStderrRunner:
    """명령 종류별 표준 오류와 지표 로그를 만드는 실행기

    - `black_ratio`: 구간 길이 대비 검은 구간 비율
    - `boxes`: cropdetect가 돌려줄 사각형 목록
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

    def count_commands(self, marker: str) -> int:
        """지정 문자열이 든 명령 개수"""
        return sum(1 for args in self.commands if any(marker in token for token in args))

    def tiers_measured(self, metric: str) -> list[str]:
        """지표별로 측정한 규격 키 목록(중복 포함)"""
        found: list[str] = []
        for args in self.commands:
            if "-filter_complex" not in args:
                continue
            chain = args[args.index("-filter_complex") + 1]
            if metric_of_chain(chain) == metric:
                found.append(tier_of_chain(chain))
        return found

    def __call__(self, args: list[str], work_dir: str) -> tuple[int, str]:
        """명령을 해석해 표준 오류와 지표 로그를 반환"""
        self.commands.append(args)
        if self.on_call is not None:
            self.on_call(args)
        if any("blackdetect" in token for token in args):
            if self.black_returncode != 0:
                return (self.black_returncode, "")
            duration = float(args[args.index("-t") + 1])
            black = min(duration, self.black_ratio * duration)
            line = f"[blackdetect @ 0x1] black_start:0 black_end:{black:.3f} black_duration:{black:.3f}\n"
            return (0, line)
        if any("cropdetect" in token for token in args):
            lines = "".join(
                f"[Parsed_cropdetect_0 @ 0x1] crop={width}:{height}:{left}:{top}\n"
                for width, height, left, top in self.boxes
            )
            return (0, lines)
        if "-filter_complex" in args:
            return self._metric(args, work_dir)
        return (0, "")

    def _metric(self, args: list[str], work_dir: str) -> tuple[int, str]:
        chain = args[args.index("-filter_complex") + 1]
        tier = tier_of_chain(chain)
        metric = metric_of_chain(chain)
        if self.fail_metric or not tier or not metric:
            return (1, "")
        if metric == "ssim":
            score, db = self.ssim_by_tier.get(tier, (0.9995, 30.0))
            text = f"n:1 Y:1 U:1 V:1 All:{score:.5f} ({db:.2f})\n"
            Path(work_dir, "metric_ssim.log").write_text(text, encoding="utf-8")
            return (0, "")
        score = self.vmaf_by_tier.get(tier, 99.0)
        payload = {"pooled_metrics": {"vmaf": {"mean": score}}}
        Path(work_dir, "metric_vmaf.json").write_text(json.dumps(payload), encoding="utf-8")
        return (0, "")
