"""HDR 프레임을 SDR BT.709로 톤 매핑하는 필터 체인 정의 모듈

- PQ, HLG 프레임을 8-bit SDR로 변환해야 화질 분석 지표와 검은 여백 판정이 SDR과 같은 기준을 씀
- zscale(리니어 변환), tonemap(hable), bt709 출력 순서를 한 곳에 두어 스캔과 지표 명령이 같은 체인을 씀
- zscale은 FFmpeg 빌드에 따라 없을 수 있으므로 필터 이름 확인과 실패 문구 판정을 함께 제공
"""

from __future__ import annotations

# - PQ, HLG 공통 체인. 리니어로 옮겨 톤 커브를 적용한 뒤 TV 범위 BT.709 4:2:0 8-bit로 돌림
_TONEMAP_NODES: tuple[tuple[str, str], ...] = (
    ("zscale", "t=linear:npl=100"),
    ("format", "gbrpf32le"),
    ("zscale", "p=bt709"),
    ("tonemap", "hable:desat=0"),
    ("zscale", "t=bt709:m=bt709:r=tv"),
    ("format", "yuv420p"),
)
# - 체인이 실제로 쓰는 필터 이름. 빌드에 하나라도 없으면 HDR을 SDR로 옮길 수 없음
TONEMAP_FILTER_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(name for name, _args in _TONEMAP_NODES)
)
TONEMAP_UNAVAILABLE_REASON: str = (
    "FFmpeg 빌드에 HDR 톤 매핑 필터(zscale, tonemap)가 없어 HDR 영상을 측정하지 못했습니다. "
    "zscale 필터가 들어간 FFmpeg로 바꾸면 측정할 수 있습니다."
)
_TONEMAP_ERROR_MARKERS: tuple[str, ...] = (
    "zscale",
    "tonemap",
    "gbrpf32",
    "no such filter",
    "error reinitializing filters",
)


class ToneMapUnavailableError(RuntimeError):
    """HDR 톤 매핑 필터를 쓸 수 없어 프레임이나 지표를 만들지 못했을 때"""


def tonemap_filter_chain(*, hdr: bool) -> str:
    """필터 그래프에 넣을 톤 매핑 체인 문자열, SDR이면 빈 문자열

    - 호출자가 `,`로 뒤 필터와 잇거나 `[0:v]` 뒤에 그대로 붙임
    """
    if not hdr:
        return ""
    return ",".join(f"{name}={args}" if args else name for name, args in _TONEMAP_NODES)


def tonemap_graph_prefix(*, hdr: bool) -> str:
    """필터 그래프에서 입력 레이블 뒤에 붙일 톤 매핑 체인(끝에 `,` 포함), SDR이면 빈 문자열

    - `[0:v]` 뒤, `crop`이나 `split` 앞에 두어 원본과 축소본을 같은 변환 결과로 비교하게 함
    """
    chain = tonemap_filter_chain(hdr=hdr)
    return "" if not chain else f"{chain},"


def tonemap_filter_nodes(*, hdr: bool) -> tuple[tuple[str, str], ...]:
    """PyAV 필터 그래프를 만들 때 쓸 (필터 이름, 인자) 목록, SDR이면 빈 튜플

    - FFmpeg 명령 문자열과 같은 체인을 순서 그대로 돌려줌
    """
    return _TONEMAP_NODES if hdr else ()


def missing_tonemap_filters(available: frozenset[str]) -> tuple[str, ...]:
    """사용할 수 있는 필터 이름 목록에서 빠진 톤 매핑 필터 이름"""
    return tuple(name for name in TONEMAP_FILTER_NAMES if name not in available)


def is_tonemap_filter_error(stderr: str) -> bool:
    """FFmpeg 표준 오류가 톤 매핑 필터를 만들지 못한 실패인지

    - zscale, tonemap 미지원이나 pix_fmt 변환 실패 문구만 톤 매핑 불가로 보고, 다른 실패는 그대로 둠
    """
    text = stderr.lower()
    return any(marker in text for marker in _TONEMAP_ERROR_MARKERS)
