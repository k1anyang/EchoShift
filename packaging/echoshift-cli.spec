# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the EchoShift command line build.

Build with::

    pyinstaller packaging/echoshift-cli.spec --noconfirm

Set ``ECHOSHIFT_ONEFILE=1`` for a single exe.
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ONEFILE = (os.environ.get("ECHOSHIFT_ONEFILE") or os.environ.get("AUDIOCONV_ONEFILE")) == "1"

datas = [
    (str(ROOT / "vendor" / "ffmpeg"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "keys"), "vendor/keys"),
]

excludes = [
    "PIL", "numpy", "scipy", "pandas", "matplotlib",
    "pytest", "_pytest", "IPython", "setuptools", "pip",
    "tkinter",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch_cli.py")],
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
        name="echoshift",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
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
        name="echoshift",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
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
        name="echoshift",
    )
