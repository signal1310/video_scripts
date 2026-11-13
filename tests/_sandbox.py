"""테스트 실행 중 실제 환경(.env·.rootdir·레지스트리·외부 파일) 접근과 변조를 원천 차단하는 격리 가드 모듈"""

from __future__ import annotations

import atexit
import builtins
import io
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import PyQt6.QtCore as QtCore
from PyQt6.QtCore import QSettings

_P = ParamSpec("_P")
_R = TypeVar("_R")

_installed = False
_sandbox_root: str | None = None
_REPO_ROOT_NORM = os.path.normcase(
    os.path.realpath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
)
_DEVNULL_NORM = os.path.normcase(os.path.abspath(os.devnull))
_WRITE_MODE_FLAGS = ("w", "a", "x", "+")

_OrigQSettings = QSettings


def _normalize_path(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> str:
    text = os.fsdecode(path)
    return os.path.normcase(os.path.realpath(os.path.abspath(text)))


def _is_within_dir(target_norm: str, base_norm: str) -> bool:
    try:
        return os.path.commonpath([target_norm, base_norm]) == base_norm
    except ValueError:
        return False


def _is_allowed_write_path(
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
) -> bool:
    """임시 디렉터리·널 디바이스·저장소 내 파이썬 바이트코드 캐시만 쓰기 허용"""
    target_norm = _normalize_path(path)
    if target_norm == _DEVNULL_NORM:
        return True

    temp_root_norm = _normalize_path(tempfile.gettempdir())
    if _is_within_dir(target_norm, temp_root_norm):
        return True

    if _sandbox_root is not None and _is_within_dir(
        target_norm, _normalize_path(_sandbox_root)
    ):
        return True

    # 모듈 임포트 중 파이썬이 생성하는 __pycache__/*.pyc 컴파일 산출물은 허용
    parent_name = os.path.basename(os.path.dirname(target_norm))
    file_name = os.path.basename(target_norm)
    return (
        _is_within_dir(target_norm, _REPO_ROOT_NORM)
        and parent_name == "__pycache__"
        and ".pyc" in file_name
    )


def _assert_allowed_write_path(
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    *,
    op: str,
) -> None:
    if not _is_allowed_write_path(path):
        raise PermissionError(
            f"[TEST SANDBOX] 격리 영역 외부 쓰기 작업이 차단되었습니다 ({op}): {os.fsdecode(path)}"
        )


def get_sandbox_dir() -> str:
    """현재 테스트 프로세스의 격리 임시 디렉터리 경로를 반환"""
    if _sandbox_root is None:
        raise RuntimeError("테스트 격리 샌드박스가 아직 초기화되지 않았습니다.")
    return _sandbox_root


def _sandbox_env_path() -> str:
    return os.path.join(get_sandbox_dir(), ".env")


def _sandbox_rootdir_path() -> str:
    return os.path.join(get_sandbox_dir(), ".rootdir")


def _sandbox_app_settings() -> _OrigQSettings:
    ini_path = os.path.join(get_sandbox_dir(), "ui_settings.ini")
    return _OrigQSettings(ini_path, _OrigQSettings.Format.IniFormat)


class _GuardedQSettings(_OrigQSettings):
    """테스트 중 레지스트리(NativeFormat) 및 샌드박스 외부 QSettings 접근을 차단하는 래퍼"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if (
            len(args) >= 2
            and args[1] == _OrigQSettings.Format.IniFormat
            and isinstance(args[0], str)
        ):
            _assert_allowed_write_path(args[0], op="QSettings")
            super().__init__(*args, **kwargs)
            return
        raise PermissionError(
            f"[TEST SANDBOX] 격리되지 않은 QSettings 레지스트리 접근이 차단되었습니다: {args!r}"
        )


def _patch_app_config_and_settings() -> None:
    """실제 .env, .rootdir, QSettings 진입점을 임시 샌드박스 경로로 전환"""
    import src.core.config as cfg_mod
    import src.gui.constants as const_mod

    cfg_mod.get_env_path = _sandbox_env_path
    cfg_mod.get_rootdir_path = _sandbox_rootdir_path
    const_mod.get_app_settings = _sandbox_app_settings

    core_pkg = sys.modules.get("src.core")
    if core_pkg is not None:
        core_pkg.get_env_path = _sandbox_env_path  # type: ignore[attr-defined]
        core_pkg.get_rootdir_path = _sandbox_rootdir_path  # type: ignore[attr-defined]

    for mod_name in ("src.gui.theme", "src.gui.main_window"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "get_app_settings"):
            mod.get_app_settings = _sandbox_app_settings  # type: ignore[attr-defined]

    QtCore.QSettings = _GuardedQSettings


def _install_filesystem_write_guards() -> None:
    """임시 디렉터리 외부의 파일 생성·수정·삭제·이동 및 SQLite 파일 쓰기를 차단"""
    orig_open = builtins.open

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if (
            not isinstance(file, int)
            and isinstance(mode, str)
            and any(flag in mode for flag in _WRITE_MODE_FLAGS)
        ):
            _assert_allowed_write_path(file, op=f"open(mode={mode!r})")
        return orig_open(file, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_open

    def wrap_first_arg(
        func: Callable[_P, _R], *, op: str
    ) -> Callable[_P, _R]:
        @wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            target = args[0] if args else kwargs.get("path")
            if isinstance(target, (str, bytes, os.PathLike)):
                _assert_allowed_write_path(target, op=op)
            return func(*args, **kwargs)

        return wrapper

    def wrap_two_paths(
        func: Callable[_P, _R], *, op: str, check_src: bool
    ) -> Callable[_P, _R]:
        @wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            src = args[0] if len(args) >= 1 else kwargs.get("src")
            dst = args[1] if len(args) >= 2 else kwargs.get("dst")
            if check_src and isinstance(src, (str, bytes, os.PathLike)):
                _assert_allowed_write_path(src, op=f"{op}(src)")
            if isinstance(dst, (str, bytes, os.PathLike)):
                _assert_allowed_write_path(dst, op=f"{op}(dst)")
            return func(*args, **kwargs)

        return wrapper

    for name in ("mkdir", "makedirs", "remove", "unlink", "rmdir"):
        orig_fn = getattr(os, name)
        setattr(os, name, wrap_first_arg(orig_fn, op=f"os.{name}"))

    for name in ("rename", "replace"):
        orig_fn = getattr(os, name)
        setattr(os, name, wrap_two_paths(orig_fn, op=f"os.{name}", check_src=True))

    shutil.move = wrap_two_paths(shutil.move, op="shutil.move", check_src=True)
    for name in ("copy", "copy2", "copyfile", "copytree"):
        orig_fn = getattr(shutil, name)
        setattr(
            shutil,
            name,
            wrap_two_paths(orig_fn, op=f"shutil.{name}", check_src=False),
        )
    shutil.rmtree = wrap_first_arg(shutil.rmtree, op="shutil.rmtree")

    _install_pathlib_write_guards()

    orig_sqlite_connect = sqlite3.connect

    @wraps(orig_sqlite_connect)
    def guarded_sqlite_connect(
        database: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        db_str = os.fsdecode(database)
        if db_str != ":memory:" and not db_str.startswith("file::memory:"):
            target = db_str[5:].split("?", 1)[0] if db_str.startswith("file:") else db_str
            _assert_allowed_write_path(target, op="sqlite3.connect")
        return orig_sqlite_connect(database, *args, **kwargs)

    sqlite3.connect = guarded_sqlite_connect


def _install_pathlib_write_guards() -> None:
    """pathlib.Path의 파일·디렉터리 변조 메서드가 샌드박스 외부를 변경하지 못하도록 차단"""
    orig_path_open = pathlib.Path.open

    @wraps(orig_path_open)
    def guarded_path_open(
        self: pathlib.Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> Any:
        if any(flag in mode for flag in _WRITE_MODE_FLAGS):
            _assert_allowed_write_path(self, op=f"Path.open(mode={mode!r})")
        return orig_path_open(self, mode, *args, **kwargs)

    pathlib.Path.open = guarded_path_open

    for method_name in ("mkdir", "unlink", "rmdir", "touch"):
        orig_method = getattr(pathlib.Path, method_name)

        def _make_single_method(
            orig: Callable[..., Any], op_name: str
        ) -> Callable[..., Any]:
            @wraps(orig)
            def _guarded(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
                _assert_allowed_write_path(self, op=op_name)
                return orig(self, *args, **kwargs)

            return _guarded

        setattr(
            pathlib.Path,
            method_name,
            _make_single_method(orig_method, f"Path.{method_name}"),
        )

    for method_name in ("rename", "replace"):
        orig_method = getattr(pathlib.Path, method_name)

        def _make_move_method(
            orig: Callable[..., Any], op_name: str
        ) -> Callable[..., Any]:
            @wraps(orig)
            def _guarded(
                self: pathlib.Path, target: str | os.PathLike[str]
            ) -> Any:
                _assert_allowed_write_path(self, op=f"{op_name}(src)")
                _assert_allowed_write_path(target, op=f"{op_name}(dst)")
                return orig(self, target)

            return _guarded

        setattr(
            pathlib.Path,
            method_name,
            _make_move_method(orig_method, f"Path.{method_name}"),
        )


def _install_windows_registry_guards() -> None:
    """Windows 환경에서 winreg를 통한 직접 레지스트리 쓰기 호출을 차단"""
    if sys.platform == "win32":
        import winreg

        for fn_name in (
            "CreateKey",
            "CreateKeyEx",
            "SetValue",
            "SetValueEx",
            "DeleteKey",
            "DeleteKeyEx",
            "DeleteValue",
        ):
            if not hasattr(winreg, fn_name):
                continue

            def _make_blocked(op_name: str) -> Callable[..., Any]:
                def _blocked(*_args: Any, **_kwargs: Any) -> Any:
                    raise PermissionError(
                        f"[TEST SANDBOX] 테스트 중 Windows 레지스트리 쓰기가 차단되었습니다: winreg.{op_name}"
                    )

                return _blocked

            setattr(winreg, fn_name, _make_blocked(fn_name))


def install_test_sandbox() -> str:
    """프로세스 전역 테스트 격리 샌드박스와 외부 쓰기 차단 가드를 1회 설치"""
    global _installed, _sandbox_root
    if _installed and _sandbox_root is not None:
        return _sandbox_root

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _sandbox_root = tempfile.mkdtemp(prefix="video_scripts_test_sandbox_")
    atexit.register(shutil.rmtree, _sandbox_root, ignore_errors=True)

    _patch_app_config_and_settings()
    _install_filesystem_write_guards()
    _install_windows_registry_guards()
    _installed = True
    return _sandbox_root
