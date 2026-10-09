"""Console encoding helpers.

Windows consoles default to a legacy code page (GBK on Chinese systems), which
cannot encode the check marks and arrows this tool prints.  Both front ends
call :func:`enable_utf8_output` before writing anything.
"""

from __future__ import annotations

import sys

__all__ = ["enable_utf8_output"]


def enable_utf8_output() -> None:
    """Best-effort switch of stdout/stderr to UTF-8.

    Never raises: if the streams are already redirected to something that
    cannot be reconfigured, the original encoding is left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, LookupError):
            pass
