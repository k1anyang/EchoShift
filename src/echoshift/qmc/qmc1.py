"""QMC1 cipher -- QQ Music's original, keyless obfuscation.

QMC1 is a repeating XOR mask derived from a fixed 64-byte table.  No external
key is involved, so ``.qmcflac`` / ``.qmc0`` / ``.qmc3`` / ``.qmcogg`` files
decode out of the box.

Reference: presburger/qmc-decoder and bczhc/qmc-decrypt, as reimplemented in
ownlight6/qmc-decoder ``src/qmc1.rs``.
"""

from __future__ import annotations

from ._xor import xor_repeating, xor_stream

__all__ = ["KEY_TABLE", "mask_at", "keystream", "decrypt_in_place", "decrypt"]

#: The 64-byte mask table, expanded from the original 8x7 seed map.
KEY_TABLE: bytes = bytes(
    [
        0xC3, 0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52,
        0xD8, 0xA1, 0x66, 0x62, 0x9F, 0x5B, 0x09, 0x00,
        0xC3, 0x5E, 0x95, 0x23, 0x9F, 0x13, 0x11, 0x7E,
        0xD8, 0x92, 0x3F, 0xBC, 0x90, 0xBB, 0x74, 0x0E,
        0xC3, 0x47, 0x74, 0x3D, 0x90, 0xAA, 0x3F, 0x51,
        0xD8, 0xF4, 0x11, 0x84, 0x9F, 0xDE, 0x95, 0x1D,
        0xC3, 0xC6, 0x09, 0xD5, 0x9F, 0xFA, 0x66, 0xF9,
        0xD8, 0xF0, 0xF7, 0xA0, 0x90, 0xA1, 0xD6, 0xF3,
    ]
)

_PERIOD = 0x7FFF


def mask_at(offset: int) -> int:
    """Return the XOR mask byte for a given stream offset."""
    index = (offset % _PERIOD) & 0x7F
    if index > 0x3F:
        index = (0x80 - index) & 0x3F
    return KEY_TABLE[index]


#: The mask repeats every 0x7FFF bytes, so one period can be precomputed and
#: the per-byte Python loop replaced with big-int XOR.
_CYCLE: bytes = bytes(mask_at(i) for i in range(_PERIOD))


def keystream(length: int, offset: int = 0) -> bytes:
    """Return ``length`` bytes of QMC1 mask starting at stream ``offset``."""
    if length <= 0:
        return b""
    cursor = offset % _PERIOD
    pieces: list[bytes] = []
    remaining = length
    while remaining:
        take = min(remaining, _PERIOD - cursor)
        pieces.append(_CYCLE[cursor : cursor + take])
        remaining -= take
        cursor = 0
    return b"".join(pieces)


def decrypt_in_place(buffer: bytearray, offset: int = 0) -> bytearray:
    """XOR ``buffer`` with the QMC1 mask starting at ``offset``."""
    return xor_repeating(buffer, _CYCLE, offset)


def decrypt_range(data: bytearray, offset: int, length: int) -> bytearray:
    """XOR ``length`` bytes of ``data`` with the mask at stream ``offset``.

    Lets a large payload be decrypted in independently-scheduled chunks.
    """
    return xor_stream(data, keystream(length, offset), 0)


def decrypt(data: bytes) -> bytes:
    """Return a decrypted copy of ``data``."""
    return bytes(decrypt_in_place(bytearray(data)))
