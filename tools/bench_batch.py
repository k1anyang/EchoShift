"""Reproduce the batch stall and turn it into a red/green signal.

This is the feedback loop for the "converting many files at once freezes the
window" report.  It runs the real :class:`~echoshift.core.pipeline.Pipeline`
against a real fixture set while a real Tk main loop ticks a 200 ms heartbeat,
so the *number that goes red* is **UI heartbeat latency**: how long the Tk
event loop is late in running a scheduled callback.  A frozen window is exactly
that number exploding; the conversion succeeding afterwards is irrelevant.

It also samples the process tree, so "how many processes did we actually
create" is measured rather than inferred.

    python tools/bench_batch.py --count 12 --workers 2 --decrypt-workers default
    python tools/bench_batch.py --count 12 --workers 4 --decrypt-workers 1
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psutil  # noqa: E402

WORKSPACE_TMP = ROOT / ".tmp"
WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(WORKSPACE_TMP)

from echoshift.core.ffmpeg import ProcessLimiter, find_toolchain  # noqa: E402
from echoshift.core.pipeline import (  # noqa: E402
    Pipeline,
    PipelineOptions,
    effective_workers,
)
from echoshift.core.settings import ChannelMode, EncodeSettings  # noqa: E402

#: How often the simulated UI asks to be woken up.
HEARTBEAT_MS = 200


@dataclass
class Sample:
    at: float
    cpu_percent: float
    python_procs: int
    ffmpeg_procs: int
    rss_mib: float
    heartbeat_latency_ms: float


@dataclass
class Report:
    elapsed: float = 0.0
    per_file: list[float] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    states: dict[str, int] = field(default_factory=dict)
    progress_events: int = 0

    def heartbeat(self) -> tuple[float, float, float]:
        values = [s.heartbeat_latency_ms for s in self.samples]
        if not values:
            return 0.0, 0.0, 0.0
        return statistics.median(values), max(values), statistics.quantiles(
            values, n=20
        )[-1] if len(values) >= 20 else max(values)

    def peak_procs(self) -> tuple[int, int]:
        if not self.samples:
            return 0, 0
        return (
            max(s.python_procs for s in self.samples),
            max(s.ffmpeg_procs for s in self.samples),
        )


class Heartbeat:
    """Ticks a Tk callback and records how late the event loop runs it."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.latencies: list[float] = []
        self._armed_at = 0.0
        self._stop = False
        self._job: str | None = None

    def start(self) -> None:
        self._arm()

    def stop(self) -> None:
        self._stop = True
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _arm(self) -> None:
        if self._stop:
            return
        self._armed_at = time.perf_counter()
        self._job = self.root.after(HEARTBEAT_MS, self._tick)

    def _tick(self) -> None:
        late = (time.perf_counter() - self._armed_at) * 1000 - HEARTBEAT_MS
        self.latencies.append(max(0.0, late))
        self._arm()


def _sample_tree(proc: psutil.Process | None) -> tuple[int, int, float]:
    python = 0
    ffmpeg = 0
    rss = 0.0
    try:
        family = proc.children(recursive=True) if proc is not None else []
        family = [proc, *family] if proc is not None else []
    except psutil.Error:
        family = []
    for child in family:
        try:
            name = child.name().lower()
            if "python" in name or "echoshift" in name:
                python += 1
            elif "ffmpeg" in name or "ffprobe" in name:
                ffmpeg += 1
            rss += child.memory_info().rss / (1 << 20)
        except psutil.Error:
            continue
    return python, ffmpeg, rss


def run_once(
    fixtures: list[Path],
    output_dir: Path,
    *,
    workers: int,
    decrypt_workers: int,
    heartbeat: Heartbeat | None,
    sample_interval: float = 0.25,
) -> Report:
    report = Report()
    toolchain = find_toolchain()
    options = PipelineOptions(
        settings=EncodeSettings(
            sample_rate=None,
            channels=ChannelMode.KEEP,
        ).clamped(),
        template="{filename}.mp3",
        output_dir=output_dir,
        verify=True,
        deep_verify=True,
        retries=1,
        work_dir=WORKSPACE_TMP / "batch_work",
        decrypt_workers=decrypt_workers,
    )
    limiter = ProcessLimiter(2)
    pipeline = Pipeline(toolchain, options, process_limiter=limiter)

    per_file: dict[int, float] = {}
    lock = threading.Lock()
    me = psutil.Process()

    def on_result(index: int, result) -> None:
        with lock:
            per_file[index] = result.elapsed

    stop_sampling = threading.Event()
    period = psutil.cpu_percent(interval=None)

    def sampler() -> None:
        nonlocal period
        while not stop_sampling.wait(sample_interval):
            elapsed = time.perf_counter() - started
            period = psutil.cpu_percent(interval=None)
            python_procs, ffmpeg_procs, rss = _sample_tree(me)
            with lock:
                latency = (
                    heartbeat.latencies[-1]
                    if heartbeat is not None and heartbeat.latencies
                    else 0.0
                )
            report.samples.append(
                Sample(elapsed, period, python_procs, ffmpeg_procs, rss, latency)
            )

    started = time.perf_counter()
    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    progress_events = 0

    def on_progress(index: int, fraction: float, stage: str) -> None:
        nonlocal progress_events
        progress_events += 1

    try:
        results = pipeline.run_batch(
            fixtures,
            on_result=on_result,
            on_progress=on_progress,
            cancel=threading.Event(),
            workers=workers,
        )
    finally:
        stop_sampling.set()
        thread.join(timeout=2)
        report.elapsed = time.perf_counter() - started
        report.per_file = [per_file.get(i, 0.0) for i in range(len(fixtures))]

    states: dict[str, int] = {}
    for result in results:
        states[result.state.value] = states.get(result.state.value, 0) + 1
    report.states = states
    report.progress_events = progress_events
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / ".tmp" / "load"))
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--decrypt-workers",
        default="default",
        help="'default' keeps Pipeline's own policy; a number forces it",
    )
    parser.add_argument("--no-gui", action="store_true", help="skip the Tk heartbeat")
    parser.add_argument("--keep-output", action="store_true")
    args = parser.parse_args()

    directory = Path(args.dir)
    fixtures = sorted(directory.glob("*.mflac"))[: args.count]
    if not fixtures:
        print(f"no fixtures in {directory}")
        return 2

    output_dir = WORKSPACE_TMP / "batch_out"
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    decrypt_workers = (
        0 if args.decrypt_workers == "default" else int(args.decrypt_workers)
    )

    root = None
    heartbeat = None
    report: Report | None = None
    if not args.no_gui:
        root = tk.Tk()
        root.geometry("300x100+40+40")
        root.update()
        heartbeat = Heartbeat(root)
        heartbeat.start()

    requested = max(1, args.workers)
    planned = effective_workers(requested)
    print(
        f"fixtures={len(fixtures)}  workers(requested={requested}->{planned})  "
        f"decrypt_workers={args.decrypt_workers}  gui={not args.no_gui}"
    )

    if root is not None:
        # Keep Tk ticking on this (main) thread while the batch runs elsewhere,
        # exactly as the real app does: workers never touch a widget.
        report_holder: list[Report] = []
        batch_thread = threading.Thread(
            target=lambda: report_holder.append(
                run_once(
                    fixtures,
                    output_dir,
                    workers=planned,
                    decrypt_workers=decrypt_workers,
                    heartbeat=heartbeat,
                )
            ),
            daemon=True,
        )
        batch_thread.start()
        while batch_thread.is_alive():
            root.update()
            time.sleep(0.005)
        batch_thread.join()
        heartbeat.stop()
        root.destroy()
        report = report_holder[0]
    else:
        report = run_once(
            fixtures,
            output_dir,
            workers=planned,
            decrypt_workers=decrypt_workers,
            heartbeat=None,
        )
    assert report is not None

    median, worst, p95 = report.heartbeat()
    peak_python, peak_ffmpeg = report.peak_procs()
    print(f"\n  total            {report.elapsed:8.1f} s")
    print(f"  per-file median  {statistics.median(report.per_file) if report.per_file else 0:8.2f} s")
    print(f"  states           {report.states}")
    print(f"  progress events  {report.progress_events}")
    print(f"  peak python procs{peak_python:8d}")
    print(f"  peak ffmpeg procs{peak_ffmpeg:8d}")
    print(
        f"  UI heartbeat     median {median:7.1f} ms   p95 {p95:7.1f} ms   "
        f"worst {worst:8.1f} ms"
    )
    if report.samples:
        cpu = [s.cpu_percent for s in report.samples]
        rss = [s.rss_mib for s in report.samples]
        print(f"  CPU              median {statistics.median(cpu):7.1f} %   max {max(cpu):7.1f} %")
        print(f"  RSS (tree)       max {max(rss):7.0f} MiB")
    worst_proc = max(report.samples, key=lambda s: s.python_procs, default=None)
    if worst_proc is not None:
        print(
            f"  widest sample    t={worst_proc.at:6.1f}s  python={worst_proc.python_procs} "
            f"ffmpeg={worst_proc.ffmpeg_procs} cpu={worst_proc.cpu_percent:.0f}%"
        )

    if not args.keep_output:
        shutil.rmtree(output_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
