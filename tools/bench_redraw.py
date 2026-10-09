"""Break a queue repaint into its two halves: text layout vs canvas drawing."""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = ROOT / ".tmp"
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)

from echoshift.console import enable_utf8_output  # noqa: E402
from echoshift.core.pipeline import JobState  # noqa: E402
from echoshift.gui.app import EchoShiftApp  # noqa: E402
from echoshift.gui.queue_view import QueueRow  # noqa: E402

enable_utf8_output()


def timed(fn, n: int = 20) -> tuple[float, float]:
    fn()
    values = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return statistics.median(values), max(values)


def main() -> int:
    root = tk.Tk()
    app = EchoShiftApp(root)
    view = app.queue_view
    for _ in range(30):
        root.update()

    view.set_rows([
        QueueRow(
            filename=f"王力宏 _ 欧阳靖 _ 李岩 - 测试曲目 {i:03d}.mflac",
            source="MFLAC · QMC2 v1 · 内嵌 ekey · FLAC · 44.1 kHz · 立体声 · 16 bit",
            state=[JobState.DONE, JobState.ENCODING, JobState.PENDING, JobState.FAILED][i % 4],
            progress=(i % 10) / 10,
            message="1.2s · 校验通过（7 项）" if i % 4 == 0 else "",
        )
        for i in range(200)
    ])
    root.update()

    first, last = view._visible_range()
    print(f"画布高度 {view.canvas.winfo_height()}px  可见行 {last - first} / 共 {view.row_count}")

    median, worst = timed(view._redraw)
    print(f"\n完整重绘          中位 {median:6.1f} ms   最坏 {worst:6.1f} ms")

    # Text layout only: do everything _draw_row does except touch the canvas.
    def layout_only() -> None:
        for index in range(first, last):
            row = view._rows[index]
            _x, width = view._column("file")
            view._ellipsize(row.filename, width - 8, view._font, "ui")
            _x, width = view._column("source")
            view._ellipsize(row.source, width - 8, view._font_small, "sm")
            _x, width = view._column("message")
            view._ellipsize(row.message, width - 8, view._font_small, "sm")
            view._text_width(row.state.label, view._font_small, "sm")

    median, worst = timed(layout_only)
    print(f"仅文字排版        中位 {median:6.1f} ms   最坏 {worst:6.1f} ms")

    def draw_only() -> None:
        view.canvas.delete("all")
        for index in range(first, last):
            view._draw_row(index)

    median, worst = timed(draw_only)
    print(f"仅画布绘制        中位 {median:6.1f} ms   最坏 {worst:6.1f} ms")

    # How many canvas items does one repaint create?
    view.canvas.delete("all")
    before = len(view.canvas.find_all())
    for index in range(first, last):
        view._draw_row(index)
    after = len(view.canvas.find_all())
    rows = last - first
    print(f"\n每次重绘创建 {after - before} 个图元 / {rows} 行 = {(after - before) / max(rows, 1):.1f} 个/行")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
