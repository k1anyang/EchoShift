"""Measure QMC decryption throughput, serial vs multi-process.

    python tools/bench_decrypt.py [megabytes]
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = ROOT / ".tmp"
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)

from echoshift.qmc import qmc1  # noqa: E402
from echoshift.qmc.decoder import decrypt_to, inspect  # noqa: E402
from echoshift.qmc.qmc2 import Qmc2Crypto  # noqa: E402

RC4_KEY = bytes((i * 31 + 5) & 0xFF for i in range(512))
MAP_KEY = bytes((i * 7 + 11) & 0xFF for i in range(128))


def make_payload(size: int) -> bytes:
    """A FLAC-shaped payload that is cheap to build and varies throughout."""
    block = bytes(range(256)) * 4096
    body = (block * (size // len(block) + 1))[: size - 4]
    return b"fLaC" + body


def build(kind: str, size: int) -> tuple[Path, bytes]:
    payload = make_payload(size)
    if kind == "qmc1":
        # QMC1 containers are recognised by extension.
        path = TMP / "bench_qmc1.qmcflac"
        path.write_bytes(qmc1.decrypt(payload))
    else:
        path = TMP / f"bench_{kind}.mflac"
        key = MAP_KEY if kind == "map" else RC4_KEY
        buffer = bytearray(payload)
        Qmc2Crypto(key).decrypt(buffer, 0)
        path.write_bytes(bytes(buffer) + key + struct.pack("<I", len(key)))
    return path, payload


def main() -> int:
    megabytes = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    size = megabytes * 1024 * 1024
    print(f"payload {megabytes} MiB · cpu_count={os.cpu_count()}")
    print(f"{'cipher':8s} {'workers':>8s} {'seconds':>9s} {'MB/s':>8s}  identical")

    for kind in ("qmc1", "map", "rc4"):
        source, payload = build(kind, size)
        digest = hashlib.sha256(payload).hexdigest()
        info = inspect(source)
        for workers in (1, 2, 4, 8):
            out = TMP / f"bench_{kind}_w{workers}.out"
            start = time.perf_counter()
            decrypt_to(info, out, workers=workers)
            elapsed = time.perf_counter() - start
            same = hashlib.sha256(out.read_bytes()).hexdigest() == digest
            print(
                f"{kind:8s} {workers:8d} {elapsed:9.2f} {size / 1048576 / elapsed:8.2f}  {same}"
            )
            out.unlink()
        source.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
