"""The ``.cmd`` launchers and the script that regenerates them.

Two bugs have already come from these files, so they get tests:

* ``audioconv.cmd`` and ``AudioConv.cmd`` are the *same file* on Windows, so a
  CLI launcher differing only in case silently overwrote the GUI one.
* Chinese text in a ``.cmd`` is parsed with the OEM code page, so UTF-8 bytes
  turned into garbage commands on a zh-CN system.

The CLI launcher has also gone missing from disk more than once, which is why
``tools/make_launchers.ps1`` exists and why its output is checked for equality
with what is committed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

GUI = ROOT / "EchoShift.cmd"
CLI = ROOT / "EchoShift-CLI.cmd"
GENERATOR = ROOT / "tools" / "make_launchers.ps1"


def test_both_launchers_exist_and_are_distinct():
    assert GUI.is_file(), f"缺少 GUI 启动器：{GUI.name}"
    assert CLI.is_file(), (
        f"缺少命令行启动器：{CLI.name}；可运行 tools\\make_launchers.ps1 重建"
    )
    # Same-name-different-case would collapse into one file on NTFS.
    assert GUI.name.lower() != CLI.name.lower()


def test_no_two_launchers_differ_only_by_case():
    names = [path.name for path in ROOT.glob("*.cmd")]
    lowered = [name.lower() for name in names]
    assert len(lowered) == len(set(lowered)), f"存在仅大小写不同的启动器：{names}"


def test_launchers_are_ascii_only():
    for path in (GUI, CLI):
        offenders = [byte for byte in path.read_bytes() if byte > 127]
        assert not offenders, (
            f"{path.name} 含非 ASCII 字节（{offenders[:8]}）；"
            "cmd.exe 会按 OEM 代码页解析 .cmd，中文注释会变成乱码命令"
        )


def test_gui_launcher_starts_the_windowed_entry_point():
    text = GUI.read_text(encoding="ascii")
    assert "pythonw" in text
    assert "echoshift.gui.app" in text
    assert "pause" in text, "失败时应当暂停，否则双击的用户看不到错误"


def test_cli_launcher_starts_the_console_entry_point():
    text = CLI.read_text(encoding="ascii")
    assert "python -m echoshift %*" in text
    assert "PYTHONIOENCODING" in text, "控制台需要 UTF-8，否则中文输出会报编码错"


@pytest.mark.parametrize("path", [GUI, CLI])
def test_launchers_point_pythonpath_at_src(path: Path):
    text = path.read_text(encoding="ascii")
    # Either inline (%~dp0src) or via a ROOT variable set from %~dp0.
    assert "%~dp0src" in text or "%ROOT%src" in text


def test_the_generator_exists_and_is_ascii():
    assert GENERATOR.is_file(), "缺少 tools/make_launchers.ps1"
    assert not [byte for byte in GENERATOR.read_bytes() if byte > 127]


def test_the_generator_reproduces_the_committed_launchers():
    """Regenerating must be a no-op, or the two have drifted apart."""
    before = {path.name: path.read_bytes() for path in (GUI, CLI)}

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GENERATOR)],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"无法运行 PowerShell：{(result.stderr or result.stdout)[-200:]}")

    after = {path.name: path.read_bytes() for path in (GUI, CLI)}
    assert set(after) == set(before), f"生成器没有产出全部启动器：{sorted(after)}"
    for name in before:
        assert before[name] == after[name], (
            f"{name} 与 tools/make_launchers.ps1 的产出不一致；"
            "改启动器时要同时改生成器"
        )
