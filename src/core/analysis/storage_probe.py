"""작업 폴더가 놓인 저장 장치 종류(SSD, HDD, 네트워크)를 Windows 볼륨 속성 조회로 판별하는 모듈

- 볼륨 핸들은 접근 권한 0으로 열어 속성만 조회하며, 데이터 읽기와 쓰기 계열 제어 코드는 사용하지 않음
- 조회 실패나 매체 정보가 없는 장치(USB 변환 칩 등)는 UNKNOWN으로 두어 기존 동작을 유지
- 결과는 볼륨 단위로 캐시하며, 호출은 워커 스레드에서만 수행
"""

import ctypes
import enum
import functools
import os
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

IOCTL_STORAGE_QUERY_PROPERTY: int = 0x002D1400
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS: int = 0x00560000
ALLOWED_IOCTL_CODES: frozenset[int] = frozenset(
    {IOCTL_STORAGE_QUERY_PROPERTY, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS}
)

BUS_TYPE_USB: int = 7
_STORAGE_DEVICE_PROPERTY: int = 0
_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY: int = 7
_PROPERTY_STANDARD_QUERY: int = 0
_DRIVE_REMOTE: int = 4
_FILE_SHARE_READ_WRITE: int = 0x3
_OPEN_EXISTING: int = 3
_INVALID_HANDLE_VALUE: int | None = ctypes.c_void_p(-1).value
_UNC_PREFIX: str = "\\\\"
_VOLUME_PATH_PREFIX: str = "\\\\.\\"


class StorageKind(enum.Enum):
    """작업 폴더 저장 장치 종류"""

    SSD = "ssd"
    HDD = "hdd"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StorageInfo:
    """판별 결과와 진단용 원시 값(버스 종류, 물리 디스크 번호, 없으면 None)"""

    kind: StorageKind
    bus_type: int | None = None
    disk_number: int | None = None


class _StoragePropertyQuery(ctypes.Structure):
    _fields_ = [
        ("PropertyId", ctypes.c_ulong),
        ("QueryType", ctypes.c_ulong),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


class _SeekPenaltyDescriptor(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("Size", ctypes.c_ulong),
        ("IncursSeekPenalty", ctypes.c_ubyte),
    ]


class _StorageDeviceDescriptor(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("Size", ctypes.c_ulong),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", ctypes.c_ulong),
        ("ProductIdOffset", ctypes.c_ulong),
        ("ProductRevisionOffset", ctypes.c_ulong),
        ("SerialNumberOffset", ctypes.c_ulong),
        ("BusType", ctypes.c_ulong),
        ("RawPropertiesLength", ctypes.c_ulong),
    ]


class _DiskExtent(ctypes.Structure):
    _fields_ = [
        ("DiskNumber", ctypes.c_ulong),
        ("StartingOffset", ctypes.c_longlong),
        ("ExtentLength", ctypes.c_longlong),
    ]


class _VolumeDiskExtents(ctypes.Structure):
    _fields_ = [
        ("NumberOfDiskExtents", ctypes.c_ulong),
        ("Extents", _DiskExtent * 1),
    ]


_CACHE: dict[str, StorageInfo] = {}
_CACHE_LOCK = threading.Lock()
_TStruct = TypeVar("_TStruct", bound=ctypes.Structure)


def _is_windows() -> bool:
    return sys.platform == "win32"


@functools.lru_cache(maxsize=1)
def _kernel32() -> ctypes.WinDLL:
    """호출 형식을 명시한 kernel32 함수 집합(Windows에서만 로드)"""
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    dll.CreateFileW.restype = ctypes.c_void_p
    dll.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    dll.DeviceIoControl.restype = ctypes.c_int
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    dll.CloseHandle.restype = ctypes.c_int
    dll.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    dll.GetDriveTypeW.restype = ctypes.c_uint
    return dll


def _drive_type(root: str) -> int:
    return int(_kernel32().GetDriveTypeW(root))


def _open_volume(drive: str) -> int | None:
    """드라이브 문자의 볼륨을 접근 권한 0으로 열어 핸들 반환, 실패하면 None"""
    handle = _kernel32().CreateFileW(
        f"{_VOLUME_PATH_PREFIX}{drive}",
        0,
        _FILE_SHARE_READ_WRITE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE_VALUE:
        return None
    return int(handle)


def _close_handle(handle: int) -> None:
    _kernel32().CloseHandle(ctypes.c_void_p(handle))


def _device_query(
    handle: int,
    code: int,
    out_type: type[_TStruct],
    in_struct: ctypes.Structure | None = None,
) -> _TStruct | None:
    """허용된 제어 코드로 속성을 조회하고 결과 구조체를 반환, 실패하면 None

    - 허용 목록 밖의 코드는 DeviceIoControl 호출 전에 거부
    """
    if code not in ALLOWED_IOCTL_CODES:
        raise ValueError(f"허용되지 않은 장치 제어 코드입니다: {code:#x}")
    out = out_type()
    returned = ctypes.c_ulong(0)
    in_ptr = ctypes.byref(in_struct) if in_struct is not None else None
    in_size = ctypes.sizeof(in_struct) if in_struct is not None else 0
    ok = _kernel32().DeviceIoControl(
        handle,
        code,
        in_ptr,
        in_size,
        ctypes.byref(out),
        ctypes.sizeof(out),
        ctypes.byref(returned),
        None,
    )
    return out if ok else None


def _query_seek_penalty(handle: int) -> bool | None:
    """탐색 지연 속성(회전 매체 여부) 조회, 장치가 알려 주지 않으면 None"""
    query = _StoragePropertyQuery(
        PropertyId=_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY,
        QueryType=_PROPERTY_STANDARD_QUERY,
    )
    result = _device_query(handle, IOCTL_STORAGE_QUERY_PROPERTY, _SeekPenaltyDescriptor, query)
    return None if result is None else bool(result.IncursSeekPenalty)


def _query_bus_type(handle: int) -> int | None:
    """장치 버스 종류 조회(USB 연결 여부 진단용), 실패하면 None"""
    query = _StoragePropertyQuery(
        PropertyId=_STORAGE_DEVICE_PROPERTY,
        QueryType=_PROPERTY_STANDARD_QUERY,
    )
    result = _device_query(handle, IOCTL_STORAGE_QUERY_PROPERTY, _StorageDeviceDescriptor, query)
    return None if result is None else int(result.BusType)


def _query_disk_number(handle: int) -> int | None:
    """볼륨이 올라간 물리 디스크 번호 조회, 여러 디스크에 걸쳐 있으면 buffer 부족으로 None"""
    result = _device_query(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, _VolumeDiskExtents)
    if result is None or result.NumberOfDiskExtents < 1:
        return None
    return int(result.Extents[0].DiskNumber)


def classify_storage(seek_penalty: bool | None) -> StorageKind:
    """탐색 지연 속성으로 SSD, HDD를 구분하며 속성이 없으면 UNKNOWN"""
    if seek_penalty is None:
        return StorageKind.UNKNOWN
    return StorageKind.HDD if seek_penalty else StorageKind.SSD


def _probe_volume(drive: str) -> StorageInfo:
    if _drive_type(f"{drive}\\") == _DRIVE_REMOTE:
        return StorageInfo(StorageKind.NETWORK)
    handle = _open_volume(drive)
    if handle is None:
        return StorageInfo(StorageKind.UNKNOWN)
    try:
        seek_penalty = _query_seek_penalty(handle)
        bus_type = _query_bus_type(handle)
        disk_number = _query_disk_number(handle)
    finally:
        _close_handle(handle)
    return StorageInfo(
        kind=classify_storage(seek_penalty),
        bus_type=bus_type,
        disk_number=disk_number,
    )


def probe_storage(path: str) -> StorageInfo:
    """경로가 놓인 저장 장치의 종류를 판별하며, 볼륨 단위 결과는 캐시

    - UNC 경로는 네트워크, 비 Windows와 드라이브 문자가 없는 경로는 UNKNOWN
    """
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if drive.startswith(_UNC_PREFIX):
        return StorageInfo(StorageKind.NETWORK)
    if not _is_windows() or len(drive) != 2 or drive[1] != ":":
        return StorageInfo(StorageKind.UNKNOWN)
    key = drive.upper()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    info = _probe_volume(key)
    with _CACHE_LOCK:
        _CACHE[key] = info
    return info


HDD_LANE_LIMIT: int | None = None
"""HDD 대기열의 동시 실행 상한, None은 제한 없음

- 상한을 낮출수록 처리량이 떨어져 외장 HDD에서도 제한하지 않음
"""
_THREAD_WORKER_CAP: int = 32
_THREAD_WORKER_EXTRA: int = 4
_NETWORK_LANE_DIVISOR: int = 2


def default_worker_count(cpu_count: int) -> int:
    """ThreadPoolExecutor 기본 작업 수와 같은 CPU 기준 값"""
    return min(_THREAD_WORKER_CAP, cpu_count + _THREAD_WORKER_EXTRA)


def concurrency_limit(kind: StorageKind, cpu_count: int) -> int | None:
    """저장 장치 종류별 동시 실행 상한, None은 제한 없음

    - SSD와 판별 실패는 기존 동작을 유지하고, 네트워크는 CPU 기준 중간값으로 제한
    """
    if kind is StorageKind.HDD:
        return HDD_LANE_LIMIT
    if kind is StorageKind.NETWORK:
        return max(1, default_worker_count(cpu_count) // _NETWORK_LANE_DIVISOR)
    return None


def _lane_key(path: str, info: StorageInfo) -> tuple[str, int | str]:
    """대기열 묶음 키, 물리 디스크 번호가 있으면 드라이브 문자와 무관하게 디스크 단위로 묶음"""
    if info.disk_number is not None:
        return ("disk", info.disk_number)
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    return ("drive", drive.upper())


def plan_lanes(paths: Sequence[str], cpu_count: int) -> tuple[list[int], dict[int, int]]:
    """작업 경로별 대기열 번호와 대기열별 동시 실행 상한을 계산

    - 같은 물리 디스크(또는 같은 원격 공유)의 경로는 한 대기열로 묶음
    """
    lane_of_key: dict[tuple[str, int | str], int] = {}
    limits: dict[int, int] = {}
    lanes: list[int] = []
    for path in paths:
        info = probe_storage(path)
        key = _lane_key(path, info)
        lane = lane_of_key.get(key)
        if lane is None:
            lane = len(lane_of_key)
            lane_of_key[key] = lane
            limit = concurrency_limit(info.kind, cpu_count)
            if limit is not None:
                limits[lane] = limit
        lanes.append(lane)
    return lanes, limits


def clear_storage_cache() -> None:
    """볼륨 판별 캐시를 비움(테스트 격리와 장치 구성 변경 후 사용)"""
    with _CACHE_LOCK:
        _CACHE.clear()
