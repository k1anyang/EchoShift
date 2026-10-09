"""Generate test fixtures with the bundled ffmpeg.

Because QMC1 and QMC2 are symmetric XOR ciphers, this script can *produce*
valid encrypted containers: it encodes a real FLAC, runs the cipher over it,
and appends the container footer that the decoder expects.  That gives the
test suite genuine end-to-end coverage of the MFLAC path without needing any
real DRM-protected file.

Run directly to populate ``samples/``::

    python tools/make_samples.py [output_dir]
"""

from __future__ import annotations

import base64
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echoshift.core.ffmpeg import CREATE_NO_WINDOW, find_toolchain  # noqa: E402
from echoshift.qmc import qmc1  # noqa: E402
from echoshift.qmc.qmc2 import Qmc2Crypto  # noqa: E402
from echoshift.qmc.tc_tea import derive_tea_key, tc_tea_encrypt  # noqa: E402

#: EncV2 layering constants, mirrored from ``echoshift.qmc.qmc2``.
_ENCV2_PREFIX = b"QQMusic EncV2,Key:"
_ENCV2_STAGE1_KEY = b"386ZJY!@#*$%^&)("
_ENCV2_STAGE2_KEY = b"**#!(#$%&^a1cZ,T"

__all__ = [
    "make_flac",
    "make_cover_png",
    "qmc1_encrypt",
    "qmc2_encrypt",
    "build_ekey_for_key",
    "build_all",
]


def _ffmpeg() -> Path:
    return find_toolchain().ffmpeg


def make_cover_png(destination: Path, *, size: int = 300, colour: tuple[int, int, int] = (32, 96, 160)) -> Path:
    """Create a small PNG to use as embedded cover art."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), colour)
    draw = ImageDraw.Draw(image)
    draw.rectangle([size // 4, size // 4, size * 3 // 4, size * 3 // 4], fill=(240, 220, 120))
    image.save(destination, "PNG")
    return destination


def make_flac(
    destination: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
    duration: float = 2.5,
    frequency: int = 440,
    tags: dict[str, str] | None = None,
    cover: Path | None = None,
) -> Path:
    """Render a tagged (optionally illustrated) FLAC test tone."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    tone = f"sine=frequency={frequency}:sample_rate={sample_rate}:duration={duration}"

    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    command += ["-f", "lavfi", "-i", tone]

    if cover is not None:
        command += ["-i", str(cover)]

    if channels > 1:
        # Build an N-channel signal by fanning the mono tone out.
        layout = {2: "stereo", 6: "5.1"}.get(channels, f"{channels}c")
        pairs = "|".join(f"c{i}=c0" for i in range(channels))
        command += ["-af", f"pan={layout}|{pairs}"]

    command += ["-map", "0:a"]
    if cover is not None:
        command += ["-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"]

    command += ["-c:a", "flac", "-compression_level", "5"]
    for key, value in (tags or {}).items():
        command += ["-metadata", f"{key}={value}"]
    command.append(str(destination))

    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {destination.name}:\n{result.stderr}")
    return destination


# --------------------------------------------------------------------------- #
# container construction
# --------------------------------------------------------------------------- #


def build_ekey_for_key(key: bytes) -> str:
    """Wrap raw key bytes in the EncV1 ekey format the decoder understands.

    ``parse_ekey`` reverses this exactly, so tests can assert that a
    hand-built ekey round-trips.
    """
    if len(key) < 9:
        raise ValueError("key must be at least 9 bytes for the EncV1 layout")
    header, body = key[:8], key[8:]
    blob = header + tc_tea_encrypt(body, derive_tea_key(header))
    return base64.b64encode(blob).decode("ascii")


def build_encv2_ekey(key: bytes) -> str:
    """Wrap a key in the *EncV2* ekey format (``QQMusic EncV2,Key:``).

    Real QQ Music downloads use this outer layer, and :func:`parse_ekey` peels
    it with two TC-TEA passes before reaching the EncV1 blob.  Building one here
    means that code path is covered by the end-to-end tests too.
    """
    encv1 = build_ekey_for_key(key)
    stage2 = tc_tea_encrypt(encv1.encode("ascii"), _ENCV2_STAGE2_KEY)
    stage1 = tc_tea_encrypt(stage2, _ENCV2_STAGE1_KEY)
    return base64.b64encode(_ENCV2_PREFIX + stage1).decode("ascii")


def qmc2_encrypt_v1_text(
    source: Path, destination: Path, key: bytes, *, encv2: bool = True
) -> Path:
    """The layout real ``.mflac`` downloads actually use.

    The V1 trailer holds the ekey as **NUL-terminated ASCII base64 text**, and
    the trailing u32 is the length of that text *including* the NUL.  This is
    the shape that made every real file fail while the synthetic raw-key
    fixtures passed.
    """
    ekey = build_encv2_ekey(key) if encv2 else build_ekey_for_key(key)
    trailer = ekey.encode("ascii") + b"\x00"
    payload = bytearray(source.read_bytes())
    Qmc2Crypto(key).decrypt(payload, 0)
    destination.write_bytes(bytes(payload) + trailer + struct.pack("<I", len(trailer)))
    return destination


def qmc1_encrypt(source: Path, destination: Path) -> Path:
    """Wrap a FLAC in the keyless QMC1 container (``.qmcflac``)."""
    payload = source.read_bytes()
    destination.write_bytes(qmc1.decrypt(payload))
    return destination


def qmc2_encrypt(source: Path, destination: Path, key: bytes) -> Path:
    """Wrap a FLAC in a QMC2 container using a V1 footer that embeds ``key``."""
    payload = bytearray(source.read_bytes())
    Qmc2Crypto(key).decrypt(payload, 0)
    # V1 footer: <raw key><u32 little-endian key length>
    destination.write_bytes(bytes(payload) + key + struct.pack("<I", len(key)))
    return destination


def qmc2_encrypt_qtag(
    source: Path, destination: Path, key: bytes, song_id: str = "1234567"
) -> Path:
    """Wrap a FLAC in a QMC2 container using a QTag footer.

    This is the layout QQ Music <= 19.51 writes: the ekey travels *inside* the
    file as an EncV1 base64 blob, followed by a big-endian length and the
    ``QTag`` marker.
    """
    ekey = build_ekey_for_key(key)
    meta = f"{ekey},{song_id},mflac".encode("utf-8")
    payload = bytearray(source.read_bytes())
    Qmc2Crypto(key).decrypt(payload, 0)
    destination.write_bytes(
        bytes(payload) + meta + struct.pack(">I", len(meta)) + b"QTag"
    )
    return destination


# --------------------------------------------------------------------------- #
# the standard fixture set
# --------------------------------------------------------------------------- #

STANDARD_TAGS = {
    "title": "测试曲目",
    "artist": "EchoShift 测试",
    "album": "Sample Album",
    "album_artist": "Various",
    "track": "3",
    "date": "2024",
    "genre": "Test",
}

#: 128 bytes keeps QMC2 on the short-key Map cipher.
MAP_KEY = bytes((i * 7 + 11) & 0xFF for i in range(128))
#: 512 bytes pushes QMC2 onto the modified-RC4 cipher.
RC4_KEY = bytes((i * 31 + 5) & 0xFF for i in range(512))
#: 256 bytes -- the size real QQ Music downloads most often use, still Map.
REAL_KEY = bytes((i * 17 + 29) & 0xFF for i in range(256))


def build_all(directory: Path) -> dict[str, Path]:
    """Create the whole fixture set and return a name -> path mapping."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    cover = make_cover_png(directory / "cover.png", size=240)
    standard = make_flac(
        directory / "standard.flac",
        tags=STANDARD_TAGS,
        cover=cover,
    )
    make_flac(
        directory / "hires.flac",
        sample_rate=96000,
        duration=1.5,
        tags={"title": "96kHz 测试", "artist": "EchoShift 测试"},
    )
    make_flac(
        directory / "surround.flac",
        sample_rate=48000,
        channels=6,
        duration=1.5,
        tags={"title": "5.1 测试", "artist": "EchoShift 测试"},
    )
    make_flac(
        directory / "untagged.flac",
        duration=1.0,
    )

    qmc1_encrypt(standard, directory / "standard.qmcflac")
    qmc2_encrypt(standard, directory / "standard_map.mflac", MAP_KEY)
    qmc2_encrypt(standard, directory / "standard_rc4.mflac", RC4_KEY)
    # The QTag layout is what QQ Music <= 19.51 produces, and it exercises a
    # completely separate footer parser from the V1 one above.
    qmc2_encrypt_qtag(standard, directory / "standard_qtag.mflac", RC4_KEY)
    # ...and this mirrors an actual QQ Music download byte-for-byte in shape:
    # V1 trailer holding NUL-terminated ASCII base64 text of an EncV2 ekey.
    qmc2_encrypt_v1_text(standard, directory / "standard_v1text.mflac", REAL_KEY)

    return {
        "cover": cover,
        "standard": standard,
        "hires": directory / "hires.flac",
        "surround": directory / "surround.flac",
        "untagged": directory / "untagged.flac",
        "qmc1": directory / "standard.qmcflac",
        "mflac_map": directory / "standard_map.mflac",
        "mflac_rc4": directory / "standard_rc4.mflac",
        "mflac_qtag": directory / "standard_qtag.mflac",
        "mflac_v1text": directory / "standard_v1text.mflac",
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples"
    created = build_all(target)
    print(f"fixtures written to {target}")
    for name, path in sorted(created.items()):
        if path.suffix == ".png":
            continue
        print(f"  {name:12s} {path.name:26s} {path.stat().st_size / 1024:8.1f} KiB")
    print(f"\nQMC2 Map key (128 B)  ekey = {build_ekey_for_key(MAP_KEY)}")
    print(f"QMC2 RC4 key (512 B)  ekey = {build_ekey_for_key(RC4_KEY)}")
