"""python -m src 루트 패키지 통합 진입점 (무인자 GUI, 인자/--cli 지정 시 CLI 분기)"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence

_GUI_FLAG = "--gui"


def dispatch_main(argv: Sequence[str] | None = None) -> int:
    """실행 인자 유무 및 --gui/--cli 플래그에 따라 GUI 또는 스탠드얼론 CLI를 실행"""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or raw_args == [_GUI_FLAG]:
        gui_mod = importlib.import_module("src.gui")
        return int(gui_mod.run_gui())

    cli_mod = importlib.import_module("src.cui.cli")
    return int(
        cli_mod.main(
            raw_args,
            prog="python -m src",
            include_dispatch_flags=True,
        )
    )


if __name__ == "__main__":
    sys.exit(dispatch_main())
