# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the EchoShift GUI.

Build with::

    pyinstaller packaging/EchoShift.spec --noconfirm

Set ``ECHOSHIFT_ONEFILE=1`` in the environment (``build.ps1 -OneFile``) to
produce a single self-extracting exe instead of a folder.  The folder layout is
the default because the bundled ffmpeg DLLs total ~50 MB, and a one-file build
re-extracts all of them on every launch.
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ONEFILE = (os.environ.get("ECHOSHIFT_ONEFILE") or os.environ.get("AUDIOCONV_ONEFILE")) == "1"

datas = [
    (str(ROOT / "vendor" / "ffmpeg"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "keys"), "vendor/keys"),
]

# Nothing outside the standard library is imported at runtime; keep the bundle
# lean by refusing the usual accidental pickups.
excludes = [
    "PIL", "numpy", "scipy", "pandas", "matplotlib",
    "pytest", "_pytest", "IPython", "setuptools", "pip",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="EchoShift",
        icon=str(ROOT / "assets" / "echoshift.ico"),
        version=str(ROOT / "packaging" / "version_info.txt"),
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="EchoShift",
        icon=str(ROOT / "assets" / "echoshift.ico"),
        version=str(ROOT / "packaging" / "version_info.txt"),
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="EchoShift",
    )
