"""PyInstaller entry point for the windowed GUI build.

A dedicated launcher (rather than pointing PyInstaller at ``gui/app.py``)
because app.py uses relative imports and cannot run as ``__main__``.
``freeze_support()`` is essential: the multi-process decryption path relies on
multiprocessing, which needs it once the app is frozen.
"""

from __future__ import annotations

import multiprocessing
import sys

from echoshift.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
