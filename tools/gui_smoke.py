"""Drive the Tkinter GUI without a human: build it, queue files, convert.

Runs the real widget tree and the real event pump (via ``root.update()``
instead of ``mainloop``), so widget wiring, config round-tripping and the
worker/queue handshake are all exercised.  It also writes screenshots of the
idle and finished states to ``.tmp/shots`` for visual review.

    python tools/gui_smoke.py
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
from echoshift.core.settings import BitrateMode  # noqa: E402
from echoshift.gui.app import EchoShiftApp  # noqa: E402

enable_utf8_output()

SAMPLES = ROOT / "samples"
SHOTS = WORKSPACE_TMP / "shots"
FIXTURES = [
    "standard.flac",
    "standard_v1text.mflac",
    "standard_qtag.mflac",
    "standard.qmcflac",
    "hires.flac",
    "surround.flac",
]


def pump(root: tk.Tk, seconds: float) -> None:
    """Keep the Tk event loop turning for ``seconds``."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        root.update()
        time.sleep(0.02)


def shoot(root: tk.Tk, name: str) -> None:
    """Grab the window client area, if the platform allows it."""
    try:
        from PIL import ImageGrab

        SHOTS.mkdir(parents=True, exist_ok=True)
        root.update_idletasks()
        root.lift()
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(SHOTS / name)
    except Exception as exc:  # noqa: BLE001 - screenshots are a nicety
        print(f"    （截图跳过：{type(exc).__name__}: {exc}）")


def main() -> int:
    out_dir = WORKSPACE_TMP / "gui_out"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    app = EchoShiftApp(root)
    pump(root, 0.4)

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"{'OK  ' if ok else 'FAIL'} {name}{' — ' + detail if detail else ''}")

    check("窗口标题已不再只写 FLAC/MFLAC", "FLAC" not in app.root.title(), app.root.title())
    check("ffmpeg 就绪", app.toolchain is not None,
          app.toolchain.describe() if app.toolchain else str(app.toolchain_error))

    # --- configuration widgets -------------------------------------------
    app.mode_var.set("CBR 固定码率")
    app._refresh_bitrate_choices()
    bitrate_values = list(app.bitrate_box.cget("values"))
    check("CBR 码率选项来自采样率规则",
          "320 kbps" in bitrate_values and "8 kbps" not in bitrate_values)

    app.rate_var.set("8000 Hz")
    app._refresh_bitrate_choices()
    low_rate_values = list(app.bitrate_box.cget("values"))
    check("切到 8kHz 后码率上限降到 160",
          "320 kbps" not in low_rate_values and "160 kbps" in low_rate_values)

    app.preset_var.set("V0 高保真")
    app._on_preset()
    settings = app.collect_settings()
    check("预设写入控件",
          settings.mode is BitrateMode.VBR and settings.vbr_quality == 0,
          settings.describe())
    check("参数摘要标签同步", "-q:a 0" in app.plan_label.cget("text"),
          app.plan_label.cget("text"))

    # --- queue -------------------------------------------------------------
    available = [SAMPLES / name for name in FIXTURES if (SAMPLES / name).is_file()]
    app.add_paths(available)
    pump(root, 2.5)
    check("队列填入所有夹具", len(app.items) == len(available), f"{len(app.items)} 行")
    check("队列视图同步", app.queue_view.row_count == len(app.items))
    check("后台探测填了源信息", all(item.source_desc for item in app.items),
          app.items[0].source_desc)
    check("队列行带上了时长",
          all(item.duration for item in app.items),
          ", ".join(item.duration for item in app.items))
    check("加密容器被识别",
          any("MFLAC" in item.source_desc for item in app.items),
          next((i.source_desc for i in app.items if "MFLAC" in i.source_desc), ""))

    shoot(root, "idle.png")

    # --- run ---------------------------------------------------------------
    app.output_mode_var.set("custom")
    app.output_dir_var.set(str(out_dir))
    app._refresh_output_state()
    app.template_var.set("{filename}.mp3")
    # Pin the policy: the test creates colliding output names on purpose, and
    # must not inherit whatever was last saved.
    app.overwrite_var.set("自动改名（不覆盖）")
    app.mode_var.set("CBR 固定码率")
    app.rate_var.set("保持原样")
    app._refresh_bitrate_choices()
    app.bitrate_var.set("192 kbps")
    app.channel_var.set("立体声")
    app.workers_var.set(2)

    app._start()
    deadline = time.perf_counter() + 120
    while app._is_running() and time.perf_counter() < deadline:
        pump(root, 0.2)
    pump(root, 1.5)

    check("转换已结束", not app._is_running())
    states = [item.state.value for item in app.items]
    check("全部完成", all(s == "done" for s in states), str(states))
    produced = sorted(p.name for p in out_dir.glob("*.mp3"))
    check("产出数量正确", len(produced) == len(app.items), str(produced))
    check("进度条跑到 100%", abs(app.progress.fraction - 1.0) < 1e-6,
          str(app.progress.fraction))
    check("状态栏有汇总", "完成" in app.status_var.get(), app.status_var.get())
    check("完成行有说明文字", all(item.message for item in app.items),
          app.items[0].message)
    check("成功时日志保持收起", not app._log_expanded)
    check("日志记录了参数", "参数：" in app.log_widget.get("1.0", "end"))

    shoot(root, "finished.png")

    # --- config round-trip -------------------------------------------------
    app.template_var.set("{artist}/{title}.mp3")
    app.retries_var.set(3)
    cfg = app.collect_config()
    check("collect_config 捕获控件值",
          cfg.template == "{artist}/{title}.mp3" and cfg.retries == 3,
          f"{cfg.template} / {cfg.retries}")

    root.destroy()

    failures = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failures)}/{len(checks)} 通过")
    if failures:
        print("失败：" + "、".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
