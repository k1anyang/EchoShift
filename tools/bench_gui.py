"""Measure where the GUI spends its time.

Two numbers matter for the "resizing feels laggy" complaint:

* **cold start** -- process launch to first painted window, which is what a
  ``.cmd`` shortcut costs before anything appears;
* **redraw cost** -- how long one queue repaint takes, and how many of them a
  resize drag triggers.

    python tools/bench_gui.py
"""

from __future__ import annotations

import statistics
import subprocess
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

from echoshift.console import enable_utf8_output  # noqa: E402
from echoshift.core.pipeline import JobState  # noqa: E402
from echoshift.gui.app import EchoShiftApp  # noqa: E402

enable_utf8_output()

ROWS = 40


def measure_cold_start(runs: int = 3) -> list[float]:
    """Time ``python -c "import echoshift.gui.app"`` as a proxy for launch."""
    env = {"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    import os

    full_env = {**os.environ, **env}
    timings: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import echoshift.gui.app"],
            env=full_env, capture_output=True, check=True,
        )
        timings.append(time.perf_counter() - start)
    return timings


def main() -> int:
    print("=== 冷启动（导入到可用）===")
    imports = measure_cold_start()
    for value in imports:
        print(f"  {value * 1000:7.0f} ms")
    print(f"  中位数 {statistics.median(imports) * 1000:.0f} ms")
    print("  （tkinter 窗口创建另需约 300-600 ms）")

    root = tk.Tk()
    app = EchoShiftApp(root)
    for _ in range(30):
        root.update()

    # Fill the queue with enough rows that virtualisation matters.
    from echoshift.gui.queue_view import QueueRow

    rows = [
        QueueRow(
            filename=f"王力宏 _ 欧阳靖 _ 李岩 - 测试曲目 {i:03d}.mflac",
            source="MFLAC · QMC2 v1 · 内嵌 ekey · FLAC · 44.1 kHz · 立体声 · 16 bit",
            state=[JobState.DONE, JobState.ENCODING, JobState.PENDING, JobState.FAILED][i % 4],
            progress=(i % 10) / 10,
            message="1.2s · 校验通过（7 项）" if i % 4 == 0 else "",
        )
        for i in range(ROWS)
    ]
    view = app.queue_view
    view.set_rows(rows)
    root.update()

    print(f"\n=== 队列重绘（{ROWS} 行）===")
    widths = list(range(700, 1180, 20))
    timings: list[float] = []
    for width in widths:
        view._width = width
        view._layout = view._compute_layout(width)
        start = time.perf_counter()
        view._redraw()
        timings.append(time.perf_counter() - start)
        root.update()  # drain events outside the measurement
    print(f"  单次重绘 中位数 {statistics.median(timings) * 1000:6.1f} ms"
          f"  最大 {max(timings) * 1000:6.1f} ms")
    print(f"  一次拖拽按 60 次 Configure 估算：{statistics.median(timings) * 60 * 1000:.0f} ms"
          "  ← 超过 16 ms/帧 就会明显卡")

    first, last = view._visible_range()
    print("\n=== 可见行 ===")
    print(f"  画布 {view.canvas.winfo_height()}px -> 绘制 {last - first} / {view.row_count} 行")

    print("\n=== 字体测量（按字符缓存后）===")
    import tkinter.font as tkfont

    font = tkfont.Font(font=app.fonts.ui)
    text = "王力宏 _ 欧阳靖 _ 李岩 - 测试曲目 001.mflac"
    view._text_width(text, font, "ui")  # warm the character table
    start = time.perf_counter()
    for _ in range(2000):
        view._text_width(text, font, "ui")
    print(f"  2000 次 _text_width: {(time.perf_counter() - start) * 1000:.1f} ms")

    start = time.perf_counter()
    for _ in range(2000):
        view._ellipsize(
            "MFLAC · QMC2 v1 · 内嵌 ekey · FLAC · 44.1 kHz · 立体声 · 16 bit",
            200, font, "ui",
        )
    print(f"  2000 次 _ellipsize:  {(time.perf_counter() - start) * 1000:.1f} ms")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
