"""Import-order regression tests.

A cycle once existed where importing ``echoshift.qmc.decoder`` first pulled in
``echoshift.core.__init__``, which pulled in ``core.pipeline``, which imported
``qmc.decoder`` while it was still half-built.  Every module must therefore
import cleanly *on its own*, in a fresh interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Child processes get an explicit PYTHONPATH: pytest's ``pythonpath`` setting
#: only affects this interpreter.
SRC = Path(__file__).resolve().parents[1] / "src"
_ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        part for part in (str(SRC), os.environ.get("PYTHONPATH", "")) if part
    ),
}

MODULES = [
    "echoshift",
    "echoshift.errors",
    "echoshift.paths",
    "echoshift.console",
    "echoshift.core",
    "echoshift.core.args",
    "echoshift.core.config",
    "echoshift.core.ffmpeg",
    "echoshift.core.naming",
    "echoshift.core.pipeline",
    "echoshift.core.probe",
    "echoshift.core.settings",
    "echoshift.core.verify",
    "echoshift.qmc",
    "echoshift.qmc.decoder",
    "echoshift.qmc.footer",
    "echoshift.qmc.keystore",
    "echoshift.qmc.parallel",
    "echoshift.qmc.qmc1",
    "echoshift.qmc.qmc2",
    "echoshift.qmc.tc_tea",
    "echoshift.cli",
    "echoshift.gui.app",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_first_in_a_fresh_interpreter(module: str):
    """Each module must be importable as the very first import of the process."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
        env=_ENV,
    )
    assert result.returncode == 0, (
        f"importing {module} first failed:\n{result.stderr}"
    )


def test_qmc_layer_does_not_import_the_core_package():
    """``core`` may depend on ``qmc``; the reverse direction must stay clean."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import echoshift.qmc.decoder; "
            "bad = [m for m in sys.modules if m.startswith('echoshift.core')]; "
            "print(bad)",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=_ENV,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", f"qmc pulled in {result.stdout.strip()}"


@pytest.mark.parametrize("utf8_mode", [True, False])
def test_the_documented_cli_invocation_prints_chinese(utf8_mode: bool):
    """``python -X utf8 -m echoshift`` is the only documented command line.

    A Chinese Windows console defaults to GBK, which cannot encode the tool's
    own output.  ``-X utf8`` is what the README tells people to use; the second
    case pins ``console.py`` as the safety net for anyone who forgets it -- both
    must produce real text, and the safety net must never raise or mangle it.
    """
    cmd = [sys.executable]
    if utf8_mode:
        cmd.append("-X")
        cmd.append("utf8")
    cmd += ["-m", "echoshift", "--list-presets"]

    # Force the legacy codec regardless of what this machine's console uses.
    env = {**_ENV, "PYTHONIOENCODING": "gbk"}
    result = subprocess.run(cmd, capture_output=True, timeout=120, env=env)

    assert result.returncode == 0, result.stderr
    text = result.stdout.decode("utf-8")
    assert "内置预设" in text, text[:200]
    assert "\ufffd" not in text, "输出被替换成了乱码，说明编码没有真正切换"
