"""QuickLook(Windows 미리 보기 프로그램) 실행 요청, 상주 웜업, Microsoft Store 판 앱 활성화 모듈"""

import ctypes
import os
import shutil
import subprocess

QUICKLOOK_EXE_NAME: str = "QuickLook.exe"
# - Microsoft Store 판의 패키지 앱 ID. 실행 파일은 WindowsApps 아래에 있어 직접 실행이 거부되므로 앱 활성화 COM으로 호출
QUICKLOOK_STORE_APP_ID: str = "21090PaddyXu.QuickLook_egxr34yet59cg!Main"
_CLSID_APPLICATION_ACTIVATION_MANAGER: str = "{45BA127D-10A8-46EA-8AB7-56EA9078943C}"
_IID_APPLICATION_ACTIVATION_MANAGER: str = "{2E941141-7F97-4756-BA1D-9DECDE894A3D}"
_CLSCTX_LOCAL_SERVER: int = 0x4
_COINIT_APARTMENTTHREADED: int = 0x2
_HRESULT_OK: int = 0
_HRESULT_S_FALSE: int = 1
_ACTIVATE_OPTION_NONE: int = 0
# - IUnknown 다음 IApplicationActivationManager 메서드 순서 기준 인덱스
_VTBL_INDEX_RELEASE: int = 2
_VTBL_INDEX_ACTIVATE_APPLICATION: int = 3


def open_with_quicklook(filepath: str) -> bool:
    """QuickLook에 미리 보기를 요청하고 요청 성공 여부 반환, 같은 파일을 보는 중이면 QuickLook이 창을 닫음"""
    full_path = os.path.normpath(filepath)
    if _activate_store_quicklook(f'"{full_path}"'):
        return True
    exe_path = shutil.which(QUICKLOOK_EXE_NAME)
    if exe_path is None:
        return False
    subprocess.Popen([exe_path, full_path])
    return True


class _Guid(ctypes.Structure):
    """COM GUID 구조체"""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


def is_quicklook_running() -> bool:
    """QuickLook 프로세스 실행 여부 반환, 목록 조회에 실패하면 실행 중이 아닌 것으로 판단"""
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {QUICKLOOK_EXE_NAME}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return QUICKLOOK_EXE_NAME.lower() in result.stdout.lower()


def warm_up_quicklook() -> None:
    """실행 중이 아닐 때만 QuickLook을 인수 없이 활성화해 상주시킴, 이미 실행 중이면 중복 실행 안내 창이 뜨므로 건너뜀"""
    if not is_quicklook_running():
        _activate_store_quicklook("")


def _activate_store_quicklook(arguments: str) -> bool:
    """Store 판 QuickLook을 IApplicationActivationManager로 활성화하고 인수 문자열을 전달, 성공 여부 반환"""
    ole32 = ctypes.windll.ole32
    init_hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    # - S_OK, S_FALSE일 때만 짝을 맞춰 해제, RPC_E_CHANGED_MODE는 이미 다른 모드로 초기화된 상태이므로 해제하지 않음
    should_uninitialize = init_hr in (_HRESULT_OK, _HRESULT_S_FALSE)
    try:
        return _call_activate_application(arguments)
    finally:
        if should_uninitialize:
            ole32.CoUninitialize()


def _call_activate_application(arguments: str) -> bool:
    """활성화 관리자 객체를 만들어 ActivateApplication을 호출하고 객체를 해제"""
    ole32 = ctypes.windll.ole32
    clsid = _Guid()
    iid = _Guid()
    ole32.CLSIDFromString(_CLSID_APPLICATION_ACTIVATION_MANAGER, ctypes.byref(clsid))
    ole32.CLSIDFromString(_IID_APPLICATION_ACTIVATION_MANAGER, ctypes.byref(iid))
    manager = ctypes.c_void_p()
    create_hr = ole32.CoCreateInstance(
        ctypes.byref(clsid), None, _CLSCTX_LOCAL_SERVER, ctypes.byref(iid), ctypes.byref(manager)
    )
    manager_addr = manager.value
    if create_hr != _HRESULT_OK or manager_addr is None:
        return False
    vtbl = ctypes.cast(manager_addr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    activate_addr = vtbl[_VTBL_INDEX_ACTIVATE_APPLICATION]
    release_addr = vtbl[_VTBL_INDEX_RELEASE]
    pid = ctypes.c_uint32(0)
    try:
        if activate_addr is None:
            return False
        activate = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint32),
        )(activate_addr)
        hr = activate(
            manager_addr,
            QUICKLOOK_STORE_APP_ID,
            arguments,
            _ACTIVATE_OPTION_NONE,
            ctypes.byref(pid),
        )
        return hr == _HRESULT_OK
    finally:
        if release_addr is not None:
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(release_addr)(manager_addr)
