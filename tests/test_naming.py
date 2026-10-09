"""Output path templating and Windows-safe sanitisation."""

from __future__ import annotations

from pathlib import Path

import pytest

from echoshift.core.naming import (
    DEFAULT_TEMPLATE,
    render_output_path,
    sanitize_component,
)
from echoshift.core.probe import AudioStreamInfo, MediaInfo
from echoshift.core.settings import BitrateMode, EncodeSettings

OUT = Path("C:/out")


def _info(tags: dict[str, str] | None = None, name: str = "song.flac") -> MediaInfo:
    return MediaInfo(
        path=Path(name),
        format_name="flac",
        format_long_name="raw FLAC",
        duration=180.0,
        size=1024,
        bit_rate=900_000,
        tags=tags or {},
        audio=AudioStreamInfo(
            index=0,
            codec_name="flac",
            codec_long_name="FLAC",
            sample_rate=44100,
            channels=2,
            channel_layout="stereo",
            bit_rate=None,
            duration=180.0,
            bits_per_raw_sample=16,
        ),
    )


def _plan(settings: EncodeSettings | None = None):
    return (settings or EncodeSettings()).resolve(44100, 2)


# --------------------------------------------------------------------------- #
# sanitisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("normal", "normal"),
        ("a/b", "a_b"),
        ("a\\b", "a_b"),
        ('a<b>c:d"e|f?g*h', "a_b_c_d_e_f_g_h"),
        ("trailing dots...", "trailing dots"),
        ("trailing spaces   ", "trailing spaces"),
        ("", "_"),
        ("   ", "_"),
        ("\x01\x02", "_"),
    ],
)
def test_sanitize_component_makes_windows_safe_names(raw, expected):
    assert sanitize_component(raw) == expected


@pytest.mark.parametrize("reserved", ["CON", "nul", "COM1", "lpt9"])
def test_sanitize_component_escapes_reserved_device_names(reserved):
    assert sanitize_component(reserved) == f"_{reserved}"


def test_sanitize_component_truncates_absurdly_long_names():
    assert len(sanitize_component("x" * 500)) <= 120


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_default_template_uses_artist_and_title():
    info = _info({"artist": "歌手", "title": "曲名"})
    assert render_output_path(DEFAULT_TEMPLATE, info, _plan(), OUT) == OUT / "歌手 - 曲名.mp3"


def test_missing_tags_fall_back_to_the_original_filename():
    info = _info({}, name="mystery.flac")
    assert render_output_path("{artist} - {title}.mp3", info, _plan(), OUT) == OUT / "mystery.mp3"


def test_original_path_drives_the_filename_placeholder():
    """Decrypted intermediates must not leak their temp name into the output."""
    info = _info({}, name="echoshift_ab12cd34.flac")
    result = render_output_path(
        "{filename}.mp3", info, _plan(), OUT, original_path=Path("D:/music/Track 01.mflac")
    )
    assert result == OUT / "Track 01.mp3"


def test_nested_template_creates_directories():
    info = _info({"artist": "A", "album": "B", "title": "C"})
    result = render_output_path("{artist}/{album}/{title}.mp3", info, _plan(), OUT)
    assert result == OUT / "A" / "B" / "C.mp3"


def test_track_number_format_spec():
    info = _info({"artist": "A", "album": "B", "title": "C", "track": "7"})
    result = render_output_path("{artist}/{album}/{track:02d} {title}.mp3", info, _plan(), OUT)
    assert result == OUT / "A" / "B" / "07 C.mp3"


def test_missing_track_does_not_leave_a_dangling_prefix():
    info = _info({"artist": "A", "album": "B", "title": "C"})
    result = render_output_path("{artist}/{album}/{track:02d} {title}.mp3", info, _plan(), OUT)
    assert result == OUT / "A" / "B" / "C.mp3"


def test_track_with_leading_garbage_still_parses():
    info = _info({"title": "C", "track": "7/12"})
    result = render_output_path("{track:02d} {title}.mp3", info, _plan(), OUT)
    assert result == OUT / "07 C.mp3"


def test_albumartist_falls_back_to_artist():
    info = _info({"artist": "Solo", "album": "Alb", "title": "T"})
    result = render_output_path("{albumartist}/{album}/{title}.mp3", info, _plan(), OUT)
    assert result == OUT / "Solo" / "Alb" / "T.mp3"


def test_illegal_characters_in_tags_are_neutralised():
    info = _info({"artist": "AC/DC", "title": 'Say "Hi"?'})
    result = render_output_path("{artist} - {title}.mp3", info, _plan(), OUT)
    assert result.parent == OUT
    assert "/" not in result.name and '"' not in result.name and "?" not in result.name


def test_path_traversal_is_contained():
    info = _info({"title": "..", "artist": ".."})
    result = render_output_path("{artist}/{title}.mp3", info, _plan(), OUT)
    assert OUT in result.parents
    assert ".." not in result.parts


def test_absolute_template_path_cannot_escape_the_output_root():
    info = _info({"title": "song"})
    result = render_output_path("C:/Windows/song.mp3", info, _plan(), OUT)
    assert OUT in result.parents
    assert result.name == "song.mp3"
    # Nothing in the result is absolute: it all hangs off the output root, and
    # the drive colon is neutralised (then trimmed as edge punctuation).
    assert result.relative_to(OUT).parts == ("C", "Windows", "song.mp3")


def test_tag_values_containing_slashes_stay_inside_one_component():
    """A tag is data, not a path: 'AC/DC' must not create a directory."""
    info = _info({"artist": "AC/DC", "title": "Back in Black"})
    result = render_output_path("{artist}/{title}.mp3", info, _plan(), OUT)
    assert result == OUT / "AC_DC" / "Back in Black.mp3"


def test_extension_is_forced():
    info = _info({"title": "song"})
    assert render_output_path("{title}.flac", info, _plan(), OUT).name == "song.mp3"
    assert render_output_path("{title}.mp3", info, _plan(), OUT).name == "song.mp3"
    info2 = _info({"title": "noext"})
    assert render_output_path("{title}", info2, _plan(), OUT).name == "noext.mp3"


def test_index_placeholder():
    info = _info({"title": "T"})
    result = render_output_path("{index:03d} {title}.mp3", info, _plan(), OUT, index=12)
    assert result.name == "012 T.mp3"


def test_encode_placeholders_reflect_the_plan():
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192, sample_rate=44100)
    info = _info({"title": "T"})
    result = render_output_path(
        "{title} {mode} {bitrate} {samplerate}.mp3", info, settings.resolve(44100, 2), OUT
    )
    assert result.name == "T cbr 192 44100.mp3"


def test_unknown_placeholder_is_blank_not_an_error():
    info = _info({"title": "T"})
    result = render_output_path("{title} {nonsense}.mp3", info, _plan(), OUT)
    assert result.name == "T.mp3"


def test_empty_template_falls_back_to_the_default():
    info = _info({"artist": "A", "title": "T"})
    assert render_output_path("", info, _plan(), OUT).name == "A - T.mp3"
    assert render_output_path("   ", info, _plan(), OUT).name == "A - T.mp3"


def test_bad_format_spec_raises_a_readable_error():
    info = _info({"title": "T"})
    with pytest.raises(ValueError):
        render_output_path("{title:!!}.mp3", info, _plan(), OUT)
