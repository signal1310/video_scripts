"""볼륨 식별자가 ctypes 호출을 모킹한 상태에서 로컬, 네트워크, UNC, 실패 대체를 구분하는지 검증

- 드라이브 문자 변경에도 같은 볼륨 위치가 되는지 함께 확인
- Windows 경로 계산은 호스트 드라이브 구성과 무관하게 `ntpath` 규칙으로만 확인
"""

import ctypes
import ntpath
import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from src.core.analysis import storage_probe, volume_identity
from src.core.analysis.volume_identity import (
    clear_volume_cache,
    volume_location,
)

_MOD = "src.core.analysis.volume_identity"
_DRIVE_FIXED: int = 3
_SERIAL: int = 0x001A2B3C
_ERROR_CONNECTION_UNAVAIL: int = 1201
# 위생 검사가 UNC 절대 경로를 잡지 않도록 조각으로 만듬
_BACKSLASH: str = "\\"
_SHARE_ROOT: str = _BACKSLASH * 2 + "Server" + _BACKSLASH + "Share"
_SHARE_ID: str = "unc:" + _BACKSLASH * 2 + "server" + _BACKSLASH + "share"
_DRIVE_ROOT: str = "E:" + _BACKSLASH
_DRIVE_ID: str = "vol:001a2b3c"
# POSIX bind mount 검증용 가짜 장치 번호와 stat 결과
_BIND_MOUNT_DEVICE: int = 2049


class _FakeStat:
    """`os.stat` 결과 중 장치 번호만 흉내내는 최소 대체 객체"""

    def __init__(self, device: int) -> None:
        self.st_dev = device


class _FakeKernel32:
    """kernel32의 볼륨 조회 함수를 흉내내는 최소 대체 객체

    - `root`가 문자열이면 그 값을 마운트 루트로, 호출 가능하면 경로에서 계산하고, `None`이면 실패로 응답
    """

    def __init__(
        self,
        *,
        root: Any = "E:\\",
        serial: int | None = _SERIAL,
        drive_type: int = _DRIVE_FIXED,
    ) -> None:
        self.root = root
        self.serial = serial
        self.drive_type = drive_type
        self.root_calls: list[str] = []
        self.serial_calls: list[str] = []
        self.drive_type_calls: list[str] = []

    def GetVolumePathNameW(self, path: str, buffer: Any, size: int) -> int:
        self.root_calls.append(path)
        if self.root is None:
            return 0
        buffer.value = self.root(path) if callable(self.root) else self.root
        return 1

    def GetVolumeInformationW(
        self,
        root: str,
        label: Any,
        label_size: int,
        serial: Any,
        max_component: Any,
        flags: Any,
        fs_name: Any,
        fs_name_size: int,
    ) -> int:
        self.serial_calls.append(root)
        if self.serial is None:
            return 0
        ctypes.cast(serial, ctypes.POINTER(ctypes.c_ulong)).contents.value = self.serial
        return 1

    def GetDriveTypeW(self, root: str) -> int:
        self.drive_type_calls.append(root)
        return self.drive_type


class _FakeMpr:
    """mpr의 원격 공유 조회를 흉내내는 최소 대체 객체"""

    def __init__(self, remote: str | None) -> None:
        self.remote = remote
        self.calls: list[str] = []

    def WNetGetConnectionW(self, drive: str, buffer: Any, length: Any) -> int:
        self.calls.append(drive)
        if self.remote is None:
            return _ERROR_CONNECTION_UNAVAIL
        buffer.value = self.remote
        return 0


class TestVolumeLocation(unittest.TestCase):
    """Windows 경로의 볼륨 식별자와 상대 경로를 모킹으로 검증"""

    def setUp(self) -> None:
        clear_volume_cache()
        self.addCleanup(clear_volume_cache)

    def _patched(self, kernel: _FakeKernel32, mpr: _FakeMpr | None = None) -> None:
        patchers = [
            patch(f"{_MOD}._is_windows", return_value=True),
            patch(f"{_MOD}.kernel32", return_value=kernel),
            patch(f"{_MOD}.drive_type", side_effect=kernel.GetDriveTypeW),
        ]
        if mpr is not None:
            patchers.append(patch(f"{_MOD}._mpr", return_value=mpr))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_local_volume_uses_serial_and_mount_relative_path(self) -> None:
        """로컬 볼륨은 일련 번호를 8자리 16진수로 쓰고 마운트 루트 기준 상대 경로를 소문자 posix로 만드는지 확인"""
        kernel = _FakeKernel32(root="E:" + _BACKSLASH)
        self._patched(kernel)
        self.assertEqual(
            volume_location("E:\\Clips\\Sub\\A.MP4"),
            volume_identity.VolumeLocation("vol:001a2b3c", "clips/sub/a.mp4"),
        )
        self.assertEqual(volume_location("E:\\"), volume_identity.VolumeLocation("vol:001a2b3c", ""))
        self.assertEqual(kernel.drive_type_calls, ["E:" + _BACKSLASH])
        self.assertEqual(kernel.serial_calls, ["E:" + _BACKSLASH])

    def test_network_drive_letter_uses_remote_share_name(self) -> None:
        """네트워크 드라이브 문자는 WNetGetConnectionW의 공유 경로로 식별하는지 확인"""
        kernel = _FakeKernel32(root="Z:\\", drive_type=volume_identity.DRIVE_REMOTE)
        mpr = _FakeMpr(_SHARE_ROOT)
        self._patched(kernel, mpr)
        result = volume_location("Z:\\Clips\\A.MP4")
        self.assertEqual(result, volume_identity.VolumeLocation(_SHARE_ID, "clips/a.mp4"))
        self.assertEqual(mpr.calls, ["Z:"])
        self.assertEqual(kernel.serial_calls, [])
        self.assertEqual(kernel.drive_type_calls, ["Z:" + _BACKSLASH])

    def test_subfolder_mapped_drive_matches_unc_path(self) -> None:
        """공유 하위 폴더에 연결한 드라이브 문자가 같은 파일의 UNC 경로와 같은 볼륨 위치가 되는지 확인

        - WNetGetConnectionW는 연결 폴더까지 담은 원격 경로를 주므로 공유 루트로 자르고 그 폴더를 상대 경로 접두사로 씀
        """
        remote = _SHARE_ROOT + _BACKSLASH + "Media"
        kernel = _FakeKernel32(root="Z:" + _BACKSLASH, drive_type=volume_identity.DRIVE_REMOTE)
        self._patched(kernel, _FakeMpr(remote))
        mapped = volume_location("Z:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        share = volume_location(remote + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        self.assertEqual(mapped, volume_identity.VolumeLocation(_SHARE_ID, "media/clips/a.mp4"))
        self.assertEqual(mapped, share)

    def test_mapped_drive_with_failed_mount_query_keeps_subfolder_prefix(self) -> None:
        """마운트 루트 조회가 실패해도 연결 폴더 접두사를 유지해 UNC 경로와 같은 볼륨 위치가 되는지 확인

        - 조회 실패 시 드라이브 루트의 원격 연결로 식별하되, 공유 루트 기준 연결 폴더를 상대 경로에 붙여야 함
        """
        remote = _SHARE_ROOT + _BACKSLASH + "Media"
        kernel = _FakeKernel32(root=None, drive_type=volume_identity.DRIVE_REMOTE)
        self._patched(kernel, _FakeMpr(remote))
        mapped = volume_location("Z:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        share = volume_location(remote + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        self.assertEqual(mapped, volume_identity.VolumeLocation(_SHARE_ID, "media/clips/a.mp4"))
        self.assertEqual(mapped, share)
        self.assertNotEqual(mapped, volume_location(_SHARE_ROOT + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4"))

    def test_mapped_drive_root_is_subfolder_of_share(self) -> None:
        """공유 하위 폴더에 연결한 드라이브 문자의 루트 자체도 그 폴더의 상대 경로를 갖는지 확인"""
        remote = _SHARE_ROOT + _BACKSLASH + "Media" + _BACKSLASH
        kernel = _FakeKernel32(root="Z:" + _BACKSLASH, drive_type=volume_identity.DRIVE_REMOTE)
        self._patched(kernel, _FakeMpr(remote))
        self.assertEqual(volume_location("Z:" + _BACKSLASH), volume_identity.VolumeLocation(_SHARE_ID, "media"))

    def test_unc_path_uses_share_root_without_volume_queries(self) -> None:
        """UNC 경로는 공유 루트를 마운트 루트로 쓰고 볼륨 조회를 하지 않는지 확인"""
        kernel = _FakeKernel32()
        self._patched(kernel)
        result = volume_location(_SHARE_ROOT + "\\Clips\\A.MP4")
        self.assertEqual(result, volume_identity.VolumeLocation(_SHARE_ID, "clips/a.mp4"))
        self.assertEqual(kernel.root_calls, [])
        self.assertEqual(kernel.serial_calls, [])

    def test_api_failure_falls_back_to_normcased_root(self) -> None:
        """마운트 루트와 일련 번호 조회가 모두 실패하면 정규화한 드라이브 루트로 식별하는지 확인"""
        self._patched(_FakeKernel32(root=None, serial=None))
        self.assertEqual(
            volume_location("E:\\Clips\\A.MP4"),
            volume_identity.VolumeLocation("path:e:\\", "clips/a.mp4"),
        )

    def test_remote_connection_failure_falls_back_to_drive_root(self) -> None:
        """원격 드라이브의 공유 조회가 실패하면 드라이브 루트로 식별하는지 확인"""
        self._patched(_FakeKernel32(root="Z:\\", drive_type=volume_identity.DRIVE_REMOTE), _FakeMpr(None))
        self.assertEqual(volume_location("Z:\\A.MP4").volume_id, "path:z:\\")

    def test_same_serial_on_two_drive_letters_is_one_location(self) -> None:
        """일련 번호가 같은 두 드라이브 문자의 같은 상대 경로는 같은 볼륨 위치가 되는지 확인"""
        self._patched(_FakeKernel32(root=lambda path: ntpath.splitdrive(path)[0] + _BACKSLASH))
        first = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        second = volume_location("F:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        self.assertEqual(first, second)
        self.assertEqual(first.volume_id, _DRIVE_ID)

    def test_relative_path_is_resolved_before_lookup(self) -> None:
        """상대 경로는 현재 작업 폴더 기준 절대 경로로 바꾼 뒤 같은 볼륨 위치가 되는지 확인

        - Windows `ntpath.abspath`는 C 구현을 쓰므로 작업 폴더만 바꾸는 대신 변환 지점을 대체
        """
        self._patched(_FakeKernel32(root=lambda path: ntpath.splitdrive(path)[0] + _BACKSLASH))
        cwd = "E:" + _BACKSLASH + "Work"
        with patch(f"{_MOD}.ntpath.abspath", side_effect=lambda path: ntpath.join(cwd, path)):
            relative = volume_location("Clips" + _BACKSLASH + "A.MP4")
        self.assertEqual(relative, volume_identity.VolumeLocation(_DRIVE_ID, "work/clips/a.mp4"))

    def test_mount_root_on_another_drive_falls_back_to_drive_root(self) -> None:
        """정션이 다른 볼륨을 가리켜 마운트 루트가 경로의 상위가 아니면 드라이브 루트와 path 식별자로 되돌아가는지 확인"""
        kernel = _FakeKernel32(root="F:" + _BACKSLASH)
        self._patched(kernel)
        result = volume_location("E:" + _BACKSLASH + "Junction" + _BACKSLASH + "A.MP4")
        self.assertEqual(result, volume_identity.VolumeLocation("path:e:" + _BACKSLASH, "junction/a.mp4"))
        self.assertEqual(kernel.serial_calls, [])

    def test_mount_root_prefix_must_end_on_segment_boundary(self) -> None:
        """마운트 루트가 경로의 이름 일부만 겹치면 상위로 인정하지 않고 드라이브 루트로 되돌아가는지 확인"""
        self._patched(_FakeKernel32(root="E:" + _BACKSLASH + "Clips"))
        result = volume_location("E:" + _BACKSLASH + "Clips2" + _BACKSLASH + "A.MP4")
        self.assertEqual(result, volume_identity.VolumeLocation("path:e:" + _BACKSLASH, "clips2/a.mp4"))

    def test_mount_root_without_trailing_separator_maps_to_itself(self) -> None:
        """끝 구분자 없이 주어진 탑재 폴더 경로가 같은 위치의 루트로 식별되는지 확인

        - 드라이브 종류는 드라이브 루트로, 일련 번호는 탑재 폴더 루트로 조회해야 함
        """
        mount_root = "E:" + _BACKSLASH + "Mount" + _BACKSLASH
        kernel = _FakeKernel32(root=mount_root)
        self._patched(kernel)
        result = volume_location("E:" + _BACKSLASH + "Mount")
        self.assertEqual(result, volume_identity.VolumeLocation(_DRIVE_ID, ""))
        self.assertEqual(kernel.serial_calls, [mount_root])
        self.assertEqual(kernel.drive_type_calls, [_DRIVE_ROOT])

    def test_serial_lookup_normalizes_trailing_separator(self) -> None:
        """끝 구분자가 없는 탑재 루트도 일련 번호 조회에 구분자 하나를 붙여 같은 볼륨으로 식별하는지 확인"""
        mount_root = "E:" + _BACKSLASH + "Mount"
        kernel = _FakeKernel32(root=mount_root)
        self._patched(kernel)
        without_separator = volume_location(mount_root)
        with_separator = volume_location(mount_root + _BACKSLASH)
        self.assertEqual(without_separator.volume_id, _DRIVE_ID)
        self.assertEqual(with_separator.volume_id, _DRIVE_ID)
        self.assertEqual(kernel.serial_calls, [mount_root + _BACKSLASH])

    def test_zero_serial_falls_back_to_path_id(self) -> None:
        """일련 번호가 0인 볼륨은 다른 가상 볼륨과 섞이지 않도록 경로 기반 식별자로 되돌아가는지 확인"""
        self._patched(_FakeKernel32(root=_DRIVE_ROOT, serial=0))
        self.assertEqual(
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4"),
            volume_identity.VolumeLocation("path:e:" + _BACKSLASH, "clips/a.mp4"),
        )

    def test_mount_root_without_trailing_separator_keeps_segment_boundary(self) -> None:
        """끝 구분자를 뗀 탑재 폴더 루트가 이름 일부만 겹치는 경로를 상위로 인정하지 않는지 확인"""
        self._patched(_FakeKernel32(root="E:" + _BACKSLASH + "Mount" + _BACKSLASH))
        result = volume_location("E:" + _BACKSLASH + "Mounted" + _BACKSLASH + "A.MP4")
        self.assertEqual(result, volume_identity.VolumeLocation("path:e:" + _BACKSLASH, "mounted/a.mp4"))

    def test_volume_id_is_cached_per_mount_root(self) -> None:
        """같은 마운트 루트를 다시 조회할 때 일련 번호 조회를 반복하지 않는지 확인"""
        kernel = _FakeKernel32(root=_DRIVE_ROOT)
        self._patched(kernel)
        volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        volume_location("E:" + _BACKSLASH + "Other" + _BACKSLASH + "B.MP4")
        self.assertEqual(kernel.serial_calls, [_DRIVE_ROOT])

    def test_mount_root_lookup_runs_once_for_files_in_one_folder(self) -> None:
        """한 폴더의 파일 여러 개를 조회할 때 마운트 루트 조회가 한 번만 실행되는지 확인

        - 파일마다 GetVolumePathNameW를 부르면 시작 복원과 분석이 파일 수에 비례해 느려짐
        """
        kernel = _FakeKernel32(root=_DRIVE_ROOT)
        self._patched(kernel)
        locations = [
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + name)
            for name in ("A.MP4", "B.MP4", "C.MP4", "D.MP4")
        ]
        self.assertEqual(
            [location.volume_path for location in locations],
            ["clips/a.mp4", "clips/b.mp4", "clips/c.mp4", "clips/d.mp4"],
        )
        self.assertEqual(kernel.root_calls, ["E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4"])

    def test_mount_root_lookup_is_repeated_per_folder(self) -> None:
        """폴더가 다르면 마운트 루트 조회를 따로 하는지 확인(폴더 단위 캐시)"""
        kernel = _FakeKernel32(root=_DRIVE_ROOT)
        self._patched(kernel)
        volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        volume_location("E:" + _BACKSLASH + "Other" + _BACKSLASH + "A.MP4")
        self.assertEqual(
            kernel.root_calls,
            [
                "E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4",
                "E:" + _BACKSLASH + "Other" + _BACKSLASH + "A.MP4",
            ],
        )

    def test_failed_mount_root_lookup_is_cached_per_folder(self) -> None:
        """마운트 루트 조회 실패도 폴더 단위로 캐시해 같은 폴더에서 다시 부르지 않는지 확인"""
        kernel = _FakeKernel32(root=None)
        self._patched(kernel)
        first = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        second = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "B.MP4")
        self.assertEqual(first.volume_id, second.volume_id)
        self.assertEqual((first.volume_path, second.volume_path), ("clips/a.mp4", "clips/b.mp4"))
        self.assertEqual(len(kernel.root_calls), 1)

    def test_mount_point_folder_result_is_not_shared_with_its_parent_folder(self) -> None:
        """탑재 폴더 경로를 조회한 결과를 그 상위 폴더의 다른 경로에 쓰지 않는지 확인

        - 탑재 폴더 경로는 상위 폴더와 같은 폴더 키를 갖게 되어, 결과를 캐시하면 상위 폴더의 파일이
          탑재 폴더가 아닌 마운트 루트를 받아 경로 기반 식별자로 떨어짐
        """
        mount_root = "E:" + _BACKSLASH + "Clips" + _BACKSLASH + "Sub"

        def _root(path: str) -> str:
            return mount_root + _BACKSLASH if path.endswith("Sub") else _DRIVE_ROOT

        kernel = _FakeKernel32(root=_root)
        self._patched(kernel)
        self.assertEqual(volume_location(mount_root), volume_identity.VolumeLocation(_DRIVE_ID, ""))
        sibling = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
        self.assertEqual(sibling, volume_identity.VolumeLocation(_DRIVE_ID, "clips/a.mp4"))
        self.assertEqual(len(kernel.root_calls), 2)

    def test_mount_root_lookup_cache_is_bounded(self) -> None:
        """폴더가 상한을 넘으면 마운트 루트 캐시를 비워 계속 커지지 않는지 확인"""
        kernel = _FakeKernel32(root=_DRIVE_ROOT)
        self._patched(kernel)
        with patch(f"{_MOD}._VOLUME_ROOT_CACHE_MAX_ENTRIES", 1):
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
            volume_location("E:" + _BACKSLASH + "Other" + _BACKSLASH + "A.MP4")
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "B.MP4")
        self.assertEqual(len(kernel.root_calls), 3)

    def test_mount_root_lookup_cache_expires(self) -> None:
        """캐시 수명이 지나면 마운트 루트를 다시 조회하는지 확인(드라이브 재연결 반영)"""
        root = "E:" + _BACKSLASH
        kernel = _FakeKernel32(root=root)
        self._patched(kernel)
        now = [1000.0]
        with patch(f"{_MOD}._clock", side_effect=lambda: now[0]):
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "B.MP4")
            now[0] += volume_identity._VOLUME_ROOT_CACHE_TTL_SECONDS + 1
            volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "C.MP4")
        self.assertEqual(len(kernel.root_calls), 2)

    def test_reassigned_drive_letter_reloads_volume_id_after_ttl(self) -> None:
        """드라이브 문자에 다른 볼륨이 연결되면 캐시 수명이 지난 뒤 새 일련 번호로 식별하는지 확인"""
        kernel = _FakeKernel32(root=_DRIVE_ROOT, serial=0x11111111)
        self._patched(kernel)
        now = [1000.0]
        with patch(f"{_MOD}._clock", side_effect=lambda: now[0]):
            first = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "A.MP4")
            kernel.serial = 0x22222222
            within_ttl = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "B.MP4")
            now[0] += volume_identity._VOLUME_ID_CACHE_TTL_SECONDS + 1
            expired = volume_location("E:" + _BACKSLASH + "Clips" + _BACKSLASH + "C.MP4")
        self.assertEqual(first.volume_id, "vol:11111111")
        self.assertEqual(within_ttl.volume_id, "vol:11111111")
        self.assertEqual(expired.volume_id, "vol:22222222")
        self.assertEqual(kernel.serial_calls, [_DRIVE_ROOT, _DRIVE_ROOT])


class TestResolvedVolumeId(unittest.TestCase):
    """볼륨 식별자가 실제 볼륨을 가리키는지 판정"""

    def test_real_volume_ids_are_resolved(self) -> None:
        for volume_id in ("vol:001a2b3c", "unc:" + _BACKSLASH * 2 + "server" + _BACKSLASH + "share", "dev:2049:/mnt/a"):
            self.assertTrue(volume_identity.is_resolved_volume_id(volume_id))

    def test_path_fallback_id_is_unresolved(self) -> None:
        self.assertFalse(volume_identity.is_resolved_volume_id("path:e:" + _BACKSLASH))


class TestNonWindowsLocation(unittest.TestCase):
    """POSIX 경로의 볼륨 식별자와 마운트 루트 탐색을 검증"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_device_id_and_path_relative_to_mount_root(self) -> None:
        """POSIX는 장치 번호와 마운트 루트로 식별하고 마운트 루트 기준 상대 경로를 쓰는지 확인"""
        clips = os.path.join(self.tmp.name, "Clips")
        with patch(f"{_MOD}._is_windows", return_value=False), patch(f"{_MOD}._mount_root", return_value=self.tmp.name):
            result = volume_location(clips)
        self.assertEqual(
            result,
            volume_identity.VolumeLocation(
                f"dev:{os.stat(self.tmp.name).st_dev}:{os.path.normcase(self.tmp.name)}",
                os.path.normcase("Clips"),
            ),
        )

    def test_bind_mount_subdirectory_gets_distinct_id(self) -> None:
        """같은 장치의 bind mount는 마운트 루트 경로가 달라 서로 다른 볼륨 위치가 되는지 확인"""
        with patch(f"{_MOD}.os.stat", side_effect=lambda path: _FakeStat(_BIND_MOUNT_DEVICE)):
            inside = volume_identity._device_volume_id("/mnt/a")
            source = volume_identity._device_volume_id("/srv/data")
        self.assertEqual(inside, f"dev:{_BIND_MOUNT_DEVICE}:" + os.path.normcase("/mnt/a"))
        self.assertEqual(source, f"dev:{_BIND_MOUNT_DEVICE}:" + os.path.normcase("/srv/data"))
        self.assertNotEqual(inside, source)

    def test_mount_root_walks_up_to_mount_point(self) -> None:
        """마운트 지점을 만나면 그 지점을, 없으면 최상위 경로를 마운트 루트로 쓰는지 확인"""
        with patch(f"{_MOD}.os.path.ismount", side_effect=lambda path: path == "/mnt"):
            self.assertEqual(volume_identity._mount_root("/mnt/sub/dir"), "/mnt")
        with patch(f"{_MOD}.os.path.ismount", return_value=False):
            self.assertEqual(volume_identity._mount_root("/alpha/beta"), "/")

    def test_missing_mount_root_falls_back_to_normcased_path(self) -> None:
        """마운트 루트를 stat하지 못하면 정규화한 경로로 식별하는지 확인"""
        with patch(f"{_MOD}.os.stat", side_effect=OSError):
            self.assertEqual(
                volume_identity._device_volume_id("/Missing"),
                "path:" + os.path.normcase("/Missing"),
            )


class TestRootWithoutDrive(unittest.TestCase):
    """드라이브 문자나 UNC 접두사가 없는 루트가 경로 기반 식별자로 처리되는지 검증"""

    def test_root_without_drive_uses_path_prefix(self) -> None:
        """드라이브 문자가 없는 마운트 루트는 정규화한 경로로 식별하고 상대 경로 접두사를 두지 않는지 확인"""
        self.assertEqual(
            volume_identity._compute_volume_identity("/MountPoint"),
            ("path:" + os.path.normcase("/MountPoint"), ""),
        )


class _FakeDllFunction:
    """DLL 함수 흉내: argtypes와 restype만 저장"""

    def __init__(self) -> None:
        self.argtypes: list[Any] = []
        self.restype: Any = None


class _FakeDll:
    """ctypes.WinDLL 흉내: 선언한 함수 이름만 돌려주고 나머지는 AttributeError"""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.functions: dict[str, _FakeDllFunction] = {name: _FakeDllFunction() for name in names}

    def __getattr__(self, name: str) -> _FakeDllFunction:
        functions = self.__dict__.get("functions", {})
        if name not in functions:
            raise AttributeError(name)
        return functions[name]


class TestDllDeclarations(unittest.TestCase):
    """지연 로드 DLL 함수 집합이 호출 형식을 선언하고 캐시되는지 검증"""

    def test_kernel32_loader_is_shared_with_storage_probe(self) -> None:
        """볼륨 식별이 저장 장치 판별과 같은 kernel32 로더와 원격 드라이브 상수를 쓰는지 확인"""
        self.assertIs(volume_identity.kernel32, storage_probe.kernel32)
        self.assertIs(volume_identity.drive_type, storage_probe.drive_type)
        self.assertEqual(volume_identity.DRIVE_REMOTE, storage_probe.DRIVE_REMOTE)

    def test_call_signatures_are_declared(self) -> None:
        """kernel32와 mpr 로더가 WinDLL을 한 번 만들고 인자 형식을 지정하는지 확인"""
        kernel_dll = _FakeDll(
            ("CreateFileW", "DeviceIoControl", "CloseHandle", "GetDriveTypeW", "GetVolumePathNameW", "GetVolumeInformationW")
        )
        mpr_dll = _FakeDll(("WNetGetConnectionW",))
        volume_identity.kernel32.cache_clear()
        self.addCleanup(volume_identity.kernel32.cache_clear)
        volume_identity._mpr.cache_clear()
        self.addCleanup(volume_identity._mpr.cache_clear)
        with patch.object(ctypes, "WinDLL", side_effect=[kernel_dll, mpr_dll], create=True):
            self.assertIs(volume_identity.kernel32(), kernel_dll)
            self.assertIs(volume_identity._mpr(), mpr_dll)
        self.assertEqual(len(kernel_dll.functions["GetVolumePathNameW"].argtypes), 3)
        self.assertEqual(len(kernel_dll.functions["GetVolumeInformationW"].argtypes), 8)
        self.assertIs(kernel_dll.functions["GetDriveTypeW"].restype, ctypes.c_uint)
        self.assertEqual(len(mpr_dll.functions["WNetGetConnectionW"].argtypes), 3)


class TestRealVolumeLocation(unittest.TestCase):
    """실제 볼륨 조회가 예외 없이 결과 형태를 갖추는지 검증"""

    def setUp(self) -> None:
        clear_volume_cache()
        self.addCleanup(clear_volume_cache)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @unittest.skipUnless(sys.platform == "win32", "Windows 볼륨 조회 전용")
    def test_temp_directory_returns_mount_root_identity(self) -> None:
        """임시 폴더 경로가 실제 마운트 루트의 일련 번호와 그 루트 기준 상대 경로로 식별되는지 확인"""
        root = volume_identity._volume_root(self.tmp.name)
        if root is None:
            self.skipTest("호스트가 GetVolumePathNameW로 마운트 루트를 돌려주지 않음")
        serial = volume_identity._volume_serial(root)
        expected_id = f"vol:{serial:08x}" if serial else "path:" + ntpath.normcase(root)
        expected_rel = ntpath.relpath(ntpath.normcase(self.tmp.name), ntpath.normcase(root)).replace(_BACKSLASH, "/")
        self.assertEqual(volume_location(self.tmp.name), volume_identity.VolumeLocation(expected_id, expected_rel))


if __name__ == "__main__":
    unittest.main()
