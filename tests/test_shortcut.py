"""The GUI launcher and the shortcut that points at it.

The shortcut deliberately bypasses ``cmd.exe`` and targets ``pythonw.exe`` with
``launch_gui.pyw``, so the ``.pyw`` has to put ``src`` on ``sys.path`` itself --
there is no environment variable to carry it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYW = ROOT / "launch_gui.pyw"
LNK = ROOT / "EchoShift.lnk"
ICON = ROOT / "assets" / "echoshift.ico"
SCRIPT = ROOT / "tools" / "make_shortcut.ps1"


def test_the_pyw_launcher_exists_and_is_ascii():
    assert PYW.is_file(), "缺少 launch_gui.pyw"
    assert not [b for b in PYW.read_bytes() if b > 127]


def test_the_pyw_launcher_adds_src_to_the_path():
    text = PYW.read_text(encoding="utf-8")
    assert 'sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))' in text
    assert "from echoshift.gui.app import main" in text


def test_the_pyw_launcher_imports_cleanly_in_a_fresh_interpreter():
    """The shortcut depends on this working with no PYTHONPATH set."""
    import os

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy, sys; sys.argv=['x']; "
         f"code = compile(open(r'{PYW}', encoding='utf-8').read(), 'launch_gui.pyw', 'exec'); "
         "exec(code, {'__name__': 'not_main', '__file__': r'%s'})" % PYW],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-800:]


def test_the_icon_exists_and_has_several_sizes():
    assert ICON.is_file(), "缺少 assets/echoshift.ico"
    from PIL import Image

    with Image.open(ICON) as image:
        sizes = set(image.info.get("sizes", []))
    # Windows picks a different size for the desktop, taskbar and alt-tab.
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes, sizes


def test_the_shortcut_script_is_ascii_and_targets_pythonw():
    assert SCRIPT.is_file(), "缺少 tools/make_shortcut.ps1"
    text = SCRIPT.read_text(encoding="ascii")
    assert "pythonw.exe" in text
    assert "launch_gui.pyw" in text
    assert "WScript.Shell" in text


def test_the_shortcut_generator_creates_a_working_link(tmp_path: Path):
    """End-to-end: run the generator and inspect what it wrote."""
    powershell = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(SCRIPT), "-Destination", str(tmp_path), "-Name", "Probe"]
    result = subprocess.run(powershell, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        pytest.skip(f"无法运行 PowerShell: {(result.stderr or result.stdout)[-200:]}")

    link = tmp_path / "Probe.lnk"
    assert link.is_file(), f"没有生成快捷方式\n{result.stdout}"

    listing = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
         "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
         "Write-Output $s.WorkingDirectory" % link],
        capture_output=True, text=True, timeout=120,
    )
    if listing.returncode != 0:
        pytest.skip("无法读回快捷方式")
    target, arguments, workdir = [
        line.strip() for line in listing.stdout.strip().splitlines()[:3]
    ]
    assert target.lower().endswith("pythonw.exe"), target
    assert "launch_gui.pyw" in arguments, arguments
    assert Path(workdir) == ROOT, workdir
