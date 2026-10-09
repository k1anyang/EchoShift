"""Where the application keeps its bundled resources.

A leaf module on purpose: both :mod:`echoshift.core` and
:mod:`echoshift.qmc` need it, and importing anything under ``core`` from
``qmc`` would close an import cycle through ``core.pipeline``.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["app_root", "bundle_roots", "resource_dir"]


def _source_root() -> Path:
    # .../src/echoshift/paths.py -> project root
    return Path(__file__).resolve().parents[2]


def bundle_roots() -> list[Path]:
    """Directories that may contain bundled resources, most specific first.

    When frozen by PyInstaller the payload lives in ``sys._MEIPASS``; when
    running from source it is the project root.  ``sys.executable``'s directory
    covers the "unpacked next to the exe" layout.
    """
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(sys.executable).resolve().parent)
    roots.append(_source_root())
    return roots


def app_root() -> Path:
    """The directory the application considers its home."""
    return bundle_roots()[-1]


def resource_dir(name: str) -> list[Path]:
    """Candidate locations of the bundled ``name`` resource directory."""
    return [root / name for root in bundle_roots()]
