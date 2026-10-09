"""QMC container footer detection."""

from __future__ import annotations

import base64
import struct

from echoshift.qmc.footer import (
    FooterKind,
    detect_footer,
    read_utf16_le,
)

AUDIO = b"fLaC" + bytes(range(64))


def _tail_of(blob: bytes, limit: int = 1 << 20) -> tuple[bytes, int]:
    return (blob[-limit:] if len(blob) > limit else blob), len(blob)


# --------------------------------------------------------------------------- #
# UTF-16 helper
# --------------------------------------------------------------------------- #


def test_read_utf16_le_stops_at_the_terminator():
    data = b"\x00\x00" + "歌曲".encode("utf-16-le") + b"\x00\x00" + b"\xff" * 8
    assert read_utf16_le(data, 2, 64) == "歌曲"


def test_read_utf16_le_respects_max_len():
    payload = b"".join(ch.encode("utf-16-le") for ch in "abcdefgh")
    assert read_utf16_le(payload, 0, 4) == "ab"


def test_read_utf16_le_handles_empty_and_short_data():
    assert read_utf16_le(b"", 0, 10) == ""
    assert read_utf16_le(b"\x41", 0, 10) == ""


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #


def test_no_footer_for_a_plain_flac():
    blob = AUDIO + b"\x00" * 32
    tail, size = _tail_of(blob)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.NONE
    assert footer.audio_length is None


def test_v1_footer_reports_the_embedded_key():
    key = bytes(range(1, 17))
    blob = AUDIO + key + struct.pack("<I", len(key))
    tail, size = _tail_of(blob)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.V1
    assert footer.raw_key == key
    assert base64.b64decode(footer.ekey) == key
    assert footer.key_size == len(key)
    assert footer.audio_length == len(AUDIO)
    assert footer.embeds_key


def test_qtag_footer_extracts_ekey_and_song_id():
    meta = b"MYEKEY123,456789,extra"
    blob = AUDIO + meta + struct.pack(">I", len(meta)) + b"QTag"
    tail, size = _tail_of(blob)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.QTAG
    assert footer.ekey == "MYEKEY123"
    assert footer.song_id == "456789"
    assert footer.audio_length == len(AUDIO)


def test_qtag_footer_tolerates_a_missing_second_comma():
    meta = b"MYEKEY123,456789"
    blob = AUDIO + meta + struct.pack(">I", len(meta)) + b"QTag"
    tail, size = _tail_of(blob)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.QTAG
    assert footer.ekey == "MYEKEY123"
    assert footer.song_id == "456789"


def test_qtag_with_an_empty_ekey_is_not_trusted():
    meta = b",456789,extra"
    blob = AUDIO + meta + struct.pack(">I", len(meta)) + b"QTag"
    tail, size = _tail_of(blob)
    assert detect_footer(tail, size).kind is not FooterKind.QTAG


def test_musicex_footer_carries_no_key():
    mid = "001QuXxx0ABCDef".encode("utf-16-le") + b"\x00\x00"
    filename = "track.mflac".encode("utf-16-le") + b"\x00\x00"
    meta = bytearray(0x8C)
    meta[0x00:0x04] = struct.pack("<I", 424242)
    meta[0x0C : 0x0C + len(mid)] = mid
    meta[0x48 : 0x48 + len(filename)] = filename
    footer_size = len(meta) + 16
    blob = (
        AUDIO
        + bytes(meta)
        + struct.pack("<I", footer_size)
        + struct.pack("<I", 1)
        + b"musicex\x00"
    )
    tail, size = _tail_of(blob)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.MUSICEX
    assert footer.song_id == "424242"
    assert footer.mid == "001QuXxx0ABCDef"
    assert footer.filename == "track.mflac"
    assert footer.ekey is None
    assert not footer.embeds_key
    assert footer.audio_length == len(AUDIO)


def test_musicex_with_a_bad_version_is_ignored():
    blob = AUDIO + bytes(0x80) + struct.pack("<I", 0x90) + struct.pack("<I", 7) + b"musicex\x00"
    tail, size = _tail_of(blob)
    assert detect_footer(tail, size).kind is not FooterKind.MUSICEX


def test_tiny_files_are_rejected():
    assert detect_footer(b"", 0).kind is FooterKind.NONE
    assert detect_footer(b"abc", 3).kind is FooterKind.NONE


def test_absurd_key_size_is_not_treated_as_v1():
    blob = AUDIO + struct.pack("<I", 0x7FFF_FFFF)
    tail, size = _tail_of(blob)
    assert detect_footer(tail, size).kind is FooterKind.NONE


def test_detection_works_from_a_truncated_tail():
    """Only the end of the file is needed, so huge files stay cheap to inspect."""
    key = bytes(range(1, 33))
    blob = AUDIO + key + struct.pack("<I", len(key))
    tail, size = _tail_of(blob, limit=64)
    footer = detect_footer(tail, size)
    assert footer.kind is FooterKind.V1
    assert footer.audio_length == len(AUDIO)
