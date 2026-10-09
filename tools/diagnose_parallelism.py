"""Is the machine actually able to run N Python processes concurrently?

The decryption benchmark showed no speed-up from extra workers, which could
mean either a bug in the chunking or a CPU quota far below
``os.cpu_count()``.  This isolates the question with a pure-Python busy loop.

    python tools/diagnose_parallelism.py
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor

ITERATIONS = 6_000_000
JOBS = 8


def burn(iterations: int) -> int:
    value = 0
    for index in range(iterations):
        value = (value * 31 + index) & 0xFFFFFFFF
    return value


def main() -> int:
    print(f"os.cpu_count()          = {os.cpu_count()}")
    print(f"os.process_cpu_count()  = {getattr(os, 'process_cpu_count', lambda: 'n/a')()}")
    print(f"len(os.sched_getaffinity) = {len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else 'n/a'}")
    for name in ("NUMBER_OF_PROCESSORS", "OMP_NUM_THREADS", "DSH_CPU_LIMIT"):
        if name in os.environ:
            print(f"{name:24s}= {os.environ[name]}")

    start = time.perf_counter()
    burn(ITERATIONS)
    single = time.perf_counter() - start
    print(f"\n1 job  (serial burn)    = {single:.2f}s")

    start = time.perf_counter()
    burn(ITERATIONS)
    burn(ITERATIONS)
    two_serial = time.perf_counter() - start
    print(f"2 jobs (serial in-proc) = {two_serial:.2f}s")

    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(burn, [ITERATIONS] * JOBS))
    parallel = time.perf_counter() - start
    print(f"{JOBS} jobs ({JOBS} processes)   = {parallel:.2f}s")

    ideal = single * JOBS
    print(f"\nserial-equivalent would be {ideal:.1f}s")
    print(f"observed speed-up          {ideal / parallel:.2f}×  (ideal {JOBS}×)")

    if parallel > two_serial:
        print("\n结论：进程之间没有真正并行 —— 这台机器的可用 CPU 远低于 cpu_count()。")
    else:
        print("\n结论：多进程确实并行，加速来自真实的多核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
