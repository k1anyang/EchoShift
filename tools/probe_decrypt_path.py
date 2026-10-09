"""Trace one decrypt: which path is taken, and where the seconds go.

The batch benchmark showed a forced ``decrypt_workers=2`` still spawning no
child processes, which contradicts the pool probe succeeding elsewhere.  This
prints the decision the decoder makes for one container, with the same code
path the pipeline uses.

    python tools/probe_decrypt_path.py .tmp/load_rc4/load_01.mflac 2
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echoshift.qmc import decoder, parallel  # noqa: E402

WORK = ROOT / ".tmp" / "probe_decrypt"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".tmp/load_rc4/load_01.mflac"
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    WORK.mkdir(parents=True, exist_ok=True)

    info = decoder.inspect(path)
    print(f"{path.name}: {info.audio_length / (1 << 20):.1f} MiB, {info.describe()}")
    choice = decoder._choose_key(info)
    total = info.audio_length
    print(f"  key bytes={len(choice.raw_key) if choice.raw_key else 0} "
          f"is_qmc1={info.format.is_qmc1}")

    print(f"  pool_available() = {parallel.pool_available()}")
    print(f"  gates: workers={workers} > 1 -> {workers > 1}; "
          f"total >= MIN_PARALLEL_BYTES({parallel.MIN_PARALLEL_BYTES}) -> "
          f"{total >= parallel.MIN_PARALLEL_BYTES}")

    chunks = parallel.build_chunks(
        source=info.path, destination=WORK / "x.flac", total=total,
        key=choice.raw_key, is_qmc1=info.format.is_qmc1,
    )
    print(f"  chunks = {len(chunks)} (need >= 2 -> {len(chunks) >= 2}); "
          f"pool would use min(workers, MAX={parallel.MAX_WORKERS}, "
          f"cpu={parallel.os.cpu_count()}) = {min(workers, parallel.MAX_WORKERS, parallel.os.cpu_count() or 2)}")

    warnings: list[str] = []
    for label, used_workers in (("parallel", workers), ("serial", 1)):
        out = WORK / f"{label}.flac"
        started = time.perf_counter()
        decoder.decrypt_to(
            info, out,
            workers=used_workers,
            on_warning=warnings.append,
        )
        elapsed = time.perf_counter() - started
        size = out.stat().st_size / (1 << 20)
        print(f"  {label:9s} w={used_workers}: {elapsed:6.2f}s "
              f"({size / elapsed:6.2f} MiB/s) out={size:.1f} MiB")
        out.unlink(missing_ok=True)

    if warnings:
        print("  fallback warnings:")
        for text in warnings:
            print(f"    - {text}")
    else:
        print("  no fallback warning -> the parallel path was used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
