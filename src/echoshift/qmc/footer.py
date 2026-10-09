"""Detection of the trailing metadata QMC2 files carry.

Three layouts exist, distinguished by what sits at the very end of the file:

``V1``
    4 raw key bytes followed by a little-endian u32 key size.
``QTag``
    ``<ekey>,<song_id>,…`` followed by a big-endian u32 length and ``QTag``.
``musicex``
    Song id / mid / original filename, with **no** key -- this is the modern
    layout (QQ Music >= 19.57) and it needs an external ekey.

Ported from ownlight6/qmc-decoder ``detect_footer`` in ``src/lib.rs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["FooterKind", "Footer", "detect_footer", "read_utf16_le", "MAX_KEY_SIZE"]

MAX_KEY_SIZE = 0x400
_QTAG_MAGIC = 0x6761_5451  # b"QTag" read as a little-endian u32
_MUSICEX_MAGIC = b"musicex\x00"


class FooterKind(str, Enum):
    NONE = "none"
    V1 = "v1"
    QTAG = "qtag"
    MUSICEX = "musicex"

    @property
    def label(self) -> str:
        return {
            FooterKind.NONE: "无尾部元数据",
            FooterKind.V1: "QMC2 v1（密钥内嵌）",
            FooterKind.QTAG: "QTag（ekey 内嵌）",
            FooterKind.MUSICEX: "musicex（需外部 ekey）",
        }[self]

    @property
    def short_label(self) -> str:
        """Compact form for the queue list, where width is scarce."""
        return {
            FooterKind.NONE: "无尾部",
            FooterKind.V1: "QMC2 v1",
            FooterKind.QTAG: "QTag",
            FooterKind.MUSICEX: "musicex",
        }[self]


@dataclass(frozen=True)
class Footer:
    """What was found at the end of a QMC container."""

    kind: FooterKind = FooterKind.NONE
    #: Number of leading bytes that are actual audio payload.
    audio_length: int | None = None
    ekey: str | None = None
    #: Every plausible reading of the trailer, best first.  The decoder tries
    #: them in order and keeps the one that actually decrypts.
    ekey_candidates: tuple[str, ...] = ()
    song_id: str | None = None
    mid: str | None = None
    filename: str | None = None
    key_size: int | None = None
    #: Raw trailer bytes, present only for the V1 layout.
    raw_key: bytes | None = None

    @property
    def embeds_key(self) -> bool:
        return self.ekey is not None or self.raw_key is not None

    def describe(self) -> str:
        bits = [self.kind.label]
        if self.mid:
            bits.append(f"mid={self.mid}")
        if self.song_id:
            bits.append(f"song_id={self.song_id}")
        if self.filename:
            bits.append(f"原文件名={self.filename}")
        return " · ".join(bits)


def _v1_ekey_candidates(trailer: bytes) -> tuple[str, ...]:
    """Read a V1 trailer, which real files lay out two different ways.

    QQ Music's actual ``.mflac`` files put an **ASCII base64 ekey** here, padded
    or terminated with NUL bytes -- the text is already everything
    :func:`~echoshift.qmc.qmc2.parse_ekey` wants.  Some tooling instead treats
    these bytes as the *raw* cipher key and base64-encodes them; both readings
    are offered, ASCII first, and the decoder keeps whichever decrypts.
    """
    candidates: list[str] = []

    stripped = trailer.rstrip(b"\x00")
    if stripped and all(32 <= byte < 127 for byte in stripped):
        candidates.append(stripped.decode("ascii"))

    import base64

    encoded = base64.b64encode(trailer).decode("ascii")
    if encoded not in candidates:
        candidates.append(encoded)
    return tuple(candidates)


def _u32le(data: bytes) -> int:
    return int.from_bytes(data[:4], "little")


def _u32be(data: bytes) -> int:
    return int.from_bytes(data[:4], "big")


def read_utf16_le(data: bytes, offset: int, max_len: int) -> str:
    """Read a NUL-terminated UTF-16LE string from ``data`` at ``offset``."""
    end = min(offset + max_len, len(data))
    codes: list[int] = []
    i = offset
    while i + 1 < end:
        code = int.from_bytes(data[i : i + 2], "little")
        if code == 0:
            break
        codes.append(code)
        i += 2
    return bytes(
        b"".join(code.to_bytes(2, "little") for code in codes)
    ).decode("utf-16-le", errors="replace")


def detect_footer(tail: bytes, total_size: int) -> Footer:
    """Classify a QMC container from the last bytes of the file.

    ``tail`` must be the *end* of the file; ``total_size`` is the full file
    size so absolute offsets can be reported.
    """
    length = len(tail)
    if length < 8 or total_size < 8:
        return Footer(FooterKind.NONE)

    def last(count: int) -> bytes | None:
        return tail[length - count :] if count <= length else None

    # ---------------------------------------------------------------- musicex
    if length >= 16 and tail[length - 8 :] == _MUSICEX_MAGIC:
        version = _u32le(tail[length - 12 : length - 8])
        footer_size = _u32le(tail[length - 16 : length - 12])
        metadata_size = max(0, footer_size - 16)
        if version == 1 and metadata_size > 0 and metadata_size <= total_size - 16:
            if metadata_size + 16 <= length:
                meta = tail[length - 16 - metadata_size : length - 16]
                song_id_value = _u32le(meta[:4]) if len(meta) > 4 else 0
                return Footer(
                    kind=FooterKind.MUSICEX,
                    audio_length=total_size - footer_size,
                    song_id=str(song_id_value),
                    mid=read_utf16_le(meta, 0x0C, 60),
                    filename=read_utf16_le(meta, 0x48, 68),
                )

    # ------------------------------------------------------------------- QTag
    last4 = last(4)
    if last4 is not None and _u32le(last4) == _QTAG_MAGIC:
        size_bytes = last(8)
        if size_bytes is not None:
            meta_size = _u32be(size_bytes[:4])
            if 0 < meta_size <= length - 8:
                meta = tail[length - 8 - meta_size : length - 8]
                first = meta.find(b",")
                if first != -1:
                    ekey = meta[:first].decode("utf-8", "replace")
                    rest = meta[first + 1 :]
                    if not ekey:
                        return Footer(FooterKind.NONE)
                    # Real files carry "<ekey>,<song id>,<extra>", but accept a
                    # single comma too rather than rejecting a usable key.
                    second = rest.find(b",")
                    song_id_text = (rest if second == -1 else rest[:second]).decode(
                        "utf-8", "replace"
                    )
                    return Footer(
                        kind=FooterKind.QTAG,
                        audio_length=total_size - 8 - meta_size,
                        ekey=ekey,
                        song_id=song_id_text,
                    )

    # --------------------------------------------------------------------- V1
    if last4 is not None:
        key_size = _u32le(last4)
        if 0 < key_size <= MAX_KEY_SIZE and length >= 4 + key_size:
            trailer = tail[length - 4 - key_size : length - 4]
            candidates = _v1_ekey_candidates(trailer)
            return Footer(
                kind=FooterKind.V1,
                audio_length=total_size - 4 - key_size,
                ekey=candidates[0] if candidates else None,
                ekey_candidates=candidates,
                key_size=key_size,
                raw_key=trailer,
            )

    return Footer(FooterKind.NONE)
