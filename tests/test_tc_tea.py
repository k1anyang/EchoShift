"""TC-TEA test vectors.

The first four cases are the vectors published with jixunmoe's ``tc_tea`` Rust
crate, so they pin this port to the reference implementation.
"""

from __future__ import annotations

import pytest

from echoshift.qmc.tc_tea import (
    TcTeaError,
    derive_tea_key,
    simple_make_key,
    tc_tea_decrypt,
    tc_tea_encrypt,
    tea_decrypt_block,
    tea_encrypt_block,
)

ENCRYPTION_KEY = bytes([0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38,
                        0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48])

GOOD_ENCRYPTED_DATA = bytes([
    0x91, 0x09, 0x51, 0x62, 0xE3, 0xF5, 0xB6, 0xDC,
    0x6B, 0x41, 0x4B, 0x50, 0xD1, 0xA5, 0xB8, 0x4E,
    0xC5, 0x0D, 0x0C, 0x1B, 0x11, 0x96, 0xFD, 0x3C,
])

EXPECTED_PLAIN_TEXT = bytes([1, 2, 3, 4, 5, 6, 7, 8])


def test_simple_make_key_matches_reference_schedule():
    assert simple_make_key(106, 8) == bytes([0x69, 0x56, 0x46, 0x38, 0x2B, 0x20, 0x15, 0x0B])


def test_derive_tea_key_interleaves_schedule_with_header():
    header = bytes([0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8])
    assert derive_tea_key(header) == bytes([
        0x69, 0xF1, 0x56, 0xF2, 0x46, 0xF3, 0x38, 0xF4,
        0x2B, 0xF5, 0x20, 0xF6, 0x15, 0xF7, 0x0B, 0xF8,
    ])


def test_derive_tea_key_rejects_wrong_header_size():
    with pytest.raises(TcTeaError):
        derive_tea_key(b"short")


def test_tea_ecb_block_roundtrip_matches_reference_vector():
    key = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
                 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x00])
    cipher = tea_encrypt_block(EXPECTED_PLAIN_TEXT, key)
    assert cipher == bytes([0x56, 0x27, 0x6B, 0xA9, 0x80, 0xB9, 0xEC, 0x16])
    assert tea_decrypt_block(cipher, key) == EXPECTED_PLAIN_TEXT


def test_cbc_decrypt_matches_reference_vector():
    plain = tc_tea_decrypt(GOOD_ENCRYPTED_DATA, ENCRYPTION_KEY)
    assert plain == EXPECTED_PLAIN_TEXT


def test_cbc_decrypt_rejects_corrupted_sentinel():
    corrupted = bytearray(GOOD_ENCRYPTED_DATA)
    corrupted[-1] ^= 0xFF
    with pytest.raises(TcTeaError):
        tc_tea_decrypt(bytes(corrupted), ENCRYPTION_KEY)


def test_cbc_rejects_non_multiple_of_eight():
    with pytest.raises(TcTeaError):
        tc_tea_decrypt(b"\x00" * 12, ENCRYPTION_KEY)


def test_cbc_rejects_short_key():
    with pytest.raises(TcTeaError):
        tc_tea_decrypt(GOOD_ENCRYPTED_DATA, b"tooshort")


@pytest.mark.parametrize("length", [0, 1, 7, 8, 9, 15, 16, 33, 64, 127])
def test_encrypt_decrypt_roundtrip_various_lengths(length):
    payload = bytes((i * 7 + 3) & 0xFF for i in range(length))
    blob = tc_tea_encrypt(payload, ENCRYPTION_KEY)
    assert len(blob) % 8 == 0
    assert tc_tea_decrypt(blob, ENCRYPTION_KEY) == payload
