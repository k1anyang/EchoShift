"""Native Windows drag-and-drop for Tk widgets.

``tkinterdnd2`` is not a standard-library package, so this module wires up the
Win32 ``WM_DROPFILES`` mechanism directly with ``ctypes``: mark the toplevel as
a drop target, then subclass its window procedure to intercept drops and chain
everything else to Tk's own handler.

The whole thing is optional.  If any step fails, :func:`enable_file_drop`
returns ``False`` and the caller falls back to the browse buttons.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable, Sequence

__all__ = ["enable_file_drop", "is_supported"]

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
_MAX_PATH = 32768


def is_supported() -> bool:
    return sys.platform == "win32"


if is_supported():  # pragma: no cover - Windows-only, exercised manually
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    _WNDPROC = ctypes.WINFUNCTYPE(
        _LONG_PTR, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    )

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _get_window_long = _user32.GetWindowLongPtrW
        _set_window_long = _user32.SetWindowLongPtrW
    else:
        _get_window_long = _user32.GetWindowLongW
        _set_window_long = _user32.SetWindowLongW

    _get_window_long.restype = _LONG_PTR
    _get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _set_window_long.restype = _LONG_PTR
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, _LONG_PTR]

    _call_window_proc = _user32.CallWindowProcW
    _call_window_proc.restype = _LONG_PTR
    _call_window_proc.argtypes = [
        _LONG_PTR, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    ]

    _drag_accept_files = _shell32.DragAcceptFiles
    _drag_accept_files.argtypes = [wintypes.HWND, wintypes.BOOL]

    _drag_query_file = _shell32.DragQueryFileW
    _drag_query_file.argtypes = [
        wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
    ]
    _drag_query_file.restype = wintypes.UINT

    _drag_finish = _shell32.DragFinish
    _drag_finish.argtypes = [wintypes.HANDLE]

    def _query_paths(drop_handle: int) -> list[str]:
        count = _drag_query_file(drop_handle, 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = _drag_query_file(drop_handle, index, None, 0)
            if not length:
                continue
            buffer = ctypes.create_unicode_buffer(min(length + 1, _MAX_PATH))
            _drag_query_file(drop_handle, index, buffer, len(buffer))
            if buffer.value:
                paths.append(buffer.value)
        return paths


class _DropTarget:
    """Keeps the subclass procedure alive for the lifetime of the window."""

    def __init__(self, hwnd: int, on_drop: Callable[[Sequence[str]], None]) -> None:
        self.hwnd = hwnd
        self.on_drop = on_drop
        self._previous = _get_window_long(hwnd, GWLP_WNDPROC)
        if not self._previous:
            raise OSError("GetWindowLongPtrW failed")
        self._proc = _WNDPROC(self._handle)
        _set_window_long(hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p).value)
        _drag_accept_files(hwnd, True)

    def _handle(self, hwnd, msg, wparam, lparam):  # noqa: ANN001 - Win32 signature
        if msg == WM_DROPFILES:
            try:
                paths = _query_paths(wparam)
            except Exception:
                paths = []
            finally:
                try:
                    _drag_finish(wparam)
                except Exception:
                    pass
            if paths:
                try:
                    self.on_drop(paths)
                except Exception:
                    pass
            # Returning non-zero tells Windows the drop was consumed.
            return 1
        return _call_window_proc(self._previous, hwnd, msg, wparam, lparam)


def enable_file_drop(widget, on_drop: Callable[[Sequence[str]], None]) -> bool:
    """Accept file/folder drops on ``widget``; returns whether it worked.

    The returned object is attached to the widget so it is not garbage
    collected while the window is alive.
    """
    if not is_supported():
        return False
    try:
        widget.update_idletasks()
        hwnd = ctypes.c_void_p(widget.winfo_id()).value
        if not hwnd:
            return False
        widget._echoshift_drop_target = _DropTarget(hwnd, on_drop)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False
