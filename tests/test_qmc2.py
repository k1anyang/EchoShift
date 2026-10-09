"""QMC2 cipher tests.

The reference vectors (``test_map_l``, ``test_map_l_boundary``,
``test_rc4_hash_base``, ``test_rc4_first_segment``, ``test_parse_ekey``) are
copied from ownlight6/qmc-decoder's Rust suite, which in turn derives from
jixunmoe/qmc2-rust.  They pin this port to the published behaviour.
"""

from __future__ import annotations

import pytest

from echoshift.qmc.qmc2 import (
    OTHER_SEGMENT_SIZE,
    Qmc2Crypto,
    Qmc2Error,
    Qmc2MapCrypto,
    Qmc2Rc4Crypto,
    parse_ekey,
)

#: Key used by the upstream map-cipher vectors.
MAP_TEST_KEY = bytes(range(0x41, 0x51))


# --------------------------------------------------------------------------- #
# ekey parsing
# --------------------------------------------------------------------------- #


def test_parse_ekey_unwraps_encv1_blob():
    ekey = "VGhpcyBpcyBHFWEh4cjZ1Vi7rJ56XeoPlqGM1sxBGPg7mt89umKclFBr9iqfmFdS"
    assert parse_ekey(ekey) == b"This is a test key for test purpose :D"


def test_parse_ekey_rejects_empty_payload():
    with pytest.raises(Qmc2Error):
        parse_ekey("")


def test_parse_ekey_tolerates_trailing_nul_bytes():
    ekey = "VGhpcyBpcyBHFWEh4cjZ1Vi7rJ56XeoPlqGM1sxBGPg7mt89umKclFBr9iqfmFdS"
    assert parse_ekey(ekey + "\x00\x00") == b"This is a test key for test purpose :D"


def test_crypto_facade_selects_cipher_by_key_length():
    assert Qmc2Crypto(b"A" * 300).is_rc4 is False
    assert Qmc2Crypto(b"A" * 301).is_rc4 is True


# --------------------------------------------------------------------------- #
# Map cipher
# --------------------------------------------------------------------------- #


def test_map_cipher_reference_vector_at_offset_zero():
    crypto = Qmc2MapCrypto(MAP_TEST_KEY)
    buf = bytearray(16)
    crypto.decrypt(buf, 0)
    assert bytes(buf) == bytes([
        0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A,
        0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A,
    ])


def test_map_cipher_reference_vector_straddling_0x7fff():
    crypto = Qmc2MapCrypto(MAP_TEST_KEY)
    buf = bytearray(16)
    crypto.decrypt(buf, 0x7FFF - 8)
    assert bytes(buf) == bytes([
        0x8A, 0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1,
        0x8A, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A,
    ])


@pytest.mark.parametrize("key_len", [1, 2, 16, 127, 300])
def test_map_keystream_matches_naive_lookup(key_len):
    """The precomputed fast path must agree with the per-byte reference."""
    crypto = Qmc2MapCrypto(bytes((i * 37 + 11) & 0xFF for i in range(key_len)))
    span = 0x20000
    fast = crypto.keystream(span)
    assert len(fast) == span
    for offset in range(span):
        assert fast[offset] == crypto.mask_at(offset), f"diverged at offset {offset}"


def test_map_keystream_agrees_far_beyond_the_first_period():
    crypto = Qmc2MapCrypto(MAP_TEST_KEY)
    probes = [0x2_0000, 0x5_0000, 0x7_FFFE, 0x7_FFFF, 0x8_0000, 0x10_0000, 0x40_0001]
    for offset in probes:
        block = crypto.keystream(offset + 64)[offset:]
        for i, byte in enumerate(block):
            assert byte == crypto.mask_at(offset + i), f"diverged at offset {offset + i}"


def test_map_decrypt_is_its_own_inverse():
    crypto = Qmc2MapCrypto(MAP_TEST_KEY)
    payload = bytes((i * 13 + 5) & 0xFF for i in range(0x10000 + 123))
    once = bytes(crypto.decrypt(bytearray(payload), 0))
    assert once != payload
    assert bytes(crypto.decrypt(bytearray(once), 0)) == payload


def test_map_decrypt_respects_stream_offset():
    crypto = Qmc2MapCrypto(MAP_TEST_KEY)
    full = crypto.keystream(0x9000)
    chunk = bytes(crypto.decrypt(bytearray(0x1000), 0x8000))
    assert chunk == full[0x8000:0x9000]


# --------------------------------------------------------------------------- #
# RC4 cipher
# --------------------------------------------------------------------------- #


def test_rc4_hash_base_reference_vectors():
    assert Qmc2Rc4Crypto._calc_hash_base(bytes([1, 99])) == 1
    assert Qmc2Rc4Crypto._calc_hash_base(bytes([0xFF] * 16)) == 0xFC05FC01


def test_rc4_first_segment_reference_vector():
    key = bytes(i & 0xFF for i in range(255))
    crypto = Qmc2Rc4Crypto(key)
    buf = bytearray(16)
    crypto.decrypt(buf, 0)
    assert bytes(buf) == bytes([0, 50, 16, 8, 5, 3, 2, 1, 1, 1, 0, 0, 0, 0, 0, 0])


@pytest.mark.parametrize("key_len", [301, 302, 384, 512, 700])
def test_rc4_decrypt_is_its_own_inverse(key_len):
    key = bytes((i * 53 + 7) & 0xFF for i in range(key_len))
    crypto = Qmc2Rc4Crypto(key)
    # Two full segments plus a partial one, so both segment algorithms run.
    payload = bytes((i * 29 + 3) & 0xFF for i in range(OTHER_SEGMENT_SIZE * 2 + 777))
    once = bytes(crypto.decrypt(bytearray(payload), 0))
    assert once != payload
    assert bytes(crypto.decrypt(bytearray(once), 0)) == payload


def test_rc4_keystream_is_offset_addressed():
    """Decrypting a slice must match the same window of the full stream."""
    key = bytes((i * 17 + 1) & 0xFF for i in range(512))
    crypto = Qmc2Rc4Crypto(key)
    span = OTHER_SEGMENT_SIZE * 2 + 1000
    full = crypto.keystream(span)
    for start, length in [(0, 64), (100, 200), (OTHER_SEGMENT_SIZE - 5, 40),
                          (OTHER_SEGMENT_SIZE, 300), (span - 50, 50)]:
        window = bytes(crypto.decrypt(bytearray(length), start))
        assert window == full[start:start + length], f"mismatch at {start}+{length}"


def test_rc4_rejects_empty_key():
    with pytest.raises(Qmc2Error):
        Qmc2Rc4Crypto(b"")


def test_map_rejects_empty_key():
    with pytest.raises(Qmc2Error):
        Qmc2MapCrypto(b"")
