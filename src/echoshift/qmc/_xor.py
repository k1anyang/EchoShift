"""Fast in-place XOR of a byte buffer against a keystream.

Pure-Python byte loops are far too slow for multi-megabyte audio payloads, so
the XOR is performed on big integers, which CPython evaluates in C.
"""

from __future__ import annotations

__all__ = ["xor_stream", "xor_bytes", "xor_repeating"]

_CHUNK = 1 << 22  # 4 MiB per big-int operation keeps peak memory reasonable


def xor_stream(data: bytearray, keystream: bytes, offset: int = 0) -> bytearray:
    """XOR ``data`` in place with ``keystream[offset:offset+len(data)]``."""
    total = len(data)
    if len(keystream) - offset < total:
        raise ValueError(
            f"keystream too short: need {total} bytes at offset {offset}, "
            f"have {len(keystream) - offset}"
        )
    pos = 0
    while pos < total:
        end = min(pos + _CHUNK, total)
        width = end - pos
        lhs = int.from_bytes(data[pos:end], "big")
        rhs = int.from_bytes(keystream[offset + pos : offset + end], "big")
        data[pos:end] = (lhs ^ rhs).to_bytes(width, "big")
        pos = end
    return data


def xor_repeating(data: bytearray, cycle: bytes, offset: int = 0) -> bytearray:
    """XOR ``data`` in place against ``cycle`` repeated forever.

    Used by ciphers whose mask is periodic, which lets the per-byte Python loop
    be replaced by big-int arithmetic over a precomputed cycle.
    """
    period = len(cycle)
    if period == 0:
        raise ValueError("cycle must not be empty")
    total = len(data)
    pos = 0
    while pos < total:
        end = min(pos + _CHUNK, total)
        width = end - pos
        cursor = (offset + pos) % period
        pieces: list[bytes] = []
        remaining = width
        while remaining:
            take = min(remaining, period - cursor)
            pieces.append(cycle[cursor : cursor + take])
            remaining -= take
            cursor = 0
        lhs = int.from_bytes(data[pos:end], "big")
        rhs = int.from_bytes(b"".join(pieces), "big")
        data[pos:end] = (lhs ^ rhs).to_bytes(width, "big")
        pos = end
    return data


def xor_bytes(data: bytes, keystream: bytes, offset: int = 0) -> bytes:
    """Return a copy of ``data`` XORed with ``keystream``."""
    return bytes(xor_stream(bytearray(data), keystream, offset))
