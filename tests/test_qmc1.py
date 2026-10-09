"""QMC1 cipher tests."""

from __future__ import annotations

from echoshift.qmc.qmc1 import (
    KEY_TABLE,
    decrypt,
    decrypt_in_place,
    decrypt_range,
    keystream,
    mask_at,
)


def test_key_table_is_the_documented_64_bytes():
    assert len(KEY_TABLE) == 64
    assert KEY_TABLE[0] == 0xC3
    assert KEY_TABLE[1] == 0x4A
    assert KEY_TABLE[-1] == 0xF3


def test_first_masks_match_reference():
    assert mask_at(0) == 0xC3
    assert mask_at(1) == 0x4A


def test_mask_is_periodic_with_0x7fff():
    for offset in range(0, 4096):
        assert mask_at(offset) == mask_at(offset + 0x7FFF)
        assert mask_at(offset) == mask_at(offset + 0x7FFF * 2)


def test_mask_reflects_above_0x3f():
    # Indices 0x40..0x7F fold back through ``(0x80 - index) & 0x3F``, which
    # mirrors around 0x40 and maps 0x40 itself onto slot 0.
    assert mask_at(0x3F) == KEY_TABLE[0x3F]
    assert mask_at(0x40) == KEY_TABLE[0x00]
    assert mask_at(0x41) == KEY_TABLE[0x3F]
    assert mask_at(0x7F) == KEY_TABLE[0x01]
    assert mask_at(0x50) == KEY_TABLE[0x30]


def test_decrypt_is_its_own_inverse():
    original = b"Hello, World! This is a test of QMC1 decryption."
    once = decrypt(original)
    assert once != original
    assert decrypt(once) == original


def test_decrypt_in_place_honours_offset():
    buffer = bytearray(b"\x00" * 16)
    decrypt_in_place(buffer, offset=0x100)
    reference = bytes(mask_at(0x100 + i) for i in range(16))
    assert bytes(buffer) == reference


def test_decrypt_crosses_segment_boundaries_without_drift():
    size = 0x10000 + 37
    data = bytes((i * 31 + 7) & 0xFF for i in range(size))
    assert decrypt(decrypt(data)) == data


def test_keystream_matches_per_byte_reference():
    """The precomputed cycle must reproduce ``mask_at`` exactly."""
    window = keystream(0x10000 + 5)
    for offset in range(0x10000 + 5):
        assert window[offset] == mask_at(offset), f"diverged at offset {offset}"


def test_keystream_honours_offset_and_repeats():
    chunk = keystream(32, offset=0x7FF0)
    assert chunk == bytes(mask_at(0x7FF0 + i) for i in range(32))
    # Offsets past the first period keep matching the reference.
    far = keystream(16, offset=0x3_0000 - 4)
    assert far == bytes(mask_at(0x3_0000 - 4 + i) for i in range(16))


def test_decrypt_range_matches_full_decryption():
    size = 0x20000
    data = bytes((i * 11 + 3) & 0xFF for i in range(size))
    full = bytearray(decrypt(data))
    for offset, length in [(0, 100), (0x7FF0, 64), (0x8000, 4096), (size - 10, 10)]:
        piece = bytearray(data[offset : offset + length])
        decrypt_range(piece, offset, length)
        assert bytes(piece) == bytes(full[offset : offset + length])
