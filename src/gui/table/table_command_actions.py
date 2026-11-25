"""표 선택 영상의 예정 FFmpeg 명령을 클립보드로 복사하는 동작 모듈

- 명령은 표 모델이 이미 계산해 둔 예정 계획을 그대로 씀(프로세스 실행 없음)
- 복사한 건수는 창 하단 상태 줄에 알림
"""

from collections.abc import Sequence
from typing import Protocol

from src.core.models import VideoEvaluation
from src.gui.table.table_actions import copy_lines_to_clipboard

COPIED_NOTICE: str = "FFmpeg 명령 {count}개를 복사했습니다."


class _PlannedSource(Protocol):
    """예정 명령줄을 제공하는 표 모델"""

    def planned_command_line(self, ev: VideoEvaluation) -> str | None:
        """영상의 예정 명령줄, 만들 수 없으면 None"""
        ...


def planned_command_lines(model: _PlannedSource, evals: Sequence[VideoEvaluation]) -> list[str]:
    """선택 영상의 예정 명령줄 목록. 만들 수 없는 영상은 뺌

    - 붙여 넣어 실행할 전체 명령이므로 줄마다 한 명령
    - 같은 영상이 여러 번 와도 한 번만 넣음
    """
    lines: list[str] = []
    seen: set[str] = set()
    for ev in evals:
        command = model.planned_command_line(ev)
        if not command or command in seen:
            continue
        seen.add(command)
        lines.append(command)
    return lines


def copy_ffmpeg_commands(
    model: _PlannedSource, evals: Sequence[VideoEvaluation], window: object
) -> int:
    """선택 영상의 예정 명령을 줄바꿈으로 복사하고 건수를 반환, 하단 상태 줄에 알림"""
    count = copy_lines_to_clipboard(planned_command_lines(model, evals))
    footer = getattr(window, "footer_bar", None)
    if count > 0 and footer is not None:
        footer.set_status_text(COPIED_NOTICE.format(count=count))
    return count


__all__ = ["COPIED_NOTICE", "copy_ffmpeg_commands", "planned_command_lines"]
