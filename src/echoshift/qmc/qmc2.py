"""QMC2 cipher -- QQ Music's ekey-based obfuscation.

QMC2 wraps the audio in one of two ciphers, chosen by the length of the
*decoded* key:

``<= 300 bytes``
    "Map" cipher -- a repeating XOR mask indexed by ``offset**2``.
``> 300 bytes``
    A modified RC4 stream cipher, re-keyed once per 5120-byte segment.

The ekey itself is a base64 string that may be further wrapped in Tencent's
TC-TEA ("EncV2" / "EncV1") layering, or may be a raw key handed back by the
QQ Music API.

References: jixunmoe/qmc2-rust, bczhc/qmc-decrypt, and the Rust port in
ownlight6/qmc-decoder ``src/qmc2.rs``.  The test vectors in
``tests/test_qmc2.py`` are the ones published alongside those sources.
"""

from __future__ import annotations

import base64
import math

from ._xor import xor_stream
from .tc_tea import TcTeaError, derive_tea_key, tc_tea_decrypt

__all__ = [
    "Qmc2Error",
    "Qmc2Crypto",
    "parse_ekey",
    "FIRST_SEGMENT_SIZE",
    "OTHER_SEGMENT_SIZE",
    "MAP_KEY_LIMIT",
]

#: Decoded keys at or below this length select the Map cipher.
MAP_KEY_LIMIT = 300

FIRST_SEGMENT_SIZE = 0x80
OTHER_SEGMENT_SIZE = 0x1400

_ENCV2_PREFIX = b"QQMusic EncV2,Key:"
_ENCV2_STAGE1_KEY = b"386ZJY!@#*$%^&)("
_ENCV2_STAGE2_KEY = b"**#!(#$%&^a1cZ,T"

_U64_MAX = 0xFFFFFFFFFFFFFFFF

#: ``mask_at`` reduces offsets above this before squaring.
_MAP_OFFSET_WRAP = 0x7FFF

#: Mask table size: ``mask_at`` only ever depends on the key index.
_MAP_CYCLE = 0x7FFF


class Qmc2Error(ValueError):
    """Raised when an ekey is unusable or a QMC2 payload is malformed."""


def _b64decode(text: str) -> bytes:
    cleaned = text.strip("\x00").strip()
    padding = (-len(cleaned)) % 4
    try:
        return base64.b64decode(cleaned + "=" * padding, validate=False)
    except Exception as exc:  # pragma: no cover - base64 raises binascii.Error
        raise Qmc2Error(f"ekey is not valid base64: {exc}") from exc


def parse_ekey(ekey: str) -> bytes:
    """Unwrap an ekey string down to the raw cipher key."""
    decoded = _b64decode(ekey)
    if not decoded:
        raise Qmc2Error("decoded ekey is empty")

    if decoded.startswith(_ENCV2_PREFIX):
        blob = decoded[len(_ENCV2_PREFIX) :]
        try:
            stage1 = tc_tea_decrypt(blob, _ENCV2_STAGE1_KEY)
            stage2 = tc_tea_decrypt(stage1, _ENCV2_STAGE2_KEY)
        except TcTeaError as exc:
            raise Qmc2Error(f"EncV2 ekey could not be unwrapped: {exc}") from exc
        decoded = _b64decode(stage2.decode("ascii", "ignore"))

    if len(decoded) < 8:
        raise Qmc2Error(f"decoded ekey is only {len(decoded)} bytes, need at least 8")

    header, body = decoded[:8], decoded[8:]
    if not body:
        return header

    try:
        plain_body = tc_tea_decrypt(body, derive_tea_key(header))
    except TcTeaError:
        # Not an EncV1 blob -- most likely a raw key from the QQ Music API.
        return decoded
    return header + plain_body


def _f64_to_u64_saturating(value: float) -> int:
    """Emulate Rust's ``f64 as u64`` (saturating, truncating, NaN -> 0)."""
    if math.isnan(value) or value <= 0:
        return 0
    if value >= float(_U64_MAX):
        return _U64_MAX
    return int(value)


def _scramble_by_index(value: int, index: int) -> int:
    """``(value << r) | (value >> r)`` truncated to a byte, ``r = (index+4)&7``."""
    rotation = (index + 4) & 0b111
    return ((value << rotation) & 0xFF) | (value >> rotation)


class Qmc2MapCrypto:
    """Short-key cipher: a key-derived XOR mask indexed by squared offset."""

    is_rc4 = False

    def __init__(self, key: bytes) -> None:
        if not key:
            raise Qmc2Error("map cipher needs a non-empty key")
        self.key = key
        self._cycle = self._build_cycle()

    def _index_at(self, offset: int) -> int:
        offset_local = offset
        if offset_local > _MAP_OFFSET_WRAP:
            offset_local %= _MAP_OFFSET_WRAP
        return (offset_local * offset_local + 71214) % len(self.key)

    def mask_at(self, offset: int) -> int:
        """Reference (per-byte) mask lookup, mirroring the Rust implementation."""
        index = self._index_at(offset)
        return _scramble_by_index(self.key[index], index)

    def _build_cycle(self) -> bytes:
        """Precompute the mask sequence for one full 0x7FFF period.

        ``mask_at(o)`` depends only on ``o % 0x7FFF`` for ``o > 0x7FFF``, so the
        stream is periodic apart from the single offset ``0x7FFF`` itself, where
        the Rust guard (``if offset > 0x7FFF``) leaves the value unreduced.
        """
        cycle = bytearray(_MAP_CYCLE)
        for offset in range(_MAP_CYCLE):
            index = (offset * offset + 71214) % len(self.key)
            cycle[offset] = _scramble_by_index(self.key[index], index)
        return bytes(cycle)

    def keystream(self, length: int) -> bytes:
        """Return the mask stream for ``length`` bytes starting at offset 0.

        Offsets ``0..0x7FFE`` walk the cycle directly.  ``0x7FFF`` is the one
        value the Rust guard leaves unreduced, and from ``0x8000`` on the
        reduced offsets restart at 1, so the cycle repeats rotated left by one.
        """
        if length <= 0:
            return b""
        cycle = self._cycle
        if length <= _MAP_CYCLE:
            return cycle[:length]

        head = cycle + bytes([self.mask_at(_MAP_CYCLE)])
        if length <= len(head):
            return head[:length]

        rotated = cycle[1:] + cycle[:1]
        tail_needed = length - len(head)
        repeats = tail_needed // _MAP_CYCLE + 2
        return head + (rotated * repeats)[:tail_needed]

    def decrypt(self, buf: bytearray, offset: int = 0) -> bytearray:
        """XOR ``buf`` in place with the mask stream at ``offset``."""
        return xor_stream(buf, self.keystream(offset + len(buf)), offset)


class Qmc2Rc4Crypto:
    """Long-key cipher: modified RC4, re-keyed once per 5120-byte segment."""

    is_rc4 = True

    def __init__(self, rc4_key: bytes) -> None:
        n = len(rc4_key)
        if n == 0:
            raise Qmc2Error("RC4 cipher needs a non-empty key")
        self.key = rc4_key
        self._n = n

        if n > 256:
            s = bytearray(i & 0xFF for i in range(n))
        else:
            # Mirrors ``(0..n as u8)``: a 256-byte key wraps to an empty range.
            s = bytearray(range(n if n < 256 else 0))

        j = 0
        for i in range(n):
            j = (j + s[i] + rc4_key[i]) % n
            s[i], s[j] = s[j], s[i]

        self._s = s
        self.hash = self._calc_hash_base(rc4_key)

    @staticmethod
    def _calc_hash_base(data: bytes) -> int:
        """Product of the key's non-zero bytes, stopping on overflow/wraparound."""
        hash_value = 1
        for value in data:
            if value == 0:
                continue
            next_hash = (hash_value * value) & 0xFFFFFFFF
            if next_hash == 0 or next_hash <= hash_value:
                break
            hash_value = next_hash
        return hash_value

    def _calc_segment_key(self, segment_id: int, seed: int) -> int:
        divisor = (segment_id + 1) * seed
        if divisor == 0:
            return _U64_MAX
        return _f64_to_u64_saturating((float(self.hash) / float(divisor)) * 100.0)

    def _xor_first_segment(self, offset: int, buf: bytearray, start: int, length: int) -> None:
        key = self.key
        n = self._n
        for i in range(length):
            key1 = key[offset % n]
            key2 = self._calc_segment_key(offset, key1)
            buf[start + i] ^= key[key2 % n]
            offset += 1

    def _xor_other_segment(self, offset: int, buf: bytearray, start: int, length: int) -> None:
        n = self._n
        segment_id = offset // OTHER_SEGMENT_SIZE
        # The reference indexes the key with ``segment_id & 0x1FF``; wrap it so
        # keys shorter than 512 bytes cannot run off the end.
        seed = self.key[(segment_id & 0x1FF) % n]
        discard = (self._calc_segment_key(segment_id, seed) & 0x1FF) + (
            offset % OTHER_SEGMENT_SIZE
        )

        s = bytearray(self._s)
        j = 0
        k = 0
        for _ in range(discard):
            j += 1
            if j >= n:
                j -= n
            k += s[j]
            if k >= n:
                k -= n
            s[j], s[k] = s[k], s[j]

        for i in range(length):
            j += 1
            if j >= n:
                j -= n
            k += s[j]
            if k >= n:
                k -= n
            s[j], s[k] = s[k], s[j]
            index = s[j] + s[k]
            if index >= n:
                index -= n
            buf[start + i] ^= s[index]

    def _xor_range(self, offset: int, buf: bytearray, start: int, length: int) -> None:
        """XOR ``length`` bytes starting at stream ``offset`` into ``buf``."""
        if length <= 0:
            return
        cursor = start
        remaining = length

        if offset < FIRST_SEGMENT_SIZE:
            take = min(remaining, FIRST_SEGMENT_SIZE - offset)
            self._xor_first_segment(offset, buf, cursor, take)
            cursor += take
            remaining -= take
            offset += take

        align = offset % OTHER_SEGMENT_SIZE
        if align != 0 and remaining > 0:
            take = min(remaining, OTHER_SEGMENT_SIZE - align)
            self._xor_other_segment(offset, buf, cursor, take)
            cursor += take
            remaining -= take
            offset += take

        while remaining > OTHER_SEGMENT_SIZE:
            self._xor_other_segment(offset, buf, cursor, OTHER_SEGMENT_SIZE)
            cursor += OTHER_SEGMENT_SIZE
            remaining -= OTHER_SEGMENT_SIZE
            offset += OTHER_SEGMENT_SIZE

        if remaining > 0:
            self._xor_other_segment(offset, buf, cursor, remaining)

    def keystream(self, length: int) -> bytes:
        """Return the keystream for ``length`` bytes (decrypting zeros)."""
        if length <= 0:
            return b""
        out = bytearray(length)
        self._xor_range(0, out, 0, length)
        return bytes(out)

    def decrypt(self, buf: bytearray, offset: int = 0) -> bytearray:
        """XOR ``buf`` in place with the RC4 stream at ``offset``."""
        self._xor_range(offset, buf, 0, len(buf))
        return buf


class Qmc2Crypto:
    """Facade that picks the Map or RC4 cipher for a given ekey."""

    def __init__(self, key: bytes) -> None:
        self.key = key
        if len(key) > MAP_KEY_LIMIT:
            self._impl: Qmc2MapCrypto | Qmc2Rc4Crypto = Qmc2Rc4Crypto(key)
        else:
            self._impl = Qmc2MapCrypto(key)

    @classmethod
    def from_ekey(cls, ekey: str) -> "Qmc2Crypto":
        return cls(parse_ekey(ekey))

    @property
    def is_rc4(self) -> bool:
        return self._impl.is_rc4

    def keystream(self, length: int) -> bytes:
        return self._impl.keystream(length)

    def decrypt(self, buf: bytearray, offset: int = 0) -> bytearray:
        return self._impl.decrypt(buf, offset)
