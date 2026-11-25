"""인코딩 탭의 툴팁 문구와 출력 형식 설명을 모은 모듈

- 행 구성과 배치는 execution_sections.py가 맡고, 이 모듈은 툴팁, 선택 항목 이름, 출력 형식 설명 문구만 둠
- 출력 형식 설명은 encoder_specs.py의 계열별 표에서 만들어 설명과 실제 저장 규칙이 어긋나지 않게 함
"""

from src.core.config import (
    CONCURRENT_JOBS_MAX,
    CONTAINER_POLICY_KEEP,
    CONTAINER_POLICY_MP4,
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_SKIP,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
)
from src.core.encode.encode_concurrency import AUTO_JOBS_CPU, AUTO_JOBS_HARDWARE
from src.core.encode.ffmpeg_capability import MIN_FFMPEG_VERSION
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.encoder_specs import ENCODE_FAMILY_LABELS, keep_container_block_reason, keep_container_exts

CODEC_AVAILABLE_TEXT: str = "이 PC에서 사용 가능"
CODEC_UNAVAILABLE_PREFIX: str = "이 PC에서 사용할 수 없음: "
CODEC_CHECKING_TEXT: str = "확인 중"

SPEED_DESC: str = (
    "1080p 30fps 영상을 영상 길이의 몇 배 속도로 인코딩하는지 나타냅니다. 2.0배속이면 10분 영상에 약 5분이 걸립니다. "
    "해상도와 FPS가 큰 영상은 화소 처리량에 비례해 더 오래 걸리는 것으로 계산해 표와 하단 합계의 예상 시간에 반영합니다. 모르면 옆의 돋보기 버튼(시스템 추천)을 누르세요."
)
TOOLTIP_SPEED_DETECT: str = (
    "현재 PC의 CPU 스레드와 GPU 하드웨어 인코더(NVENC/QSV/AMF) 지원 여부를 조회해\n"
    "1080p 30fps 기준 추천 인코딩 배속을 자동으로 입력합니다."
)
TOOLTIP_FFMPEG: str = (
    "인코딩에 쓰는 ffmpeg 실행 파일의 경로입니다. 비워 두면 PATH에서 찾습니다. "
    f"찾지 못하거나 버전이 {MIN_FFMPEG_VERSION[0]}.{MIN_FFMPEG_VERSION[1]}보다 낮으면 "
    "아래 FFmpeg 상태에 사유가 나오고 인코딩을 시작할 수 없습니다."
)
TOOLTIP_FFPROBE: str = "영상 정보를 읽는 ffprobe 실행 파일의 경로입니다. 비워 두면 PATH에서 찾습니다."
TOOLTIP_CODEC: str = (
    "실제 인코딩에 쓰는 인코더입니다. 판정(출력 BR 비율, 과화질), 예상 용량, 예상 시간도 이 값을 따릅니다. "
    "방식을 바꾸면 현재 PC에 맞는 기준 인코딩 배속을 다시 추천합니다."
)
TOOLTIP_RESULT_POLICY: str = (
    "인코딩 결과를 원본과 어떻게 놓을지 정합니다. 결과는 검사를 통과한 뒤에만 옮깁니다. "
    "원본 보관 후 교체: 원본을 보관 폴더로 옮긴 뒤 결과를 원래 이름으로 둡니다(기본). "
    "별도 폴더에 저장: 원본은 그대로 두고 결과를 별도 출력 폴더에 저장합니다. "
    "원본 교체: 원본을 결과로 바로 바꾸며 원본은 보관되지 않아 되돌릴 수 없습니다."
)
TOOLTIP_ARCHIVE_DIR: str = (
    "원본 보관 후 교체일 때 원본을 옮겨 둘 폴더 이름입니다. 원본 영상과 같은 폴더 안에 만듭니다. "
    "기본값은 _원본보관입니다."
)
TOOLTIP_OUTPUT_DIR: str = (
    "별도 폴더에 저장할 때 결과를 모을 폴더 이름입니다. 작업 폴더 바로 아래에 만들고 원본의 하위 폴더 구조를 "
    "유지합니다. 기본값은 _인코딩결과입니다."
)
TOOLTIP_CONCURRENT: str = (
    f"동시에 인코딩할 영상 수입니다. 0이면 인코더에 맞춰 자동으로 정하며, 하드웨어 인코더는 {AUTO_JOBS_HARDWARE}개, "
    f"소프트웨어 인코더는 {AUTO_JOBS_CPU}개입니다. 수를 늘려도 빨라지지 않을 수 있고, 메모리와 CPU 사용이 늘어납니다. "
    f"최대 {CONCURRENT_JOBS_MAX}개입니다."
)
TOOLTIP_PREVENT_SLEEP: str = (
    "인코딩 중 PC가 절전 모드로 들어가지 않게 합니다. 화면 끄기는 막지 않습니다. "
    "사용 안 함이면 긴 인코딩 중 PC가 절전으로 들어갈 수 있습니다."
)
TOOLTIP_AFTER_COMPLETE: str = (
    "모든 인코딩이 끝난 뒤 할 일입니다. 없음: 아무것도 하지 않습니다. 절전: PC를 절전 모드로 바꿉니다. "
    "최대 절전이 켜져 있으면 최대 절전으로 들어갈 수 있습니다. 시스템 종료: PC를 끕니다. 앱 종료: 이 프로그램을 끕니다."
)
TOOLTIP_PRIORITY: str = (
    "인코딩 프로세스의 CPU 우선순위입니다. 높을수록 인코딩이 빨라질 수 있지만 다른 프로그램이 느려질 수 있습니다. "
    "이름은 Windows 작업 관리자의 우선순위 단계와 같습니다."
)
TOOLTIP_NAME_CONFLICT: str = (
    "결과 파일 이름이 이미 있을 때의 처리입니다. 건너뜀: 그 영상은 인코딩하지 않고 건너뜀으로 남깁니다. "
    "번호 붙여 저장: 파일 이름 뒤에 번호를 붙여 저장하며, 기존 파일은 덮어쓰지 않습니다."
)
TOOLTIP_TAG_MODE: str = (
    "결과 파일에 인코딩 표시를 남기는 방식입니다. 태그와 숨은 기록: 탐색기 태그와 숨은 기록을 모두 남깁니다(기본). "
    "숨은 기록만: 탐색기 태그 칸에는 보이지 않고 숨은 기록만 남깁니다. "
    "기록 안 함: 인코딩 태그와 숨은 기록을 남기지 않으며, 같은 영상은 인코딩 이력으로만 가려냅니다. "
    "FFmpeg가 쓰는 인코더 정보 태그는 그대로 남습니다."
)
TOOLTIP_TAG_NAME: str = (
    "탐색기 태그 칸에 보이는 키워드입니다. 탐색기에서 검색하거나 묶어 볼 때 쓰입니다. "
    "공백을 뺀 1자 이상, 64자 이하이며 세미콜론(;)과 제어 문자는 쓸 수 없습니다. "
    "바꾸기 전에 쓴 이름으로 된 인코딩 기록도 계속 인식합니다. 기본값은 re_encoded입니다."
)

RESULT_POLICY_CHOICES: tuple[tuple[str, str], ...] = (
    ("원본 보관 후 교체 (기본)", RESULT_POLICY_ARCHIVE),
    ("별도 폴더에 저장", RESULT_POLICY_SEPARATE),
    ("원본 교체 (되돌릴 수 없음)", RESULT_POLICY_REPLACE),
)
CONTAINER_CHOICES: tuple[tuple[str, str], ...] = (
    ("원본 형식 유지 (기본)", CONTAINER_POLICY_KEEP),
    ("항상 MP4", CONTAINER_POLICY_MP4),
)
PREVENT_SLEEP_CHOICES: tuple[tuple[str, bool], ...] = (
    ("사용 (기본)", True),
    ("사용 안 함", False),
)
AFTER_COMPLETE_CHOICES: tuple[tuple[str, str], ...] = (
    ("없음 (기본)", "none"),
    ("절전", "sleep"),
    ("시스템 종료", "shutdown"),
    ("앱 종료", "exit"),
)
PRIORITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("높음", "high"),
    ("보통 초과", "above_normal"),
    ("보통 (기본)", "normal"),
    ("보통 미만", "below_normal"),
    ("낮음", "low"),
)
NAME_CONFLICT_CHOICES: tuple[tuple[str, str], ...] = (
    ("건너뜀", NAME_CONFLICT_SKIP),
    ("번호 붙여 저장 (기본)", NAME_CONFLICT_NUMBER),
)
TAG_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("태그와 숨은 기록 (기본)", ENCODE_TAG_MODE_VISIBLE),
    ("숨은 기록만", ENCODE_TAG_MODE_HIDDEN),
    ("기록 안 함", ENCODE_TAG_MODE_OFF),
)

# - 출력 형식 설명에 나열하는 확장자 순서. 원본 형식 유지 목록과 MP4로 바뀌는 목록이 같은 순서를 따름
_CONTAINER_LABELS: tuple[tuple[str, str], ...] = (
    ("mp4", "MP4"),
    ("mov", "MOV"),
    ("mkv", "MKV"),
    ("avi", "AVI"),
    ("wmv", "WMV"),
    ("flv", "FLV"),
    ("webm", "WebM"),
    ("ts", "TS"),
)
_PENDING_CONTAINER_TEXT: str = "5단계에서 확인"


def build_container_desc(family: str) -> str:
    """목표 인코딩 방식 계열에서 출력 형식 설명 문장을 만듦

    - 원본 형식 유지 목록은 계열 표와 태그 기록 가능 형식의 교집합, 바뀌는 형식과 사유는 규칙 함수에서 가져옴
    - 계열이 아직 확인되지 않은 경우(AV1)는 확인 시점만 안내
    """
    head = f"결과 파일의 형식입니다. 현재 목표 인코딩 방식({ENCODE_FAMILY_LABELS.get(family, family)}) 기준입니다. "
    kept = keep_container_exts(family)
    if kept is None:
        body = f"원본 형식 유지(기본)와 MP4로 바뀌는 형식은 {_PENDING_CONTAINER_TEXT}합니다. "
    else:
        kept_text = ", ".join(label for ext, label in _CONTAINER_LABELS if ext in kept)
        converted = [
            f"{label}({reason})"
            for ext, label in _CONTAINER_LABELS
            if (reason := keep_container_block_reason(ext, family)) is not None
        ]
        converted_text = ", ".join(converted) if converted else "없음"
        body = (
            f"원본 형식 유지(기본): {kept_text} 원본은 같은 형식으로 저장하고, 그 밖의 형식은 MP4로 저장합니다. "
            f"MP4로 바뀌는 형식(원본 형식 유지일 때): {converted_text}. "
        )
    tail = (
        "항상 MP4: 모든 결과를 MP4로 저장합니다. "
        "비트맵 자막(PGS, VobSub 등)이 있는 영상은 두 방식 모두 MKV로 저장합니다. MP4에 저장할 수 없기 때문입니다."
    )
    return head + body + tail
