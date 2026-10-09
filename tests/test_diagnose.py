"""The ``--diagnose`` forensic dump.

The point of this command is to identify a container's real layout from its
bytes instead of from documentation, so the tests pin down what it reports for
each layout we can construct.
"""

from __future__ import annotations

import struct
from pathlib import Path

from echoshift.qmc.diagnose import diagnose, render
from echoshift.qmc.footer import FooterKind
from echoshift.qmc.keystore import KeyStore
from echoshift.qmc.qmc2 import Qmc2Crypto

from .conftest import MFLAC_QTAG, MFLAC_RC4, QMC1, STANDARD


def test_v1_container_is_reported_as_decryptable(samples: Path):
    result = diagnose(samples / MFLAC_RC4, KeyStore())
    assert result.footer.kind is FooterKind.V1
    assert result.ekey_present
    assert result.decrypted_head_sniff == "flac"
    assert "正确" in result.verdict


def test_qtag_container_is_reported_as_decryptable(samples: Path):
    result = diagnose(samples / MFLAC_QTAG, KeyStore())
    assert result.footer.kind is FooterKind.QTAG
    assert result.ekey_present
    assert result.decrypted_head_sniff == "flac"
    assert any(marker == b"QTag" for marker, _ in result.markers)


def test_qmc1_container_needs_no_key(samples: Path):
    result = diagnose(samples / QMC1, KeyStore())
    assert result.ekey_present is False
    assert "QMC1" in (result.cipher or "")
    assert result.decrypted_head_sniff == "flac"


def test_missing_key_is_explained(tmp_path: Path):
    """A container with no footer at all must say so, and say why."""
    audio = b"fLaC" + bytes(4096)
    target = tmp_path / "keyless.mflac"
    target.write_bytes(audio + bytes(200))

    result = diagnose(target, KeyStore())
    assert result.footer.kind is FooterKind.NONE
    assert result.ekey_present is False
    assert "无法解密" in result.verdict
    assert any("不在文件里" in hint for hint in result.hints)
    # The hex dump must still be present: that is what identifies the layout.
    assert result.tail


def test_musicex_container_explains_the_missing_ekey(tmp_path: Path):
    meta = bytearray(0x8C)
    meta[0x00:0x04] = struct.pack("<I", 4242)
    footer_size = len(meta) + 16
    blob = (
        b"fLaC"
        + bytes(4096)
        + bytes(meta)
        + struct.pack("<I", footer_size)
        + struct.pack("<I", 1)
        + b"musicex\x00"
    )
    target = tmp_path / "locked.mflac"
    target.write_bytes(blob)

    result = diagnose(target, KeyStore())
    assert result.footer.kind is FooterKind.MUSICEX
    assert result.ekey_present is False
    assert any("19.57" in hint for hint in result.hints)
    assert any("重新下载" in hint for hint in result.hints)


def test_a_wrong_key_is_reported_as_such(tmp_path: Path):
    payload = bytearray((b"fLaC" + bytes(range(256)) * 40))
    real_key = bytes((i * 31 + 5) & 0xFF for i in range(512))
    wrong_key = bytes((i * 13 + 200) & 0xFF for i in range(512))
    Qmc2Crypto(real_key).decrypt(payload, 0)

    target = tmp_path / "evil.mflac"
    target.write_bytes(bytes(payload) + wrong_key + struct.pack("<I", len(wrong_key)))

    result = diagnose(target, KeyStore())
    assert result.ekey_present
    assert result.decrypted_head_sniff is None
    assert "不是已知音频签名" in result.verdict
    assert result.decrypted_head is not None


def test_plain_flac_named_mflac_is_detected(samples: Path):
    """A .mflac that is already decrypted should be reported, not decrypted."""
    result = diagnose(samples / STANDARD, KeyStore())
    assert result.head_sniff == "flac"


def test_render_contains_the_evidence(samples: Path):
    text = render(diagnose(samples / MFLAC_QTAG, KeyStore()))
    for expected in ("文件头", "尾部", "尾部类型", "ekey", "选定算法", "结论"):
        assert expected in text, expected
    # The tail dump is what lets a failing file be identified remotely.
    assert "QTag" in text
