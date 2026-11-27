"""경로가 놓인 볼륨의 식별자와 마운트 루트 기준 상대 경로를 계산하는 모듈

- 볼륨 식별자는 드라이브 문자나 연결 방식이 바뀌어도 유지되는 값이라 분석 캐시의 위치 키로 씀
- Windows는 `GetVolumePathNameW`로 마운트 루트를, 로컬 볼륨은 일련 번호로, 네트워크는 공유 경로로 식별
- 네트워크는 공유 단위로 식별하고 공유 안의 상대 경로를 접두사로 붙여, 공유 하위 폴더에 연결한 드라이브 문자와 UNC 경로가 같은 값을 내도록 맞춤
- 조회가 실패하면 경로 기반 식별자로 되돌아가며 예외를 던지지 않음
"""

import ctypes
import functools
import ntpath
import os
import sys
import threading
import time
from dataclasses import dataclass

from src.core.analysis.storage_probe import DRIVE_REMOTE, drive_type, kernel32

NO_ERROR: int = 0

_VOLUME_ID_VOLUME_PREFIX: str = "vol:"
_VOLUME_ID_UNC_PREFIX: str = "unc:"
_VOLUME_ID_PATH_PREFIX: str = "path:"
_VOLUME_ID_DEV_PREFIX: str = "dev:"
_UNC_PREFIX: str = "\\\\"
_SEPARATOR: str = "\\"

# 일련 번호를 16진수 8자리로 고정해 볼륨마다 같은 폭의 키를 만듬
_SERIAL_HEX_WIDTH: int = 8
_VOLUME_ROOT_BUFFER_SIZE: int = 1024
_REMOTE_NAME_BUFFER_SIZE: int = 1024
# 드라이브 문자에 다른 볼륨이 연결된 경우까지 반영하도록 식별자 캐시는 짧게 유지
_VOLUME_ID_CACHE_TTL_SECONDS: float = 60.0
# 마운트 루트 조회는 파일마다 부르면 비용이 커서 같은 폴더의 파일이 결과를 나눠 쓰도록 캐시
_VOLUME_ROOT_CACHE_TTL_SECONDS: float = _VOLUME_ID_CACHE_TTL_SECONDS
# 폴더가 아주 많으면 캐시가 계속 커지므로 상한을 넘으면 비움(다시 채우는 비용이 조회 한 번)
_VOLUME_ROOT_CACHE_MAX_ENTRIES: int = 4096


@dataclass(frozen=True, slots=True)
class VolumeLocation:
    """볼륨 식별자와 마운트 루트 기준 상대 경로

    - `volume_id`: 로컬 `vol:<16진 일련 번호>`, 네트워크 `unc:<공유 경로>`, POSIX `dev:<st_dev>:<마운트 루트>`, 실패 시 `path:<루트>`
    - `volume_path`: 마운트 루트 기준 posix 상대 경로이며 Windows는 소문자로 정규화하고 루트 자체는 빈 문자열
    - 공유 하위 폴더에 연결한 드라이브 문자는 공유 루트를 기준으로 하므로 연결 폴더까지 상대 경로에 포함됨
    """

    volume_id: str
    volume_path: str


_CACHE: dict[str, tuple[tuple[str, str], float]] = {}
_VOLUME_ROOT_CACHE: dict[str, tuple[str | None, float]] = {}
_CACHE_LOCK = threading.Lock()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _clock() -> float:
    """캐시 만료 판단에 쓰는 시각(테스트에서 고정할 수 있도록 주입 지점으로 둠)"""
    return time.monotonic()


@functools.lru_cache(maxsize=1)
def _mpr() -> ctypes.WinDLL:
    """네트워크 드라이브 연결 조회 함수 집합(mpr.dll은 Windows에서만 로드)"""
    dll = ctypes.WinDLL("mpr", use_last_error=True)
    dll.WNetGetConnectionW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    dll.WNetGetConnectionW.restype = ctypes.c_ulong
    return dll


def _volume_root(path: str) -> str | None:
    """경로가 속한 마운트 루트 조회, 조회에 실패하면 None"""
    buffer = ctypes.create_unicode_buffer(_VOLUME_ROOT_BUFFER_SIZE)
    if not kernel32().GetVolumePathNameW(path, buffer, len(buffer)) or not buffer.value:
        return None
    return buffer.value


def _cached_volume_root(path: str) -> str | None:
    """경로가 속한 마운트 루트를 같은 폴더의 다른 경로와 나눠 쓰는 조회

    - `GetVolumePathNameW`는 폴더 안 어느 경로를 넘겨도 그 볼륨의 마운트 루트를 돌려주므로 폴더 단위로 캐시
      (파일마다 호출하면 시작 복원처럼 파일이 많은 경로에서 조회 시간이 파일 수에 비례해 늘어남)
    - 조회한 경로 자신이 탑재 지점이면(그 경로가 마운트 루트) 폴더 키로 값을 나눠 쓰면 그 폴더에 놓인 다른 경로가 잘못된 루트를 받으므로 캐시하지 않음
    - 실패 결과(None)는 그 폴더의 모든 경로에 같은 값이므로 캐시
    """
    folder_key = ntpath.normcase(ntpath.dirname(path))
    now = _clock()
    with _CACHE_LOCK:
        cached = _VOLUME_ROOT_CACHE.get(folder_key)
    if cached is not None and now - cached[1] < _VOLUME_ROOT_CACHE_TTL_SECONDS:
        return cached[0]
    root = _volume_root(path)
    if root is not None and ntpath.normcase(_strip_trailing_separator(root)) == ntpath.normcase(
        _strip_trailing_separator(path)
    ):
        return root
    with _CACHE_LOCK:
        if len(_VOLUME_ROOT_CACHE) >= _VOLUME_ROOT_CACHE_MAX_ENTRIES:
            _VOLUME_ROOT_CACHE.clear()
        _VOLUME_ROOT_CACHE[folder_key] = (root, now)
    return root


def _strip_trailing_separator(root: str) -> str:
    """끝 구분자를 하나 뗀 루트(POSIX 루트 `/`처럼 구분자만 남는 경우 그대로 둠)"""
    if len(root) > 1 and root.endswith(_SEPARATOR):
        return root[:-1]
    return root


def _is_path_root(path: str, root: str) -> bool:
    """`root`가 경로의 상위 경로인지(이름 일부만 겹치는 경우 제외) 판별

    - 탑재 폴더 루트는 끝 구분자가 붙은 채 조회되므로 끝 구분자를 하나 뗀 값과 비교해 경로가 루트 자체여도 상위로 인정
    """
    normalized_path = ntpath.normcase(path)
    normalized_root = _strip_trailing_separator(ntpath.normcase(root))
    if normalized_path == normalized_root:
        return True
    if not normalized_path.startswith(normalized_root):
        return False
    return normalized_path[len(normalized_root) :].startswith(_SEPARATOR)


def _is_unc_path(path: str) -> bool:
    """UNC 경로인지 판별"""
    return path.startswith(_UNC_PREFIX)


def _unc_share_root(path: str) -> str | None:
    """UNC 경로의 공유 루트를 반환, 공유 이름이 없으면 None"""
    parts = [part for part in path[len(_UNC_PREFIX) :].split(_SEPARATOR) if part]
    if len(parts) < 2:
        return None
    return _UNC_PREFIX + _SEPARATOR.join(parts[:2])


def _fallback_volume_root(path: str) -> str:
    """마운트 루트를 쓸 수 없는 경로의 대체 루트

    - 드라이브 문자는 그 루트를 씀
    - 드라이브가 없는 경로는 최상위를 씀
    """
    drive, _ = ntpath.splitdrive(path)
    if len(drive) == 2 and drive[1] == ":":
        return drive + _SEPARATOR
    return drive or _SEPARATOR


def _volume_serial(root: str) -> int | None:
    """볼륨 일련 번호 조회, 실패하면 None

    - `GetVolumeInformationW`는 끝 구분자 하나를 요구하므로 루트의 구분자 유무와 무관하게 같은 값을 조회
    """
    serial = ctypes.c_ulong(0)
    query_root = _strip_trailing_separator(root) + _SEPARATOR
    opened = kernel32().GetVolumeInformationW(query_root, None, 0, ctypes.pointer(serial), None, None, None, 0)
    return int(serial.value) if opened else None


def _remote_connection(drive: str) -> str | None:
    """드라이브 문자에 연결된 원격 공유 경로 조회, 실패하면 None

    - `WNetGetConnectionW`는 `Z:` 형식의 장치 이름만 받으며 오류 코드를 반환하므로 연결이 끊긴 드라이브도 실패로 다룸
    """
    buffer = ctypes.create_unicode_buffer(_REMOTE_NAME_BUFFER_SIZE)
    length = ctypes.c_ulong(len(buffer))
    if _mpr().WNetGetConnectionW(drive, buffer, ctypes.byref(length)) != NO_ERROR:
        return None
    return buffer.value or None


def is_resolved_volume_id(volume_id: str) -> bool:
    """볼륨 식별자가 실제 볼륨을 가리키는지(경로 기반 대체 식별자가 아닌지) 반환

    - 경로 기반 식별자는 볼륨을 확인하지 못했을 때만 쓰는 값이라, 같은 폴더를 다시 찾으려면 마운트 상태가 같은 동안에만 유효
    """
    return not volume_id.startswith(_VOLUME_ID_PATH_PREFIX)


def volume_location(path: str) -> VolumeLocation:
    """경로의 볼륨 식별자와 마운트 루트 기준 상대 경로를 계산

    - 상대 경로는 현재 작업 폴더 기준 절대 경로로 바꾼 뒤 계산
    - 볼륨 식별자는 마운트 루트 단위로 짧게 캐시
    """
    if _is_windows():
        return _windows_volume_location(ntpath.abspath(path))
    return _posix_volume_location(os.path.abspath(path))


def _windows_volume_location(path: str) -> VolumeLocation:
    """Windows 경로의 볼륨 위치 계산, UNC 경로는 공유 루트를 그대로 마운트 루트로 씀

    - 볼륨 식별자와 마운트 루트는 공유 단위로 계산하고, 공유 하위 폴더에 연결한 드라이브 문자는 그 폴더를 상대 경로 접두사로 붙여 UNC 경로와 같은 값을 냄
    - 정션, 탑재 폴더처럼 조회 결과가 경로의 상위가 아니면 그 위치를 경로 기반 식별자로 씀
      - 이 대체 식별자는 드라이브 문자로 만들어 그 문자에 다른 볼륨이 연결되면 값이 바뀜
      - 원격 공유를 가리키는 정션도 공유 식별자 대신 이 경로 기반 값을 씀
    - 조회에 실패하면(아직 없는 경로) 드라이브 루트의 볼륨으로 식별
    """
    if _is_unc_path(path):
        share_root = _unc_share_root(path)
        if share_root is not None:
            volume_id, base = _volume_identity(share_root)
            return VolumeLocation(volume_id, _join_relative(base, _relative_path(path, share_root)))
    kernel_root = _cached_volume_root(path)
    if kernel_root is not None and _is_path_root(path, kernel_root):
        volume_id, base = _volume_identity(kernel_root)
        return VolumeLocation(volume_id, _join_relative(base, _relative_path(path, kernel_root)))
    fallback = _fallback_volume_root(path)
    if kernel_root is None:
        # 공유 하위 폴더에 연결한 원격 드라이브는 조회가 실패해도 연결 폴더 접두사를 유지해야 같은 파일의 UNC 경로와 키가 일치
        volume_id, base = _volume_identity(fallback)
        return VolumeLocation(volume_id, _join_relative(base, _relative_path(path, fallback)))
    return VolumeLocation(_VOLUME_ID_PATH_PREFIX + ntpath.normcase(fallback), _relative_path(path, fallback))


def _join_relative(base: str, relative: str) -> str:
    """볼륨 식별자의 상대 경로 접두사와 마운트 루트 기준 상대 경로를 이어 붙임"""
    if not base:
        return relative
    if not relative:
        return base
    return f"{base}/{relative}"


def _posix_volume_location(path: str) -> VolumeLocation:
    root = _mount_root(path)
    return VolumeLocation(_device_volume_id(root), _posix_relative_path(path, root))


def _mount_root(path: str) -> str:
    """마운트 지점을 만날 때까지 상위 폴더로 올라가 마운트 루트를 찾음"""
    current = path
    while True:
        parent = os.path.dirname(current)
        if current == parent or os.path.ismount(current):
            return current
        current = parent


def _device_volume_id(root: str) -> str:
    """마운트 루트의 장치 번호와 루트 경로로 식별자 계산

    - bind mount는 원본과 `st_dev`가 같아 장치 번호만으로는 서로 다른 위치가 같은 키가 되므로 정규화한 마운트 루트 경로를 함께 씀
    """
    try:
        device = os.stat(root).st_dev
    except OSError:
        return _VOLUME_ID_PATH_PREFIX + os.path.normcase(root)
    return f"{_VOLUME_ID_DEV_PREFIX}{device}:{os.path.normcase(root)}"


def _volume_identity(root: str) -> tuple[str, str]:
    """마운트 루트의 볼륨 식별자와 상대 경로 접두사, 같은 루트는 수명이 지나기 전까지 다시 조회하지 않음"""
    key = ntpath.normcase(root)
    now = _clock()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None and now - cached[1] < _VOLUME_ID_CACHE_TTL_SECONDS:
        return cached[0]
    identity = _compute_volume_identity(root)
    with _CACHE_LOCK:
        _CACHE[key] = (identity, now)
    return identity


def _compute_volume_identity(root: str) -> tuple[str, str]:
    """마운트 루트 문자열에서 볼륨 식별자와 상대 경로 접두사를 계산

    - UNC 마운트 루트는 공유 경로로 식별하며 공유 루트 자체이므로 접두사가 없음
    - 드라이브 문자 루트는 볼륨 일련 번호나 원격 공유로 식별
    """
    drive, _ = ntpath.splitdrive(root)
    if drive.startswith(_UNC_PREFIX):
        return _VOLUME_ID_UNC_PREFIX + ntpath.normcase(drive), ""
    if len(drive) == 2 and drive[1] == ":":
        return _drive_volume_identity(drive, root)
    return _VOLUME_ID_PATH_PREFIX + ntpath.normcase(root), ""


def _drive_volume_identity(drive: str, root: str) -> tuple[str, str]:
    """드라이브 문자 루트의 식별자와 상대 경로 접두사를 계산

    - 원격 드라이브는 공유 경로로 식별해 드라이브 문자 변경을 흡수하고, 공유 하위 폴더에 연결한 경우 연결 폴더를 상대 경로 접두사로 붙여 같은 파일의 UNC 경로와 값이 일치하게 함
    - 공유 조회가 실패한 원격 드라이브는 경로로 되돌아감
    - 일련 번호가 0인 가상 볼륨은 다른 볼륨과 섞이지 않도록 경로로 되돌아감
    """
    drive_root = f"{drive}\\"
    if drive_type(drive_root) == DRIVE_REMOTE:
        remote = _remote_connection(drive)
        if remote:
            remote = _strip_trailing_separator(remote)
            share_root = _unc_share_root(remote)
            if share_root is not None:
                return _VOLUME_ID_UNC_PREFIX + ntpath.normcase(share_root), _relative_path(remote, share_root)
        return _VOLUME_ID_PATH_PREFIX + ntpath.normcase(drive_root), ""
    serial = _volume_serial(root)
    if not serial:
        return _VOLUME_ID_PATH_PREFIX + ntpath.normcase(root), ""
    return f"{_VOLUME_ID_VOLUME_PREFIX}{serial:0{_SERIAL_HEX_WIDTH}x}", ""


def _relative_path(path: str, root: str) -> str:
    """마운트 루트 기준 상대 경로를 소문자 posix 표기로 반환, 루트 자체는 빈 문자열

    - `ntpath.relpath`는 드라이브가 다르면 예외를 던지므로 정규화한 접두사를 직접 제거
    - 루트가 경로의 상위가 아니면 드라이브를 제외한 경로 전체를 상대 경로로 씀
    """
    normalized_path = ntpath.normcase(path)
    normalized_root = _strip_trailing_separator(ntpath.normcase(root))
    if normalized_path == normalized_root:
        return ""
    if _is_path_root(path, root):
        relative = normalized_path[len(normalized_root) :]
    else:
        relative = ntpath.splitdrive(normalized_path)[1]
    relative = relative.lstrip(_SEPARATOR)
    if not relative:
        return ""
    return relative.replace(_SEPARATOR, "/")


def _posix_relative_path(path: str, root: str) -> str:
    """POSIX 마운트 루트 기준 상대 경로를 posix 표기로 반환, 루트 자체는 빈 문자열

    - `os.path.normcase`는 POSIX에서 대소문자를 바꾸지 않으므로 POSIX 상대 경로의 대소문자는 보존됨
    """
    relative = os.path.relpath(path, root)
    if relative == os.curdir:
        return ""
    return os.path.normcase(relative).replace(os.sep, "/")


def clear_volume_cache() -> None:
    """볼륨 식별자와 마운트 루트 캐시를 비움(테스트 격리와 볼륨 구성 변경 후 사용)"""
    with _CACHE_LOCK:
        _CACHE.clear()
        _VOLUME_ROOT_CACHE.clear()
