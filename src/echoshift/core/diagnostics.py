"""Small, privacy-conscious rotating diagnostic log for the desktop app."""

from __future__ import annotations

import logging
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import config_path

__all__ = ["diagnostic_log_path", "sanitize_diagnostic", "write_diagnostic"]

_EKEY_RE = re.compile(r"(?i)(\bekey\s*[=:：]\s*)([^\s,;，；]+)")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![a-z0-9_])[a-z]:[\\/][^\r\n]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\\\r\n]+\\[^\r\n]*")
_LOCK = threading.Lock()
_LOGGER: logging.Logger | None = None


def diagnostic_log_path() -> Path:
    return config_path().parent / "logs" / "echoshift.log"


def sanitize_diagnostic(message: str) -> str:
    """Remove secrets and user directory structures from persisted text."""
    text = _EKEY_RE.sub(r"\1<REDACTED>", str(message))
    text = _UNC_PATH_RE.sub("<PATH>", text)
    return _WINDOWS_PATH_RE.sub("<PATH>", text)


def _logger() -> logging.Logger:
    global _LOGGER
    with _LOCK:
        if _LOGGER is not None:
            return _LOGGER
        path = diagnostic_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("echoshift.diagnostics")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        _LOGGER = logger
        return logger


def write_diagnostic(message: str) -> None:
    try:
        _logger().info("%s", sanitize_diagnostic(message))
    except OSError:
        # Diagnostics must never become a new reason for conversion to fail.
        pass
