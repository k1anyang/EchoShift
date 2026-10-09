"""Quantify the decryption subsystem: throughput, spawn cost, and contention.

The batch stall is about *where the CPU goes when many files decrypt at once*,
so this measures the decryption layer in isolation with the real fixture size:

1. serial throughput on one container (the pure-Python cipher ceiling);
2. one ``ProcessPoolExecutor`` reused across containers;
3. a **fresh** pool per container -- what the pipeline does today;
4. N containers decrypted concurrently, each with its own pool -- what the
   pipeline does when the queue has many files and "parallel jobs" is 2-4.

    python tools/bench_decrypt_scaling.py [fixture_dir] [count]
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echoshift.qmc import decoder  # noqa: E402
from echoshift.qmc.parallel import CHUNK_BYTES, build_chunks, run_chunk  # noqa: E402

WORK = ROOT / ".tmp" / "bench_decrypt"


def _prepare(fixtures: list[Path]) -> list[decoder.ContainerInfo]:
    return [decoder.inspect(path) for path in fixtures]


def _decrypt_once(info: decoder.ContainerInfo, workers: int, *, pool=None) -> tuple[float, int]:
    """Decrypt one container; return (seconds, chunks)."""
    destination = WORK / f"out_{info.path.stem}_{workers}.flac"
    destination.parent.mkdir(parents=True, exist_ok=True)
    choice = decoder._choose_key(info)
    chunks = build_chunks(
        source=info.path,
        destination=destination,
        total=info.audio_length,
        key=choice.raw_key,
        is_qmc1=info.format.is_qmc1,
    )
    started = time.perf_counter()
    if pool is None:
        with open(destination, "wb") as handle:
            handle.truncate(info.audio_length)
        with ProcessPoolExecutor(max_workers=workers) as own:
            list(own.map(run_chunk, chunks))
    else:
        with open(destination, "wb") as handle:
            handle.truncate(info.audio_length)
        list(pool.map(run_chunk, chunks))
    elapsed = time.perf_counter() - started
    destination.unlink(missing_ok=True)
    return elapsed, len(chunks)


def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".tmp" / "load"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    fixtures = sorted(directory.glob("*.mflac"))[:count]
    if not fixtures:
        print(f"no fixtures in {directory}; run tools/make_load_fixture.py first")
        return 2

    infos = _prepare(fixtures)
    size_mib = infos[0].audio_length / (1 << 20)
    print(f"cpu_count={os.cpu_count()}  chunk={CHUNK_BYTES / (1 << 20):.0f} MiB")
    print(f"{len(infos)} containers, {size_mib:.1f} MiB audio each\n")

    # -- 1. serial ---------------------------------------------------------
    serial: list[float] = []
    for info in infos[:3]:
        destination = WORK / "serial.flac"
        destination.parent.mkdir(parents=True, exist_ok=True)
        choice = decoder._choose_key(info)
        chunks = build_chunks(
            source=info.path, destination=destination, total=info.audio_length,
            key=choice.raw_key, is_qmc1=info.format.is_qmc1,
        )
        started = time.perf_counter()
        decoder.decrypt_to(info, destination, workers=1)
        serial.append(time.perf_counter() - started)
        destination.unlink(missing_ok=True)
        print(f"  serial  {info.path.name}: {serial[-1]:6.2f}s "
              f"({size_mib / serial[-1]:5.2f} MiB/s, {len(chunks)} chunks)")
    median_serial = statistics.median(serial)
    print(f"  -> serial median {median_serial:.2f}s for {size_mib:.1f} MiB\n")

    # -- 2/3. pool reuse vs fresh pool per file ----------------------------
    for workers in (1, 2, 4):
        started = time.perf_counter()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            reused = [_decrypt_once(info, workers, pool=pool)[0] for info in infos]
        print(f"  shared pool w={workers}: total {time.perf_counter() - started:6.2f}s "
              f"per-file {statistics.median(reused):5.2f}s")

    for workers in (2, 4):
        started = time.perf_counter()
        fresh = [_decrypt_once(info, workers)[0] for info in infos]
        print(f"  FRESH pool w={workers}: total {time.perf_counter() - started:6.2f}s "
              f"per-file {statistics.median(fresh):5.2f}s")

    # -- 4. concurrent containers, each with its own pool ------------------
    from concurrent.futures import ThreadPoolExecutor

    for file_workers, decrypt_workers in ((2, 2), (4, 2), (4, 4)):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=file_workers) as jobs:
            list(jobs.map(lambda info: _decrypt_once(info, decrypt_workers)[0], infos))
        total = time.perf_counter() - started
        print(f"  {file_workers} files x {decrypt_workers} procs: total {total:6.2f}s "
              f"-> per-file {total / len(infos):5.2f}s, "
              f"peak python procs ~= {file_workers * decrypt_workers + 1}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
