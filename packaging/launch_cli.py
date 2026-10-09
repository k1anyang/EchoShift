"""PyInstaller entry point for the console build."""

from __future__ import annotations

import multiprocessing
import sys

from echoshift.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
