"""Shared test fixtures.

The suites generate real audio and pipe it through the bundled ffmpeg, so they
need a scratch directory.  ``tempfile`` is pointed at a workspace-local folder
because some sandboxes block the system temp directory beyond one level.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for entry in (str(SRC), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

WORKSPACE_TMP = ROOT / ".tmp"
WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(WORKSPACE_TMP)
os.environ.setdefault("ECHOSHIFT_TEMP", str(WORKSPACE_TMP / "work"))

# Keep the suite hermetic: without this, AppConfig.load() reads whatever the
# person running the tests last saved, and results change from machine to
# machine (a saved "skip existing files" policy silently skipped a conversion).
_ISOLATED_APPDATA = WORKSPACE_TMP / "appdata"
_ISOLATED_APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(_ISOLATED_APPDATA)
os.environ["XDG_CONFIG_HOME"] = str(_ISOLATED_APPDATA)

from echoshift.core.ffmpeg import find_toolchain  # noqa: E402
from echoshift.errors import ToolNotFoundError  # noqa: E402
from echoshift.qmc.keystore import KeyStore  # noqa: E402

SAMPLES = ROOT / "samples"

#: Printed when the toolchain cannot be resolved.  A fresh clone has no
#: ``vendor/ffmpeg`` (it is fetched, not committed), and without this the whole
#: suite dies with a traceback instead of saying what to run.
_NO_FFMPEG_HINT = (
    "找不到 ffmpeg / ffprobe，依赖它的测试将被跳过。\n"
    "克隆的仓库不含二进制，请先执行：\n"
    "    powershell -ExecutionPolicy Bypass -File tools\\vendor_ffmpeg.ps1\n"
    "或安装 ffmpeg 并确保它在 PATH 上。"
)

#: Sample definitions shared by the end-to-end tests.
STANDARD = "standard.flac"
HIRES = "hires.flac"
SURROUND = "surround.flac"
UNTAGGED = "untagged.flac"
QMC1 = "standard.qmcflac"
MFLAC_MAP = "standard_map.mflac"
MFLAC_RC4 = "standard_rc4.mflac"
#: QTag footer -- the layout QQ Music <= 19.51 writes, parsed by different code
#: than the V1 footer used by the two fixtures above.
MFLAC_QTAG = "standard_qtag.mflac"
#: V1 footer holding NUL-terminated ASCII base64 text of an EncV2 ekey.  This
#: mirrors a real QQ Music download; the two V1 fixtures above instead store
#: raw key bytes, which is what a naive reading of the format assumes.
MFLAC_V1TEXT = "standard_v1text.mflac"

#: Every encrypted fixture, for the parametrised tests.
ENCRYPTED_FIXTURES = (QMC1, MFLAC_MAP, MFLAC_RC4, MFLAC_QTAG, MFLAC_V1TEXT)


@pytest.fixture(scope="session")
def toolchain():
    """The real ffmpeg pair, or a skip that explains how to get one.

    ``vendor/ffmpeg`` is fetched by ``tools/vendor_ffmpeg.ps1`` rather than
    committed, so a clean clone legitimately has no toolchain.  Skipping (rather
    than erroring) keeps the pure-logic tests — scheduling, naming, ciphers,
    config — runnable without it.
    """
    try:
        found = find_toolchain()
    except ToolNotFoundError:
        pytest.skip(_NO_FFMPEG_HINT)
    if not found.has_libmp3lame:
        pytest.skip(f"找到的 ffmpeg 不含 libmp3lame：{found.describe()}")
    return found


@pytest.fixture(scope="session")
def samples() -> Path:
    """Ensure the fixture set exists, then hand back its directory."""
    if not (SAMPLES / MFLAC_V1TEXT).is_file():
        from tools.make_samples import build_all

        build_all(SAMPLES)
    return SAMPLES


def _fresh_dir(name: str) -> Path:
    """Create an empty directory using plain ``mkdir``.

    ``tmp_path``/``TemporaryDirectory`` rely on ``tempfile.mkdtemp``, which some
    sandboxes make unwritable; a deterministic path avoids that entirely.
    """
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", name)[:120] or "case"
    path = WORKSPACE_TMP / "scratch" / safe
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_path(request) -> Path:
    """Stand-in for pytest's ``tmp_path`` that works under a locked-down FS."""
    return _fresh_dir(request.node.name)


@pytest.fixture
def work_dir() -> Path:
    path = WORKSPACE_TMP / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def out_dir(request) -> Path:
    """A clean per-test output directory."""
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)
    path = WORKSPACE_TMP / "out" / safe
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def keystore() -> KeyStore:
    return KeyStore()


@pytest.fixture(scope="module")
def tk_root():
    """A withdrawn Tk root shared by the widget tests.

    Module scope so the whole GUI test module pays for Tk startup once.  Tests
    that need a real window build their own Toplevel on top of it.
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless CI
        pytest.skip(f"无法打开显示：{exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass
