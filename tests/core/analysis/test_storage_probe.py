"""저장 장치 판별이 ctypes 호출을 모킹한 상태에서 SSD, HDD, 네트워크, USB 정보 없음, 실패, 같은 디스크 두 볼륨을 구분하고 허용 제어 코드만 쓰는지 검증"""

import ctypes
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from src.core.analysis import storage_probe
from src.core.analysis.storage_probe import (
    ALLOWED_IOCTL_CODES,
    StorageInfo,
    StorageKind,
    classify_storage,
    clear_storage_cache,
    probe_storage,
)
from src.core.messages import format_storage_kind

_BUS_TYPE_USB: int = 7
_MOD = "src.core.analysis.storage_probe"


class TestClassifyTable(unittest.TestCase):
    """탐색 지연 속성 값에 따른 종류 판정 표"""

    def test_seek_penalty_maps_to_kind(self) -> None:
        """속성 없음은 UNKNOWN, 탐색 지연 있음은 HDD, 없음은 SSD로 판정하는지 확인"""
        self.assertIs(classify_storage(None), StorageKind.UNKNOWN)
        self.assertIs(classify_storage(True), StorageKind.HDD)
        self.assertIs(classify_storage(False), StorageKind.SSD)


class TestProbeStorage(unittest.TestCase):
    """볼륨 조회 결과의 판정과 캐시를 모킹으로 검증"""

    def setUp(self) -> None:
        clear_storage_cache()
        self.addCleanup(clear_storage_cache)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.drive, _ = os.path.splitdrive(os.path.abspath(self.tmp.name))

    def _patched(
        self,
        *,
        seek: bool | None,
        bus: int | None = None,
        disk: int | None = 0,
        remote: bool = False,
        handle: int | None = 1,
    ) -> dict[str, object]:
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks: dict[str, object] = {}
        mocks["open"] = stack.enter_context(patch(f"{_MOD}._open_volume", return_value=handle))
        stack.enter_context(patch(f"{_MOD}._close_handle"))
        stack.enter_context(patch(f"{_MOD}._is_windows", return_value=True))
        stack.enter_context(
            patch(f"{_MOD}.drive_type", return_value=storage_probe.DRIVE_REMOTE if remote else 3)
        )
        stack.enter_context(patch(f"{_MOD}._query_seek_penalty", return_value=seek))
        stack.enter_context(patch(f"{_MOD}._query_bus_type", return_value=bus))
        stack.enter_context(patch(f"{_MOD}._query_disk_number", return_value=disk))
        return mocks

    def test_ssd_and_hdd_are_reported_with_disk_number(self) -> None:
        """탐색 지연 속성에 따라 SSD와 HDD가 판정되고 디스크 번호가 함께 기록되는지 확인"""
        self._patched(seek=False, disk=0)
        self.assertEqual(probe_storage(self.tmp.name), StorageInfo(StorageKind.SSD, None, 0))
        clear_storage_cache()
        self._patched(seek=True, disk=1)
        self.assertEqual(probe_storage(self.tmp.name), StorageInfo(StorageKind.HDD, None, 1))

    def test_usb_bridge_without_media_property_is_unknown(self) -> None:
        """USB 버스이면서 탐색 지연 속성이 없으면 UNKNOWN이고 버스 종류는 진단용으로 남는지 확인"""
        self._patched(seek=None, bus=_BUS_TYPE_USB, disk=2)
        info = probe_storage(self.tmp.name)
        self.assertIs(info.kind, StorageKind.UNKNOWN)
        self.assertEqual(info.bus_type, _BUS_TYPE_USB)

    def test_remote_drive_is_network_without_opening_volume(self) -> None:
        """원격 드라이브는 볼륨을 열지 않고 NETWORK로 판정하는지 확인"""
        mocks = self._patched(seek=True, remote=True)
        self.assertIs(probe_storage(self.tmp.name).kind, StorageKind.NETWORK)
        mocks["open"].assert_not_called()  # type: ignore[attr-defined]

    def test_unc_path_is_network_without_any_query(self) -> None:
        """UNC 경로는 드라이브 문자가 없으므로 조회 없이 NETWORK로 판정하는지 확인"""
        unc = "\\" + "\\" + "host" + "\\" + "share" + "\\" + "clip.mp4"
        with patch(f"{_MOD}._open_volume") as opened:
            self.assertIs(probe_storage(unc).kind, StorageKind.NETWORK)
        opened.assert_not_called()

    def test_non_windows_is_unknown(self) -> None:
        """Windows가 아니면 조회 없이 UNKNOWN을 반환하는지 확인"""
        with patch(f"{_MOD}._is_windows", return_value=False), patch(
            f"{_MOD}._open_volume"
        ) as opened:
            self.assertIs(probe_storage(self.tmp.name).kind, StorageKind.UNKNOWN)
        opened.assert_not_called()

    def test_open_failure_is_unknown(self) -> None:
        """볼륨 핸들을 열지 못하면 UNKNOWN으로 두어 기존 동작을 유지하는지 확인"""
        self._patched(seek=True, handle=None)
        self.assertIs(probe_storage(self.tmp.name).kind, StorageKind.UNKNOWN)

    def test_two_volumes_on_same_disk_share_disk_number(self) -> None:
        """같은 물리 디스크의 두 볼륨은 디스크 번호와 종류가 같고 캐시는 볼륨 단위로 나뉘는지 확인"""
        self._patched(seek=False, disk=5)
        first = probe_storage(self.tmp.name)
        other_drive = chr(ord(self.drive[0].upper()) + 1) + ":"
        with patch(f"{_MOD}._open_volume", return_value=2) as opened, patch(
            f"{_MOD}._query_seek_penalty", return_value=False
        ), patch(f"{_MOD}._query_disk_number", return_value=5), patch(
            f"{_MOD}._query_bus_type", return_value=None
        ):
            second = storage_probe._probe_volume(other_drive)
        self.assertEqual(first.disk_number, second.disk_number)
        self.assertEqual(first.kind, second.kind)
        opened.assert_called_once()

    def test_result_is_cached_per_volume(self) -> None:
        """같은 볼륨을 두 번 판별할 때 두 번째는 장치 조회를 다시 하지 않는지 확인"""
        mocks = self._patched(seek=False)
        probe_storage(self.tmp.name)
        probe_storage(self.tmp.name)
        mocks["open"].assert_called_once()  # type: ignore[attr-defined]


class TestStorageLogLine(unittest.TestCase):
    """분석 시작 로그의 저장 장치 문장이 승인된 문구와 종류별 표기를 지키는지 검증"""

    def test_log_sentences_follow_approved_wording(self) -> None:
        """판별 불가 USB 장치는 승인 문구를, SSD와 네트워크 드라이브는 종류별 문장을 만드는지 확인"""
        self.assertEqual(
            format_storage_kind(StorageInfo(StorageKind.UNKNOWN, storage_probe.BUS_TYPE_USB, 1)),
            "작업 폴더 저장 장치는 판별 불가입니다(USB 연결).",
        )
        self.assertEqual(
            format_storage_kind(StorageInfo(StorageKind.UNKNOWN, 17, 0)),
            "작업 폴더 저장 장치는 판별 불가입니다.",
        )
        self.assertEqual(
            format_storage_kind(StorageInfo(StorageKind.SSD, 17, 0)),
            "작업 폴더 저장 장치는 SSD입니다.",
        )
        self.assertEqual(
            format_storage_kind(StorageInfo(StorageKind.NETWORK)),
            "작업 폴더 저장 장치는 네트워크 드라이브입니다.",
        )


class TestIoctlAllowList(unittest.TestCase):
    """쓰기 계열 제어 코드가 쓰이지 않도록 허용 목록과 호출 경로를 검증"""

    def test_module_ioctl_constants_are_read_only_queries(self) -> None:
        """모듈의 제어 코드 상수가 허용 목록과 정확히 같고 그 밖의 값이 없는지 확인"""
        declared = {
            value
            for name, value in vars(storage_probe).items()
            if name.startswith("IOCTL_") and isinstance(value, int)
        }
        self.assertEqual(declared, set(ALLOWED_IOCTL_CODES))
        self.assertEqual(
            ALLOWED_IOCTL_CODES, frozenset({0x002D1400, 0x00560000})
        )

    def test_disallowed_code_is_rejected_before_device_call(self) -> None:
        """허용 목록 밖의 제어 코드는 DeviceIoControl 호출 전에 거부되는지 확인"""
        with patch(f"{_MOD}.kernel32") as kernel, self.assertRaises(ValueError):
            storage_probe._device_query(1, 0x00070000, storage_probe._VolumeDiskExtents)
        kernel.assert_not_called()

    def test_query_structures_have_expected_size(self) -> None:
        """조회 구조체 크기가 Windows 정의와 맞아 버퍼가 어긋나지 않는지 확인"""
        self.assertEqual(ctypes.sizeof(storage_probe._SeekPenaltyDescriptor), 12)
        self.assertEqual(ctypes.sizeof(storage_probe._StorageDeviceDescriptor), 36)


if __name__ == "__main__":
    unittest.main()
