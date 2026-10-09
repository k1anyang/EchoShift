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
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ONEFILE = (os.environ.get("ECHOSHIFT_ONEFILE") or os.environ.get("AUDIOCONV_ONEFILE")) == "1"

datas = [
    (str(ROOT / "vendor" / "ffmpeg"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "keys"), "vendor/keys"),
]

# Shared libraries PyInstaller cannot resolve for a conda interpreter.
#
# It bundles the Tcl/Tk *data* directories but not the Tcl/Tk DLLs themselves,
# and it cannot find the CPython extension dependencies either -- for each of
# these it only logs "Library not found", and the resulting exe then dies at
# startup with "DLL load failed while importing <module>".  On conda they all
# live in the interpreter's Library\bin; for a python.org install the same
# libraries sit in DLLs\ or next to the interpreter, so search all of them and
# match case-insensitively (conda ships both zlib.dll and zlib1.dll).
_WANTED = [
    "tcl86t.dll",       # _tkinter
    "tk86t.dll",        # _tkinter
    "ffi.dll",          # _ctypes
    "liblzma.dll",      # _lzma
    "libbz2.dll",       # _bz2
    "libexpat.dll",     # pyexpat
    "libmpdec-4.dll",   # _decimal
]
_here = Path(sys.executable).resolve().parent
_search_dirs = [
    _here / "DLLs",              # python.org
    _here / "Library" / "bin",   # conda / Anaconda
    _here,
]
binaries = []
for _name in _WANTED:
    for _directory in _search_dirs:
        if not _directory.is_dir():
            continue
        _match = next(
            (p for p in _directory.iterdir() if p.name.lower() == _name.lower()),
            None,
        )
        if _match is not None:
            binaries.append((str(_match), "."))
            break
    else:
        print(f"EchoShift.spec: warning: {_name} not found; the build will not start")

# Nothing outside the standard library is imported at runtime; keep the bundle
# lean by refusing the usual accidental pickups.
excludes = [
    "PIL", "numpy", "scipy", "pandas", "matplotlib",
    "pytest", "_pytest", "IPython", "setuptools", "pip",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
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
