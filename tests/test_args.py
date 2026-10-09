"""ffmpeg argv construction."""

from __future__ import annotations

from pathlib import Path

from echoshift.core.args import build_ffmpeg_args, build_decode_check_args
from echoshift.core.settings import BitrateMode, ChannelMode, EncodeSettings

FFMPEG = Path("C:/tools/ffmpeg.exe")
SOURCE = Path("C:/music/song.flac")
OUTPUT = Path("C:/out/song.mp3")


def _plan(settings: EncodeSettings, rate: int | None = None, channels: int | None = None):
    return settings.resolve(rate, channels)


def _args(settings: EncodeSettings, plan=None, **kwargs) -> list[str]:
    return build_ffmpeg_args(
        FFMPEG, SOURCE, OUTPUT, settings, plan or _plan(settings), **kwargs
    )


def _value_after(args: list[str], flag: str) -> str | None:
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def test_uses_libmp3lame_and_maps_audio_first():
    args = _args(EncodeSettings())
    assert args[0] == str(FFMPEG)
    assert "-hide_banner" in args
    assert "-nostdin" in args
    assert _value_after(args, "-i") == str(SOURCE)
    assert _value_after(args, "-map") == "0:a:0"
    assert _value_after(args, "-c:a") == "libmp3lame"
    assert args[-1] == str(OUTPUT)


def test_vbr_uses_q_scale_and_no_bitrate():
    args = _args(EncodeSettings(mode=BitrateMode.VBR, vbr_quality=4))
    assert _value_after(args, "-q:a") == "4"
    assert "-b:a" not in args
    assert "-abr" not in args


def test_cbr_sets_bitrate_without_abr_flag():
    args = _args(EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=224))
    assert _value_after(args, "-b:a") == "224k"
    assert "-abr" not in args
    assert "-q:a" not in args


def test_abr_sets_bitrate_and_the_abr_switch():
    args = _args(EncodeSettings(mode=BitrateMode.ABR, abr_bitrate=96))
    assert _value_after(args, "-b:a") == "96k"
    assert _value_after(args, "-abr") == "1"


def test_no_sample_rate_flag_when_keeping_the_source():
    args = _args(EncodeSettings(), plan=_plan(EncodeSettings(), 44100, 2))
    assert "-ar" not in args
    assert "-ac" not in args


def test_explicit_sample_rate_and_channels_are_emitted():
    settings = EncodeSettings(sample_rate=22050, channels=ChannelMode.MONO)
    args = _args(settings, plan=_plan(settings, 44100, 2))
    assert _value_after(args, "-ar") == "22050"
    assert _value_after(args, "-ac") == "1"


def test_auto_clamped_rate_is_emitted():
    settings = EncodeSettings()
    args = _args(settings, plan=_plan(settings, 96000, 2))
    assert _value_after(args, "-ar") == "48000"


def test_surround_source_forces_stereo():
    settings = EncodeSettings(channels=ChannelMode.KEEP)
    args = _args(settings, plan=_plan(settings, 48000, 6))
    assert _value_after(args, "-ac") == "2"


def test_cover_art_mapping_is_optional():
    args = _args(EncodeSettings(write_cover=True))
    maps = [args[i + 1] for i, token in enumerate(args) if token == "-map"]
    assert maps == ["0:a:0", "0:v?"]
    assert _value_after(args, "-c:v") == "copy"
    assert _value_after(args, "-disposition:v:0") == "attached_pic"


def test_cover_art_mapping_can_be_disabled():
    args = _args(EncodeSettings(write_cover=False))
    maps = [args[i + 1] for i, token in enumerate(args) if token == "-map"]
    assert maps == ["0:a:0"]
    assert "-disposition:v:0" not in args


def test_tag_copy_can_be_disabled():
    assert _value_after(_args(EncodeSettings(copy_tags=True)), "-map_metadata") == "0"
    assert _value_after(_args(EncodeSettings(copy_tags=False)), "-map_metadata") == "-1"


def test_id3_options_are_explicit():
    args = _args(EncodeSettings(id3_version=3, write_id3v1=True))
    assert _value_after(args, "-id3v2_version") == "3"
    assert _value_after(args, "-write_id3v1") == "1"


def test_joint_stereo_is_pinned():
    assert _value_after(_args(EncodeSettings(joint_stereo=True)), "-joint_stereo") == "1"
    assert _value_after(_args(EncodeSettings(joint_stereo=False)), "-joint_stereo") == "0"


def test_overwrite_flag_follows_the_setting():
    assert "-y" in _args(EncodeSettings(), overwrite=True)
    assert "-n" in _args(EncodeSettings(), overwrite=False)


def test_progress_flags_are_opt_in():
    with_progress = _args(EncodeSettings(), progress=True)
    assert _value_after(with_progress, "-progress") == "pipe:1"
    assert "-nostats" in with_progress
    assert "-progress" not in _args(EncodeSettings(), progress=False)


def test_decode_check_targets_the_null_muxer():
    args = build_decode_check_args(FFMPEG, OUTPUT)
    assert "-xerror" in args
    assert args[args.index("-f") + 1] == "null"
    assert args[-1] == "-"


def test_args_are_all_strings():
    for token in _args(EncodeSettings()):
        assert isinstance(token, str)
