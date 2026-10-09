"""``python -m echoshift`` runs the command line interface."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
