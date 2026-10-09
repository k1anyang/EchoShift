"""Launch the EchoShift GUI without a console window.

Windows associates ``.pyw`` with ``pythonw.exe``, which has no console, so a
shortcut pointing at this file starts the app with no ``cmd.exe`` round-trip and
no black window flashing.  It also puts ``src`` on the import path itself, which
is why the shortcut needs no environment variables.

    pythonw launch_gui.pyw
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from echoshift.gui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
