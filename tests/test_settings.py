"""Bitrate / sample-rate / channel rules."""

from __future__ import annotations

import pytest

from echoshift.errors import SettingsError
from echoshift.core.settings import (
    MP3_BITRATES_MPEG1,
    MP3_BITRATES_MPEG2,
    SAMPLE_RATES,
    BitrateMode,
    ChannelMode,
    EncodeSettings,
    allowed_bitrates,
    mpeg_version,
    nearest_supported_rate,
)


@pytest.mark.parametrize(
    "rate,expected",
    [
        (48000, "1"),
        (44100, "1"),
        (32000, "1"),
        (24000, "2"),
        (22050, "2"),
        (16000, "2"),
        (12000, "2.5"),
        (11025, "2.5"),
        (8000, "2.5"),
    ],
)
def test_mpeg_version_matches_the_mp3_spec(rate, expected):
    assert mpeg_version(rate) == expected


def test_mpeg_version_rejects_unsupported_rate():
    with pytest.raises(SettingsError):
        mpeg_version(96000)


def test_allowed_bitrates_switch_at_32khz():
    assert allowed_bitrates(44100) == MP3_BITRATES_MPEG1
    assert allowed_bitrates(32000) == MP3_BITRATES_MPEG1
    assert allowed_bitrates(24000) == MP3_BITRATES_MPEG2
    assert allowed_bitrates(8000) == MP3_BITRATES_MPEG2
    assert max(allowed_bitrates(8000)) == 160


@pytest.mark.parametrize(
    "rate,expected",
    [
        (96000, 48000),
        (192000, 48000),
        (88200, 48000),
        (64000, 48000),
        (44100, 44100),
        (40000, 32000),
        (4000, 8000),
    ],
)
def test_nearest_supported_rate_never_upsamples(rate, expected):
    assert nearest_supported_rate(rate) == expected


def test_sample_rates_match_libmp3lame_capability():
    assert SAMPLE_RATES == (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)


# --------------------------------------------------------------------------- #
# validate()
# --------------------------------------------------------------------------- #


def test_validate_accepts_defaults():
    EncodeSettings().validate()


def test_validate_rejects_bad_vbr_quality():
    with pytest.raises(SettingsError):
        EncodeSettings(vbr_quality=11).validate()
    with pytest.raises(SettingsError):
        EncodeSettings(vbr_quality=-1).validate()


def test_validate_rejects_unsupported_sample_rate():
    with pytest.raises(SettingsError):
        EncodeSettings(sample_rate=96000).validate()


def test_validate_rejects_bitrate_illegal_at_the_target_rate():
    # 320 kbps simply does not exist below 32 kHz.
    with pytest.raises(SettingsError):
        EncodeSettings(
            mode=BitrateMode.CBR, cbr_bitrate=320, sample_rate=22050
        ).validate()
    with pytest.raises(SettingsError):
        EncodeSettings(
            mode=BitrateMode.ABR, abr_bitrate=192, sample_rate=8000
        ).validate()
    # ...but it is fine at 44.1 kHz.
    EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320, sample_rate=44100).validate()


def test_validate_rejects_bad_id3_version():
    with pytest.raises(SettingsError):
        EncodeSettings(id3_version=2).validate()


# --------------------------------------------------------------------------- #
# resolve()
# --------------------------------------------------------------------------- #


def test_resolve_keeps_source_rate_when_supported():
    plan = EncodeSettings().resolve(44100, 2)
    assert plan.sample_rate is None
    assert plan.channels is None
    assert plan.warnings == ()


def test_resolve_clamps_hires_source_and_warns():
    plan = EncodeSettings().resolve(96000, 2)
    assert plan.sample_rate == 48000
    assert any("96000" in w for w in plan.warnings)


def test_resolve_downsamples_surround_and_warns():
    plan = EncodeSettings(channels=ChannelMode.KEEP).resolve(48000, 6)
    assert plan.channels == 2
    assert any("6 声道" in w for w in plan.warnings)


def test_resolve_honours_explicit_stereo_request_without_warning():
    plan = EncodeSettings(channels=ChannelMode.STEREO).resolve(48000, 6)
    assert plan.channels == 2
    assert plan.warnings == ()


def test_resolve_mono_request():
    assert EncodeSettings(channels=ChannelMode.MONO).resolve(44100, 2).channels == 1


def test_resolve_reports_the_vbr_nominal_bitrate():
    plan = EncodeSettings(mode=BitrateMode.VBR, vbr_quality=0).resolve(44100, 2)
    assert plan.vbr_quality == 0
    assert plan.nominal_kbps == 245


def test_resolve_clamps_an_illegal_bitrate_for_the_effective_rate():
    # No explicit sample rate, so the check happens against the source's.
    plan = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320).resolve(22050, 2)
    assert plan.nominal_kbps == 160
    assert any("码率" in w for w in plan.warnings)


# --------------------------------------------------------------------------- #
# serialisation / clamping
# --------------------------------------------------------------------------- #


def test_roundtrip_through_dict():
    original = EncodeSettings(
        mode=BitrateMode.ABR,
        abr_bitrate=128,
        sample_rate=22050,
        channels=ChannelMode.MONO,
        id3_version=3,
        write_id3v1=True,
    )
    assert EncodeSettings.from_dict(original.to_dict()) == original


def test_from_dict_tolerates_garbage():
    assert EncodeSettings.from_dict(None) == EncodeSettings()
    assert EncodeSettings.from_dict({}) == EncodeSettings()
    settings = EncodeSettings.from_dict(
        {
            "mode": "nonsense",
            "channels": "nonsense",
            "sample_rate": "keep",
            "vbr_quality": "loud",
            "id3_version": 9,
        }
    )
    assert settings.mode is BitrateMode.VBR
    assert settings.channels is ChannelMode.KEEP
    assert settings.sample_rate is None
    assert settings.vbr_quality == EncodeSettings().vbr_quality
    assert settings.id3_version == 3  # 9 is not 4, so it snaps to 3


def test_clamped_pulls_values_into_range():
    settings = EncodeSettings(
        vbr_quality=42, cbr_bitrate=1000, sample_rate=12345
    ).clamped()
    assert settings.vbr_quality == 9
    assert settings.cbr_bitrate == 320
    assert settings.sample_rate == 12000
