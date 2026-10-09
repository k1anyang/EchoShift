"""Container inspection and stream decryption against real fixtures."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from echoshift.errors import DecryptionError, UnsupportedInputError
from echoshift.qmc.decoder import (
    QMC_FORMATS,
    VIDEO_CONTAINER_EXTENSIONS,
    format_for_path,
    inspect,
    is_qmc_path,
    is_supported_path,
    looks_like_audio,
    prepare_input,
    sniff_audio,
)
from echoshift.qmc.footer import FooterKind
from echoshift.qmc.keystore import KeyStore

from .conftest import MFLAC_MAP, MFLAC_QTAG, MFLAC_RC4, MFLAC_V1TEXT, QMC1, STANDARD


# --------------------------------------------------------------------------- #
# mapping helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,is_qmc1,audio_ext",
    [
        ("a.qmcflac", True, "flac"),
        ("a.qmc0", True, "mp3"),
        ("a.qmc3", True, "mp3"),
        ("a.qmcogg", True, "ogg"),
        ("a.mflac", False, "flac"),
        ("a.mflac0", False, "flac"),
        ("a.mflach", False, "flac"),
        ("a.mgg", False, "ogg"),
        ("a.mgg1", False, "ogg"),
    ],
)
def test_container_mapping(name, is_qmc1, audio_ext):
    fmt = format_for_path(name)
    assert fmt is not None
    assert fmt.is_qmc1 is is_qmc1
    assert fmt.audio_extension == audio_ext


def test_extension_matching_is_case_insensitive():
    assert format_for_path(Path("SONG.MFLAC")).extension == "mflac"
    assert is_qmc_path("song.QMCFLAC")
    assert not is_qmc_path("song.flac")


def test_all_container_formats_have_labels():
    for fmt in QMC_FORMATS.values():
        assert fmt.label
        assert fmt.cipher_name in ("QMC1", "QMC2")


@pytest.mark.parametrize(
    "path,supported",
    [("a.flac", True), ("a.mflac", True), ("a.mp3", True), ("a.txt", False), ("a", False)],
)
def test_supported_extension_filter(path, supported):
    assert is_supported_path(path) is supported


@pytest.mark.parametrize(
    "head,expected",
    [
        (b"fLaC\x00\x00\x00\x22", "flac"),
        (b"OggS\x00\x02", "ogg"),
        (b"ID3\x04\x00", "mp3"),
        (b"RIFF\x00\x00\x00\x00WAVE", "wav"),
        (b"\xff\xfb\x90\x00", "mp3"),
        (b"\x00\x01\x02\x03", None),
        (b"", None),
    ],
)
def test_sniff_audio(head, expected):
    assert sniff_audio(head) == expected


# --------------------------------------------------------------------------- #
# inspection
# --------------------------------------------------------------------------- #


def test_inspect_qmc1_needs_no_key(samples: Path):
    info = inspect(samples / QMC1)
    assert info.format.is_qmc1
    assert not info.needs_ekey
    assert info.ekey is None


def test_inspect_mflac_v1_footer_supplies_the_key(samples: Path):
    for name in (MFLAC_MAP, MFLAC_RC4):
        info = inspect(samples / name)
        assert info.format.is_qmc1 is False
        assert info.footer.kind is FooterKind.V1
        assert info.ekey
        assert not info.needs_ekey


def test_inspect_rejects_a_non_container(tmp_path: Path):
    plain = tmp_path / "song.flac"
    plain.write_bytes(b"fLaC")
    with pytest.raises(UnsupportedInputError):
        inspect(plain)


def test_inspect_rejects_an_empty_container(tmp_path: Path):
    empty = tmp_path / "song.mflac"
    empty.write_bytes(b"")
    with pytest.raises(UnsupportedInputError):
        inspect(empty)


def test_inspect_detects_an_already_decrypted_container(samples: Path, tmp_path: Path):
    """A .mflac whose bytes are plain FLAC should pass straight through."""
    mislabelled = tmp_path / "renamed.mflac"
    mislabelled.write_bytes((samples / STANDARD).read_bytes())
    info = inspect(mislabelled)
    assert info.already_plain
    assert not info.needs_ekey


def test_inspect_reports_missing_key_for_musicex(tmp_path: Path):
    audio = b"fLaC" + bytes(64)
    meta = bytearray(0x8C)
    meta[0x00:0x04] = struct.pack("<I", 7)
    footer_size = len(meta) + 16
    blob = (
        audio
        + bytes(meta)
        + struct.pack("<I", footer_size)
        + struct.pack("<I", 1)
        + b"musicex\x00"
    )
    path = tmp_path / "locked.mflac"
    path.write_bytes(blob)

    info = inspect(path)
    assert info.footer.kind is FooterKind.MUSICEX
    assert info.needs_ekey

    with pytest.raises(DecryptionError) as excinfo:
        prepare_input(path, tmp_path, keystore=KeyStore())
    message = str(excinfo.value)
    assert "ekey" in message
    assert "同名 .ekey" in message


# --------------------------------------------------------------------------- #
# decryption
# --------------------------------------------------------------------------- #


def _decrypt_bytes(source: Path, work: Path, keystore: KeyStore) -> bytes:
    with prepare_input(source, work, keystore=keystore) as prepared:
        assert prepared.temporary
        return prepared.path.read_bytes()


def test_qmc1_roundtrip_restores_the_original_flac(samples: Path, work_dir: Path):
    assert _decrypt_bytes(samples / QMC1, work_dir, KeyStore()) == (
        samples / STANDARD
    ).read_bytes()


def test_mflac_map_roundtrip_restores_the_original_flac(samples: Path, work_dir: Path):
    assert _decrypt_bytes(samples / MFLAC_MAP, work_dir, KeyStore()) == (
        samples / STANDARD
    ).read_bytes()


def test_mflac_rc4_roundtrip_restores_the_original_flac(samples: Path, work_dir: Path):
    assert _decrypt_bytes(samples / MFLAC_RC4, work_dir, KeyStore()) == (
        samples / STANDARD
    ).read_bytes()


def test_prepare_input_passes_plain_files_through(samples: Path, work_dir: Path):
    with prepare_input(samples / STANDARD, work_dir, keystore=KeyStore()) as prepared:
        assert not prepared.temporary
        assert prepared.path == samples / STANDARD


def test_prepare_input_cleans_up_its_temp_file(samples: Path, work_dir: Path):
    with prepare_input(samples / MFLAC_MAP, work_dir, keystore=KeyStore()) as prepared:
        temp = prepared.path
        assert temp.is_file()
    assert not temp.exists()


def test_temp_files_do_not_reuse_the_source_name(samples: Path, work_dir: Path):
    with prepare_input(samples / MFLAC_MAP, work_dir, keystore=KeyStore()) as prepared:
        assert prepared.original == samples / MFLAC_MAP
        assert prepared.path != prepared.original
        assert prepared.path.suffix == ".flac"


def test_a_wrong_key_is_detected_instead_of_writing_garbage(
    samples: Path, tmp_path: Path, work_dir: Path
):
    """Encrypt with one key, advertise another: decryption must fail loudly."""
    from echoshift.qmc.qmc2 import Qmc2Crypto

    wrong_key = bytes((i * 13 + 200) & 0xFF for i in range(128))
    payload = bytearray((samples / STANDARD).read_bytes())
    Qmc2Crypto(bytes((i * 7 + 11) & 0xFF for i in range(128))).decrypt(payload, 0)
    target = tmp_path / "evil.mflac"
    target.write_bytes(bytes(payload) + wrong_key + struct.pack("<I", len(wrong_key)))

    with pytest.raises(DecryptionError) as excinfo:
        prepare_input(target, work_dir, keystore=KeyStore())
    assert "ekey 很可能不正确" in str(excinfo.value)


def test_temp_file_is_removed_when_decryption_fails(
    samples: Path, tmp_path: Path, work_dir: Path
):
    from echoshift.qmc.qmc2 import Qmc2Crypto

    wrong_key = bytes((i * 13 + 200) & 0xFF for i in range(128))
    payload = bytearray((samples / STANDARD).read_bytes())
    Qmc2Crypto(bytes((i * 7 + 11) & 0xFF for i in range(128))).decrypt(payload, 0)
    target = tmp_path / "evil2.mflac"
    target.write_bytes(bytes(payload) + wrong_key + struct.pack("<I", len(wrong_key)))

    before = set(work_dir.glob("echoshift_*"))
    with pytest.raises(DecryptionError):
        prepare_input(target, work_dir, keystore=KeyStore())
    assert set(work_dir.glob("echoshift_*")) == before


def test_embedded_key_wins_over_a_bogus_explicit_one(samples: Path, work_dir: Path):
    """A container that carries its own key must not be broken by --ekey."""
    from tools.make_samples import RC4_KEY, build_ekey_for_key

    with prepare_input(
        samples / MFLAC_MAP,
        work_dir,
        keystore=KeyStore(),
        ekey=build_ekey_for_key(RC4_KEY),
    ) as prepared:
        assert prepared.path.read_bytes() == (samples / STANDARD).read_bytes()


def test_decrypt_progress_is_reported(samples: Path, work_dir: Path):
    seen: list[float] = []
    with prepare_input(
        samples / MFLAC_MAP,
        work_dir,
        keystore=KeyStore(),
        on_progress=seen.append,
        chunk_size=4096,
    ):
        pass
    assert seen
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# the QTag footer -- a separate parser from the V1 one, so it needs its own
# end-to-end coverage rather than relying on the footer unit tests
# --------------------------------------------------------------------------- #


def test_qtag_container_is_recognised(samples: Path):
    info = inspect(samples / MFLAC_QTAG)
    assert info.footer.kind is FooterKind.QTAG
    assert info.footer.song_id
    assert info.ekey and not info.needs_ekey


def test_qtag_roundtrip_restores_the_original_flac(samples: Path, work_dir: Path):
    """QQ Music <= 19.51 writes QTag footers; this must decode byte-for-byte."""
    with prepare_input(samples / MFLAC_QTAG, work_dir, keystore=KeyStore()) as prepared:
        assert prepared.temporary
        assert prepared.path.read_bytes() == (samples / STANDARD).read_bytes()


def test_qtag_reports_the_embedded_key_as_its_source(samples: Path):
    info = inspect(samples / MFLAC_QTAG)
    assert "内嵌" in (info.ekey_source or "")


# --------------------------------------------------------------------------- #
# the V1 trailer is ambiguous, and real files use the *other* reading
# --------------------------------------------------------------------------- #


def test_real_world_v1_text_container_decrypts(samples: Path, work_dir: Path):
    """A real QQ Music download stores ASCII base64 ekey text in the V1 trailer.

    Treating those bytes as raw key material (the naive reading) base64-encodes
    them a second time and yields garbage -- this fixture is the regression
    guard for exactly that.
    """
    with prepare_input(samples / MFLAC_V1TEXT, work_dir, keystore=KeyStore()) as prepared:
        assert prepared.path.read_bytes() == (samples / STANDARD).read_bytes()


def test_v1_text_footer_offers_both_readings(samples: Path):
    info = inspect(samples / MFLAC_V1TEXT)
    assert info.footer.kind is FooterKind.V1
    # Best-first: the readable ASCII ekey, then the raw-bytes interpretation.
    assert len(info.footer.ekey_candidates) == 2
    assert info.ekey == info.footer.ekey_candidates[0]
    assert "\x00" not in info.ekey
    # "UVFNdXNpYyBFbmNWMixLZXk6" is base64 for "QQMusic EncV2,Key:".
    assert info.ekey.startswith("UVFNdXNpYyBFbmNWMixLZXk6")
    assert info.footer.raw_key is not None and info.footer.raw_key.endswith(b"\x00")


def test_v1_text_ekey_is_encv2_wrapped(samples: Path):
    """Real downloads nest EncV1 inside EncV2; parse_ekey must peel both."""
    import base64

    from echoshift.qmc.qmc2 import parse_ekey

    info = inspect(samples / MFLAC_V1TEXT)
    decoded = base64.b64decode(info.ekey + "=" * (-len(info.ekey) % 4))
    assert decoded.startswith(b"QQMusic EncV2,Key:")
    assert len(parse_ekey(info.ekey)) == 256


def test_raw_key_v1_trailer_still_works(samples: Path, work_dir: Path):
    """The other reading must not regress: raw key bytes in the trailer."""
    info = inspect(samples / MFLAC_MAP)
    assert info.footer.kind is FooterKind.V1
    with prepare_input(samples / MFLAC_MAP, work_dir, keystore=KeyStore()) as prepared:
        assert prepared.path.read_bytes() == (samples / STANDARD).read_bytes()


def test_key_selection_is_verified_against_the_file(samples: Path):
    """The decoder probes candidates and keeps the one that decrypts."""
    from echoshift.qmc.decoder import _choose_key

    choice = _choose_key(inspect(samples / MFLAC_V1TEXT))
    assert choice.raw_key is not None
    assert len(choice.raw_key) == 256
    assert choice.ekey == inspect(samples / MFLAC_V1TEXT).footer.ekey_candidates[0]


def test_every_v1_candidate_is_parseable_for_our_fixtures(samples: Path):
    """Both readings must survive parse_ekey, or selection could never work."""
    from echoshift.qmc.qmc2 import parse_ekey

    for name in (MFLAC_MAP, MFLAC_V1TEXT):
        for candidate in inspect(samples / name).all_ekeys:
            assert parse_ekey(candidate)


# --------------------------------------------------------------------------- #
# header-only peeking (fills the UI's duration column without a full decrypt)
# --------------------------------------------------------------------------- #


def test_parse_streaminfo_reads_duration_and_format(samples: Path):
    from echoshift.qmc.decoder import _parse_streaminfo

    head = (samples / STANDARD).read_bytes()[:64]
    peeked = _parse_streaminfo(head)
    assert peeked.sniffed == "flac"
    assert peeked.sample_rate == 44100
    assert peeked.channels == 2
    assert peeked.bits_per_sample == 16
    assert peeked.duration == pytest.approx(2.5, abs=0.1)


def test_parse_streaminfo_rejects_non_flac():
    from echoshift.qmc.decoder import _parse_streaminfo

    assert _parse_streaminfo(b"OggS" + bytes(60)).duration is None
    assert _parse_streaminfo(b"").duration is None


@pytest.mark.parametrize("name", [MFLAC_V1TEXT, MFLAC_QTAG, MFLAC_MAP, QMC1])
def test_peek_matches_the_real_duration_for_encrypted_containers(
    samples: Path, name: str
):
    from echoshift.qmc.decoder import peek

    info, peeked = peek(samples / name, KeyStore())
    assert info.path.name == name
    assert peeked.sniffed == "flac"
    assert peeked.duration == pytest.approx(2.5, abs=0.1)
    assert peeked.channels == 2


def test_peek_on_a_plain_file_does_not_need_a_key(samples: Path):
    from echoshift.qmc.decoder import peek

    container, peeked = peek(samples / STANDARD)
    assert container is None
    assert peeked.duration == pytest.approx(2.5, abs=0.1)


def test_peek_returns_nothing_useful_without_a_key(tmp_path: Path):
    from echoshift.qmc.decoder import peek

    # Encrypted-looking bytes with no recognisable trailer: no key anywhere.
    target = tmp_path / "keyless.mflac"
    target.write_bytes(bytes(range(256)) * 20)

    info, peeked = peek(target, KeyStore())
    assert info is not None and info.needs_ekey
    assert peeked.duration is None


def test_peek_describes_itself(samples: Path):
    from echoshift.qmc.decoder import peek

    _info, peeked = peek(samples / MFLAC_V1TEXT, KeyStore())
    text = peeked.describe()
    assert "FLAC" in text and "44.1 kHz" in text and "立体声" in text


# --------------------------------------------------------------------------- #
# content sniffing -- the escape hatch for unfamiliar extensions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "head,expected",
    [
        (b"fLaC\x00\x00\x00\x22", "flac"),
        (b"OggS\x00\x02", "ogg"),
        (b"ID3\x04\x00", "mp3"),
        (b"\xff\xfb\x90\x00", "mp3"),
        (b"\x00\x00\x00\x20ftypM4A ", "isobmff"),
        (b"\x1aE\xdf\xa3\x01\x00", "matroska"),
        (b"FLV\x01\x05", "flv"),
        (b"MThd\x00\x00\x00\x06", "midi"),
        (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "asf"),
        (b"\x0b\x77\x00\x00", "ac3"),
        (b"TTA1\x01\x00", "tta"),
        (b"caff\x00\x01", "caf"),
        (b"\x47\x40\x00\x10", "mpegts"),
        (b"\x89PNG\r\n\x1a\n", None),
        (b"\xff\xd8\xff\xe0", None),
        (b"PK\x03\x04", None),
        (b"hello world", None),
    ],
)
def test_sniff_audio_recognises_media_containers(head, expected):
    assert sniff_audio(head) == expected


def test_looks_like_audio_reads_the_file(tmp_path: Path):
    audio = tmp_path / "mislabelled.bin"
    audio.write_bytes(b"fLaC" + bytes(64))
    assert looks_like_audio(audio)

    picture = tmp_path / "cover.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0" + bytes(64))
    assert not looks_like_audio(picture)


def test_looks_like_audio_handles_unreadable_paths(tmp_path: Path):
    assert not looks_like_audio(tmp_path / "missing.bin")


def test_video_containers_are_declared_supported():
    for extension in ("mkv", "mp4", "webm", "mov"):
        assert extension in VIDEO_CONTAINER_EXTENSIONS
        assert is_supported_path(f"movie.{extension}")


def test_extension_list_covers_the_common_audio_formats():
    for extension in ("m4b", "oga", "opus", "ape", "wv", "dsf", "aiff", "wma", "amr"):
        assert is_supported_path(f"track.{extension}"), extension
