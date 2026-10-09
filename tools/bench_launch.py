"""Compare how the app can be launched, in milliseconds to first painted window.

Reported per launch path so the trade-offs are visible rather than guessed.

    python tools/bench_launch.py
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
ENV = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}

# Import everything, build the window, lay it out and paint once.
BOOT = (
    "import time, tkinter as tk;"
    "t0=time.perf_counter();"
    "from echoshift.gui.app import EchoShiftApp, _enable_dpi_awareness;"
    "t1=time.perf_counter();"
    "_enable_dpi_awareness();"
    "root=tk.Tk();"
    "app=EchoShiftApp(root);"
    "root.update();"
    "t2=time.perf_counter();"
    "print(f'{(t1-t0)*1000:.0f} {(t2-t0)*1000:.0f}');"
    "root.destroy()"
)


def measure(cmd: list[str], runs: int = 3) -> tuple[float, float] | None:
    imports: list[float] = []
    totals: list[float] = []
    for _ in range(runs):
        result = subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=180)
        if result.returncode != 0:
            print(f"  失败：{(result.stderr or result.stdout or '?')[-300:]}")
            return None
        parts = result.stdout.strip().split()
        imports.append(float(parts[0]))
        totals.append(float(parts[1]))
    return statistics.median(imports), statistics.median(totals)


def warm_probe_cache() -> bool:
    """Pre-seed the ffmpeg probe cache, the way a previous run would have."""
    from echoshift.core.config import AppConfig
    from echoshift.core.ffmpeg import find_toolchain

    config = AppConfig()
    config.ffmpeg_cache = find_toolchain().to_cache()
    try:
        config.save()
        return True
    except OSError as exc:
        print(f"  注意：配置目录不可写（{exc}），无法测量缓存命中后的启动")
        return False


def main() -> int:
    python = sys.executable
    pythonw = str(Path(python).with_name("pythonw.exe"))

    print(f"解释器 {python}")
    print(f"源码   {SRC}")
    print()

    warm = warm_probe_cache()
    print(f"ffmpeg 探测缓存：{'已预热' if warm else '不可用（每次都会重新探测）'}")
    print()
    print(f"{'启动路径':<30}{'导入':>10}{'到首次绘制':>14}")

    result = measure([python, "-c", BOOT])
    if result is None:
        return 1
    imp, total = result
    print(f"{'python -m（控制台解释器）':<28}{imp:8.0f}ms{total:12.0f}ms")

    if Path(pythonw).is_file():
        result = measure([pythonw, "-c", BOOT])
        if result is not None:
            imp, total = result
            print(f"{'pythonw（无控制台窗口）':<28}{imp:8.0f}ms{total:12.0f}ms")

    start = time.perf_counter()
    subprocess.run(["cmd", "/c", "echo x"], capture_output=True, env=ENV)
    cmd_overhead = (time.perf_counter() - start) * 1000
    print(f"{'cmd.exe 本身的开销（对照）':<26}{'—':>10}{cmd_overhead:12.0f}ms")

    print()
    print("对照：")
    print("  PyInstaller onedir   发布给用户的形态；启动与 pythonw 相当",
          f"（快约 {cmd_overhead:.0f}ms，无需先起解释器）")
    print("  PyInstaller onefile  每次启动都要把 50 MB 内置 ffmpeg 解压到临时目录，")
    print("                       这类应用通常要 2-4 秒，是本项目最不推荐的形态")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
