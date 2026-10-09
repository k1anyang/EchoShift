"""Decisive A/B: force the process-pool decrypt path inside a multi-file batch.

``Pipeline.run_batch`` unconditionally sets ``decrypt_workers = 1`` whenever the
queue has more than one file, so the process-pool decrypt path can never be
reached for a batch -- every decryption then runs as pure Python inside several
GIL-competing threads.  This script neutralises that one line *in memory* (the
file on disk is untouched) so the two strategies can be compared on identical
input, and records every ``decrypt_to`` call plus per-stage timings.

    python tools/diag_force_parallel.py <workers> <decrypt_workers>
"""

from __future__ import annotations

import sys
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psutil  # noqa: E402

from echoshift.core.ffmpeg import find_toolchain, run_with_progress  # noqa: E402
from echoshift.core.pipeline import PipelineOptions  # noqa: E402
from echoshift.core.settings import EncodeSettings  # noqa: E402
from echoshift.qmc import decoder as decoder_mod  # noqa: E402

WORK = ROOT / ".tmp" / "diag_force"
STAGE_LOG: list[tuple[str, str, float]] = []
DECRYPT_CALLS: list[tuple[float, int]] = []


def load_patched_pipeline():
    """Import pipeline.py with the ``decrypt_workers = 1`` override removed."""
    import types

    source = (ROOT / "src" / "echoshift" / "core" / "pipeline.py").read_text(encoding="utf-8")
    needle = "            self.decrypt_workers = 1\n"
    if needle not in source:
        raise SystemExit("override line not found; pipeline.py changed")
    source = source.replace(needle, "            pass  # diagnostic: override disabled\n")
    # dataclasses resolves annotations through sys.modules, so the module has to
    # be registered before exec or the @dataclass decorators blow up.
    module = types.ModuleType("echoshift.core.pipeline_patched")
    module.__file__ = str(ROOT / "src" / "echoshift" / "core" / "pipeline.py")
    module.__package__ = "echoshift.core"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


def instrument() -> None:
    original_decrypt = decoder_mod.decrypt_to

    def spy_decrypt(info, destination, **kwargs):
        started = time.perf_counter()
        try:
            return original_decrypt(info, destination, **kwargs)
        finally:
            DECRYPT_CALLS.append((time.perf_counter() - started, int(kwargs.get("workers") or 1)))

    decoder_mod.decrypt_to = spy_decrypt

    import echoshift.core.ffmpeg as ffmpeg_mod

    original_progress = ffmpeg_mod.run_with_progress

    def spy_progress(cmd, **kwargs):
        started = time.perf_counter()
        try:
            return original_progress(cmd, **kwargs)
        finally:
            label = Path(str(cmd[0])).name
            STAGE_LOG.append(("ffmpeg", label, time.perf_counter() - started))

    ffmpeg_mod.run_with_progress = spy_progress


class Heartbeat(threading.Thread):
    """Measures main-thread stall directly: how late does a 200 ms tick run?"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.latencies: list[float] = []
        self._stop = threading.Event()

    def run(self) -> None:
        interval = 0.2
        while not self._stop.is_set():
            expected = time.perf_counter() + interval
            self._stop.wait(interval)
            self.latencies.append(max(0.0, (time.perf_counter() - expected) * 1000))

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    decrypt_workers = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    shared = "--shared" in sys.argv
    mode = "original" if "--original" in sys.argv else "fixed"

    pipeline_mod = load_patched_pipeline()
    if mode == "original":
        # The override must still be present for the baseline, so import the
        # real module instead of the patched copy.
        from echoshift.core import pipeline as pipeline_mod  # type: ignore[no-redef]
    instrument()

    files = sorted((ROOT / ".tmp" / "load_rc4").glob("*.mflac"))[:count]
    output_dir = WORK / "out"
    # A stale output directory turns every job into a no-op via the rename
    # policy, which silently removes the encoding stage from the measurement.
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = WORK / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    options = PipelineOptions(
        settings=EncodeSettings().clamped(),
        template="{filename}.mp3",
        output_dir=output_dir,
        retries=1,
        work_dir=work_dir,
        decrypt_workers=decrypt_workers,
    )
    pipeline = pipeline_mod.Pipeline(find_toolchain(), options)
    pool_holder = []
    if shared:
        from concurrent.futures import ProcessPoolExecutor

        from echoshift.qmc import parallel as parallel_mod

        pool = ProcessPoolExecutor(max_workers=decrypt_workers)
        pool_holder.append(pool)
        limits = pool._max_workers  # noqa: SLF001 - diagnostics only
        print(f"  [shared pool of {limits} injected]")

        def reuse_pool(
            *, source, destination, total, key, is_qmc1, workers,
            chunk_bytes=None, on_progress=None, should_stop=None, head_probe=None,
        ):
            """Same policy as parallel.decrypt_parallel but on a shared pool."""
            if total < parallel_mod.MIN_PARALLEL_BYTES:
                return False, None
            chunks = parallel_mod.build_chunks(
                source=source, destination=destination, total=total, key=key,
                is_qmc1=is_qmc1, chunk_bytes=chunk_bytes or parallel_mod.CHUNK_BYTES,
            )
            if len(chunks) < 2:
                return False, None
            try:
                with open(destination, "wb") as handle:
                    handle.truncate(total)
                completed = 0
                for offset, head, error in pool.map(parallel_mod.run_chunk, chunks):
                    if error:
                        return False, f"parallel failed at {offset}: {error}"
                    if head is not None and head_probe is not None:
                        head_probe(head)
                    completed += 1
                    if on_progress is not None:
                        on_progress(completed / len(chunks))
                    if should_stop is not None and should_stop():
                        return False, "cancelled"
                return True, None
            except Exception as exc:  # noqa: BLE001
                return False, f"{type(exc).__name__}: {exc}"

        parallel_mod.decrypt_parallel = reuse_pool
    print(f"mode={mode} workers={workers} decrypt_workers={decrypt_workers} "
          f"pid={psutil.Process().pid}")

    heartbeat = Heartbeat()
    heartbeat.start()
    me = psutil.Process()
    peak = 0
    stop = threading.Event()

    def watch() -> None:
        nonlocal peak
        while not stop.wait(0.2):
            try:
                kids = me.children(recursive=True)
            except psutil.Error:
                continue
            peak = max(peak, 1 + len(kids))

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    started = time.perf_counter()
    if mode == "original":
        # Exactly what the shipped code does: run_batch forces decrypt_workers=1
        # for any multi-file batch, so every decryption is pure Python inside
        # `workers` GIL-competing threads.
        results = pipeline_mod.Pipeline.run_batch(
            pipeline, files, cancel=threading.Event(), workers=workers
        )
    else:
        # Same scheduling, but the per-file override is gone so decryption can
        # use the process pool.
        results_holder: list = [None] * len(files)

        def work(position: int, source: Path) -> None:
            results_holder[position] = pipeline.run_job(
                source, index=position + 1, cancel=threading.Event()
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda pair: work(*pair), list(enumerate(files))))
        results = [r for r in results_holder if r is not None]
    elapsed = time.perf_counter() - started
    stop.set()
    watcher.join(timeout=1)
    heartbeat.stop()
    heartbeat.join(timeout=1)

    latencies = sorted(heartbeat.latencies)
    median = latencies[len(latencies) // 2] if latencies else 0.0
    worst = latencies[-1] if latencies else 0.0
    decode = [elapsed for elapsed, used in DECRYPT_CALLS if used > 1]
    print(f"\n  total              {elapsed:7.1f} s")
    print(f"  states             {[r.state.value for r in results]}")
    print(f"  decrypt_to calls   {len(DECRYPT_CALLS)}  "
          f"workers used = {sorted({used for _e, used in DECRYPT_CALLS})}")
    print(f"  decrypt seconds    total {sum(e for e, _u in DECRYPT_CALLS):7.1f} s  "
          f"median {sorted(e for e, _u in DECRYPT_CALLS)[len(DECRYPT_CALLS) // 2]:5.2f} s")
    print(f"  parallel decrypts  {len(decode)}")
    print(f"  ffmpeg invocations {len(STAGE_LOG)}  "
          f"total {sum(d for _k, _l, d in STAGE_LOG):7.1f} s")
    print(f"  peak process count {peak}")
    print(f"  main-thread stall  median {median:8.1f} ms   worst {worst:9.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
