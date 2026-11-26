"""사용자 동작 기록에 남길 분류와 문구를 한 곳에서 만드는 모듈

- 기록 문구를 화면 코드에서 조립하지 않아 같은 동작이 항상 같은 모양으로 남음
- 파일 경로는 이름만 남겨 사용자 폴더 구조가 로그에 쌓이지 않게 함
"""

from __future__ import annotations

from src.core.diagnostics.breadcrumbs import base_name
from src.core.workspace_paths import parse_root_dirs

CATEGORY_PREVIEW: str = "미리 보기"
CATEGORY_ANALYSIS: str = "분석"
CATEGORY_ENCODE: str = "인코딩"
CATEGORY_FOLDER: str = "작업 폴더"
CATEGORY_SETTINGS: str = "설정"
CATEGORY_ERROR: str = "오류"

ACTION_START: str = "시작"
ACTION_STOP: str = "중지"
ACTION_FINISH: str = "완료"
ACTION_OPEN: str = "열기"
ACTION_CHANGE: str = "변경"

# - 오류 문구는 여러 줄 스택을 담을 수 있어 한 줄 첫 문장만 남겨 링 버퍼를 짧게 유지
ERROR_LINE_LIMIT: int = 120


def describe_task(label: str, action: str) -> str:
    """작업 이름과 동작을 합친 기록 문구 생성"""
    return f"{label} {action}"


def describe_file(action: str, path: str) -> str:
    """동작과 파일 이름만 남긴 기록 문구 생성"""
    return f"{action}: {base_name(path)}"


def describe_folder_change(root_spec: str) -> str:
    """작업 폴더 변경을 폴더 이름으로 표현. 여러 폴더면 첫 폴더와 개수만 남김

    - 작업 폴더는 구분자로 이어진 목록이므로, 기록에서는 이름 하나와 나머지 개수로 줄임
    """
    roots = parse_root_dirs(root_spec)
    if not roots:
        return ACTION_CHANGE
    primary = base_name(roots[0])
    if len(roots) == 1:
        return f"{ACTION_CHANGE}: {primary}"
    return f"{ACTION_CHANGE}: {primary} 외 {len(roots) - 1}개"


def describe_error(message: str) -> str:
    """오류 메시지의 첫 줄만 잘라 기록 문구 생성"""
    first = next((line.strip() for line in message.splitlines() if line.strip()), "")
    if len(first) > ERROR_LINE_LIMIT:
        return first[:ERROR_LINE_LIMIT]
    return first
