"""Build a realistic *large* MFLAC workload to reproduce the batch stall.

``samples/`` fixtures are 39 KiB -- far too small for the parallel decryption
path (``MIN_PARALLEL_BYTES`` is 1 MiB) and far too fast to show where a batch
spends its time.  Real QQ Music downloads are 20-40 MB, which is the size class
where the stalls are reported, so this script builds that.

The payload is uncorrelated stereo noise: it is the worst case for FLAC, so a
short file already reaches the target byte size.

    python tools/make_load_fixture.py [output_dir] [count] [seconds]
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from echoshift.core.ffmpeg import CREATE_NO_WINDOW, find_toolchain  # noqa: E402
from make_samples import RC4_KEY, REAL_KEY, qmc2_encrypt_v1_text  # noqa: E402

__all__ = ["build_load_set"]

#: The two cipher paths QMC2 can take.  Which one a container uses is decided by
#: the *decoded key length*: <=300 bytes is the fast Map cipher, more is the
#: modified RC4 whose pure-Python inner loop runs at ~1.9 MB/s.  Both shapes
#: occur in the wild, so a workload has to cover both.
CIPHERS = {"map": REAL_KEY, "rc4": RC4_KEY}


def make_noise_flac(destination: Path, *, seconds: float, sample_rate: int = 44100) -> Path:
    """Render incompressible stereo noise as FLAC, so size is driven by duration."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_toolchain().ffmpeg
    # Two independent noise sources merged into stereo: decorrelated channels
    # cannot be mid/side compressed away, which is what keeps the file large.
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=colour=white:sample_rate={sample_rate}:duration={seconds}:seed=1",
        "-f", "lavfi", "-i", f"anoisesrc=colour=white:sample_rate={sample_rate}:duration={seconds}:seed=2",
        "-filter_complex", "[0:a][1:a]amerge=inputs=2[a]",
        "-map", "[a]",
        "-c:a", "flac", "-compression_level", "5",
        "-metadata", f"title={destination.stem}",
        "-metadata", "artist=EchoShift Load Test",
        "-metadata", "album=Load",
        str(destination),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {destination.name}:\n{result.stderr}")
    return destination


def build_load_set(
    directory: Path, *, count: int, seconds: float, cipher: str = "map"
) -> list[Path]:
    key = CIPHERS[cipher]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "_payload.flac"
    if not source.is_file() or source.stat().st_size < 1 << 20:
        started = time.perf_counter()
        make_noise_flac(source, seconds=seconds)
        print(f"payload FLAC: {source.name} {source.stat().st_size / (1 << 20):.1f} MiB"
              f" in {time.perf_counter() - started:.1f}s")

    built: list[Path] = []
    for index in range(1, count + 1):
        target = directory / f"load_{index:02d}.mflac"
        if target.is_file() and target.stat().st_size > source.stat().st_size:
            built.append(target)
            continue
        qmc2_encrypt_v1_text(source, target, key)
        built.append(target)
    return built


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".tmp" / "load"
    amount = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    length = float(sys.argv[3]) if len(sys.argv) > 3 else 80.0
    cipher = sys.argv[4] if len(sys.argv) > 4 else "map"
    files = build_load_set(out, count=amount, seconds=length, cipher=cipher)
    total = sum(path.stat().st_size for path in files)
    print(f"\n{len(files)} {cipher.upper()} containers in {out}")
    print(f"each {files[0].stat().st_size / (1 << 20):.1f} MiB, total {total / (1 << 20):.0f} MiB")
