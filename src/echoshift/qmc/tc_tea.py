"""Tencent's variant of TEA (TC-TEA), used to unwrap QQ Music ekeys.

Ported from jixunmoe's ``tc_tea`` crate (0.1.x).  The cipher is ordinary TEA
with 16 rounds and big-endian words, wrapped in a tweaked CBC mode that keeps
*two* chained IVs instead of one.  The plaintext carries its own framing::

    PadLen  (1 byte; only the low 3 bits are meaningful)
    Padding (0..7 bytes)
    Salt    (2 bytes)
    Body    (payload)
    Zero    (7 bytes)

The trailing 7 zero bytes double as an integrity check: if the last seven
bytes of the decrypted block are not all zero the blob was not encrypted with
this key, which is exactly how callers detect "not an EncV1 ekey".
"""

from __future__ import annotations

import math
import struct

__all__ = [
    "TcTeaError",
    "tc_tea_decrypt",
    "tc_tea_encrypt",
    "tea_decrypt_block",
    "tea_encrypt_block",
    "simple_make_key",
    "derive_tea_key",
]

_DELTA = 0x9E3779B9
_ROUNDS = 16
_MASK32 = 0xFFFFFFFF
_U64_MAX = 0xFFFFFFFFFFFFFFFF

_SALT_LEN = 2
_ZERO_LEN = 7
_FIXED_PADDING_LEN = 1 + _SALT_LEN + _ZERO_LEN


class TcTeaError(ValueError):
    """Raised when a TC-TEA blob cannot be decrypted."""


def _f32(value: float) -> float:
    """Round ``value`` to the nearest IEEE-754 single, like Rust's ``f32``."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _f64_to_u8_saturating(value: float) -> int:
    """Emulate Rust's ``f64 as u8``: NaN -> 0, saturating, truncating."""
    if math.isnan(value) or value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value)


def _f64_to_u64_saturating(value: float) -> int:
    """Emulate Rust's ``f64 as u64``: NaN -> 0, saturating, truncating."""
    if math.isnan(value) or value <= 0:
        return 0
    if value >= float(_U64_MAX):
        return _U64_MAX
    return int(value)


def simple_make_key(seed: int, size: int) -> bytes:
    """Rebuild the obfuscated key schedule used for EncV1 ekey headers.

    ``simple_make_key(106, 8)`` yields ``69 56 46 38 2b 20 15 0b``.
    """
    out = bytearray()
    seed_f = _f32(float(seed))
    for i in range(size):
        step = _f32(_f32(float(i)) * _f32(0.1))
        value = _f32(seed_f + step)
        # Rust calls f32::tan; a double-precision tan rounded back to f32 is
        # indistinguishable for the seed values this scheme actually uses.
        magnitude = _f32(abs(math.tan(value)))
        out.append(_f64_to_u8_saturating(_f32(_f32(100.0) * magnitude)))
    return bytes(out)


def derive_tea_key(ekey_header: bytes) -> bytes:
    """Interleave the fixed schedule with the ekey header to build a TEA key."""
    if len(ekey_header) != 8:
        raise TcTeaError(f"ekey header must be 8 bytes, got {len(ekey_header)}")
    schedule = simple_make_key(106, 8)
    key = bytearray(16)
    for i in range(0, 16, 2):
        key[i] = schedule[i // 2]
        key[i + 1] = ekey_header[i // 2]
    return bytes(key)


def _parse_key(key: bytes) -> tuple[int, int, int, int]:
    if len(key) != 16:
        raise TcTeaError(f"key must be 16 bytes, got {len(key)}")
    return struct.unpack(">4I", key)  # type: ignore[return-value]


def tea_encrypt_block(block: bytes, key: bytes) -> bytes:
    """Encrypt a single 8-byte block with 16-round TEA (big-endian)."""
    k0, k1, k2, k3 = _parse_key(key)
    state = int.from_bytes(block, "big")
    v0, v1 = state >> 32, state & _MASK32

    total = 0
    for _ in range(_ROUNDS):
        total = (total + _DELTA) & _MASK32
        v0 = (v0 + ((((v1 << 4) + k0) & _MASK32) ^ ((v1 + total) & _MASK32) ^ ((v1 >> 5) + k1))) & _MASK32
        v1 = (v1 + ((((v0 << 4) + k2) & _MASK32) ^ ((v0 + total) & _MASK32) ^ ((v0 >> 5) + k3))) & _MASK32

    return ((v0 << 32) | v1).to_bytes(8, "big")


def tea_decrypt_block(block: bytes, key: bytes) -> bytes:
    """Decrypt a single 8-byte block with 16-round TEA (big-endian)."""
    k0, k1, k2, k3 = _parse_key(key)
    state = int.from_bytes(block, "big")
    v0, v1 = state >> 32, state & _MASK32

    total = (_DELTA * _ROUNDS) & _MASK32
    for _ in range(_ROUNDS):
        v1 = (v1 - ((((v0 << 4) + k2) & _MASK32) ^ ((v0 + total) & _MASK32) ^ ((v0 >> 5) + k3))) & _MASK32
        v0 = (v0 - ((((v1 << 4) + k0) & _MASK32) ^ ((v1 + total) & _MASK32) ^ ((v1 >> 5) + k1))) & _MASK32
        total = (total - _DELTA) & _MASK32

    return ((v0 << 32) | v1).to_bytes(8, "big")


def _decrypt_round(cipher_block: int, iv1: int, iv2: int, key: bytes) -> tuple[int, int, int]:
    result = cipher_block ^ iv2
    next_iv2 = int.from_bytes(tea_decrypt_block(result.to_bytes(8, "big"), key), "big")
    plain_block = next_iv2 ^ iv1
    return plain_block, cipher_block, next_iv2


def _encrypt_round(plain_block: int, iv1: int, iv2: int, key: bytes) -> tuple[int, int, int]:
    iv2_next = plain_block ^ iv1
    result = int.from_bytes(tea_encrypt_block(iv2_next.to_bytes(8, "big"), key), "big")
    cipher_block = result ^ iv2
    return cipher_block, cipher_block, iv2_next


def tc_tea_decrypt(cipher: bytes, key: bytes) -> bytes:
    """Decrypt a TC-TEA blob, returning only the payload bytes.

    Raises :class:`TcTeaError` when the blob is malformed or the trailing zero
    sentinel is absent -- which is the documented way of saying "this is not an
    EncV1 ekey".
    """
    _parse_key(key)
    input_len = len(cipher)
    if input_len < _FIXED_PADDING_LEN or input_len % 8 != 0:
        raise TcTeaError(f"cipher length {input_len} is not a multiple of 8 or too short")

    plain = bytearray(input_len)
    iv1 = 0
    iv2 = 0
    for offset in range(0, input_len, 8):
        block = int.from_bytes(cipher[offset : offset + 8], "big")
        plain_block, iv1, iv2 = _decrypt_round(block, iv1, iv2, key)
        plain[offset : offset + 8] = plain_block.to_bytes(8, "big")

    pad_size = plain[0] & 0b111
    start = 1 + pad_size + _SALT_LEN
    end = input_len - _ZERO_LEN

    if any(plain[end:]):
        raise TcTeaError("invalid TC-TEA padding (sentinel bytes are not zero)")
    if start > end:
        raise TcTeaError("invalid TC-TEA framing (padding exceeds block size)")

    return bytes(plain[start:end])


def tc_tea_encrypt(plaintext: bytes, key: bytes, salt: bytes | None = None) -> bytes:
    """Encrypt ``plaintext`` into a TC-TEA blob.

    Only used by tests and tooling -- the converter never needs to encrypt.
    ``salt`` must supply at least ``1 + pad_len + 2`` bytes of header noise;
    it defaults to a deterministic pattern so encrypted output is reproducible.
    """
    _parse_key(key)
    length = _FIXED_PADDING_LEN + len(plaintext)
    pad_len = (8 - (length & 0b111)) & 0b111
    output_len = length + pad_len
    header_len = 1 + pad_len + _SALT_LEN

    if salt is None:
        salt = bytes(range(10))
    if len(salt) < header_len:
        raise TcTeaError("salt is too short for the chosen padding")

    header = bytearray(16)
    header[:header_len] = salt[:header_len]

    copy_len = min(16 - header_len, len(plaintext))
    body = plaintext[copy_len:]
    header[header_len : header_len + copy_len] = plaintext[:copy_len]
    header[0] = (header[0] & 0b1111_1000) | (pad_len & 0b111)

    blocks: list[bytes] = [bytes(header[:8]), bytes(header[8:16])]
    last_len = len(body) % 8
    whole, tail = body[: len(body) - last_len], body[len(body) - last_len :]
    for offset in range(0, len(whole), 8):
        blocks.append(whole[offset : offset + 8])
    if last_len:
        blocks.append(tail + bytes(8 - last_len))

    out = bytearray()
    iv1 = 0
    iv2 = 0
    for block in blocks:
        chunk, iv1, iv2 = _encrypt_round(int.from_bytes(block, "big"), iv1, iv2, key)
        out += chunk.to_bytes(8, "big")

    return bytes(out[:output_len])
