"""Render the GUI in a few representative states and save PNGs for the docs.

    python tools/screenshot_gui.py [output_dir]

Produces ``idle.png`` (fresh queue), ``busy.png`` (a conversion in flight) and
``done.png`` (finished, with the log expanded).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE_TMP = ROOT / ".tmp"
WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(WORKSPACE_TMP)

# Keep the suite hermetic: without this, AppConfig.load() reads whatever the
# person running the tests last saved, and results change from machine to
# machine (a saved "skip existing files" policy silently skipped a conversion).
_ISOLATED_APPDATA = WORKSPACE_TMP / "appdata"
_ISOLATED_APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(_ISOLATED_APPDATA)
os.environ["XDG_CONFIG_HOME"] = str(_ISOLATED_APPDATA)

from echoshift.console import enable_utf8_output  # noqa: E402
from echoshift.core.pipeline import JobState  # noqa: E402
from echoshift.gui.app import EchoShiftApp, _enable_dpi_awareness  # noqa: E402

enable_utf8_output()
# Must happen before the first Tk window exists.  Without it Windows treats the
# process as DPI-unaware and bitmap-stretches the window, so winfo_rootx/width
# stop describing real screen pixels and the grab lands on whatever else is on
# screen -- which is exactly how a stale `docs/*.png` gets overwritten by a
# screenshot of somebody's editor.
_enable_dpi_awareness()

SAMPLES = ROOT / "samples"
FIXTURES = [
    "standard.flac",
    "standard_v1text.mflac",
    "standard_qtag.mflac",
    "standard.qmcflac",
    "hires.flac",
    "surround.flac",
]


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        root.update()
        time.sleep(0.02)


def grab(root: tk.Tk, target: Path) -> bool:
    try:
        from PIL import ImageGrab
    except ImportError:
        print("需要 Pillow 才能截图：python -m pip install pillow")
        return False
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    # Let the window actually come to the front before reading screen pixels;
    # grabbing too early captures whatever was on top a moment ago.
    pump(root, 1.0)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    target.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    # A capture that missed the window would silently poison the docs, so check
    # the geometry rather than trusting the grab.
    if image.width < w - 2 or image.height < h - 2:
        print(f"  截图尺寸异常（{image.width}x{image.height}，期望 {w}x{h}），已跳过")
        return False
    image.save(target)
    print(f"  {target}  ({w}x{h})")
    return True


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else WORKSPACE_TMP / "shots"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    root = tk.Tk()
    app = EchoShiftApp(root)
    pump(root, 0.4)

    app.output_mode_var.set("custom")
    app.output_dir_var.set(r"E:\MP3")
    app._refresh_output_state()
    app.template_var.set("{artist} - {title}.mp3")
    app.workers_var.set(2)

    available = [SAMPLES / name for name in FIXTURES if (SAMPLES / name).is_file()]
    app.add_paths(available)
    pump(root, 2.5)

    print("idle:")
    grab(root, target / "idle.png")

    # A representative moment mid-batch: one done, one running, one failed,
    # one skipped, the rest still queued.
    demo = [
        (JobState.DONE, 1.0, "2.1s · 校验通过（5 项）"),
        (JobState.ENCODING, 0.62, ""),
        (JobState.DECRYPTING, 0.24, ""),
        (JobState.FAILED, 0.0, "解密结果不是可识别的音频数据（ekey 很可能不正确）"),
        (JobState.SKIPPED, 1.0, "已存在 standard (2).mp3"),
        (JobState.PENDING, 0.0, ""),
    ]
    for index, (state, progress, message) in enumerate(demo):
        if index < len(app.items):
            app.items[index].state = state
            app.items[index].progress = progress
            app.items[index].message = message
            app.items[index].plan_desc = "CBR 固定码率 · 192 kbps"
    app._refresh_rows()
    app._set_progress(0.48)
    app._set_status("进行中 2/6")
    app._log("参数：CBR 固定码率 · 192 kbps · 44.1 kHz")
    app._log("[3] 读取 standard_qtag.mflac")
    pump(root, 0.6)

    print("busy:")
    grab(root, target / "busy.png")

    # ...and the finished state with the log open.
    for index, item in enumerate(app.items):
        item.state = JobState.DONE
        item.progress = 1.0
        item.message = f"{1.2 + index * 0.3:.1f}s · 校验通过（7 项）"
    app._refresh_rows()
    app._set_progress(1.0)
    app._set_status("共 6 个文件 · 完成 6")
    app._toggle_log(True)
    for line in (
        "[1] 源：0:02 · FLAC · 44.1 kHz · 立体声 · 16 bit",
        "[1] ffmpeg -q/-b 参数：CBR 固定码率 · 192 kbps · 立体声",
        "[1] 校验通过（7 项）",
        "[1] 输出：E:\\MP3\\标准测试 - 曲目.mp3",
        "共 6 个文件 · 完成 6",
        "全部成功。",
    ):
        app._log(line)
    pump(root, 0.8)

    print("done:")
    grab(root, target / "done.png")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
